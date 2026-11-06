"""Purchase-order emails to vendors: draft, send, track replies, follow up.

Every email -- and every vendor reply -- is one memory in Procura's space with
``source_type="email"``, so what was sent, when, to whom and what came back is
auditable and searchable like any other fact. A mail record's status moves
``draft -> sent -> replied`` (or ``sent -> followed_up`` while a vendor is quiet).

Safety gates, all off by default:
    MAIL_ALLOW_SEND=1       nothing is sent without this (drafts are always fine)
    MAIL_TEST_RECIPIENT=..  when set, EVERY mail is redirected to this address
                            (the intended vendor is stated in the body)
    MAIL_FOLLOWUP_DAYS=3    wait this long for a reply before a follow-up
    MAIL_MAX_FOLLOWUPS=2    follow-ups per mail, never more
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.memory.client import supabase
from app.memory.embeddings import embed_or_none
from app.memory.provenance_fields import enrich_memory
from app.memory.store import get_agent_memories, save_memory

from ..identities import PROCURA
from . import composio, pdf

_PREFIX = "email:"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OPEN = ("sent", "followed_up")


class MailError(ValueError):
    """A mail operation that cannot proceed (no recipient, sending disabled, unknown record)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def sending_allowed() -> bool:
    return _flag("MAIL_ALLOW_SEND")


def settings() -> Dict[str, Any]:
    return {
        "composio_configured": composio.configured(),
        "sending_allowed": sending_allowed(),
        "test_recipient": os.getenv("MAIL_TEST_RECIPIENT", "").strip() or None,
        "followup_days": _int("MAIL_FOLLOWUP_DAYS", 3),
        "max_followups": _int("MAIL_MAX_FOLLOWUPS", 2),
    }


# --- storage --------------------------------------------------------------------------------------
def _strip(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in {"memory_id", "key"}}


def _write(key: str, fact: Dict[str, Any], *, reason: str, client: Any = None) -> Dict[str, Any]:
    """Persist one mail record. The text carries the status, so a status change is a new memory version."""
    cl = client if client is not None else supabase
    if fact.get("kind") == "reply":
        text = f"Reply from {fact.get('from')} on '{fact.get('subject')}': {str(fact.get('body'))[:600]}"
    else:
        text = f"Email to {fact.get('vendor')} <{fact.get('intended_to') or fact.get('to')}> [{fact['status']}, follow-ups {fact.get('followups', 0)}]: {fact.get('subject')}"
    saved = save_memory(
        PROCURA, key, text, source_agent_id=PROCURA, source_reference=fact.get("thread_id") or fact.get("pr_id"),
        subject_key=f"mail:{fact.get('pr_id')}", embedding=embed_or_none(text), client=cl,
    )
    enrich_memory(
        saved["id"], client=cl, source_type="email", structured_fact=fact, task_id=fact.get("pr_id"),
        source_hash=hashlib.sha256(json.dumps(fact, sort_keys=True, default=str).encode()).hexdigest(),
        change_reason=reason, verification_status="unverified" if fact.get("kind") == "reply" else "verified",
        sharing_classification="restricted",
    )
    return {**fact, "memory_id": saved["id"], "key": key}


def _all(client: Any = None) -> List[Dict[str, Any]]:
    out = []
    for row in get_agent_memories(PROCURA, client=client):
        if str(row.get("memory_key", "")).startswith(_PREFIX) and row.get("structured_fact"):
            out.append({**row["structured_fact"], "memory_id": row["id"], "key": row["memory_key"]})
    return out


def list_mail(pr_id: Optional[str] = None, *, client: Any = None) -> List[Dict[str, Any]]:
    rows = _all(client)
    if pr_id:
        rows = [r for r in rows if r.get("pr_id") == pr_id]
    return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)


def get_mail(mail_id: str, *, client: Any = None) -> Dict[str, Any]:
    for r in _all(client):
        if r.get("mail_id") == mail_id:
            return r
    raise MailError(f"no such email: {mail_id}")


# --- recipient ------------------------------------------------------------------------------------
def resolve_recipient(vendor_email: Optional[str]) -> Dict[str, Optional[str]]:
    """Where a mail really goes. A test recipient overrides the vendor so nothing reaches a real supplier."""
    test = os.getenv("MAIL_TEST_RECIPIENT", "").strip()
    if test:
        if not _EMAIL.match(test):
            raise MailError("MAIL_TEST_RECIPIENT is not a valid email address")
        return {"to": test, "intended_to": vendor_email}
    if not vendor_email or not _EMAIL.match(vendor_email):
        raise MailError("the vendor has no valid email address on file")
    return {"to": vendor_email, "intended_to": vendor_email}


# --- draft ----------------------------------------------------------------------------------------
def draft_po_email(
    *, pr_id: str, vendor: str, vendor_email: Optional[str], item: str, quantity: int, unit_price: Optional[float],
    total: Optional[float], currency: str = "INR", po_number: Optional[str] = None, delivery_location: str = "",
    required_by: str = "", sender_name: str = "Procurement Team", client: Any = None,
) -> Dict[str, Any]:
    dest = resolve_recipient(vendor_email)
    ref = po_number or pr_id
    po = {
        "reference": ref, "date": datetime.now(timezone.utc).strftime("%d %b %Y"), "vendor": vendor, "vendor_email": vendor_email,
        "buyer": sender_name, "item": item, "quantity": quantity, "unit_price": unit_price, "total": total,
        "currency": currency, "delivery_location": delivery_location, "required_by": required_by,
    }
    lines = [f"Dear {vendor} team,", "", f"Please find our purchase order {ref} attached as a PDF.", ""]
    lines += ["Kindly reply to this email to confirm the order and the delivery date.", "", "Regards,", sender_name]
    body = "\n".join(lines)
    if os.getenv("MAIL_TEST_RECIPIENT", "").strip():
        body = f"[TEST MODE: this mail would go to {vendor_email or 'the vendor (no address on file)'}]\n\n{body}"
    fact = {
        "kind": "po", "mail_id": uuid.uuid4().hex[:12], "pr_id": pr_id, "vendor": vendor, "po_number": po_number,
        "to": dest["to"], "intended_to": dest["intended_to"], "subject": f"Purchase Order {ref} - {vendor}",
        "body": body, "po": po, "attachment_name": pdf.po_filename(po), "status": "draft", "followups": 0,
        "created_at": _now(), "sent_at": None, "thread_id": None,
    }
    return _write(f"{_PREFIX}{pr_id}:{fact['mail_id']}", fact, reason="PO email drafted", client=client)


# --- send / replies / follow-ups ------------------------------------------------------------------
def _gmail() -> composio.ComposioGmail:
    if not composio.configured():
        raise MailError("Composio Gmail is not configured (COMPOSIO_API_KEY / COMPOSIO_GMAIL_ACCOUNT)")
    return composio.get_client()


def send_mail(mail_id: str, *, client: Any = None) -> Dict[str, Any]:
    if not sending_allowed():
        raise MailError("sending is disabled: set MAIL_ALLOW_SEND=1")
    rec = get_mail(mail_id, client=client)
    if rec["status"] != "draft":
        return rec  # idempotent: never send the same mail twice
    gmail = _gmail()
    attachment = None
    if rec.get("po"):  # the purchase order travels as a PDF, not as text in the body
        attachment = gmail.upload_file(rec["attachment_name"], pdf.build_po_pdf(rec["po"]), "application/pdf")
    resp = gmail.send(to=rec["to"], subject=rec["subject"], body=rec["body"], attachment=attachment)
    rec.update(status="sent", sent_at=_now(), thread_id=resp.get("threadId"), message_id=resp.get("id"))
    return _write(rec["key"], _strip(rec), reason="PO email sent", client=client)


def check_replies(*, client: Any = None) -> List[Dict[str, Any]]:
    """Look at every open mail's thread; record each new vendor reply and mark the mail ``replied``."""
    found: List[Dict[str, Any]] = []
    rows = _all(client)
    known = {r.get("gmail_message_id") for r in rows if r.get("kind") == "reply"}
    for rec in [r for r in rows if r.get("kind") == "po" and r["status"] in _OPEN and r.get("thread_id")]:
        replied = False
        for msg in _gmail().thread(rec["thread_id"]).get("messages") or []:
            if "SENT" in (msg.get("labelIds") or []) or msg.get("messageId") in known:
                continue
            reply = {
                "kind": "reply", "mail_id": uuid.uuid4().hex[:12], "pr_id": rec["pr_id"], "vendor": rec["vendor"],
                "in_reply_to": rec["mail_id"], "thread_id": rec["thread_id"], "gmail_message_id": msg.get("messageId"),
                "from": msg.get("sender"), "subject": msg.get("subject"), "body": (msg.get("messageText") or "")[:4000],
                "received_at": msg.get("messageTimestamp"), "status": "received", "created_at": _now(),
            }
            found.append(_write(f"{_PREFIX}{rec['pr_id']}:reply:{msg.get('messageId')}", reply, reason="vendor reply recorded", client=client))
            replied = True
        if replied:
            rec["status"] = "replied"
            _write(rec["key"], _strip(rec), reason="vendor replied", client=client)
    return found


def send_followups(*, older_than_days: Optional[int] = None, client: Any = None) -> List[Dict[str, Any]]:
    """Nudge vendors that have not replied. Bounded: waits the configured days, stops at the maximum."""
    if not sending_allowed():
        raise MailError("sending is disabled: set MAIL_ALLOW_SEND=1")
    cfg = settings()
    days = cfg["followup_days"] if older_than_days is None else older_than_days
    now = datetime.now(timezone.utc)
    sent: List[Dict[str, Any]] = []
    for rec in [r for r in _all(client) if r.get("kind") == "po" and r["status"] in _OPEN]:
        last = rec.get("last_followup_at") or rec.get("sent_at")
        if not last or not rec.get("thread_id") or rec.get("followups", 0) >= cfg["max_followups"]:
            continue
        if (now - datetime.fromisoformat(last)).total_seconds() < days * 86400:
            continue
        body = (
            f"Dear {rec['vendor']} team,\n\nA gentle reminder: we have not yet received a reply to our message "
            f"'{rec['subject']}'. Could you please confirm the order and the delivery date?\n\nRegards,\nProcurement Team"
        )
        _gmail().reply(thread_id=rec["thread_id"], to=rec["to"], body=body)
        rec.update(followups=rec.get("followups", 0) + 1, last_followup_at=_now(), status="followed_up")
        sent.append(_write(rec["key"], _strip(rec), reason=f"follow-up #{rec['followups']} sent", client=client))
    return sent
