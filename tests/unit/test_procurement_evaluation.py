"""Unit tests for the baseline-vs-memory-enabled comparison harness.

Uses the real SeededERPNextClient (no mocking needed there) with the real
coordinator; only the memory-write path is patched (real Supabase
otherwise, consistent with every other procurement test).
"""

from __future__ import annotations

from app.procurement.evaluation import run_comparison
from app.procurement.mcp.client import SeededERPNextClient

DEMO_TASK = (
    "Find eligible suppliers for 20 laptops, collect/compare quotations, "
    "check the available budget, and prepare the purchase for approval."
)


def test_comparison_runs_both_modes_and_completes():
    client = SeededERPNextClient()
    result = run_comparison(client, task_description=DEMO_TASK, item_code="ITEM-LAPTOP-14", quantity=20, repetitions=2)

    assert len(result.baseline) == 2
    assert len(result.memory_enabled) == 2
    assert all(r.task_completed for r in result.baseline)
    assert all(r.task_completed for r in result.memory_enabled)


def test_comparison_reports_no_fabricated_superiority_when_none_is_measured():
    """Honest finding: this coordinator doesn't yet use memory to change
    control flow, so a single task should show no completion/provenance
    difference -- this must not be silently reported as a "win" either way."""
    client = SeededERPNextClient()
    result = run_comparison(client, task_description=DEMO_TASK, item_code="ITEM-LAPTOP-14", quantity=20, repetitions=2)

    assert "No difference in task completion" in result.finding
    assert "cost, not yet a measured reliability win" in result.finding


def test_baseline_still_attempts_memory_writes_but_discards_them():
    """Baseline isn't "skip memory" -- it's "memory writes are no-ops" (so
    the same code path runs, same call count, just discarded), matching
    the spec's definition: "ordinary structured messages/results without
    the proposed provenance/version-aware memory features."
    """
    client = SeededERPNextClient()
    result = run_comparison(client, task_description=DEMO_TASK, item_code="ITEM-LAPTOP-14", quantity=20, repetitions=1)

    assert result.baseline[0].memory_writes_attempted == result.memory_enabled[0].memory_writes_attempted
    assert result.baseline[0].memory_writes_attempted > 0


def test_provenance_coverage_is_measured_not_assumed():
    client = SeededERPNextClient()
    result = run_comparison(client, task_description=DEMO_TASK, item_code="ITEM-LAPTOP-14", quantity=20, repetitions=1)

    # every message in this demo carries at least one source reference,
    # in both modes, because sources come from ERPNext data in the message
    # construction itself, not from whether the memory write succeeded.
    assert result.baseline[0].provenance_coverage == 1.0
    assert result.memory_enabled[0].provenance_coverage == 1.0


def test_comparison_handles_an_unrelated_task_without_crashing():
    client = SeededERPNextClient()
    result = run_comparison(client, task_description="What's the weather?", item_code="ITEM-LAPTOP-14", repetitions=1)
    assert result.baseline[0].task_completed is False
    assert result.memory_enabled[0].task_completed is False
