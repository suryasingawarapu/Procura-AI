"""Vendor email outbox: drafting, safety gates, sending, reply tracking, follow-ups.

Memory and Gmail are faked; no network and no real mail.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.procurement.mail import outbox


class FakeGmail:
    def __init__(self):
        self.sent, self.replies, self.thread_messages = [], [], []

    def upload_file(self, name, data, mimetype="application/pdf"):
        self.uploads = getattr(self, "uploads", []) + [(name, data)]
        return {"name": name, "mimetype": mimetype, "s3key": "k1"}

    def send(self, *, to, subject, body, attachment=None):
        self.sent.append((to, subject, body))
        self.attachment = attachment
        return {"id": "m1", "threadId": "t1"}

    def reply(self, *, thread_id, to, body):
        self.replies.append((thread_id, to, body))
        return {"id": "m2", "threadId": thread_id}

    def thread(self, thread_id):
        return {"messages": self.thread_messages}


@pytest.fixture
def env(monkeypatch):
    rows = {}

    def save_memory(agent, key, content, **kw):
        row = rows.setdefault(key, {"id": uuid.uuid4().hex, "memory_key": key})
        row["content"] = content
        return {"id": row["id"]}

    def enrich(memory_id, **fields):
        for r in rows.values():
            if r["id"] == memory_id:
                r["structured_fact"] = fields["structured_fact"]
                r["source_type"] = fields["source_type"]

    gmail = FakeGmail()
    monkeypatch.setattr(outbox, "save_memory", save_memory)
    monkeypatch.setattr(outbox, "enrich_memory", enrich)
    monkeypatch.setattr(outbox, "embed_or_none", lambda text: None)
    monkeypatch.setattr(outbox, "get_agent_memories", lambda agent, **kw: list(rows.values()))
    monkeypatch.setattr(outbox, "_gmail", lambda: gmail)
    for name in ("MAIL_ALLOW_SEND", "MAIL_TEST_RECIPIENT", "MAIL_FOLLOWUP_DAYS", "MAIL_MAX_FOLLOWUPS"):
        monkeypatch.delenv(name, raising=False)
    return rows, gmail


def _draft(**over):
    args = dict(pr_id="PR-1", vendor="Acme", vendor_email="sales@acme.test", item="LAPTOP", quantity=10,
                unit_price=50000.0, total=500000.0, po_number="PO-9")
    args.update(over)
    return outbox.draft_po_email(**args)


def test_draft_is_stored_as_email_memory_and_sends_nothing(env):
    rows, gmail = env
    rec = _draft()
    assert rec["status"] == "draft" and rec["to"] == "sales@acme.test"
    assert "PO-9" in rec["subject"] and "attached" in rec["body"] and "500,000.00" not in rec["body"]
    assert rec["po"]["total"] == 500000.0 and rec["attachment_name"] == "Purchase-Order-PO-9.pdf"
    assert next(iter(rows.values()))["source_type"] == "email"
    assert gmail.sent == []


def test_no_vendor_email_is_an_error(env):
    with pytest.raises(outbox.MailError, match="no valid email"):
        _draft(vendor_email=None)


def test_test_recipient_redirects_and_names_the_intended_vendor(env, monkeypatch):
    monkeypatch.setenv("MAIL_TEST_RECIPIENT", "me@test.example")
    rec = _draft()
    assert rec["to"] == "me@test.example" and rec["intended_to"] == "sales@acme.test"
    assert "TEST MODE" in rec["body"] and "sales@acme.test" in rec["body"]


def test_sending_is_off_by_default(env):
    rec = _draft()
    with pytest.raises(outbox.MailError, match="MAIL_ALLOW_SEND"):
        outbox.send_mail(rec["mail_id"])


def test_send_marks_sent_and_is_idempotent(env, monkeypatch):
    _, gmail = env
    monkeypatch.setenv("MAIL_ALLOW_SEND", "1")
    rec = _draft()
    sent = outbox.send_mail(rec["mail_id"])
    assert sent["status"] == "sent" and sent["thread_id"] == "t1"
    outbox.send_mail(rec["mail_id"])
    assert len(gmail.sent) == 1


def test_vendor_reply_is_recorded_once_and_marks_replied(env, monkeypatch):
    _, gmail = env
    monkeypatch.setenv("MAIL_ALLOW_SEND", "1")
    rec = _draft()
    outbox.send_mail(rec["mail_id"])
    gmail.thread_messages = [
        {"messageId": "m1", "labelIds": ["SENT"], "sender": "us"},
        {"messageId": "r1", "labelIds": ["INBOX"], "sender": "sales@acme.test", "subject": "Re: PO",
         "messageText": "Confirmed, delivery in 12 days.", "messageTimestamp": "2026-11-04T10:00:00Z"},
    ]
    new = outbox.check_replies()
    assert len(new) == 1 and "12 days" in new[0]["body"]
    assert outbox.get_mail(rec["mail_id"])["status"] == "replied"
    assert outbox.check_replies() == []  # the same reply is never recorded twice


def test_followup_waits_then_sends_in_thread_and_is_capped(env, monkeypatch):
    _, gmail = env
    monkeypatch.setenv("MAIL_ALLOW_SEND", "1")
    monkeypatch.setenv("MAIL_MAX_FOLLOWUPS", "1")
    rec = _draft()
    outbox.send_mail(rec["mail_id"])
    assert outbox.send_followups() == []  # just sent: not due yet
    sent = outbox.send_followups(older_than_days=0)
    assert len(sent) == 1 and sent[0]["followups"] == 1 and gmail.replies[0][0] == "t1"
    assert outbox.send_followups(older_than_days=0) == []  # cap reached


def test_followup_skips_mail_that_was_answered(env, monkeypatch):
    _, gmail = env
    monkeypatch.setenv("MAIL_ALLOW_SEND", "1")
    rec = _draft()
    outbox.send_mail(rec["mail_id"])
    gmail.thread_messages = [{"messageId": "r1", "labelIds": ["INBOX"], "sender": "v", "messageText": "ok"}]
    outbox.check_replies()
    assert outbox.send_followups(older_than_days=0) == []


def test_po_goes_as_a_pdf_attachment_not_in_the_body(env, monkeypatch):
    _, gmail = env
    monkeypatch.setenv("MAIL_ALLOW_SEND", "1")
    rec = _draft()
    outbox.send_mail(rec["mail_id"])
    (name, data), = gmail.uploads
    assert name == "Purchase-Order-PO-9.pdf" and data[:5] == b"%PDF-"
    assert gmail.attachment == {"name": name, "mimetype": "application/pdf", "s3key": "k1"}
