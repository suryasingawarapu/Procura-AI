"""Cross-system coordinator: receives a task, decides which system(s) it
actually needs, runs them in dependency order, and assembles the result.

Routing is a deterministic keyword/intent classifier, not an LLM choosing
freely which agent to call next (that was the old 5-agent demo's
architecture in app.agents.workflow -- this spec explicitly asks not to
recreate generic Planning/Execution/Validation agents "just for the sake
of it"). Procurement tasks have a fixed real-world dependency order
(Vendra's eligible suppliers feed Procura's quotation comparison, whose
total feeds Finora's budget check), so the graph here is a straight
pipeline over only the systems actually needed, decided once at the start
-- never a cycle, which is what makes "prevents circular conversations and
uncontrolled tool loops" true by construction rather than by a step cap.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .identities import FINORA, PROCURA, VENDRA
from .mcp.client import ERPNextClient, ERPNextUnavailable
from .messages import SystemMessage, TaskRequest, TaskResult, new_id
from . import propagation
from app.observability import langfuse_tracing as tracing

from .progress import emit
from .systems import finora as finora_system
from .systems import procura as procura_system
from .systems import vendra as vendra_system

__all__ = ["classify_systems_needed", "run_coordinator", "CoordinatorState"]

_KEYWORDS = {
    VENDRA: ("supplier", "vendor", "compliance", "onboard"),
    PROCURA: ("quot", "rfq", "purchase", "order", "price", "laptop", "item"),
    FINORA: ("budget", "invoice", "payment", "finance", "approval", "cost"),
}


_ai_summary_llm_cache: Dict[str, Any] = {}


def _ai_summary_llm(role: str) -> Optional[Any]:
    """The optional narration subagent for ``role`` (NVIDIA Nemotron by
    default, via app.agents.workflow.get_llm), or None.

    Opt-in via PROCUREMENT_AI_SUMMARIES=1, off by default -- this is the
    one place that decides whether any real LLM call happens at all for
    Vendra/Procura/Finora's narration layer (see app.procurement.narration),
    so the many existing unit tests that call those systems directly never
    pay for or depend on a network call unless they ask for one, while a
    deployment that wants the narration can turn it on in one place.

    Cached per role for the process's lifetime -- building a chat model
    client is real (non-trivial) work, and without this every task would
    rebuild one from scratch for each of up to three systems."""
    if os.getenv("PROCUREMENT_AI_SUMMARIES", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    if role in _ai_summary_llm_cache:
        return _ai_summary_llm_cache[role]
    try:
        from app.agents.workflow import get_llm

        llm = get_llm(role)
    except Exception:
        return None
    _ai_summary_llm_cache[role] = llm
    return llm


def classify_systems_needed(task_text: str) -> List[str]:
    """Which systems a task actually needs, in dependency order (Vendra
    before Procura before Finora) -- not every task needs all three."""
    text = task_text.lower()
    needed = [system for system, words in _KEYWORDS.items() if any(w in text for w in words)]
    # dependency order, regardless of keyword scan order
    order = [VENDRA, PROCURA, FINORA]
    return [s for s in order if s in needed]


class CoordinatorState(TypedDict, total=False):
    task_id: str
    correlation_id: str
    task_text: str
    item_code: str
    quantity: int
    systems_needed: List[str]
    step_index: int
    messages: List[Dict[str, Any]]
    eligible_supplier_names: List[str]
    recommended_supplier: Optional[str]
    recommended_total_cost: Optional[float]
    failed: bool
    blocked: bool
    error: Optional[str]
    use_memory: bool
    conflicts: List[Dict[str, Any]]
    memory_path: Dict[str, Any]


def _call_with_one_retry(fn, *args, **kwargs):
    """Timeouts/failures/retries, safely: one retry for an ERPNext/MCP
    backend hiccup, then give up cleanly -- never crash the whole task."""
    try:
        return fn(*args, **kwargs), None
    except ERPNextUnavailable as exc:
        try:
            return fn(*args, **kwargs), None
        except ERPNextUnavailable as exc2:
            return None, str(exc2)
    except Exception as exc:  # defensive: a system must never crash the coordinator
        return None, str(exc)


_STARTED = {
    VENDRA: "Vendra started: checking supplier eligibility (6 sub-agents)",
    PROCURA: "Procura started: collecting and ranking quotations (6 sub-agents)",
    FINORA: "Finora started: checking budget and approval authority (6 sub-agents)",
}


def build_coordinator(client: ERPNextClient):
    # Fresh per task: _ai_summary_llm's cache only needs to survive the
    # handful of node calls within THIS run_coordinator invocation (so
    # Vendra/Procura/Finora don't each rebuild their own chat model client
    # from scratch) -- clearing it here, rather than leaving it live for
    # the whole process, keeps this free of any cross-task or cross-test
    # staleness.
    _ai_summary_llm_cache.clear()

    def vendra_node(state: CoordinatorState) -> CoordinatorState:
        msg, error = _call_with_one_retry(
            vendra_system.run_vendor_eligibility_check,
            task_id=state["task_id"],
            correlation_id=state["correlation_id"],
            client=client,
            item_code=state.get("item_code"),
            llm=_ai_summary_llm(VENDRA),
            use_memory=bool(state.get("use_memory")),
        )
        if error:
            emit("vendra", f"Vendra failed: {error}")
            return {"failed": True, "error": f"vendra error: {error}"}
        eligible_names = [s["name"] for s in msg.payload["eligible_suppliers"]]
        emit(
            "vendra",
            f"Vendra finished: {len(eligible_names)} of {msg.payload.get('candidates_checked', '?')} "
            f"candidate supplier(s) eligible",
        )
        update: CoordinatorState = {
            "messages": state.get("messages", []) + [msg.model_dump(mode="json")],
            "eligible_supplier_names": eligible_names,
        }
        if state.get("use_memory") and eligible_names:
            relay = propagation.relay_verdicts(eligible_names, task_id=state["task_id"])
            update["memory_path"] = {**state.get("memory_path", {}), "relay": relay}
        return update

    def procura_node(state: CoordinatorState) -> CoordinatorState:
        eligible = state.get("eligible_supplier_names")
        use_memory = bool(state.get("use_memory"))
        vendra_ran = eligible is not None
        if eligible is None:
            # Procura ran without Vendra in the pipeline (a task that only
            # needed Procura) -- fall back to whatever ERPNext itself
            # reports as active, since there is no Vendra-verified list to
            # consume this time.
            eligible = [s["name"] for s in client.list_suppliers() if not s.get("on_hold")]
        selection = None
        path = {**state.get("memory_path", {})}
        if use_memory and eligible:
            if not vendra_ran:
                # No Vendra run in this task, so nothing was relayed yet: relay whatever verdict Vendra
                # holds for each candidate (compliant or not) so Procura receives it -- otherwise a
                # supplier with an expired or missing document would simply look unassessed.
                path["relay"] = propagation.relay_verdicts(eligible, task_id=state["task_id"])
            # Procura chooses from what it received: a supplier whose shared verdict is stale or not
            # compliant is excluded, even if a list says otherwise.
            selection = propagation.select_eligible(eligible)
            eligible = selection["kept"]
        msg, error = _call_with_one_retry(
            procura_system.run_procurement_task,
            task_id=state["task_id"],
            correlation_id=state["correlation_id"],
            client=client,
            item_code=state["item_code"],
            quantity=state.get("quantity") or 1,
            eligible_supplier_names=eligible,
            llm=_ai_summary_llm(PROCURA),
            use_memory=use_memory,
        )
        if error:
            emit("procura", f"Procura failed: {error}")
            return {"failed": True, "error": f"procura error: {error}"}
        if selection is not None:
            path["selection"] = {k: selection[k] for k in ("accepted", "rejected", "no_copy", "note")}
            path["selection"]["vendra_ran"] = vendra_ran
            msg.payload["eligibility_basis"] = path["selection"]
        conflicts = msg.payload.get("conflicts") or []
        if conflicts:
            emit("procura", "Procura stopped: a quoted price is disputed between sources and needs a decision")
            return {
                "messages": state.get("messages", []) + [msg.model_dump(mode="json")],
                "blocked": True,
                "conflicts": conflicts,
                "memory_path": path,
                "error": "price conflict needs clarification: "
                + "; ".join(f"{c.get('supplier')}: {c.get('reason')}" for c in conflicts),
            }
        if msg.status == "error":
            emit("procura", f"Procura stopped: {msg.error}")
            # Procura ran and completed cleanly, but produced no recommendation
            # (e.g. no suppliers passed compliance, or no quotations exist) --
            # this is a clean stop, not a pipeline crash. Reported as
            # `blocked`, never as `failed`, and the pipeline halts here so
            # Finora never runs with nothing to validate.
            return {
                "messages": state.get("messages", []) + [msg.model_dump(mode="json")],
                "blocked": True,
                "memory_path": path,
                "error": f"procurement blocked: {msg.error}",
            }
        emit(
            "procura",
            f"Procura finished: recommends {msg.payload.get('recommended_supplier')} "
            f"at {msg.payload.get('recommended_total_cost')}",
        )
        if use_memory and msg.payload.get("recommended_supplier"):
            path["forward"] = propagation.forward_evidence(
                supplier=msg.payload["recommended_supplier"], item_code=state["item_code"], task_id=state["task_id"]
            )
        return {
            "messages": state.get("messages", []) + [msg.model_dump(mode="json")],
            "recommended_supplier": msg.payload.get("recommended_supplier"),
            "recommended_total_cost": msg.payload.get("recommended_total_cost"),
            "memory_path": path,
        }

    def finora_node(state: CoordinatorState) -> CoordinatorState:
        total = state.get("recommended_total_cost")
        supplier = state.get("recommended_supplier")
        if total is None or supplier is None:
            # Finora ran without Procura in the pipeline -- nothing to
            # check a cost against yet; report that plainly rather than
            # inventing a number.
            return {
                "failed": True,
                "error": "finora error: no recommended supplier/total available to validate "
                "(Procura did not run first)",
            }
        if state.get("use_memory"):
            check = propagation.evidence_check(supplier)
            path = {**state.get("memory_path", {}), "evidence_check": check}
            if check["status"] == "stale":
                emit("finora", f"Finora stopped: Vendra's verdict for {supplier} is no longer current ({check['reason']})")
                return {
                    "blocked": True,
                    "memory_path": path,
                    "error": f"approval check blocked: Vendra's verdict for {supplier} is no longer current ({check['reason']})",
                }
            emit("finora", f"Finora checked its evidence: {check['reason']}" if check["status"] == "current" else f"Finora evidence check: {check['status']}")
            state = {**state, "memory_path": path}
        msg, error = _call_with_one_retry(
            finora_system.run_financial_validation,
            task_id=state["task_id"],
            correlation_id=state["correlation_id"],
            client=client,
            estimated_total=total,
            supplier=supplier,
            llm=_ai_summary_llm(FINORA),
        )
        if error:
            emit("finora", f"Finora failed: {error}")
            return {"failed": True, "error": f"finora error: {error}"}
        emit("finora", "Finora finished: budget and approval authority checked")
        return {"messages": state.get("messages", []) + [msg.model_dump(mode="json")], "memory_path": state.get("memory_path", {})}

    nodes = {VENDRA: vendra_node, PROCURA: procura_node, FINORA: finora_node}

    def router(state: CoordinatorState) -> str:
        needed = state.get("systems_needed", [])
        idx = state.get("step_index", 0)
        if state.get("failed") or state.get("blocked") or idx >= len(needed):
            return END
        return needed[idx]

    def after_system(state: CoordinatorState) -> str:
        if state.get("failed") or state.get("blocked"):
            return END
        # `wrapped` below already incremented step_index for the node that
        # just ran, so this is the index of the *next* system to run.
        idx = state.get("step_index", 0)
        needed = state.get("systems_needed", [])
        return END if idx >= len(needed) else needed[idx]

    graph = StateGraph(CoordinatorState)
    graph.add_conditional_edges(START, router, {**{s: s for s in (VENDRA, PROCURA, FINORA)}, END: END})
    for system_id, node_fn in nodes.items():

        def wrapped(state: CoordinatorState, _fn=node_fn, _sid=system_id) -> CoordinatorState:
            emit(_sid, _STARTED.get(_sid, f"{_sid} started"))
            with tracing.span(str(_sid).capitalize(), input=state.get("task_text")) as t:
                result = _fn(state)
                if result.get("failed"):
                    t.error(str(result.get("error") or f"{_sid} failed"))
                elif result.get("blocked"):
                    t.warning(str(result.get("error") or f"{_sid} blocked"))
            result["step_index"] = state.get("step_index", 0) + 1
            return result

        graph.add_node(system_id, wrapped)
        graph.add_conditional_edges(system_id, after_system, {**{s: s for s in (VENDRA, PROCURA, FINORA)}, END: END})

    return graph.compile()


def run_coordinator(
    task: TaskRequest,
    client: ERPNextClient,
    *,
    item_code: str,
    quantity: Optional[int] = None,
    use_memory: bool = False,
) -> TaskResult:
    """Run one task end to end: classify which systems it needs, execute
    them in dependency order, and assemble the final, honestly-labeled
    result -- never submitting a purchase order itself (that always needs
    a separate, explicit approval call; see systems.procura.submit_purchase_order)."""
    systems_needed = task.requested_systems or classify_systems_needed(task.description)
    if not systems_needed:
        return TaskResult(
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            systems_invoked=[],
            failed=True,
            error="no system matched this task's description; nothing was routed",
        )

    resolved_quantity = quantity
    if resolved_quantity is None and PROCURA in systems_needed:
        # Genuinely activates Procura's NLP subagent (NVIDIA Nemotron by
        # default, via get_llm("procura")) instead of leaving it dead code:
        # only reached when the caller didn't already give an explicit
        # quantity. interpret_purchase_request has its own regex fallback,
        # so a missing/failing LLM never blocks the task -- it only ever
        # fills in a quantity, never an item_code (no item-code resolution
        # from free text is implemented, so that stays an explicit,
        # required input; see procurement_schemas.ProcurementTaskRequest).
        parsed = procura_system.interpret_purchase_request(task.description)
        resolved_quantity = parsed.get("quantity") or 1

    graph = build_coordinator(client)
    initial: CoordinatorState = {
        "task_id": task.task_id,
        "correlation_id": task.correlation_id,
        "task_text": task.description,
        "item_code": item_code,
        "quantity": resolved_quantity or 1,
        "systems_needed": systems_needed,
        "step_index": 0,
        "messages": [],
        "use_memory": use_memory,
    }
    final_state = graph.invoke(initial)

    messages = [SystemMessage.model_validate(m) for m in final_state.get("messages", [])]
    requires_approval = (
        PROCURA in systems_needed and not final_state.get("failed") and not final_state.get("blocked")
    )
    approval_summary = None
    if requires_approval and final_state.get("recommended_supplier"):
        approval_summary = (
            f"Recommend purchase order: {final_state['recommended_supplier']} for "
            f"{final_state.get('recommended_total_cost')}. Awaiting explicit approval before submission."
        )

    return TaskResult(
        task_id=task.task_id,
        correlation_id=task.correlation_id,
        systems_invoked=systems_needed,
        messages=messages,
        requires_approval=requires_approval,
        approval_summary=approval_summary,
        result={
            "eligible_supplier_names": final_state.get("eligible_supplier_names"),
            "recommended_supplier": final_state.get("recommended_supplier"),
            "recommended_total_cost": final_state.get("recommended_total_cost"),
            "conflicts": final_state.get("conflicts") or [],
            "memory_path": final_state.get("memory_path") or {},
        },
        failed=bool(final_state.get("failed")),
        blocked=bool(final_state.get("blocked")),
        error=final_state.get("error"),
    )
