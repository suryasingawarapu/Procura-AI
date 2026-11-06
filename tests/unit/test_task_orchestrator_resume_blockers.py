"""Regression test for a real bug found live: resuming a "Waiting For
Information" task that's still missing something returned a STALE
blockers list -- a field the caller had just successfully supplied (e.g.
item_code) kept appearing as still-missing, because resume_procurement_task
never recomputed it against the task's actual current data, just returned
the snapshot frozen from when the task was first created.

workflow.get_purchase_request/_save_pr are mocked (real Supabase writes
are covered by tests/integration/test_dbwrite_task_orchestrator.py) --
this test is about the orchestration logic itself.
"""

from __future__ import annotations

from unittest.mock import patch

from app.procurement import task_orchestrator, workflow


def _sample_pr(**overrides):
    base = dict(
        pr_id="PR-TEST0001",
        requested_by="jane@example.com",
        item_description="facility maintenance",
        quantity=4,
        estimated_budget=70_000.0,
        delivery_location="(not provided)",
        required_date="2026-12-01",
        business_justification="restock",
        mode="live",
        workflow_state="Waiting For Information",
        blockers=["missing or unresolvable: item_code (no free-text item-catalog matching exists -- provide the exact ERPNext item code), delivery_location"],
        item_code=None,
    )
    base.update(overrides)
    return workflow.PurchaseRequest(**base)


@patch("app.procurement.task_orchestrator.workflow._save_pr")
@patch("app.procurement.task_orchestrator.workflow.get_purchase_request")
def test_resuming_with_item_code_supplied_drops_it_from_blockers(mock_get, mock_save):
    # item_code WAS successfully supplied, but delivery_location genuinely
    # still isn't -- the refreshed blockers must mention only the latter.
    pr = _sample_pr(item_code="FACILITY-MAINT-MO")
    mock_get.return_value = pr

    result = task_orchestrator.resume_procurement_task(pr.pr_id)

    assert result.workflow_state == "Waiting For Information"
    assert "item_code" not in result.blockers[0]
    assert "delivery_location" in result.blockers[0]
    mock_save.assert_called_once()  # the refreshed blockers were actually persisted


@patch("app.procurement.task_orchestrator.workflow._save_pr")
@patch("app.procurement.task_orchestrator.workflow.get_purchase_request")
def test_resuming_does_not_resave_when_blockers_are_unchanged(mock_get, mock_save):
    # Nothing new was actually supplied -- no pointless write.
    pr = _sample_pr(item_code=None)
    mock_get.return_value = pr

    result = task_orchestrator.resume_procurement_task(pr.pr_id)

    assert "item_code" in result.blockers[0]
    mock_save.assert_not_called()
