"""Safe Demo Mode -- clearly-labeled synthetic fixtures for demonstrating
the full procurement workflow without touching live ERPNext or real
compliance evidence.

This does NOT introduce a new execution path: it builds a seed dict for
the project's existing ``SeededERPNextClient`` (already "fully real,
in-memory, clearly-labeled seed data" per its own docstring in
app.procurement.mcp.client -- the exact class the whole unit test suite
already runs against) and hands it to the real, unmodified
``run_coordinator``. Every real specialist, the real LangGraph pipeline,
and the real deterministic math (compare_quotations/budget_check) run for
real against this data -- nothing here hardcodes a result.

Every record below carries "(SIMULATED)" in its name/reference and the
compliance "documents" are plain label strings (not files, not generated
PDFs) whose titles say, in capitals, that they are not real -- they exist
only to exercise the real filename-pattern matcher in
app.procurement.subagents.vendra, the same way the project's existing
seeded fixture's "W-9 Tax Form"/"Business License" titles already do.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Literal, Optional

DEMO_ITEM_CODE = "DEMO-LAPTOP-14"
DEMO_QUANTITY = 10
DEMO_BUDGET = 600_000.0

DEMO_SUPPLIER_TECH = "DEMO-SUP-TECH"
DEMO_SUPPLIER_EDGE = "DEMO-SUP-EDGE"

# Titles chosen to genuinely satisfy the real, unmodified India compliance
# patterns in app.procurement.subagents.vendra (word-boundary-safe: hyphens
# are non-word characters, so \b forms correctly around "PAN"/"GST" -- see
# that module's own comment on why underscores don't work for this) --
# while being unmistakably labeled as not real evidence.
_SIMULATED_PAN = "SIMULATED-PAN-CARD-DEMO-NOT-A-REAL-DOCUMENT"
_SIMULATED_GST = "SIMULATED-GST-REGISTRATION-CERTIFICATE-DEMO-NOT-A-REAL-DOCUMENT"
# Same idea for every other country's default requirement (W-9 Tax
# Form / Business License -- app.procurement.subagents.vendra's
# REQUIRED_SUPPLIER_DOCUMENTS), needed by build_demo_seed_from_vendor_pool
# below since its vendor pool spans multiple countries, not just India.
_SIMULATED_W9 = "SIMULATED-W-9-TAX-FORM-DEMO-NOT-A-REAL-DOCUMENT"
_SIMULATED_BUSINESS_LICENSE = "SIMULATED-BUSINESS-LICENSE-DEMO-NOT-A-REAL-DOCUMENT"


def build_demo_seed(scenario: Literal["eligible", "ineligible"] = "eligible") -> Dict[str, Any]:
    """Build a seed dict for SeededERPNextClient matching the demo
    scenario: DemoTech Solutions (cheaper) and SmartEdge Systems, item
    DEMO-LAPTOP-14, quantity 10, budget 600,000 -- isolated, in-memory,
    never touching live ERPNext or real Supabase data.

    ``scenario="eligible"``: both suppliers carry clearly-labeled
    synthetic PAN/GST documents, so the real compliance check (unchanged)
    finds them genuinely compliant.
    ``scenario="ineligible"``: no compliance documents at all -- the same
    honest "nothing on file" state the real live demo suppliers are
    actually in, to demonstrate the blocked path without fabricating
    anything.
    """
    compliant_documents = {
        DEMO_SUPPLIER_TECH: [{"title": _SIMULATED_PAN}, {"title": _SIMULATED_GST}],
        DEMO_SUPPLIER_EDGE: [{"title": _SIMULATED_PAN}, {"title": _SIMULATED_GST}],
    }
    supplier_documents = compliant_documents if scenario == "eligible" else {}

    return {
        "suppliers": [
            {
                "name": DEMO_SUPPLIER_TECH,
                "supplier_name": "DemoTech Solutions (SIMULATED)",
                "country": "India",
                "disabled": 0,
                "on_hold": 0,
                "source_reference": f"Supplier/{DEMO_SUPPLIER_TECH} (SIMULATED)",
                "_seeded": True,
                "_demo": True,
            },
            {
                "name": DEMO_SUPPLIER_EDGE,
                "supplier_name": "SmartEdge Systems (SIMULATED)",
                "country": "India",
                "disabled": 0,
                "on_hold": 0,
                "source_reference": f"Supplier/{DEMO_SUPPLIER_EDGE} (SIMULATED)",
                "_seeded": True,
                "_demo": True,
            },
        ],
        "supplier_documents": supplier_documents,
        "items": [
            {
                "name": DEMO_ITEM_CODE,
                "item_name": "DEMO Laptop 14-inch (SIMULATED)",
                "item_group": "Computers",
                "source_reference": f"Item/{DEMO_ITEM_CODE} (SIMULATED)",
                "_seeded": True,
            }
        ],
        "quotations": [
            {
                "name": "DEMO-SQ-TECH-0001",
                "supplier": DEMO_SUPPLIER_TECH,
                "item_code": DEMO_ITEM_CODE,
                "rate": 52_000.0,  # x10 = 520,000.00 -- the cheaper, expected-recommended quote
                "qty": DEMO_QUANTITY,
                "currency": "INR",
                "delivery_days": 7,
                "payment_terms_days": 30,
                "source_reference": "Supplier Quotation/DEMO-SQ-TECH-0001 (SIMULATED)",
                "_seeded": True,
            },
            {
                "name": "DEMO-SQ-EDGE-0001",
                "supplier": DEMO_SUPPLIER_EDGE,
                "item_code": DEMO_ITEM_CODE,
                "rate": 55_000.0,  # x10 = 550,000.00
                "qty": DEMO_QUANTITY,
                "currency": "INR",
                "delivery_days": 5,
                "payment_terms_days": 15,
                "source_reference": "Supplier Quotation/DEMO-SQ-EDGE-0001 (SIMULATED)",
                "_seeded": True,
            },
        ],
        "budget": {"default": DEMO_BUDGET},
        "invoices": [],
        "rfqs": [],
        "purchase_orders": [],
    }


# ---------------------------------------------------------------------------
# Chat-driven demo: search a real, user-seeded pool of DEMO-VENDOR-#### vendor
# master records (app.procurement.vendor_master.seed_demo_vendors), not just
# the fixed two-supplier scenario above. The real Vendra/Procura/Finora
# pipeline then genuinely searches, filters, and compares across however many
# demo vendors exist -- still entirely synthetic/in-memory, still never
# touching live ERPNext, but no longer scripted to always pick between the
# same two names regardless of what was asked for.
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: Dict[str, tuple] = {
    "IT Hardware": ("laptop", "computer", "server", "monitor", "printer", "hardware", "network", "router", "ram", "ssd"),
    "Office Supplies": ("chair", "desk", "stationery", "paper", "pen", "furniture", "office supplies"),
    "Logistics": ("shipping", "freight", "delivery", "courier", "transport", "logistics"),
    "Packaging": ("box", "carton", "packaging", "wrap", "crate"),
    "Facilities": ("cleaning", "maintenance", "facility", "hvac", "security"),
    "Software Services": ("software", "license", "saas", "subscription", "app"),
}

def _hash_int(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)


def _item_code_for(item_description: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", item_description.lower()).strip("-")[:24] or "item"
    return f"DEMO-ITEM-{slug.upper()}"


def build_demo_seed_from_vendor_pool(
    *,
    item_description: str,
    quantity: Optional[int],
    estimated_budget: Optional[float],
    demo_vendors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a seed for ``SeededERPNextClient`` from an actual, user-seeded
    pool of demo vendor-master records (``vendor_master.search_vendors
    (is_demo=True)``) instead of the fixed two-supplier scenario.

    Falls back to :func:`build_demo_seed` ("eligible") when fewer than 2
    demo vendors exist yet -- e.g. before the operator has run the
    "seed 30 demo vendors" action -- so Demo Mode always has *something*
    real to run the pipeline against.

    Compliance and pricing are derived deterministically from each
    vendor's own ``vendor_code`` (a stable hash, not randomness), so the
    same query against the same seeded pool always produces the same
    comparison -- but a different pool, or a different item, genuinely
    changes which suppliers are even considered and what they quote.
    """
    if len(demo_vendors) < 2:
        return build_demo_seed("eligible")

    text = item_description.lower()
    matched_category = next(
        (category for category, keywords in _CATEGORY_KEYWORDS.items() if any(k in text for k in keywords)),
        None,
    )
    pool = [v for v in demo_vendors if matched_category is None or v.get("business_category") == matched_category]
    if len(pool) < 2:
        pool = demo_vendors  # the category match was too narrow to compare anything -- search the whole pool instead
    pool = pool[:8]  # keep the comparison genuinely readable -- still a real multi-vendor search, not a scripted pair

    item_code = _item_code_for(item_description)
    qty = quantity or 1
    unit_target = (estimated_budget / qty) if estimated_budget and qty else 1_000.0

    suppliers: List[Dict[str, Any]] = []
    supplier_documents: Dict[str, Any] = {}
    quotations: List[Dict[str, Any]] = []
    for vendor in pool:
        code = vendor["vendor_code"]
        h = _hash_int(code)
        country = vendor.get("country") or "India"
        suppliers.append(
            {
                "name": code,
                "supplier_name": vendor.get("legal_name", code),
                "country": country,
                "disabled": 0,
                "on_hold": 0,
                "source_reference": f"Supplier/{code} (SIMULATED, vendor master)",
                "_seeded": True,
                "_demo": True,
            }
        )
        if h % 3 != 0:  # ~2 of every 3 vendors carry compliant evidence -- a real, data-driven mix to filter over
            # The real, unmodified compliance check requires different
            # evidence per country (PAN/GST for India, W-9/Business
            # License everywhere else) -- match whichever this vendor's
            # own country actually requires, or "compliant" would be a lie.
            if country == "India":
                supplier_documents[code] = [{"title": _SIMULATED_PAN}, {"title": _SIMULATED_GST}]
            else:
                supplier_documents[code] = [{"title": _SIMULATED_W9}, {"title": _SIMULATED_BUSINESS_LICENSE}]

        rate = round(unit_target * (0.80 + (h % 41) / 100.0), 2)  # 0.80x .. 1.20x of the target unit price
        quotations.append(
            {
                "name": f"DEMO-SQ-{code}-0001",
                "supplier": code,
                "item_code": item_code,
                "rate": rate,
                "qty": qty,
                "currency": "INR" if country == "India" else "USD",
                "delivery_days": 3 + (h % 20),
                "payment_terms_days": [15, 30, 45, 60][h % 4],
                "source_reference": f"Supplier Quotation/DEMO-SQ-{code}-0001 (SIMULATED)",
                "_seeded": True,
            }
        )

    return {
        "suppliers": suppliers,
        "supplier_documents": supplier_documents,
        "items": [
            {
                "name": item_code,
                "item_name": item_description[:140],
                "item_group": matched_category or "General",
                "source_reference": f"Item/{item_code} (SIMULATED)",
                "_seeded": True,
            }
        ],
        "quotations": quotations,
        "budget": {"default": estimated_budget or DEMO_BUDGET},
        "invoices": [],
        "rfqs": [],
        "purchase_orders": [],
    }
