"""Demo Budget creation planning -- pure, side-effect-free validation.

Finora's budget check (`app.procurement.systems.finora`) has always
honestly reported "no budget data found" rather than inventing a number
when ERPNext has no Budget record (see README's known gap). This module
answers a narrower question: given whatever Company/Fiscal Year/Cost
Center/Account records a live ERPNext instance *actually* has, is it safe
to create one real, clearly-attributable demo Budget row -- never by
guessing a Company, Fiscal Year, Cost Center or Account that doesn't
exist, only by using ones a caller already discovered live.

The one live write this enables (performed by a caller, not here) stays
fully separate from -- and no more permissive than -- the RFQ/PO write
gates in app.procurement.mcp.client: `plan_demo_budget` only ever builds
a payload or explains what's missing; it never calls ERPNext itself.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_DISTRIBUTION_FREQUENCIES = ("Monthly", "Quarterly", "Half-Yearly", "Yearly")

# The Budget doctype's own fixed naming series (confirmed live via
# get_doctype_schema on the configured ERPNext instance), not invented.
BUDGET_NAMING_SERIES = "BUDGET-.########"


def plan_demo_budget(
    *,
    company: Optional[str],
    fiscal_year: Optional[str],
    cost_center: Optional[str],
    account: Optional[str],
    budget_amount: Optional[float],
    distribution_frequency: str = "Yearly",
) -> Dict[str, Any]:
    """Build a `Budget` creation payload from real, already-discovered
    ERPNext records, or report exactly what's missing instead of guessing.

    Every argument must be a record a caller already confirmed exists live
    (e.g. via `list_documents`/`get_doctype_schema`) -- this function does
    not look anything up itself. Returns ``{"ready": False, "missing": [...]}``
    if any required value wasn't supplied; never fabricates a Company,
    Fiscal Year, Cost Center or Account to fill the gap.
    """
    required = {
        "company": company,
        "fiscal_year": fiscal_year,
        "cost_center": cost_center,
        "account": account,
        "budget_amount": budget_amount,
    }
    missing: List[str] = [name for name, value in required.items() if not value]
    if distribution_frequency not in _DISTRIBUTION_FREQUENCIES:
        missing.append(f"distribution_frequency (must be one of {_DISTRIBUTION_FREQUENCIES})")
    if missing:
        return {"ready": False, "missing": missing}

    return {
        "ready": True,
        "payload": {
            "naming_series": BUDGET_NAMING_SERIES,
            "budget_against": "Cost Center",
            "company": company,
            "cost_center": cost_center,
            "account": account,
            "from_fiscal_year": fiscal_year,
            "to_fiscal_year": fiscal_year,
            "distribution_frequency": distribution_frequency,
            "budget_amount": budget_amount,
        },
    }
