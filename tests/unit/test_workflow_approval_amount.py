"""Regression tests for a real, serious bug found live: the approval-limit
check (and the chat UI's own role-selection) used to compare a role's
spending limit against pr.estimated_budget -- the requester's own,
possibly very rough original guess -- instead of the agent pipeline's
actual recommended_total_cost. A recommendation can come out far higher
than the original guess (e.g. "~45,000" requested, "759,000" actually
recommended), which would have let a lower-authority role (e.g. manager,
limit 500,000) approve an amount their role was never really authorized
for. See app.procurement.workflow.approval_check_amount.
"""

from __future__ import annotations

from app.procurement import workflow


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
    )
    base.update(overrides)
    return workflow.PurchaseRequest(**base)


def test_uses_recommended_total_cost_when_a_recommendation_exists():
    pr = _sample_pr(
        coordinator_result={"result": {"recommended_supplier": "Acme", "recommended_total_cost": 759_000.0}}
    )
    assert workflow.approval_check_amount(pr) == 759_000.0  # NOT the stale 45,000 estimate


def test_falls_back_to_estimated_budget_with_no_recommendation_yet():
    pr = _sample_pr(coordinator_result=None)
    assert workflow.approval_check_amount(pr) == 45_000.0


def test_falls_back_to_estimated_budget_when_result_has_no_recommended_cost():
    pr = _sample_pr(coordinator_result={"result": {"eligible_supplier_names": []}})
    assert workflow.approval_check_amount(pr) == 45_000.0


def test_a_manager_cannot_approve_a_recommendation_that_exceeds_their_real_limit(monkeypatch):
    """The exact exploit scenario found live: estimated_budget (45,000) is
    well within a manager's 500,000 limit, but the real recommended total
    (759,000) is not -- record_approval_decision must reject this using
    the real amount, not the stale one."""
    pr = _sample_pr(
        status="Pending Approval",
        coordinator_result={"result": {"recommended_supplier": "Acme", "recommended_total_cost": 759_000.0}},
    )
    monkeypatch.setattr(workflow, "get_purchase_request", lambda pr_id: pr)
    monkeypatch.setattr(workflow, "transition_purchase_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not reach transition")))

    import pytest

    with pytest.raises(workflow.ApprovalLimitExceeded):
        workflow.record_approval_decision(pr_id=pr.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved")
