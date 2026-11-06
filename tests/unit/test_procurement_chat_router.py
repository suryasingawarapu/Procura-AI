"""Unit tests for the chat frontend's LLM-based message router. The LLM
path is tested with an injected fake (never a real network call); the
regex-heuristic fallback is tested by forcing get_llm() to fail, matching
the existing convention in test_procurement_procura.py for
interpret_purchase_request.
"""

from __future__ import annotations

from unittest.mock import patch

from app.procurement import chat_router, workflow


def _sample_pr(workflow_state="Waiting For Approval", blockers=None, coordinator_result=None):
    return workflow.PurchaseRequest(
        pr_id="PR-TEST0001",
        requested_by="jane@example.com",
        item_description="14-inch laptop",
        quantity=10,
        estimated_budget=600_000.0,
        delivery_location="Mumbai",
        required_date="2026-12-01",
        business_justification="refresh",
        mode="demo",
        workflow_state=workflow_state,
        blockers=blockers or [],
        coordinator_result=coordinator_result,
    )


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    def invoke(self, messages):
        class R:
            pass

        r = R()
        r.content = self._content
        return r


def test_new_task_intent():
    # The LLM never supplies -- and the caller never uses -- a rewritten
    # instruction; only the user's own original message is ever parsed
    # downstream (see route's own docstring on why: a paraphrase could
    # silently corrupt the deterministic budget/deadline regexes).
    llm = _FakeLLM('{"intent": "new_task"}')
    decision = chat_router.route_chat_message(message="I need 10 laptops please", pr=None, llm=llm)
    assert decision.intent == "new_task"


def test_answer_intent_returns_reply_with_no_task_action():
    llm = _FakeLLM('{"intent": "answer", "reply": "Vendra checks vendor compliance."}')
    decision = chat_router.route_chat_message(message="what does vendra do", pr=_sample_pr(), llm=llm)
    assert decision.intent == "answer"
    assert decision.reply == "Vendra checks vendor compliance."


def test_unsupported_action_intent_never_claims_success():
    llm = _FakeLLM('{"intent": "unsupported_action", "reply": "I cannot pay invoices yet."}')
    decision = chat_router.route_chat_message(message="pay the pending invoice for the vendor", pr=_sample_pr(), llm=llm)
    assert decision.intent == "unsupported_action"
    assert "cannot pay" in decision.reply


def test_send_email_to_the_vendor_is_a_real_action_decided_in_code():
    decision = chat_router.route_chat_message(message="send an email to the vendor", pr=_sample_pr(), llm=_FakeLLM("unused"))
    assert decision.intent == "send_po_email"


def test_truncated_json_salvages_the_partial_reply_text():
    """Regression test for a real bug found live: a long "answer" (e.g.
    describing all 18 specialists) can get cut off mid-JSON by the
    completion-token limit -- json.loads then fails, and the real,
    mostly-good answer the model actually wrote must be salvaged rather
    than discarded for a generic "I'm not sure" non-answer."""
    llm = _FakeLLM(
        '{"intent": "answer", "reply": "Sure! Here is what Vendra does: vendor_discovery finds '
        "active suppliers, document_verification checks compliance docs, and vendor_profile"
    )  # deliberately truncated mid-string, no closing quote/brace
    decision = chat_router.route_chat_message(message="list what vendra's specialists do", pr=_sample_pr(), llm=llm)
    assert decision.intent == "answer"
    assert "vendor_discovery" in decision.reply
    assert "document_verification" in decision.reply


def test_provide_info_intent():
    llm = _FakeLLM('{"intent": "provide_info", "reply": "Got it."}')
    decision = chat_router.route_chat_message(
        message="DEMO-LAPTOP-14", pr=_sample_pr(workflow_state="Waiting For Information"), llm=llm
    )
    assert decision.intent == "provide_info"


def test_unparseable_llm_reply_falls_back_to_heuristic():
    llm = _FakeLLM("not json at all")
    decision = chat_router.route_chat_message(message="Purchase 10 laptops", pr=None, llm=llm)
    assert decision.intent == "new_task"


def test_unknown_intent_value_falls_back_to_heuristic():
    llm = _FakeLLM('{"intent": "something_else", "reply": "??"}')
    decision = chat_router.route_chat_message(message="Purchase 10 laptops", pr=None, llm=llm)
    assert decision.intent == "new_task"


@patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no LLM provider configured"))
def test_provide_info_is_decided_deterministically_without_ever_calling_the_llm(mock_get_llm):
    """Regression test for a real bug found live: the LLM misclassified a
    message that unambiguously supplied the missing item code (while a
    task was genuinely "Waiting For Information") as a plain question
    roughly 1 in 3 times, leaving the task stuck needing the same value
    re-sent. This case is now decided deterministically, before the LLM
    is ever consulted -- get_llm must not be called at all here, not just
    "called but its answer overridden"."""
    decision = chat_router.route_chat_message(
        message="DEMO-LAPTOP-14", pr=_sample_pr(workflow_state="Waiting For Information"), llm=None
    )
    assert decision.intent == "provide_info"
    mock_get_llm.assert_not_called()


def test_provide_info_still_defers_to_the_llm_for_a_genuine_question():
    """A real question asked while a task waits ("why do you need that?")
    must not be swallowed as though it supplied the missing value."""
    llm = _FakeLLM('{"intent": "answer", "reply": "Because no free-text item-catalog matching exists yet."}')
    decision = chat_router.route_chat_message(
        message="why do you need an item code?", pr=_sample_pr(workflow_state="Waiting For Information"), llm=llm
    )
    assert decision.intent == "answer"


@patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no LLM provider configured"))
def test_heuristic_fallback_never_defaults_a_plain_greeting_to_new_task(mock_get_llm):
    """Regression test for a real bug found live: a plain "hii" with no
    active task used to fall through to "new_task" unconditionally,
    which then crashed start_procurement_task with a raw "quantity must
    be positive" error. A message that doesn't look like a purchase
    request must answer instead, even when the LLM is unavailable."""
    decision = chat_router.route_chat_message(message="hii", pr=None, llm=None)
    assert decision.intent == "answer"
    assert decision.reply


@patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no LLM provider configured"))
def test_heuristic_fallback_new_task_when_no_active_pr(mock_get_llm):
    decision = chat_router.route_chat_message(message="Purchase 10 laptops", pr=None, llm=None)
    assert decision.intent == "new_task"


@patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no LLM provider configured"))
def test_heuristic_fallback_new_task_even_while_waiting_if_message_looks_like_new_instruction(mock_get_llm):
    decision = chat_router.route_chat_message(
        message="Actually, purchase 5 chairs instead",
        pr=_sample_pr(workflow_state="Waiting For Information"),
        llm=None,
    )
    assert decision.intent == "new_task"


def test_context_summary_includes_recommendation_and_po_status():
    pr = _sample_pr(
        coordinator_result={"result": {"recommended_supplier": "Acme Corp", "recommended_total_cost": 500000.0}}
    )
    pr.purchase_order = {"status": "submitted"}
    summary = chat_router._context_summary(pr)
    assert "Acme Corp" in summary
    assert "submitted" in summary


def test_context_summary_with_no_active_task():
    assert "No active task" in chat_router._context_summary(None)


def test_context_summary_includes_per_vendor_quotation_scores():
    """Regression test for a real gap found live: a natural follow-up like
    'how much did each vendor quote' had no real data to answer from, even
    though Procura's own quotation_comparison specialist already computed
    exactly that (per-supplier total_cost/delivery_days/rank) -- it just
    was never included in the chat's context."""
    pr = _sample_pr(
        coordinator_result={
            "result": {"recommended_supplier": "Acme Corp", "recommended_total_cost": 54_400.0},
            "messages": [
                {
                    "sender": "procura",
                    "payload": {
                        "specialists": {
                            "quotation_comparison": {
                                "status": "ok",
                                "data": {
                                    "comparison": {
                                        "recommended_supplier": "Acme Corp",
                                        "scores": [
                                            {"supplier": "Acme Corp", "total_cost": 54400.0, "delivery_days": 7, "payment_terms_days": 30, "rank": 1},
                                            {"supplier": "Beta Supplies", "total_cost": 77520.0, "delivery_days": 5, "payment_terms_days": 15, "rank": 2},
                                        ],
                                        "rationale": "cheapest first",
                                    }
                                },
                            }
                        }
                    },
                }
            ],
        }
    )
    summary = chat_router._context_summary(pr)
    assert "Acme Corp" in summary and "54400.0" in summary
    assert "Beta Supplies" in summary and "77520.0" in summary
