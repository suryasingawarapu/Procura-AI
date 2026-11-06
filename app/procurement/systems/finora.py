"""Finora -- Finance & Approval Management.

Finora is itself an orchestrator: it understands the task, runs its six
specialist subagents (app.procurement.subagents.finora -- Budget
Verification, Cost Analysis, Invoice Matching, Payment Terms Review,
Approval Routing, Financial Records) and combines their results into one
message for the coordinator. The budget verdict stays 100% deterministic
(app.procurement.calculations.budget_check) -- never invented when the
budget source has no data -- and no purchase order is ever auto-approved;
Approval Routing only ever computes a human-readable routing reason, and
``requires_human_approval`` it returns is unconditionally True.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.memory.provenance_fields import enrich_memory
from app.tools.memory_tools import build_agent_tools

from ..identities import FINORA, SYSTEM_IDS
from ..mcp.client import ERPNextClient, ERPNextUnavailable
from ..messages import MessageType, SourceReference, SystemMessage, new_id
from ..narration import narrate
from ..subagents.base import run_specialists
from ..subagents.finora import SPECIALISTS as FINORA_SPECIALISTS
from ..subagents.finora import match_invoices

_tools = None


def _tool(name: str):
    global _tools
    if _tools is None:
        _tools = {t.name: t for t in build_agent_tools(FINORA, valid_ids=SYSTEM_IDS)}
    return _tools[name]


def run_financial_validation(
    *,
    task_id: str,
    correlation_id: str,
    client: ERPNextClient,
    estimated_total: float,
    supplier: str,
    cost_center: Optional[str] = None,
    llm: Optional[Any] = None,
) -> SystemMessage:
    """Finora's orchestrator entry point: check a real configured budget
    (never invented if missing), match any existing invoices for the
    supplier (reporting none-found honestly), and record the validation.

    ``llm`` is an optional subagent (NVIDIA Nemotron by default) used only
    to narrate the result (``payload["ai_summary"]``) -- ``within_budget``
    is always ``check["within_budget"]``, computed deterministically by
    ``budget_check`` above; narration can never change it."""
    context = {
        "client": client,
        "estimated_total": estimated_total,
        "supplier": supplier,
        "cost_center": cost_center,
    }
    specialist_results = run_specialists(
        FINORA_SPECIALISTS,
        context,
        needed=[
            "budget_verification",
            "cost_analysis",
            "invoice_matching",
            "payment_terms_review",
            "approval_routing",
            "financial_records",
        ],
    )

    # Budget Verification and Invoice Matching are load-bearing: a real
    # backend failure there must still surface to the coordinator (one-
    # retry policy), exactly as before this pass.
    for critical in ("budget_verification", "invoice_matching"):
        if specialist_results[critical].status == "error":
            raise ERPNextUnavailable(specialist_results[critical].error)

    check = specialist_results["budget_verification"].data["check"]
    invoices = specialist_results["invoice_matching"].data["invoices_matched"]
    available_budget = check["available_budget"]

    memory_key = f"financial_validation:{supplier}"
    if check["within_budget"] is None:
        content = f"Budget status for {supplier}: UNKNOWN -- {check['note']}"
        verification_status = "unverified"
    else:
        verdict = "within budget" if check["within_budget"] else f"OVER budget by {check['shortfall']}"
        content = f"Budget status for {supplier}: {verdict} (estimated {estimated_total}, available {available_budget})"
        verification_status = "verified"

    saved = _tool("save_memory_entry").invoke({"memory_key": memory_key, "content": content})
    enrich_memory(
        saved["id"],
        source_type="computed",
        task_id=task_id,
        verification_status=verification_status,
        sharing_classification="shareable",
        structured_fact=specialist_results["financial_records"].data.get("record", {"budget_check": check}),
    )

    sources = [SourceReference(source_type="erpnext_doctype", reference=f"Budget/{cost_center or 'default'}")]

    ai_summary = narrate(
        "You are Finora, a finance-approval assistant. In 2-3 plain-language sentences, summarize "
        "the budget check below for a human reviewer. State only what the data says -- never "
        "claim a purchase is within budget unless the data says so.",
        content + f" Invoices matched for this supplier: {len(invoices)}.",
        llm=llm,
    )

    return SystemMessage(
        message_id=new_id("msg"),
        task_id=task_id,
        correlation_id=correlation_id,
        sender=FINORA,
        receiver="coordinator",
        message_type=MessageType.RESPONSE,
        payload={
            "budget_check": check,
            "invoices_matched": invoices,
            "ai_summary": ai_summary,
            # Full per-specialist detail (status/data/error for all six),
            # keyed by name -- see app.procurement.subagents.finora.
            # Additive: every field above this one is unchanged.
            "specialists": {name: r.to_dict() for name, r in specialist_results.items()},
        },
        sources=sources,
        purpose="financial_validation",
        status="ok",
    )
