"""Acceptance tests for the single natural-language task entry point
(app.procurement.task_orchestrator). Writes real rows under the real
"procura"/"finora" identities -- opt-in:

    $env:MEMORY_ALLOW_WRITE = "1"
    .\\.venv\\Scripts\\python.exe -m pytest -m dbwrite -v

Demo-mode runs make no real ERPNext call and no real memory write (memory
is stubbed exactly like the existing /procurement/demo/run route) except
for the one real Purchase Request record this module itself persists --
which these tests clean up per-test, same as the other dbwrite suites.
Live-mode tests only exercise the already-known-blocked real demo
supplier state (read-only against ERPNext; confirmed elsewhere in this
project this makes no write).
"""

from __future__ import annotations

import os

import pytest

from app.memory.client import supabase
from app.procurement import task_orchestrator as to
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

INSTRUCTION = "Purchase 10 laptops with 16 GB RAM and 512 GB SSD, within ₹60 lakh, delivered to our Mumbai office within 30 days."


@pytest.fixture
def cleanup_pr():
    pr_ids: list[str] = []
    yield pr_ids
    for pr_id in pr_ids:
        for agent_id, key in ((PROCURA, f"purchase_request:{pr_id}"), (FINORA, f"approval:{pr_id}")):
            rows = supabase.table("agent_memories").select("id").eq("agent_id", agent_id).eq("memory_key", key).execute().data or []
            for row in rows:
                supabase.table("agent_memories").delete().eq("id", row["id"]).execute()


def test_one_instruction_parses_and_runs_the_real_pipeline_to_waiting_for_approval(cleanup_pr):
    pr = to.start_procurement_task(instruction=INSTRUCTION, requested_by="jane@example.com", mode="demo")
    cleanup_pr.append(pr.pr_id)

    assert pr.quantity == 10
    assert pr.estimated_budget == 6_000_000.0
    assert pr.delivery_location == "Mumbai office"
    assert pr.workflow_state == "Waiting For Approval"
    assert pr.status == "Pending Approval"  # the existing PR lifecycle also advanced, in step
    assert pr.coordinator_result["result"]["recommended_supplier"] == "DEMO-SUP-TECH"
    assert pr.coordinator_result["requires_approval"] is True


def test_persisted_state_is_independently_retrievable(cleanup_pr):
    pr = to.start_procurement_task(instruction=INSTRUCTION, requested_by="jane@example.com", mode="demo")
    cleanup_pr.append(pr.pr_id)

    refetched = to.get_task(pr.pr_id)  # a separate read, not the in-memory object returned above
    assert refetched.workflow_state == "Waiting For Approval"
    assert refetched.coordinator_result == pr.coordinator_result


def test_missing_item_code_in_live_mode_pauses_for_information_not_a_crash(cleanup_pr):
    pr = to.start_procurement_task(
        instruction="Purchase 20 units within $50000, delivered to our Chicago warehouse within 15 days.",
        requested_by="jane@example.com", mode="live",
    )
    cleanup_pr.append(pr.pr_id)
    assert pr.workflow_state == "Waiting For Information"
    assert any("item_code" in b for b in pr.blockers)
    assert pr.coordinator_result is None  # the coordinator was never actually called


def test_resume_before_information_is_filled_in_is_a_no_op(cleanup_pr):
    pr = to.start_procurement_task(
        instruction="Purchase 20 units within $50000, delivered to our Chicago warehouse within 15 days.",
        requested_by="jane@example.com", mode="live",
    )
    cleanup_pr.append(pr.pr_id)
    resumed = to.resume_procurement_task(pr.pr_id)
    assert resumed.workflow_state == "Waiting For Information"


def test_resume_after_filling_in_missing_info_actually_runs_the_real_pipeline(cleanup_pr):
    pr = to.start_procurement_task(
        instruction="Purchase 20 units within $50000, delivered to our Chicago warehouse within 15 days.",
        requested_by="jane@example.com", mode="live",
    )
    cleanup_pr.append(pr.pr_id)
    workflow.update_purchase_request(pr.pr_id, item_code="DEMO-LAPTOP-14")
    resumed = to.resume_procurement_task(pr.pr_id)
    # the real live suppliers are genuinely non-compliant today -- this
    # must be reported honestly, never fabricated as a success
    assert resumed.workflow_state == "Blocked"
    assert resumed.coordinator_result is not None


def test_idempotency_key_prevents_a_duplicate_task(cleanup_pr):
    pr1 = to.start_procurement_task(
        instruction=INSTRUCTION, requested_by="jane@example.com", mode="demo", idempotency_key="dup-key-1"
    )
    cleanup_pr.append(pr1.pr_id)
    pr2 = to.start_procurement_task(
        instruction=INSTRUCTION, requested_by="jane@example.com", mode="demo", idempotency_key="dup-key-1"
    )
    assert pr2.pr_id == pr1.pr_id


def test_coordinator_failure_is_recorded_as_failed_not_a_crash(cleanup_pr, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated backend outage")

    monkeypatch.setattr(to, "run_coordinator", _boom)
    pr = to.start_procurement_task(instruction=INSTRUCTION, requested_by="jane@example.com", mode="demo")
    cleanup_pr.append(pr.pr_id)
    assert pr.workflow_state == "Failed"
    assert "simulated backend outage" in pr.blockers[0]
