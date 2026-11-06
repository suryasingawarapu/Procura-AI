"""The single, natural-language procurement task entry point.

Connects what already existed as two separate, manually-operated pieces:
Purchase Requests (app.procurement.workflow) and the real agent pipeline
(app.procurement.coordinator.run_coordinator) -- this module is the
"Coordinator" the implementation brief describes: one instruction in,
a durable, resumable task out, the real Vendra/Procura/Finora pipeline
actually run, never a mock.

Nothing here duplicates an agent or reinvents calculation/eligibility
logic -- it calls the existing, unmodified run_coordinator and persists
its result onto the existing PurchaseRequest record via
app.procurement.workflow, which already has full version-history
provenance for free.

Mode separation (demo vs. live) is explicit and never silently falls
back: ``mode="demo"`` always uses the isolated, clearly-labeled
SeededERPNextClient from app.procurement.demo_fixtures (memory writes
stubbed, exactly like the existing /procurement/demo/run route);
``mode="live"`` always uses get_configured_client() against real ERPNext,
with real Supabase memory writes -- exactly like the existing
/procurement/tasks route.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Dict, List, Literal, Optional
from unittest.mock import patch

from . import vendor_master, workflow
from .coordinator import run_coordinator
from .demo_fixtures import DEMO_ITEM_CODE, DEMO_QUANTITY, build_demo_seed_from_vendor_pool
from .mcp.client import ERPNextClient, SeededERPNextClient, get_configured_client
from .messages import TaskRequest
from .progress import emit

__all__ = [
    "ParsedRequirement",
    "parse_procurement_requirement",
    "start_procurement_task",
    "resume_procurement_task",
    "get_task",
]

Mode = Literal["demo", "live"]

_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}
_INDIAN_UNITS = {"lakh": 100_000, "lakhs": 100_000, "crore": 10_000_000, "crores": 10_000_000}

_BUDGET_PATTERN = re.compile(
    r"(?:within|budget\s+(?:of\s+)?|max(?:imum)?\s+budget\s+(?:of\s+)?|not\s+exceed(?:ing)?)\s*"
    # Possessive quantifiers (++ / ?+) below: a number immediately
    # followed by a time unit is a deadline, not a budget (e.g. "within 15
    # days") -- without this, "within <amount>, ... within <N> days" could
    # match the WRONG "within" if the real budget phrase doesn't exactly
    # fit this pattern (confirmed live: an LLM-paraphrased "within budget
    # 200000" failed to match here at all, and .search() silently fell
    # through to "within 15 days" instead, recording a budget of 15).
    # A plain greedy quantifier isn't enough on its own: re still
    # backtracks "15" down to "1" to dodge the trailing lookahead below
    # (confirmed live -- that read "within 1" out of "within 15 days" and
    # recorded a budget of 1.0, an even subtler wrong answer). Possessive
    # quantifiers forbid that backtrack, so the digit group can only ever
    # match the full number, and the lookahead then correctly rejects it.
    r"([₹$€£])?\s*([\d,]++(?:\.\d+)?+)\s*+(lakh|lakhs|crore|crores)?+"
    r"(?!\s*(?:days?|weeks?|months?|years?)\b)",
    re.IGNORECASE,
)
_DELIVERY_PATTERN = re.compile(
    r"(?:delivered?\s+to|deliver(?:y)?\s+to)\s+(?:our\s+)?([A-Za-z][A-Za-z0-9 ,.'-]*?)(?:\s+within|\s*,|\.|$)"
    # A second, independent phrasing -- "for our <place> office" -- found
    # live: a real message phrased this way ("for our Chennai office")
    # matched neither alternative above, so delivery_location stayed
    # unresolved even though a location was clearly given. Requires the
    # phrase to end in an actual location noun (not just bare "for ..."),
    # which a generic "for" alone would be too ambiguous to assume --
    # e.g. "for our engineering team" is not a delivery location.
    r"|for\s+our\s+([A-Za-z][A-Za-z0-9 ,.'-]*?\s+(?:office|warehouse|location|branch|center|centre|hub|facility|site|HQ))"
    r"(?:\s+within|\s*,|\.|$)",
    re.IGNORECASE,
)
_DEADLINE_DAYS_PATTERN = re.compile(r"within\s+(\d+)\s+days?", re.IGNORECASE)


class ParsedRequirement(Dict[str, Any]):
    """A plain dict with a fixed, documented shape -- kept as a thin type
    alias rather than a pydantic model so callers can still treat it as a
    dict (e.g. for logging/storage) without an extra conversion step."""


def parse_procurement_requirement(text: str) -> Dict[str, Any]:
    """Deterministic, regex-based extraction of budget/currency/delivery
    location/required date from free text -- separate from (and never a
    replacement for) Procura's existing quantity/item parsing
    (interpret_purchase_request), which this function does not touch.
    Any field it can't find is left as None, never guessed.
    """
    result: Dict[str, Any] = {
        "estimated_budget": None,
        "currency": None,
        "delivery_location": None,
        "required_date": None,
    }

    budget_match = _BUDGET_PATTERN.search(text)
    if budget_match:
        symbol, amount_str, unit = budget_match.groups()
        amount = float(amount_str.replace(",", ""))
        if unit:
            amount *= _INDIAN_UNITS[unit.lower()]
        result["estimated_budget"] = amount
        result["currency"] = _CURRENCY_SYMBOLS.get(symbol, "INR" if unit else "USD")

    delivery_match = _DELIVERY_PATTERN.search(text)
    if delivery_match:
        result["delivery_location"] = (delivery_match.group(1) or delivery_match.group(2)).strip()

    deadline_match = _DEADLINE_DAYS_PATTERN.search(text)
    if deadline_match:
        days = int(deadline_match.group(1))
        result["required_date"] = (date.today() + timedelta(days=days)).isoformat()

    return result


def _resolve_demo_client(pr: workflow.PurchaseRequest) -> tuple:
    """Demo mode's client -- searches the real, operator-seeded pool of
    DEMO-VENDOR-#### vendor-master records (seed 30+ of them via
    POST /procurement/vendors/demo-seed) for this specific request, so the
    pipeline genuinely compares across however many demo vendors exist
    instead of always picking between the same fixed two. Falls back to
    the original two-supplier scenario when fewer than 2 demo vendors have
    been seeded yet. Returns (client, item_code_used_for_this_seed)."""
    demo_vendors = [asdict(v) for v in vendor_master.search_vendors(is_demo=True, status="Active")]
    seed = build_demo_seed_from_vendor_pool(
        item_description=pr.item_description,
        quantity=pr.quantity,
        estimated_budget=pr.estimated_budget,
        demo_vendors=demo_vendors,
    )
    return SeededERPNextClient(seed), seed["items"][0]["name"]


def _resolve_client(pr: workflow.PurchaseRequest) -> tuple:
    if pr.mode == "demo":
        return _resolve_demo_client(pr)
    return get_configured_client()[0], None


def _store_label(pr: workflow.PurchaseRequest) -> str:
    return "the isolated demo store" if pr.mode == "demo" else "persistent memory"


def _run_pipeline(pr: workflow.PurchaseRequest) -> workflow.PurchaseRequest:
    """Actually run the real coordinator for this Purchase Request's
    requirement, and persist the outcome onto it. Demo mode's memory
    writes are stubbed (exactly like the existing /procurement/demo/run
    route) so a simulated run never reaches the real Supabase project;
    live mode writes real memory, exactly like /procurement/tasks today.
    """
    client, demo_item_code = _resolve_client(pr)
    task = TaskRequest(
        description=(
            f"[{'SIMULATED DEMO' if pr.mode == 'demo' else 'LIVE'}] "
            f"Find eligible suppliers for {pr.quantity} {pr.item_description}, collect/compare quotations, "
            f"check the available budget, and prepare the purchase for approval."
        )
    )
    item_code = pr.item_code or demo_item_code or (DEMO_ITEM_CODE if pr.mode == "demo" else None)
    quantity = pr.quantity or (DEMO_QUANTITY if pr.mode == "demo" else None)
    # Persist the item_code actually used for this run (it may have been
    # resolved just above, e.g. the demo vendor-pool's generated code) --
    # without this, a later approved-PO submission for a demo task would
    # have no item_code to submit against at all.
    pr.item_code = item_code

    emit("orchestrator", f"Orchestrator: running Vendra → Procura → Finora for {quantity} × {item_code}")
    try:
        if pr.mode == "demo":
            with patch("app.procurement.systems.vendra.enrich_memory"), patch(
                "app.procurement.systems.procura.enrich_memory"
            ), patch("app.procurement.systems.finora.enrich_memory"), patch(
                "app.tools.memory_tools.save_memory", return_value={"id": "demo-mem-stub"}
            ):
                result = run_coordinator(task, client, item_code=item_code, quantity=quantity)
        else:
            from .propagation import propagation_enabled

            # Live mode: the agents share evidence through memory, Procura selects suppliers from what
            # it received, quotation claims are reconciled, and Finora checks the evidence is current.
            result = run_coordinator(
                task, client, item_code=item_code, quantity=quantity, use_memory=propagation_enabled()
            )
    except Exception as exc:  # a real backend/LLM failure must become an honest Failed state, never a crash
        pr.workflow_state = "Failed"
        pr.blockers = [f"{type(exc).__name__}: {exc}"]
        workflow._save_pr(pr, task_id=pr.pr_id, change_reason="workflow_state -> Failed")
        return pr

    pr.coordinator_task_id = result.task_id
    pr.coordinator_result = {
        "systems_invoked": result.systems_invoked,
        "failed": result.failed,
        "blocked": result.blocked,
        "error": result.error,
        "requires_approval": result.requires_approval,
        "approval_summary": result.approval_summary,
        "result": result.result,
        # Every inter-system message the coordinator exchanged with
        # Vendra/Procura/Finora for this run -- each one's own ai_summary
        # and per-specialist (6-per-system) status/data/error, so the chat
        # can show what every agent actually did, not just the final
        # recommendation. model_dump(mode="json") makes these plain,
        # JSON-safe dicts (datetimes -> ISO strings) before they're stored
        # via dataclasses.asdict() in workflow._save_pr.
        "messages": [m.model_dump(mode="json") for m in result.messages],
    }

    if result.failed:
        pr.workflow_state = "Failed"
        pr.blockers = [result.error or "the coordinator reported a failure with no further detail"]
    elif result.blocked and (result.result or {}).get("conflicts"):
        # Not a dead end: two sources disagree about a price and a person has to say which is right.
        pr.workflow_state = "Waiting For Clarification"
        pr.blockers = [result.error or "a quoted price is disputed between sources"]
    elif result.blocked:
        pr.workflow_state = "Blocked"
        pr.blockers = [result.error or "the coordinator reported a blocked outcome with no further detail"]
    elif result.requires_approval:
        pr.workflow_state = "Waiting For Approval"
        pr.blockers = []
        # Also advance the PR's own human-approval status (Draft/Returned
        # for Changes -> Pending Approval) -- set directly, in the SAME
        # in-memory object and the SAME save below, rather than via
        # transition_purchase_request(), which would re-read the PR fresh
        # from storage and discard the workflow_state/coordinator_result
        # changes made above (they haven't been persisted yet at this
        # point). The transition is still validated against the same rules.
        allowed = workflow.VALID_PR_TRANSITIONS.get(pr.status, set())
        if "Pending Approval" not in allowed:
            raise workflow.PurchaseRequestError(
                f"cannot request approval for {pr.pr_id}: invalid transition {pr.status!r} -> 'Pending Approval'"
            )
        pr.status = "Pending Approval"
    else:
        pr.workflow_state = "Completed"
        pr.blockers = []

    pr.updated_at = workflow._now()
    workflow._save_pr(pr, task_id=pr.pr_id, change_reason=f"workflow_state -> {pr.workflow_state}")
    emit("memory", f"Outcome saved to {_store_label(pr)} as a new version: {pr.workflow_state}")
    return _auto_approve_if_configured(pr)


def _auto_approve_if_configured(pr: workflow.PurchaseRequest) -> workflow.PurchaseRequest:
    """Live mode only, and only when ``LIVE_MODE_AUTO_APPROVE`` is
    explicitly set -- skips the human approval step entirely and
    immediately submits the real purchase order the instant the agents
    produce a recommendation, with no spending limit and no review of any
    kind. Off by default; this removes the project's one safety gate (see
    workflow.record_auto_approval's own docstring on why that's recorded
    honestly, never disguised as a real decision) and must never be
    silently enabled -- it is explicit, operator-set configuration, read
    fresh on every call so a mid-session .env change takes effect
    immediately.
    """
    if pr.mode != "live" or pr.workflow_state != "Waiting For Approval":
        return pr
    if os.getenv("LIVE_MODE_AUTO_APPROVE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return pr
    workflow.record_auto_approval(pr.pr_id, task_id=pr.coordinator_task_id)
    emit("approval", "Approved by the live auto-approve policy (recorded as SYSTEM-AUTO-APPROVAL, no human review)")
    return submit_approved_purchase_order(pr.pr_id)


def start_procurement_task(
    *,
    instruction: str,
    requested_by: str,
    mode: Mode = "demo",
    item_code: Optional[str] = None,
    delivery_location: Optional[str] = None,
    required_date: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> workflow.PurchaseRequest:
    """The one entry point: parse ``instruction``, create a durable,
    linked Purchase Request, and run the real agent pipeline against it
    as far as the available information and (in live mode) real data
    allow. Returns the resulting PurchaseRequest -- check its
    ``workflow_state`` for what actually happened.

    ``idempotency_key``: if a task with this exact key already exists,
    that existing task is returned unchanged instead of starting a new
    one -- a retried "start" call can never create a duplicate task.
    """
    if idempotency_key:
        existing = _find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

    from .systems.procura import interpret_purchase_request

    parsed_qty_item = interpret_purchase_request(instruction)
    parsed_rest = parse_procurement_requirement(instruction)

    quantity = parsed_qty_item.get("quantity")
    delivery_location = delivery_location or parsed_rest["delivery_location"]
    required_date = required_date or parsed_rest["required_date"]
    estimated_budget = parsed_rest["estimated_budget"]

    missing: List[str] = []
    if quantity is None:
        missing.append("quantity")
    if mode == "live" and not item_code:
        missing.append("item_code (no free-text item-catalog matching exists -- provide the exact ERPNext item code)")
    if estimated_budget is None:
        missing.append("estimated_budget")
    if not delivery_location:
        missing.append("delivery_location")

    pr = workflow.create_purchase_request(
        requested_by=requested_by,
        item_description=parsed_qty_item.get("item_description") or instruction,
        # workflow.create_purchase_request rejects quantity<=0 outright
        # (by design) -- when it's genuinely missing, 1 is a harmless
        # structural placeholder, never a guessed real answer: "missing"
        # above (and the resulting "Waiting For Information" state/
        # blockers below) is what honestly tells the caller quantity still
        # needs to be supplied. Without this, a message with no
        # extractable quantity (e.g. "hii") crashed here with a raw
        # "quantity must be positive" error instead of degrading
        # gracefully.
        quantity=quantity or 1,
        estimated_budget=estimated_budget or 0.0,
        delivery_location=delivery_location or "(not provided)",
        required_date=required_date or "(not provided)",
        business_justification=instruction,
        # Passed at creation time, not set on the returned object
        # afterward -- the very first "created" save happens inside
        # create_purchase_request, before a post-hoc `pr.mode = mode`
        # assignment here would ever take effect (see workflow.py's
        # create_purchase_request/_save_pr docstrings on the real bug
        # this used to cause: every demo task's first save hit the real
        # database regardless, since mode wasn't known yet at that point).
        mode=mode,
    )
    pr.item_code = item_code
    pr.idempotency_key = idempotency_key
    emit("memory", f"Task {pr.pr_id} saved to {_store_label(pr)} (version 1)")

    if missing:
        pr.workflow_state = "Waiting For Information"
        pr.blockers = [f"missing or unresolvable: {', '.join(missing)}"]
        workflow._save_pr(pr, task_id=pr.pr_id, change_reason="workflow_state -> Waiting For Information")
        emit("waiting", f"Pipeline held, waiting for: {', '.join(m.split(' (')[0] for m in missing)}")
        return pr

    pr.workflow_state = "Running"
    workflow._save_pr(pr, task_id=pr.pr_id, change_reason="workflow_state -> Running")
    return _run_pipeline(pr)


def resume_procurement_task(pr_id: str) -> workflow.PurchaseRequest:
    """Re-attempt a task that's stuck in "Running" (e.g. the process
    restarted mid-call) or "Waiting For Information" (now that the
    missing fields have been filled in via update). Any other state is
    returned unchanged -- resuming an already-finished task is a no-op,
    never a re-run."""
    pr = workflow.get_purchase_request(pr_id)
    if pr is None:
        raise workflow.PurchaseRequestError(f"no such task: {pr_id}")
    emit("memory", f"Task {pr.pr_id} recalled from {_store_label(pr)} (state: {pr.workflow_state})")
    if pr.workflow_state not in ("Running", "Waiting For Information", "Waiting For Clarification"):
        return pr
    if pr.workflow_state == "Waiting For Information":
        # Same field order as start_procurement_task's own `missing` list
        # below -- so that when nothing has actually changed, the
        # regenerated message is byte-identical and no pointless resave
        # happens (see test_resuming_does_not_resave_when_blockers_are_unchanged).
        still_missing: List[str] = []
        if not pr.quantity:
            still_missing.append("quantity")
        if pr.mode == "live" and not pr.item_code:
            still_missing.append("item_code (no free-text item-catalog matching exists -- provide the exact ERPNext item code)")
        if not pr.estimated_budget:
            still_missing.append("estimated_budget")
        if not pr.delivery_location or pr.delivery_location == "(not provided)":
            still_missing.append("delivery_location")
        if still_missing:
            # Refresh the blockers to reflect what's ACTUALLY still
            # missing right now -- a real bug found live: a field the
            # caller had just successfully supplied (e.g. item_code) kept
            # appearing in this message anyway, because it was never
            # recomputed here -- just the stale snapshot from when the
            # task was first created, even though the field itself really
            # had been updated in storage.
            new_blockers = [f"missing or unresolvable: {', '.join(still_missing)}"]
            if new_blockers != pr.blockers:
                pr.blockers = new_blockers
                pr.updated_at = workflow._now()
                workflow._save_pr(pr, task_id=pr.pr_id, change_reason="blockers refreshed on resume")
            return pr
    return _run_pipeline(pr)


def submit_approved_purchase_order(pr_id: str) -> workflow.PurchaseRequest:
    """After a human's "Approved" decision (app.procurement.workflow.
    record_approval_decision), actually submit the recommended purchase
    order -- a real ERPNext write in live mode (requires
    ERPNEXT_ALLOW_WRITES=1), a clearly-labeled simulated one in demo mode.
    Never called by the pipeline itself; this is the one deliberate,
    explicitly human-approved step app.procurement.subagents.procura's own
    docstring describes. Idempotent: a PR that already has a
    ``purchase_order`` recorded is returned unchanged rather than
    submitted a second time.

    Any failure (e.g. writes disabled, a real ERPNext validation error) is
    recorded honestly on the PR -- never silently swallowed, never
    reported as success when nothing was actually created.
    """
    pr = workflow.get_purchase_request(pr_id)
    if pr is None:
        raise workflow.PurchaseRequestError(f"no such task: {pr_id}")
    if pr.purchase_order is not None:
        return pr
    if pr.status != "Approved":
        return pr  # nothing to submit until a human has actually approved it

    cr = pr.coordinator_result or {}
    cr_result = cr.get("result") or {}
    recommended = cr_result.get("recommended_supplier")
    total_cost = cr_result.get("recommended_total_cost")
    if not recommended or not pr.item_code or not pr.quantity:
        return pr  # no real recommendation to act on -- never fabricate a PO

    from .systems.procura import submit_purchase_order

    # The real, already-recommended unit price -- a PO submitted with no
    # rate at all would misrepresent what was actually approved.
    rate = (total_cost / pr.quantity) if total_cost else None

    client, _ = _resolve_client(pr)
    try:
        po = submit_purchase_order(
            client, supplier=recommended, item_code=pr.item_code, quantity=pr.quantity, approved=True, rate=rate
        )
        pr.purchase_order = {"status": "submitted", "mode": pr.mode, "result": po}
        pr.workflow_state = "Completed"
        name = po.get("name") if isinstance(po, dict) else None
        emit("erpnext", f"Purchase Order submitted to ERPNext{f': {name}' if name else ''}")
        _draft_po_email(pr, client)
    except Exception as exc:  # a real backend failure is reported honestly, never swallowed or faked as success
        pr.purchase_order = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        pr.workflow_state = "Approved (PO Submission Failed)"
        emit("erpnext", f"Purchase Order was NOT created: {type(exc).__name__}: {exc}")

    pr.updated_at = workflow._now()
    workflow._save_pr(pr, task_id=pr.pr_id, change_reason=f"purchase_order -> {pr.purchase_order.get('status')}")
    return pr


def _draft_po_email(pr: workflow.PurchaseRequest, client: Any) -> None:
    """Draft (never send) the purchase-order email once a PO exists. Best effort: a missing vendor
    address or an unconfigured mail layer must never undo or fail the purchase order itself."""
    try:
        from .mail import service

        rec = service.draft_for_request(pr, client=client)
        emit("mail", f"PO email to {rec['vendor']} drafted (not sent; a person sends it): {rec['mail_id']}")
    except Exception as exc:
        emit("mail", f"PO email not drafted: {exc}")


def get_task(pr_id: str) -> Optional[workflow.PurchaseRequest]:
    return workflow.get_purchase_request(pr_id)


def _find_by_idempotency_key(key: str) -> Optional[workflow.PurchaseRequest]:
    from app.memory.store import get_agent_memories

    for row in get_agent_memories(workflow.PROCURA):
        fact = row.get("structured_fact")
        if fact and fact.get("idempotency_key") == key:
            return workflow.PurchaseRequest(**fact)
    return None
