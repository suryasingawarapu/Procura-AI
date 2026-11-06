"""Finora's six specialist subagents.

Budget Verification and Invoice Matching are the existing, unchanged
deterministic logic that predates this pass (moved here verbatim from
app.procurement.systems.finora, which still re-exports ``match_invoices``
for backward compatibility and its own existing tests). The other four
are genuinely new, each scoped to only what real ERPNext/Supabase data
this project already has honest access to:

* Cost Analysis -- deeper arithmetic over the same budget check (budget
  utilization percentage), not a duplicate of Budget Verification's
  simple within/over determination.
* Payment Terms Review -- reviews whatever payment-related fields
  (``status``/``outstanding_amount``/``due_date``) actually exist on the
  matched invoice records. Reports "no data to review" honestly when an
  invoice doesn't carry those fields, rather than assuming they do.
* Approval Routing -- formalizes the project's existing hard rule
  ("never auto-approve a purchase order") as its own named, tested step:
  ``requires_human_approval`` is unconditionally ``True`` -- this
  specialist only ever computes a human-readable routing *reason*
  (e.g. "over budget", "budget status unknown"), never a bypass.
* Financial Records -- assembles the consolidated structured record that
  Finora's orchestrator already saves to the real, existing Supabase
  memory system (app.memory.provenance_fields.enrich_memory) -- the
  already-built audit trail is the "financial records store"; this
  specialist is what prepares its content, not a new store.
"""

from __future__ import annotations

from typing import Any, Dict

from ..calculations import budget_check
from ..mcp.client import ERPNextClient


def match_invoices(client: ERPNextClient, *, supplier: str):
    return client.list_invoices(supplier=supplier)


def budget_verification(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compare the estimated cost against a real, configured budget --
    never invented when the budget source has no data."""
    client = context["client"]
    available_budget = client.get_budget(cost_center=context.get("cost_center"))
    check = budget_check(context["estimated_total"], available_budget)
    return {"check": check}


def cost_analysis(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Deeper arithmetic over Budget Verification's own result -- how much
    of the available budget this purchase would use -- not a duplicate of
    the simple within/over verdict."""
    check = results["budget_verification"]["data"]["check"]
    available_budget = check["available_budget"]
    if available_budget is None or available_budget == 0:
        return {"_status": "skipped", "reason": "no budget figure to compute utilization against"}
    utilization_pct = round((context["estimated_total"] / available_budget) * 100, 2)
    return {"budget_utilization_pct": utilization_pct, "shortfall": check["shortfall"]}


def invoice_matching(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Match any existing invoices for the supplier -- reports "none
    found" honestly, never invents a match."""
    client = context["client"]
    invoices = match_invoices(client, supplier=context["supplier"])
    return {"invoices_matched": invoices, "count": len(invoices)}


def payment_terms_review(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Reviews whatever payment-related fields actually exist on the
    matched invoices -- never assumes a field ERPNext didn't return."""
    invoices = results["invoice_matching"]["data"]["invoices_matched"]
    if not invoices:
        return {"_status": "skipped", "reason": "no invoices on file for this supplier to review"}

    reviewed = []
    for inv in invoices:
        entry = {"name": inv.get("name")}
        for field in ("status", "outstanding_amount", "due_date"):
            if field in inv:
                entry[field] = inv[field]
        reviewed.append(entry)

    has_any_field = any(len(entry) > 1 for entry in reviewed)
    if not has_any_field:
        return {
            "_status": "skipped",
            "reason": "invoice records on file carry no payment-term fields (status/outstanding_amount/due_date)",
        }
    return {"reviewed_invoices": reviewed}


def approval_routing(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Formalizes the hard rule that no purchase order is ever auto-
    approved: ``requires_human_approval`` is unconditionally True. This
    only ever computes a human-readable routing reason -- it cannot
    bypass approval under any circumstance."""
    check = results["budget_verification"]["data"]["check"]
    if check["within_budget"] is None:
        reason = f"route to human approval: budget status unknown -- {check['note']}"
    elif check["within_budget"]:
        reason = "route to human approval: within budget, no compliance concerns raised by Finora"
    else:
        reason = f"route to human approval: OVER budget by {check['shortfall']}"
    return {"requires_human_approval": True, "reason": reason}


def financial_records(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Assembles the consolidated record the orchestrator saves to the
    real, existing Supabase-backed memory system -- the audit trail this
    project already has, not a new store."""
    record = {
        "budget_check": results["budget_verification"]["data"]["check"],
        "cost_analysis": results["cost_analysis"].get("data"),
        "invoices_matched": results["invoice_matching"]["data"]["count"],
        "approval_routing": results["approval_routing"]["data"],
    }
    return {"record": record}


SPECIALISTS = {
    "budget_verification": budget_verification,
    "cost_analysis": cost_analysis,
    "invoice_matching": invoice_matching,
    "payment_terms_review": payment_terms_review,
    "approval_routing": approval_routing,
    "financial_records": financial_records,
}
