"""Shared framework every main agent's six specialists are built on.

Each main agent (Vendra/Procura/Finora) is itself an orchestrator: it
receives a task, decides which of its six specialists apply, runs them,
and combines their results. The pattern here is the exact one-level-down
analogue of ``app.procurement.coordinator``'s own router/step_index
pipeline over VENDRA/PROCURA/FINORA -- a deterministic, dependency-ordered
LangGraph pipeline, never an LLM freely choosing which specialist to call
next (that would reintroduce the uncontrolled-tool-loop risk the spec
explicitly asks to avoid).

A specialist is a plain Python callable: ``(context: dict, results: dict) ->
dict``. It receives the shared, read-only task context (the ERPNext client,
ids, task-specific parameters) plus every *prior* specialist's result so
far (keyed by name, each a plain ``{"status", "data", "error"}`` dict) --
so a later specialist can genuinely build on an earlier one's findings
(e.g. Vendra's Vendor Profile reusing Document Verification's compliance
result) instead of recomputing it or being wired together only by
convention. It returns whatever structured data it computed. It is NEVER
allowed to crash its main agent -- :func:`run_specialist_safely`
converts any exception into a typed, reported error instead of propagating
it, exactly like the cross-system coordinator's own ``_call_with_one_retry``
already does for Vendra/Procura/Finora themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

Specialist = Callable[[Dict[str, Any], Dict[str, Dict[str, Any]]], Dict[str, Any]]


@dataclass
class SpecialistResult:
    """One specialist's typed, self-contained result.

    ``status``:
      * "ok"      -- ran, ``data`` holds its findings.
      * "skipped" -- ran, deliberately found nothing to do for this
        context (e.g. no quotations to estimate delivery for) --
        distinct from an error; ``data`` may still hold why.
      * "error"   -- the specialist's own call into ERPNext/the backend
        raised; ``error`` holds a short message, ``data`` is empty.
    """

    name: str
    status: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "data": self.data, "error": self.error}


class SpecialistState(TypedDict, total=False):
    context: Dict[str, Any]
    needed: List[str]
    step_index: int
    results: Dict[str, Dict[str, Any]]


def run_specialist_safely(
    name: str, fn: Specialist, context: Dict[str, Any], results_so_far: Dict[str, Dict[str, Any]]
) -> SpecialistResult:
    """Call one specialist, normalizing any exception into a typed error
    result instead of letting it crash the main agent's own graph."""
    try:
        data = fn(context, results_so_far) or {}
        status = data.pop("_status", "ok") if isinstance(data, dict) else "ok"
        return SpecialistResult(name=name, status=status, data=data)
    except Exception as exc:  # a specialist's own backend call failing is never fatal to the main agent
        return SpecialistResult(name=name, status="error", error=f"{type(exc).__name__}: {exc}")


def build_specialist_graph(specialists: Dict[str, Specialist]):
    """Build the LangGraph pipeline that runs exactly the specialists
    listed in the initial state's ``needed`` list, in that order -- the
    same router/step_index pipeline shape as
    ``app.procurement.coordinator.build_coordinator``, one level down."""
    names = list(specialists.keys())

    def make_node(name: str, fn: Specialist):
        def node(state: SpecialistState) -> SpecialistState:
            result = run_specialist_safely(name, fn, state["context"], state.get("results", {}))
            results = dict(state.get("results", {}))
            results[name] = result.to_dict()
            return {"results": results, "step_index": state.get("step_index", 0) + 1}

        return node

    def router(state: SpecialistState) -> str:
        needed = state.get("needed", [])
        idx = state.get("step_index", 0)
        if idx >= len(needed):
            return END
        return needed[idx]

    graph = StateGraph(SpecialistState)
    graph.add_conditional_edges(START, router, {**{n: n for n in names}, END: END})
    for name, fn in specialists.items():
        graph.add_node(name, make_node(name, fn))
        graph.add_conditional_edges(name, router, {**{n: n for n in names}, END: END})
    return graph.compile()


def run_specialists(specialists: Dict[str, Specialist], context: Dict[str, Any], *, needed: Optional[List[str]] = None) -> Dict[str, SpecialistResult]:
    """Run ``specialists`` (all of them, in their declared order, unless
    ``needed`` narrows the set) over one shared ``context`` and return each
    one's :class:`SpecialistResult` keyed by name."""
    graph = build_specialist_graph(specialists)
    order = needed if needed is not None else list(specialists.keys())
    final_state = graph.invoke({"context": context, "needed": order, "step_index": 0, "results": {}})
    raw = final_state.get("results", {})
    return {
        name: SpecialistResult(name=name, status=r["status"], data=r["data"], error=r["error"])
        for name, r in raw.items()
    }
