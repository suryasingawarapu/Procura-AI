"""Chat: "vendor information" and "send the purchase order email" are handled by rule, not by the LLM."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api import procurement_routes as routes
from app.procurement import chat_router
from app.procurement.mail import outbox, service


def _pr(supplier="Clearline Hardware Partners"):
    return SimpleNamespace(
        pr_id="PR-1", workflow_state="Completed", quantity=10, item_code="LAPTOP", item_description="laptops",
        delivery_location="Mumbai", required_date="2026-11-23", purchase_order=None,
        coordinator_result={
            "result": {"recommended_supplier": supplier, "recommended_total_cost": 506000.0, "eligible_supplier_names": [supplier, "Silverline Technologies"]},
            "messages": [{"sender": "procura", "payload": {"specialists": {"quotation_comparison": {"data": {"comparison": {"scores": [
                {"rank": 1, "supplier": supplier, "total_cost": 506000.0, "delivery_days": 12, "payment_terms_days": 30},
                {"rank": 2, "supplier": "Silverline Technologies", "total_cost": 517000.0, "delivery_days": 9, "payment_terms_days": 45},
            ]}}}}}}],
        },
    )


@pytest.mark.parametrize("message", [
    "send the purchase order email to the vendor", "send email", "mail the vendor", "can u send the po to them", "email the supplier",
])
def test_send_requests_are_recognised_without_the_llm(message):
    d = chat_router.route_chat_message(message=message, pr=_pr(), llm=object())  # an llm that would fail if consulted
    assert d.intent == "send_po_email" and d.supplier == "Clearline Hardware Partners"


@pytest.mark.parametrize("message", [
    "give me the selected vendor information like email", "can u give me the information of the selected vendor",
    "send me the vendor details", "what is the supplier's phone number",
])
def test_vendor_info_requests_are_recognised(message):
    d = chat_router.route_chat_message(message=message, pr=_pr(), llm=object())
    assert d.intent == "vendor_info" and d.supplier == "Clearline Hardware Partners"


def test_vendor_info_names_another_compared_supplier():
    d = chat_router.route_chat_message(message="contact details of Silverline Technologies", pr=_pr(), llm=object())
    assert d.intent == "vendor_info" and d.supplier == "Silverline Technologies"


def test_drafting_and_plain_questions_still_go_to_the_llm():
    class Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("llm consulted")

    for message in ("Draft an email to the recommended vendor accepting their quote.", "who is the selected vendor"):
        d = chat_router.route_chat_message(message=message, pr=_pr(), llm=Boom())
        assert d.intent == "answer"  # the heuristic fallback, reached only because the LLM was consulted


def test_vendor_info_without_a_recommendation_is_left_to_the_llm():
    class Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError

    assert chat_router.route_chat_message(message="vendor details please", pr=None, llm=Boom()).intent == "answer"


class _Erp:
    def get_supplier(self, name):
        return {"email_id": "sales@clearline.example", "mobile_no": None, "website": "https://clearline.example", "country": "United States", "supplier_group": "Hardware"}


def test_vendor_info_reply_uses_real_data_and_says_what_is_missing():
    with patch.object(routes.task_orchestrator, "_resolve_client", return_value=(_Erp(), None)):
        text = routes._vendor_info_reply(_pr(), "Clearline Hardware Partners")
    assert "sales@clearline.example" in text
    assert "Phone: not on file" in text  # never invented
    assert "₹506,000" in text and "12 days" in text and "net 30 days" in text
    assert "send the purchase order email" in text


def test_po_email_response_previews_and_does_not_send(monkeypatch):
    monkeypatch.setenv("MAIL_TEST_RECIPIENT", "me@test.example")
    monkeypatch.delenv("MAIL_ALLOW_SEND", raising=False)
    mail = {"mail_id": "abc", "pr_id": "PR-1", "vendor": "Clearline", "to": "me@test.example", "intended_to": "sales@x.example",
            "subject": "Purchase Order PR-1", "body": "b", "attachment_name": "Purchase-Order-PR-1.pdf", "status": "draft"}
    with patch.object(routes.task_orchestrator, "_resolve_client", return_value=(_Erp(), None)), \
         patch.object(service, "draft_for_request", return_value=mail):
        r = routes._po_email_response(_pr())
    assert r.mail["mail_id"] == "abc" and r.mail["test_mode"] is True and r.mail["sending_allowed"] is False
    assert "only sent when you press" in r.reply or "only when you press" in r.reply
    assert "me@test.example" in r.reply and "MAIL_ALLOW_SEND" in r.reply


def test_po_email_response_explains_a_missing_address():
    with patch.object(routes.task_orchestrator, "_resolve_client", return_value=(_Erp(), None)), \
         patch.object(service, "draft_for_request", side_effect=outbox.MailError("the vendor has no valid email address on file")):
        r = routes._po_email_response(_pr())
    assert r.mail is None and "no valid email" in r.reply


def test_po_email_response_without_a_recommendation():
    r = routes._po_email_response(None)
    assert r.mail is None and "no recommended supplier" in r.reply
