"""Procura's six specialist subagents.

RFQ Management, Quotation Processing and Quotation Comparison are the
existing, unchanged deterministic logic that predates this pass (moved
here verbatim from app.procurement.systems.procura, which still re-exports
them for backward compatibility and its own existing tests). Supplier
Matching and Delivery Schedule Estimation are genuinely new, each scoped
to only what real data is already fetched:

* Supplier Matching -- which eligible suppliers actually have a
  quotation on file for this item, and which don't. Reuses the already-
  collected quotation rows; no new ERPNext call.
* Delivery Schedule Estimation -- each considered quotation's PROMISED
  delivery timeframe (the ``delivery_days`` field quotations already
  carry). Explicitly not real tracked delivery/receipt events -- no such
  data source exists yet, so this doesn't claim to track one.

Purchase Order Preparation is Procura's sixth specialist
(``submit_purchase_order``, in app.procurement.systems.procura) but is
deliberately NOT part of this module's automatic pipeline: it is a
separate, explicitly human-approved step the orchestrator never calls on
its own, exactly as before this pass.

The safety property from the compliance-gap fix is preserved exactly:
when ``eligible_supplier_names`` is empty, RFQ Management and Quotation
Processing self-skip (no ERPNext write or read is even attempted), and
Quotation Comparison still runs -- over zero quotations -- producing the
same clean "no quotations to compare" result as before.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..calculations import ComparisonResult, Quotation, compare_quotations
from ..mcp.client import ERPNextClient


def prepare_rfq(client: ERPNextClient, *, item_code: str, supplier_names: List[str], quantity: int = 1) -> Dict[str, Any]:
    return client.create_rfq(items=[item_code], supplier_names=supplier_names, qty=quantity)


def collect_quotations(client: ERPNextClient, *, item_code: str) -> List[Dict[str, Any]]:
    return client.list_supplier_quotations(item_code=item_code)


def to_quotation(row: Dict[str, Any]) -> Quotation:
    return Quotation(
        supplier=row["supplier"],
        unit_price=row["rate"],
        quantity=row.get("qty", 1),
        currency=row.get("currency", "USD"),
        delivery_days=row.get("delivery_days"),
        payment_terms_days=row.get("payment_terms_days"),
        source_reference=row.get("source_reference"),
    )


def blocking_conflicts(conflicts: List[Dict[str, Any]], comparison: ComparisonResult, quantity: int) -> List[Dict[str, Any]]:
    """The unresolved price conflicts that could change which supplier wins.

    A conflicted supplier is held out of the ranking until a person decides.
    That only matters if its price could win: when even its lowest claimed
    price does not beat the best remaining total, the conflict is reported
    but does not stop the task."""
    if not conflicts:
        return []
    best = min((s.total_cost for s in comparison.scores), default=None)
    blocking = []
    for conflict in conflicts:
        values = [c["value"] for c in conflict.get("claims", []) if c.get("value") is not None]
        if not values or best is None or min(values) * quantity < best:
            blocking.append(conflict)
    return blocking


def rfq_management(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Prepare an RFQ for the eligible suppliers. Never attempts the write
    at all when nobody is eligible (the core safety property); separately
    gated by ``allow_writes`` inside ``create_rfq`` itself."""
    eligible = context["eligible_supplier_names"]
    if not eligible:
        return {"_status": "skipped", "reason": "no eligible (compliant) suppliers", "rfq": None}
    client = context["client"]
    rfq = prepare_rfq(client, item_code=context["item_code"], supplier_names=eligible, quantity=context.get("quantity") or 1)
    return {"rfq": rfq}


def quotation_processing(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Collect quotations for the item, filtered to eligible suppliers
    only -- never falls back to comparing everyone when that filter is
    empty."""
    eligible = context["eligible_supplier_names"]
    if not eligible:
        return {"_status": "skipped", "reason": "no eligible (compliant) suppliers", "quote_rows": []}
    client = context["client"]
    all_rows = collect_quotations(client, item_code=context["item_code"])
    rows = [q for q in all_rows if q["supplier"] in eligible]
    if context.get("use_memory"):
        # Live mode: every quotation is also kept as a claim with its source, and claims that
        # disagree are reconciled before anything is compared (see quotation_claims).
        from ..quotation_claims import reconcile

        rec = reconcile(
            item_code=context["item_code"], eligible=eligible, erpnext_rows=all_rows, task_id=context.get("task_id")
        )
        return {
            "quote_rows": rec.rows,
            "reconciliation": {
                "conflicts": rec.conflicts, "resolved": rec.resolved, "external_only": rec.external_only,
                "archived": rec.archived, "claims_seen": rec.claims_seen, "note": rec.note,
            },
        }
    return {"quote_rows": rows}


def quotation_comparison(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic ranking over whatever Quotation Processing collected
    -- always runs, even over zero quotations, so a compliance block still
    produces a clean, correctly-empty comparison result."""
    quote_rows = results["quotation_processing"]["data"].get("quote_rows", [])
    quantity = context["quantity"]
    quotations = [to_quotation({**row, "qty": quantity}) for row in quote_rows]
    comparison: ComparisonResult = compare_quotations(quotations)
    conflicts = (results["quotation_processing"]["data"].get("reconciliation") or {}).get("conflicts") or []
    return {
        "comparison": comparison.model_dump(),
        "blocking_conflicts": blocking_conflicts(conflicts, comparison, quantity),
    }


def supplier_matching(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Which eligible suppliers actually have a quotation on file for this
    item, and which don't -- a genuine coverage check over the already-
    collected rows, not a new data source."""
    eligible = context["eligible_supplier_names"]
    if not eligible:
        return {"_status": "skipped", "reason": "no eligible (compliant) suppliers"}
    quote_rows = results["quotation_processing"]["data"].get("quote_rows", [])
    quoted_suppliers = {q["supplier"] for q in quote_rows}
    return {
        "matched_suppliers": [s for s in eligible if s in quoted_suppliers],
        "eligible_suppliers_without_a_quotation": [s for s in eligible if s not in quoted_suppliers],
    }


def delivery_schedule_estimation(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Each considered quotation's PROMISED delivery timeframe -- not
    real tracked delivery/receipt events, since no such data source
    exists yet."""
    quote_rows = results["quotation_processing"]["data"].get("quote_rows", [])
    estimates = [
        {"supplier": q["supplier"], "promised_delivery_days": q["delivery_days"]}
        for q in quote_rows
        if q.get("delivery_days") is not None
    ]
    if not estimates:
        return {"_status": "skipped", "reason": "no delivery_days data on any considered quotation"}
    return {"estimates": estimates}


def quotation_history(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Summary-first look at this item's quotation history: the stored summary
    and the claims newer than it first, then the supporting records of the
    suppliers being compared, to report what changed (a price or delivery time
    that moved). Live mode only -- it reads memory, and demo mode never does."""
    if not context.get("use_memory"):
        return {"_status": "skipped", "reason": "memory path is off (demo mode or disabled)"}
    from ..quotation_history import build_history

    quote_rows = results["quotation_processing"]["data"].get("quote_rows", [])
    return build_history(item_code=context["item_code"], quote_rows=quote_rows)


# Purchase Order Preparation (submit_purchase_order) is Procura's sixth
# specialist but intentionally not included here -- see module docstring.
SPECIALISTS = {
    "rfq_management": rfq_management,
    "quotation_processing": quotation_processing,
    "quotation_comparison": quotation_comparison,
    "supplier_matching": supplier_matching,
    "delivery_schedule_estimation": delivery_schedule_estimation,
    "quotation_history": quotation_history,
}
