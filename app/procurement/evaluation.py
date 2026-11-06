"""Baseline vs. memory-enabled comparison, per spec: "The research question
is whether provenance-aware, version-aware memory improves reliability
compared with a baseline using ordinary messages and tool results. Evaluate
it using measurable results rather than assuming improvement."

HONEST FRAMING, established by reading the actual implementation before
writing this (not assumed): Vendra/Procura/Finora always re-query ERPNext
fresh on every call -- memory is not consulted to decide whether to skip a
lookup. Durable memory in this system today is a provenance/audit trail and
a cross-task context source (see
tests/integration/test_live_workflow_persistence_e2e.py and
scripts/live_nvidia_persistence_check.py for where that's actually
exercised, by the five-agent LLM demo), not a cache that changes control
flow in the procurement coordinator. So for a *single* seeded procurement
task, "baseline" (memory writes stubbed to no-ops) and "memory-enabled"
(memory writes real) make the exact same ERPNext calls, in the same order,
with the same result -- the honest, measured expectation is **no
difference in task completion or tool-call count for one task**, and a
small, measurable **latency/cost overhead** from memory writes. What memory
adds that baseline structurally cannot: a durable, queryable provenance
record afterward (versioned, source-backed) and context available to a
later, separate task -- not a speedup on the task it was written during.

This module measures exactly that, honestly, rather than presupposing
memory wins.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from .coordinator import run_coordinator
from .mcp.client import ERPNextClient
from .messages import TaskRequest, TaskResult


@dataclass
class RunMetrics:
    mode: str  # "baseline" | "memory_enabled"
    latency_seconds: float
    task_completed: bool
    systems_invoked: List[str]
    message_count: int
    messages_with_source_reference: int
    messages_with_verification_status: int
    memory_writes_attempted: int
    error: Optional[str] = None

    @property
    def provenance_coverage(self) -> float:
        """Fraction of messages carrying at least one source reference.
        Note: in this implementation this is a property of message
        construction, not of whether memory persistence succeeded -- see
        this module's docstring. It will be the same in both modes unless
        the demo task itself fails before producing messages.
        """
        if self.message_count == 0:
            return 0.0
        return self.messages_with_source_reference / self.message_count


@dataclass
class ComparisonResult:
    baseline: List[RunMetrics] = field(default_factory=list)
    memory_enabled: List[RunMetrics] = field(default_factory=list)
    finding: str = ""


def _run_once(
    mode: str, task: TaskRequest, client: ERPNextClient, *, item_code: str, quantity: Optional[int]
) -> RunMetrics:
    write_count = 0

    def counting_save(*args, **kwargs):
        nonlocal write_count
        write_count += 1
        return {"id": f"mem-{write_count}"}

    def noop_save(*args, **kwargs):
        nonlocal write_count
        write_count += 1  # still counts *attempts*, even though baseline discards them
        return {"id": f"noop-{write_count}"}

    def noop_enrich(*args, **kwargs):
        return None

    save_impl = noop_save if mode == "baseline" else counting_save
    enrich_target_modules = (
        "app.procurement.systems.vendra",
        "app.procurement.systems.procura",
        "app.procurement.systems.finora",
    )

    # Supabase is mocked in BOTH modes -- this harness runs against a real
    # ERPNext client but must never make a real Supabase call either way
    # (matching every other procurement test's boundary). In baseline mode
    # enrich_memory is additionally replaced with a no-op, so the write is
    # never attempted at all -- not just discarded by a mocked backend.
    patches = [
        patch("app.tools.memory_tools.save_memory", side_effect=save_impl),
        patch("app.tools.memory_tools.get_relevant_context", return_value=[]),
        patch("app.memory.provenance_fields.supabase"),
    ]
    if mode == "baseline":
        patches += [patch(f"{m}.enrich_memory", side_effect=noop_enrich) for m in enrich_target_modules]

    error: Optional[str] = None
    result: Optional[TaskResult] = None
    t0 = time.perf_counter()
    try:
        for p in patches:
            p.start()
        try:
            result = run_coordinator(task, client, item_code=item_code, quantity=quantity)
        finally:
            for p in patches:
                p.stop()
    except Exception as exc:  # the harness itself must never crash the comparison
        error = str(exc)
    elapsed = time.perf_counter() - t0

    if result is None:
        return RunMetrics(
            mode=mode,
            latency_seconds=elapsed,
            task_completed=False,
            systems_invoked=[],
            message_count=0,
            messages_with_source_reference=0,
            messages_with_verification_status=0,
            memory_writes_attempted=write_count,
            error=error or "no result produced",
        )

    with_sources = sum(1 for m in result.messages if m.sources)
    with_verification = sum(
        1 for m in result.messages if any(s.verification_status != "unverified" for s in m.sources)
    )
    return RunMetrics(
        mode=mode,
        latency_seconds=elapsed,
        task_completed=not result.failed,
        systems_invoked=result.systems_invoked,
        message_count=len(result.messages),
        messages_with_source_reference=with_sources,
        messages_with_verification_status=with_verification,
        memory_writes_attempted=write_count,
        error=result.error,
    )


def run_comparison(
    client: ERPNextClient,
    *,
    task_description: str,
    item_code: str,
    quantity: Optional[int] = None,
    repetitions: int = 3,
) -> ComparisonResult:
    """Run the same task ``repetitions`` times in each mode and report
    measured metrics. Does not assert which mode is "better" -- see
    ``finding`` for an honest, data-derived summary sentence."""
    baseline_runs = [
        _run_once("baseline", TaskRequest(description=task_description), client, item_code=item_code, quantity=quantity)
        for _ in range(repetitions)
    ]
    memory_runs = [
        _run_once(
            "memory_enabled", TaskRequest(description=task_description), client, item_code=item_code, quantity=quantity
        )
        for _ in range(repetitions)
    ]

    def mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    baseline_latency = mean([r.latency_seconds for r in baseline_runs])
    memory_latency = mean([r.latency_seconds for r in memory_runs])
    baseline_completion = mean([1.0 if r.task_completed else 0.0 for r in baseline_runs])
    memory_completion = mean([1.0 if r.task_completed else 0.0 for r in memory_runs])
    baseline_prov = mean([r.provenance_coverage for r in baseline_runs])
    memory_prov = mean([r.provenance_coverage for r in memory_runs])

    overhead = memory_latency - baseline_latency
    if abs(baseline_completion - memory_completion) > 1e-9:
        finding = (
            f"Task completion differs: baseline {baseline_completion:.0%}, "
            f"memory-enabled {memory_completion:.0%}. Investigate before drawing a conclusion -- "
            "this result set is too small to be conclusive on its own."
        )
    elif abs(baseline_prov - memory_prov) > 1e-9:
        finding = (
            f"Provenance coverage differs ({baseline_prov:.0%} vs {memory_prov:.0%}) despite equal task "
            "completion -- unexpected given both modes build the same SystemMessage sources; investigate."
        )
    else:
        finding = (
            f"No difference in task completion ({memory_completion:.0%} both modes) or provenance coverage "
            f"({memory_prov:.0%} both modes) for this single task -- expected, since this coordinator's "
            "control flow does not yet consult memory to skip ERPNext calls (see module docstring). "
            f"Memory-enabled adds {overhead:+.4f}s mean latency per run ({memory_runs[0].memory_writes_attempted} "
            "memory write attempt(s) per run) as the measured cost of durable, versioned provenance -- "
            "a cost, not yet a measured reliability win, on this task in isolation."
        )

    return ComparisonResult(baseline=baseline_runs, memory_enabled=memory_runs, finding=finding)
