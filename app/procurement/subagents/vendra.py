"""Vendra's six specialist subagents.

Vendor Discovery and Document Verification are the existing, unchanged
deterministic logic that predates this pass (moved here verbatim from
app.procurement.systems.vendra, which still re-exports both names for
backward compatibility and its own existing tests -- not renamed, not
duplicated, not reimplemented). The other four are genuinely new, each
scoped to only what real ERPNext data this project already has honest
access to:

* Vendor Onboarding Readiness -- not a fabricated onboarding workflow (no
  such process exists); a bounded readiness triage over the real supplier
  record and real compliance result.
* Vendor Profile -- a consolidated view of real, already-fetchable master
  data + compliance + quotation count. No new data source.
* Vendor Performance Snapshot -- price competitiveness for the CURRENT
  item only, computed from real submitted quotations. Explicitly NOT an
  overall/historical performance score: no delivery or quality history
  exists to support one honestly, so this doesn't claim to be one.
* Vendor Risk Flagging -- one risk category per supplier ERPNext actually
  has on file (including ones Discovery already excluded), synthesizing
  on_hold/disabled/compliance signals that already exist.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from ..expiry import parse_expiry, today_utc
from ..mcp.client import ERPNextClient

# W-9 Tax Form is a US IRS document -- it has no legitimate bearing on a
# supplier incorporated outside the US (there is no such thing as a
# non-US entity "filing a W-9"). This was the project's original default,
# written before any supplier had a country on record; kept here as the
# fallback for a supplier whose country can't be determined, but a known
# country now gets the compliance documents actually appropriate to it
# (see REQUIRED_SUPPLIER_DOCUMENTS_BY_COUNTRY) -- never silently skipped,
# just asked for the legitimate local equivalent instead of a foreign form.
REQUIRED_SUPPLIER_DOCUMENTS = ("W-9 Tax Form", "Business License")

# Conservative, explicit filename patterns -- matched against whichever
# identifying text a document record exposes (a real Frappe File's
# file_name, live; the seeded fixture's title, in tests). Deliberately
# narrow (word-boundary on "w9", the literal phrase "business license") so
# an unrelated attachment is never mistaken for compliance evidence; no
# supplier is ever marked compliant just because *some* file exists.
_DOCUMENT_NAME_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    # Not \bw-?9\b: a trailing \b fails after "9" in e.g. "W9_Supplier.pdf"
    # since "_" is a \w character (no boundary forms there). A following-
    # digit lookahead gives the same "not w90/w95/..." exclusion without
    # that false negative on the realistic underscore-delimited filename.
    "W-9 Tax Form": re.compile(r"\bw-?9(?!\d)", re.IGNORECASE),
    "Business License": re.compile(r"business[\s_-]licen[sc]e", re.IGNORECASE),
}

# India: PAN (Permanent Account Number) is the real, standard Indian tax-
# identification document -- the legitimate local equivalent of a W-9's
# *purpose* (vendor tax-ID collection for payment/TDS reporting), not a
# literal translation of the US form. GST Registration is the standard
# real-world Indian business-compliance credential checked for a GST-
# registered vendor, in place of the US-centric "Business License" label
# (India has no single document actually called that).
REQUIRED_SUPPLIER_DOCUMENTS_BY_COUNTRY: Dict[str, tuple] = {
    "US": REQUIRED_SUPPLIER_DOCUMENTS,
    "India": ("PAN Card", "GST Registration Certificate"),
}

_DOCUMENT_NAME_PATTERNS_BY_COUNTRY: Dict[str, Dict[str, "re.Pattern[str]"]] = {
    "India": {
        # Matches "PAN Card"/"pan_card"/"PANCARD" explicitly, or an actual
        # PAN-shaped token (5 letters, 4 digits, 1 letter) embedded in the
        # filename -- real evidence, not a bare substring match on "pan"
        # (which would false-positive on e.g. "japan_office.pdf"). Uses
        # (?![a-z])/(?<![a-z0-9]) rather than a trailing \b: a realistic
        # underscore-delimited filename (e.g. "PAN_Card_ABCDE1234F.pdf")
        # has a word character on both sides of each token, so \b never
        # forms there -- the same underscore pitfall the W-9 pattern above
        # already has to work around.
        "PAN Card": re.compile(
            r"\bpan[\s_-]?card(?![a-z])|(?<![a-z0-9])[a-z]{5}\d{4}[a-z](?![a-z0-9])", re.IGNORECASE
        ),
        "GST Registration Certificate": re.compile(
            r"\bgstin(?![a-z])|\bgst[\s_-]?(?:reg(?:istration)?|cert(?:ificate)?)(?![a-z])", re.IGNORECASE
        ),
    },
}


def _document_label(document: Dict[str, Any]) -> str:
    """The identifying text for one attachment record: a real Frappe
    File's ``file_name`` (live), or the seeded fixture's ``title``
    (tests). Never fabricated -- an empty string if neither is present,
    which matches no pattern."""
    return str(document.get("file_name") or document.get("title") or "")


def discover_suppliers(client: ERPNextClient, *, item_code: Optional[str] = None) -> List[Dict[str, Any]]:
    """Vendor Discovery: suppliers that are active and not on hold.

    ``item_code`` is accepted for future supplier/item matching but not
    yet used to filter (ERPNextClient has no supplier<->item mapping
    query in this phase); every active, non-held supplier is returned.
    """
    suppliers = client.list_suppliers()
    return [s for s in suppliers if not s.get("disabled") and not s.get("on_hold")]


def check_compliance(
    client: ERPNextClient,
    supplier_name: str,
    *,
    country: Optional[str] = None,
    today: Optional[date] = None,
    recorded_expiries: Optional[Dict[str, date]] = None,
) -> Dict[str, Any]:
    """Document Verification / Compliance Check: which required documents
    are actually on file, and what's missing, for the documents actually
    appropriate to this supplier's own country -- not a one-size-fits-all
    US document set. Matches real filenames against explicit, conservative
    patterns -- an attachment only counts if its name actually looks like
    the required document, never just because *a* file exists. A supplier
    with zero attachments is reported as missing every required document;
    never invents or assumes compliance that wasn't actually evidenced.

    ``country`` is a caller-supplied optimization (pass it when you
    already fetched the supplier record, e.g. from Vendor Discovery's
    candidate list) -- if omitted, this looks the supplier up itself, and
    falls back to the US-centric default if that lookup isn't possible
    (an unknown supplier, or a client that doesn't implement get_supplier)
    rather than failing the check."""
    if country is None:
        try:
            supplier = client.get_supplier(supplier_name)
            country = supplier.get("country") if supplier else None
        except Exception:
            country = None

    required = REQUIRED_SUPPLIER_DOCUMENTS_BY_COUNTRY.get(country, REQUIRED_SUPPLIER_DOCUMENTS)
    patterns = _DOCUMENT_NAME_PATTERNS_BY_COUNTRY.get(country, _DOCUMENT_NAME_PATTERNS)

    today = today or today_utc()
    recorded_expiries = recorded_expiries or {}
    documents = client.list_supplier_documents(supplier_name)
    labels_on_file = [_document_label(d) for d in documents]
    labels_on_file = [label for label in labels_on_file if label]
    refs = sorted({f"File/{d['name']}" for d in documents if d.get("name") and _document_label(d)})

    # A file counts only if it looks like the required document AND is not
    # past its validity. A validity recorded by a person applies to the
    # document type; otherwise the date in the file name is used; with neither,
    # nothing is assumed expired.
    missing: List[str] = []
    expired: List[Dict[str, str]] = []
    valid_expiries: List[date] = []
    for required_doc in required:
        matching = [label for label in labels_on_file if patterns[required_doc].search(label)]
        if not matching:
            missing.append(required_doc)
            continue
        recorded = recorded_expiries.get(required_doc)
        usable_expiries: List[Optional[date]] = []
        newest_lapsed: Optional[tuple] = None
        for label in matching:
            expiry = recorded or parse_expiry(label)
            if expiry is not None and expiry < today:
                if newest_lapsed is None or expiry > newest_lapsed[1]:
                    newest_lapsed = (label, expiry)
            else:
                usable_expiries.append(expiry)
        if usable_expiries:
            valid_expiries += [e for e in usable_expiries if e is not None]
        else:
            expired.append({"document": required_doc, "file": newest_lapsed[0], "expired_on": newest_lapsed[1].isoformat()})

    return {
        "supplier": supplier_name,
        "documents_on_file": sorted(set(labels_on_file)),
        "document_refs": refs,
        "required_documents": list(required),
        "missing_documents": missing,
        "expired_documents": expired,
        "earliest_expiry": min(valid_expiries).isoformat() if valid_expiries and not (missing or expired) else None,
        "compliant": not missing and not expired,
    }


def vendor_discovery(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Which suppliers are even candidates: active, not on hold/disabled."""
    client = context["client"]
    candidates = discover_suppliers(client, item_code=context.get("item_code"))
    return {"candidates": candidates, "candidates_checked": len(candidates)}


def document_verification(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Which candidates actually have the required compliance documents on
    file -- the existing, unchanged deterministic check, run once per
    candidate found by Vendor Discovery. Each candidate's own country
    (already fetched by Vendor Discovery) selects the documents actually
    required of it -- a US-specific W-9 is never required of a non-US
    supplier."""
    client = context["client"]
    candidates = results["vendor_discovery"]["data"]["candidates"]
    recorded = context.get("recorded_expiries") or {}
    compliance_by_supplier = {
        s["name"]: check_compliance(
            client, s["name"], country=s.get("country"), today=context.get("today"),
            recorded_expiries=recorded.get(s["name"]),
        )
        for s in candidates
    }
    eligible_names = [name for name, c in compliance_by_supplier.items() if c["compliant"]]
    return {"compliance_by_supplier": compliance_by_supplier, "eligible_names": eligible_names}


def vendor_onboarding_readiness(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Readiness triage, not an onboarding workflow: does ERPNext have a
    real, retrievable Supplier record, and if not yet eligible, is that
    fixable by document upload (compliance gap) or a structural block
    (record missing)."""
    client = context["client"]
    candidates = results["vendor_discovery"]["data"]["candidates"]
    compliance_by_supplier = results["document_verification"]["data"]["compliance_by_supplier"]

    readiness: Dict[str, str] = {}
    for supplier in candidates:
        name = supplier["name"]
        compliance = compliance_by_supplier.get(name, {})
        if client.get_supplier(name) is None:
            readiness[name] = "blocked: no retrievable Supplier record"
        elif compliance.get("compliant"):
            readiness[name] = "ready"
        else:
            missing = compliance.get("missing_documents", [])
            expired = [e["document"] for e in compliance.get("expired_documents", [])]
            if missing or expired:
                parts = ([f"missing {', '.join(missing)}"] if missing else []) + ([f"expired {', '.join(expired)}"] if expired else [])
                readiness[name] = "blocked: " + "; ".join(parts)
            else:
                readiness[name] = "blocked: compliance unknown"
    return {"readiness_by_supplier": readiness}


def vendor_profile(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Consolidated profile per candidate: master data + compliance + how
    many quotations they've actually submitted for this item -- real data
    already fetchable elsewhere, assembled into one view."""
    client = context["client"]
    candidates = results["vendor_discovery"]["data"]["candidates"]
    compliance_by_supplier = results["document_verification"]["data"]["compliance_by_supplier"]
    item_code = context.get("item_code")

    quotes_by_supplier: Dict[str, int] = {}
    if item_code:
        for q in client.list_supplier_quotations(item_code=item_code):
            quotes_by_supplier[q["supplier"]] = quotes_by_supplier.get(q["supplier"], 0) + 1

    profiles = {
        supplier["name"]: {
            "supplier_name": supplier.get("supplier_name", supplier["name"]),
            "country": supplier.get("country"),
            "compliant": compliance_by_supplier.get(supplier["name"], {}).get("compliant", False),
            "quotations_submitted_for_item": quotes_by_supplier.get(supplier["name"], 0),
        }
        for supplier in candidates
    }
    return {"profiles": profiles}


def vendor_performance_snapshot(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Price competitiveness for the current item only -- explicitly not
    an overall/historical performance score. No delivery or quality
    history data source exists to support one honestly, so this doesn't
    claim to be one."""
    item_code = context.get("item_code")
    if not item_code:
        return {"_status": "skipped", "reason": "no item_code in this task's context"}

    client = context["client"]
    eligible_names = results["document_verification"]["data"]["eligible_names"]
    quotes = [q for q in client.list_supplier_quotations(item_code=item_code) if q["supplier"] in eligible_names]
    if not quotes:
        return {"_status": "skipped", "reason": "no eligible supplier has a quotation for this item"}

    ordered = sorted(quotes, key=lambda q: q["rate"])
    ranking = [{"supplier": q["supplier"], "rate": q["rate"], "price_rank": i + 1} for i, q in enumerate(ordered)]
    return {"item_code": item_code, "price_ranking": ranking}


def vendor_risk_flagging(context: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """One risk category per supplier ERPNext actually has on file --
    including ones Vendor Discovery already excluded -- synthesizing
    on_hold/disabled/compliance signals that already exist, never a new
    data source."""
    client = context["client"]
    all_suppliers = client.list_suppliers()
    compliance_by_supplier = results["document_verification"]["data"]["compliance_by_supplier"]

    flags: Dict[str, str] = {}
    for supplier in all_suppliers:
        name = supplier["name"]
        if supplier.get("disabled"):
            flags[name] = "disabled"
        elif supplier.get("on_hold"):
            flags[name] = "on_hold"
        elif name not in compliance_by_supplier:
            flags[name] = "not_yet_assessed"
        elif compliance_by_supplier[name].get("compliant"):
            flags[name] = "eligible"
        elif compliance_by_supplier[name].get("expired_documents"):
            flags[name] = "documents_expired"
        else:
            flags[name] = "non_compliant"
    return {"risk_by_supplier": flags}


SPECIALISTS = {
    "vendor_discovery": vendor_discovery,
    "document_verification": document_verification,
    "vendor_onboarding_readiness": vendor_onboarding_readiness,
    "vendor_profile": vendor_profile,
    "vendor_performance_snapshot": vendor_performance_snapshot,
    "vendor_risk_flagging": vendor_risk_flagging,
}
