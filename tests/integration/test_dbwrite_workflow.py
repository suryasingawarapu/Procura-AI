"""Acceptance tests for the Purchase Request / approval workflow
(app.procurement.workflow). This *writes* to the database (real rows
under the real "procura"/"finora" identities) and is therefore opt-in:

    $env:MEMORY_ALLOW_WRITE = "1"
    .\\.venv\\Scripts\\python.exe -m pytest -m dbwrite -v

Each test gets its own fresh Purchase Request from the ``make_pr``
fixture below, which cleans up every row it created (every version, not
just the active one) immediately after that one test finishes -- a
per-test fixture teardown, not a single end-of-module cleanup relying on
a shared list surviving every test in the file, so one test's cleanup can
never be skipped by however the others behaved.
"""

from __future__ import annotations

import os

import pytest

from app.memory.client import supabase
from app.memory.store import get_memory_by_key
from app.procurement import workflow
from app.procurement.identities import FINORA, PROCURA

pytestmark = [
    pytest.mark.dbwrite,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MEMORY_ALLOW_WRITE", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="set MEMORY_ALLOW_WRITE=1 to run the database write tests",
    ),
]


def _delete_all_versions(agent_id: str, memory_key: str) -> None:
    rows = (
        supabase.table("agent_memories").select("id").eq("agent_id", agent_id).eq("memory_key", memory_key).execute().data
        or []
    )
    for row in rows:
        supabase.table("agent_memories").delete().eq("id", row["id"]).execute()


@pytest.fixture
def make_pr():
    created_pr_ids: list[str] = []

    def _make(**overrides):
        defaults = dict(
            requested_by="jane.doe@example.com",
            item_description="14-inch business laptop",
            quantity=100,
            estimated_budget=4_500_000.0,
            delivery_location="Mumbai HQ",
            required_date="2026-12-01",
            business_justification="Annual hardware refresh for the engineering team.",
        )
        defaults.update(overrides)
        pr = workflow.create_purchase_request(**defaults)
        created_pr_ids.append(pr.pr_id)
        return pr

    yield _make

    for pr_id in created_pr_ids:
        _delete_all_versions(PROCURA, f"purchase_request:{pr_id}")
        _delete_all_versions(FINORA, f"approval:{pr_id}")


def test_create_and_retrieve_a_real_purchase_request(make_pr):
    pr = make_pr()
    fetched = workflow.get_purchase_request(pr.pr_id)
    assert fetched is not None
    assert fetched.status == "Draft"
    assert fetched.quantity == 100


def test_invalid_status_transition_is_rejected(make_pr):
    pr = make_pr()
    with pytest.raises(workflow.PurchaseRequestError):
        workflow.transition_purchase_request(pr.pr_id, "Approved")  # Draft -> Approved skips Pending Approval


def test_valid_transition_creates_a_new_version_full_audit_trail(make_pr):
    pr = make_pr()
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    updated = workflow.get_purchase_request(pr.pr_id)
    assert updated.status == "Pending Approval"
    history = get_memory_by_key(PROCURA, f"purchase_request:{pr.pr_id}", include_superseded=True)
    assert len(history) >= 2  # Draft, then Pending Approval -- real version history, not an overwrite


def test_stale_approval_is_rejected_not_silently_approved(make_pr):
    pr = make_pr(estimated_budget=100_000.0)
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    stale_updated_at = pr.updated_at  # the Draft version's timestamp, now outdated
    with pytest.raises(workflow.StaleApprovalError):
        workflow.record_approval_decision(
            pr_id=pr.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved",
            reviewed_updated_at=stale_updated_at,
        )
    assert workflow.get_purchase_request(pr.pr_id).status == "Pending Approval"  # unchanged


def test_approval_with_the_current_version_succeeds(make_pr):
    pr = make_pr(estimated_budget=100_000.0)
    current = workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    decision = workflow.record_approval_decision(
        pr_id=pr.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved",
        reviewed_updated_at=current.updated_at,
    )
    assert decision.decision == "Approved"
    assert decision.identity_verified is False  # honest default -- no real SSO exists to verify it


def test_approval_within_a_managers_limit_succeeds(make_pr):
    pr = make_pr(estimated_budget=200_000.0)
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    decision = workflow.record_approval_decision(
        pr_id=pr.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved", comment="Looks good."
    )
    assert decision.decision == "Approved"
    assert workflow.get_purchase_request(pr.pr_id).status == "Approved"


def test_approval_over_a_managers_limit_is_rejected_not_silently_escalated(make_pr):
    pr = make_pr(estimated_budget=4_500_000.0)  # over the manager limit, under director's
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    with pytest.raises(workflow.ApprovalLimitExceeded):
        workflow.record_approval_decision(
            pr_id=pr.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved"
        )
    # the request must still be Pending Approval -- the rejected attempt never silently approved it
    assert workflow.get_purchase_request(pr.pr_id).status == "Pending Approval"


def test_approval_checks_the_real_recommended_cost_not_the_stale_estimate(make_pr):
    """Regression test for a real bug found live: a requester's original
    estimated_budget guess (here, well within a manager's limit) must
    never be what gates approval once the agent pipeline has actually
    recommended a real total cost -- that real total is what a manager's
    limit is checked against, even though it's a completely separate
    field from estimated_budget."""
    pr = make_pr(estimated_budget=45_000.0)  # well within the manager limit
    workflow.update_purchase_request(
        pr.pr_id,
        coordinator_result={"result": {"recommended_supplier": "Acme Corp", "recommended_total_cost": 759_000.0}},
    )
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    with pytest.raises(workflow.ApprovalLimitExceeded):
        workflow.record_approval_decision(
            pr_id=pr.pr_id, approver_id="mgr-1", approver_role="manager", decision="Approved"
        )
    assert workflow.get_purchase_request(pr.pr_id).status == "Pending Approval"


def test_auto_approval_bypasses_any_limit_and_is_labeled_honestly(make_pr):
    """record_auto_approval (the live-mode, LIVE_MODE_AUTO_APPROVE-gated
    path with no human review at all) must approve regardless of amount --
    there is no role to check a limit against -- but the audit trail must
    plainly show no human was involved, never disguise this as a real
    decision."""
    pr = make_pr(estimated_budget=50_000_000.0)  # over every real role's limit, including cfo would be "no limit" too
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")

    record = workflow.record_auto_approval(pr.pr_id)

    assert record.decision == "Approved"
    assert record.approver_id == "SYSTEM-AUTO-APPROVAL"
    assert record.identity_verified is False
    assert "no human" in record.approver_role.lower() or "no human" in (record.comment or "").lower()
    assert workflow.get_purchase_request(pr.pr_id).status == "Approved"


def test_approval_history_is_queryable_and_preserves_who_approved_what(make_pr):
    pr = make_pr(estimated_budget=100_000.0)
    workflow.transition_purchase_request(pr.pr_id, "Pending Approval")
    workflow.record_approval_decision(
        pr_id=pr.pr_id, approver_id="mgr-2", approver_role="manager", decision="Approved",
        evaluation_version="eval-v1",
    )
    history = workflow.get_approval_history(pr.pr_id)
    assert len(history) == 1
    assert history[0]["approver_id"] == "mgr-2"
    assert history[0]["evaluation_version"] == "eval-v1"
