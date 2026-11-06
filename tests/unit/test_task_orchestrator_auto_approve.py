"""Unit tests for the live-mode auto-approval path
(task_orchestrator._auto_approve_if_configured / workflow.record_auto_approval)
-- explicitly opted into via LIVE_MODE_AUTO_APPROVE, off by default. Real
Supabase writes (workflow.record_auto_approval, submit_approved_purchase_order)
are mocked here; the real-write path is covered by
tests/integration/test_dbwrite_task_orchestrator.py's own conventions.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from app.procurement import task_orchestrator, workflow


def _sample_pr(**overrides):
    base = dict(
        pr_id="PR-TEST0001",
        requested_by="jane@example.com",
        item_description="laptop bags",
        quantity=15,
        estimated_budget=45_000.0,
        delivery_location="Bangalore office",
        required_date="2026-12-01",
        business_justification="restock",
        mode="live",
        workflow_state="Waiting For Approval",
        status="Pending Approval",
    )
    base.update(overrides)
    return workflow.PurchaseRequest(**base)


@patch.dict(os.environ, {}, clear=False)
def test_auto_approve_is_a_no_op_when_env_var_is_unset():
    os.environ.pop("LIVE_MODE_AUTO_APPROVE", None)
    pr = _sample_pr()
    with patch("app.procurement.task_orchestrator.workflow.record_auto_approval") as mock_approve:
        result = task_orchestrator._auto_approve_if_configured(pr)
    mock_approve.assert_not_called()
    assert result is pr  # unchanged -- still waiting for a real human


@patch.dict(os.environ, {"LIVE_MODE_AUTO_APPROVE": "1"}, clear=False)
def test_auto_approve_is_a_no_op_in_demo_mode():
    pr = _sample_pr(mode="demo")
    with patch("app.procurement.task_orchestrator.workflow.record_auto_approval") as mock_approve:
        result = task_orchestrator._auto_approve_if_configured(pr)
    mock_approve.assert_not_called()
    assert result is pr


@patch.dict(os.environ, {"LIVE_MODE_AUTO_APPROVE": "1"}, clear=False)
def test_auto_approve_is_a_no_op_when_not_waiting_for_approval():
    pr = _sample_pr(workflow_state="Blocked")
    with patch("app.procurement.task_orchestrator.workflow.record_auto_approval") as mock_approve:
        result = task_orchestrator._auto_approve_if_configured(pr)
    mock_approve.assert_not_called()
    assert result is pr


@patch.dict(os.environ, {"LIVE_MODE_AUTO_APPROVE": "1"}, clear=False)
@patch("app.procurement.task_orchestrator.submit_approved_purchase_order")
@patch("app.procurement.task_orchestrator.workflow.record_auto_approval")
def test_auto_approve_records_and_submits_when_fully_configured(mock_approve, mock_submit):
    pr = _sample_pr()
    mock_submit.return_value = _sample_pr(status="Approved", workflow_state="Completed")

    result = task_orchestrator._auto_approve_if_configured(pr)

    mock_approve.assert_called_once_with(pr.pr_id, task_id=pr.coordinator_task_id)
    mock_submit.assert_called_once_with(pr.pr_id)
    assert result.workflow_state == "Completed"


@patch.dict(os.environ, {"LIVE_MODE_AUTO_APPROVE": "yes"}, clear=False)
@patch("app.procurement.task_orchestrator.submit_approved_purchase_order")
@patch("app.procurement.task_orchestrator.workflow.record_auto_approval")
def test_auto_approve_accepts_truthy_string_variants(mock_approve, mock_submit):
    pr = _sample_pr()
    mock_submit.return_value = pr
    task_orchestrator._auto_approve_if_configured(pr)
    mock_approve.assert_called_once()
