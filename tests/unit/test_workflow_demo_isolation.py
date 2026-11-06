"""Regression tests for a real, serious bug found live: a demo-mode
Purchase Request's own lifecycle writes (create_purchase_request's first
"created" save, every subsequent _save_pr, and record_approval_decision's
own approval-record write) fell completely outside the narrow
unittest.mock.patch that only ever wrapped app.procurement.task_
orchestrator's call to run_coordinator() -- so every demo-mode task
throughout this project silently wrote a real row to production Supabase,
directly contradicting the repeated, explicit guarantee that demo mode
never touches it.

These tests exercise the REAL workflow.py functions (no mocking of
_save_pr/_tool/enrich_memory) and assert that nothing reaches the real
Supabase-backed memory tools for mode="demo" -- if it did, these tests
would either hang on a real network call or raise (no real Supabase
credentials are required for mode="demo" to work at all now, which is
itself part of what's being proven).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.procurement import workflow


@pytest.fixture(autouse=True)
def _clear_demo_stores():
    """The demo stores are module-level and must not leak state between
    tests."""
    workflow._DEMO_PR_STORE.clear()
    workflow._DEMO_APPROVAL_STORE.clear()
    yield
    workflow._DEMO_PR_STORE.clear()
    workflow._DEMO_APPROVAL_STORE.clear()


def _assert_no_real_memory_tool_is_ever_called():
    """Patches the real Supabase-backed save path to raise if touched --
    a demo-mode call must never reach it at all."""
    return patch(
        "app.procurement.workflow._tool",
        side_effect=AssertionError("a demo-mode Purchase Request reached the REAL memory tool -- regression!"),
    )


def test_create_purchase_request_demo_mode_never_touches_real_memory_tool():
    with _assert_no_real_memory_tool_is_ever_called():
        pr = workflow.create_purchase_request(
            requested_by="jane@example.com",
            item_description="laptops",
            quantity=10,
            estimated_budget=100_000.0,
            delivery_location="Mumbai",
            required_date="2026-12-01",
            business_justification="refresh",
            mode="demo",
        )
    assert pr.mode == "demo"
    assert pr.pr_id in workflow._DEMO_PR_STORE


def test_demo_pr_is_retrievable_across_separate_calls_like_separate_http_requests():
    """The whole point of the in-memory store, not a pure no-op: a demo
    task spans several separate requests (create -> supply missing info
    -> resume -> approve), each one a fresh call to get_purchase_request."""
    with _assert_no_real_memory_tool_is_ever_called():
        created = workflow.create_purchase_request(
            requested_by="jane@example.com",
            item_description="laptops",
            quantity=10,
            estimated_budget=100_000.0,
            delivery_location="Mumbai",
            required_date="2026-12-01",
            business_justification="refresh",
            mode="demo",
        )
        fetched = workflow.get_purchase_request(created.pr_id)
    assert fetched is not None
    assert fetched.pr_id == created.pr_id
    assert fetched.mode == "demo"


def test_update_and_transition_on_a_demo_pr_never_touch_real_memory_tool():
    with _assert_no_real_memory_tool_is_ever_called():
        created = workflow.create_purchase_request(
            requested_by="jane@example.com",
            item_description="laptops",
            quantity=10,
            estimated_budget=100_000.0,
            delivery_location="Mumbai",
            required_date="2026-12-01",
            business_justification="refresh",
            mode="demo",
        )
        workflow.update_purchase_request(created.pr_id, item_code="DEMO-LAPTOP-14")
        workflow.transition_purchase_request(created.pr_id, "Pending Approval")
        fetched = workflow.get_purchase_request(created.pr_id)
    assert fetched.item_code == "DEMO-LAPTOP-14"
    assert fetched.status == "Pending Approval"


def test_approval_decision_on_a_demo_pr_never_touches_real_memory_tool():
    """Regression test for the second half of the same bug: a demo task
    can genuinely reach the approval UI, and clicking Approve/Reject on
    one used to write a real approval record to Finora's real memory
    regardless of mode."""
    with _assert_no_real_memory_tool_is_ever_called():
        created = workflow.create_purchase_request(
            requested_by="jane@example.com",
            item_description="laptops",
            quantity=10,
            estimated_budget=100_000.0,
            delivery_location="Mumbai",
            required_date="2026-12-01",
            business_justification="refresh",
            mode="demo",
        )
        workflow.transition_purchase_request(created.pr_id, "Pending Approval")
        decision = workflow.record_approval_decision(
            pr_id=created.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved"
        )
        history = workflow.get_approval_history(created.pr_id)
    assert decision.decision == "Approved"
    assert len(history) == 1
    assert history[0]["approver_id"] == "mgr-1"


def test_live_mode_is_completely_unaffected_still_uses_the_real_tool():
    """This fix must only change behavior for mode="demo" -- a live-mode
    (or mode=None, e.g. the pre-existing manual PR workflow) call must
    still reach the real _tool exactly as before."""
    with patch("app.procurement.workflow._tool") as mock_tool:
        mock_tool.return_value.invoke.return_value = {"id": "real-mem-id"}
        with patch("app.procurement.workflow.enrich_memory"):
            pr = workflow.create_purchase_request(
                requested_by="jane@example.com",
                item_description="laptops",
                quantity=10,
                estimated_budget=100_000.0,
                delivery_location="Mumbai",
                required_date="2026-12-01",
                business_justification="refresh",
                mode="live",
            )
    mock_tool.assert_called()
    assert pr.pr_id not in workflow._DEMO_PR_STORE
