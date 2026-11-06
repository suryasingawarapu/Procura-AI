"""Regression test for a real bug found live: a message with no
extractable quantity (e.g. a plain "hii") used to crash
start_procurement_task with a raw "quantity must be positive" error from
workflow.create_purchase_request, instead of degrading gracefully into
"Waiting For Information" like every other genuinely-missing field
already does. workflow.create_purchase_request itself is mocked here
(real Supabase writes are covered by tests/integration/
test_dbwrite_task_orchestrator.py) -- this test is about the
orchestration logic deciding what to pass it.
"""

from __future__ import annotations

from unittest.mock import patch

from app.procurement import task_orchestrator, workflow


def _sample_pr(**overrides):
    base = dict(
        pr_id="PR-TEST0001",
        requested_by="jane@example.com",
        item_description="hii",
        quantity=1,
        estimated_budget=0.0,
        delivery_location="(not provided)",
        required_date="(not provided)",
        business_justification="hii",
    )
    base.update(overrides)
    return workflow.PurchaseRequest(**base)


@patch("app.procurement.task_orchestrator.workflow._save_pr")
@patch("app.procurement.task_orchestrator.workflow.create_purchase_request")
def test_a_message_with_no_quantity_never_crashes_create_purchase_request(mock_create, mock_save):
    mock_create.return_value = _sample_pr()
    pr = task_orchestrator.start_procurement_task(instruction="hii", requested_by="jane@example.com", mode="demo")

    # The real validation (quantity > 0) must never be handed a 0 --
    # that's what used to raise PurchaseRequestError here.
    _, kwargs = mock_create.call_args
    assert kwargs["quantity"] == 1

    assert pr.workflow_state == "Waiting For Information"
    assert any("quantity" in b for b in pr.blockers)
