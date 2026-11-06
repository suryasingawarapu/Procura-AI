"""Quotations as claims, and reconciling them when sources disagree.

Every quotation Procura sees -- an ERPNext Supplier Quotation, or a price
that arrived by email or in a vendor's PDF -- is stored as its own memory (a
*claim*) with the exact source document, a content hash, the document's date
and who recorded it. Claims about the same supplier and item share a
``subject_key`` (``quote:<item>:<supplier>``); versions of one claim keep the
history of a price or delivery time changing.

Before quotations are compared, claims for the same subject are checked for
conflicts (see :mod:`app.memory.conflicts`): the more authoritative / newer
source is used automatically, and when that cannot be decided the supplier's
price is held out and the user is asked. Used in live mode only.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.memory.client import supabase
from app.memory.conflicts import ConflictReport, claim_value, detect_conflicts, scan_conflicts
from app.memory.embeddings import embed_or_none
from app.memory.provenance_fields import enrich_memory
from app.memory.store import save_memory

from . import progress
from .identities import PROCURA

__all__ = [
    "SOURCE_TYPES",
    "subject_for",
    "record_claim",
    "reconcile",
    "Reconciliation",
    "list_claims",
    "conflict_view",
]

SOURCE_TYPES = ("erpnext_doctype", "pdf", "document", "email", "chat", "manual", "phone")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def subject_for(item_code: str, supplier: str) -> str:
    return f"quote:{item_code}:{supplier}"


def _claim_key(item_code: str, supplier: str, source_type: str, source_reference: str) -> str:
    return f"{subject_for(item_code, supplier)}:{source_type}:{_SLUG.sub('_', source_reference)[:80]}"


def _with_retry(fn: Callable[[Any], Any], attempts: int = 2, delay: float = 0.4) -> Callable[[Any], Any]:
    """Retry a memory write once: a dropped connection should not sink the whole batch."""

    def wrapped(item: Any) -> Any:
        for attempt in range(attempts):
            try:
                return fn(item)
            except Exception:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)

    return wrapped


def _parallel(fn: Callable[[Any], Any], items: Iterable[Any], workers: int = 8) -> List[Any]:
    fn = _with_retry(fn)
    items = list(items)
    if len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def record_claim(
    *,
    item_code: str,
    supplier: str,
    unit_price: float,
    source_type: str,
    source_reference: str,
    currency: str = "INR",
    document_date: Optional[date] = None,
    delivery_days: Optional[int] = None,
    payment_terms_days: Optional[int] = None,
    recorded_by: str = PROCURA,
    task_id: Optional[str] = None,
    content_sha256: Optional[str] = None,
    note: Optional[str] = None,
    client: Any = None,
) -> Dict[str, Any]:
    """Store one quotation claim. A changed price/terms for the same source
    document becomes a new version (the old one stays in history); an
    identical claim changes nothing."""
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unknown source_type {source_type!r}; expected one of {SOURCE_TYPES}")
    active_client = client if client is not None else supabase
    fact = {
        "value": round(float(unit_price), 2), "currency": currency, "supplier": supplier, "item_code": item_code,
        "document_date": document_date.isoformat() if document_date else None, "delivery_days": delivery_days,
        "payment_terms_days": payment_terms_days, "source_type": source_type, "source_reference": source_reference,
        "recorded_by": recorded_by, "note": note,
    }
    # Everything that can change belongs in the text: a memory only becomes a new version when its text changes.
    content = f"{supplier} quoted {currency} {fact['value']:.2f} per unit for {item_code} ({source_reference})"
    if delivery_days is not None:
        content += f"; delivery in {delivery_days} days"
    if payment_terms_days is not None:
        content += f"; payment terms net {payment_terms_days} days"
    saved = save_memory(
        PROCURA, _claim_key(item_code, supplier, source_type, source_reference), content,
        source_agent_id=PROCURA, source_reference=source_reference, subject_key=subject_for(item_code, supplier),
        observed_at=document_date.isoformat() if document_date else None,
        embedding=embed_or_none(content), client=active_client,
    )
    if not saved.get("source_type"):  # new row or new version: tag it (an unchanged claim is already tagged)
        enrich_memory(
            saved["id"], client=active_client, source_type=source_type,
            source_hash=content_sha256 or hashlib.sha256(json.dumps(fact, sort_keys=True).encode()).hexdigest(),
            task_id=task_id, change_reason=f"quotation claim recorded by {recorded_by}",
            verification_status="verified" if source_type == "erpnext_doctype" else "unverified",
            sharing_classification="shareable", structured_fact=fact,
        )
        saved = {**saved, "source_type": source_type, "structured_fact": fact}
    return saved


def list_claims(item_code: str, supplier: Optional[str] = None, *, client: Any = None) -> List[Dict[str, Any]]:
    active_client = client if client is not None else supabase
    subject = subject_for(item_code, supplier) if supplier else f"quote:{item_code}:"
    query = active_client.table("agent_memories").select("*").eq("agent_id", PROCURA).eq("status", "active")
    query = query.eq("subject_key", subject) if supplier else query.like("subject_key", subject + "%")
    return query.order("observed_at").execute().data or []


# -- reconciliation ---------------------------------------------------------------------------------


@dataclass
class Reconciliation:
    rows: List[Dict[str, Any]] = field(default_factory=list)  # one quote row per supplier, ready to compare
    conflicts: List[Dict[str, Any]] = field(default_factory=list)  # unresolved: a person must decide
    resolved: List[Dict[str, Any]] = field(default_factory=list)  # decided (automatically or by a person)
    external_only: List[str] = field(default_factory=list)  # priced only by a non-ERPNext source
    claims_seen: int = 0
    archived: List[str] = field(default_factory=list)  # claims whose ERPNext document no longer exists
    note: Optional[str] = None


def _claim_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    fact = row.get("structured_fact") or {}
    return {
        "memory_id": row["id"], "source_type": row.get("source_type"), "source_reference": row.get("source_reference"),
        "value": fact.get("value"), "currency": fact.get("currency"), "document_date": fact.get("document_date"),
        "delivery_days": fact.get("delivery_days"), "verification_status": row.get("verification_status"),
    }


def conflict_view(report: ConflictReport, *, item_code: str) -> Dict[str, Any]:
    fact = (report.claims[0].get("structured_fact") or {}) if report.claims else {}
    return {
        "subject_key": report.subject_key, "supplier": fact.get("supplier"), "item_code": item_code,
        "status": report.status, "reason": report.reason, "manual": report.manual,
        "winner_memory_id": report.winner["id"] if report.winner else None,
        "claims": [_claim_summary(r) for r in report.claims],
    }


def _row_from_claim(claim: Dict[str, Any], erpnext_rows: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    fact = claim.get("structured_fact") or {}
    original = erpnext_rows.get(claim.get("source_reference") or "")
    if original is not None:
        return original
    return {
        "name": f"claim:{claim['id']}", "supplier": fact.get("supplier"), "item_code": fact.get("item_code"),
        "rate": fact.get("value"), "currency": fact.get("currency") or "INR", "delivery_days": fact.get("delivery_days"),
        "payment_terms_days": fact.get("payment_terms_days"), "source_reference": claim.get("source_reference"),
    }


def reconcile(
    *, item_code: str, eligible: List[str], erpnext_rows: List[Dict[str, Any]], task_id: Optional[str] = None,
    client: Any = None,
) -> Reconciliation:
    """Record the ERPNext quotations as claims, check every claim for the item
    for conflicts, and return the rows to compare. Never raises: if memory is
    unavailable the ERPNext rows are returned unchanged with a note."""
    active_client = client if client is not None else supabase
    result = Reconciliation()
    try:
        by_ref = {r.get("source_reference") or f"Supplier Quotation/{r.get('name')}": r for r in erpnext_rows}

        def record(row: Dict[str, Any]):
            doc_date = None
            try:
                doc_date = date.fromisoformat(str(row.get("document_date"))[:10]) if row.get("document_date") else None
            except ValueError:
                pass
            return record_claim(
                item_code=item_code, supplier=row["supplier"], unit_price=row["rate"], currency=row.get("currency") or "INR",
                source_type="erpnext_doctype", source_reference=row.get("source_reference") or f"Supplier Quotation/{row['name']}",
                document_date=doc_date, delivery_days=row.get("delivery_days"),
                payment_terms_days=row.get("payment_terms_days"), recorded_by=PROCURA, task_id=task_id, client=active_client,
            )

        _parallel(record, [r for r in erpnext_rows if r.get("supplier") in eligible and r.get("rate") is not None])

        # A claim from an ERPNext document that no longer exists (or no longer prices this item) is
        # out of date: archive it so it can neither be priced nor create a phantom conflict.
        for claim in list_claims(item_code, client=active_client):
            if claim.get("source_type") == "erpnext_doctype" and claim.get("source_reference") not in by_ref:
                active_client.table("agent_memories").update({"status": "archived"}).eq("id", claim["id"]).execute()
                result.archived.append(claim.get("source_reference"))

        reports = {r.subject_key: r for r in scan_conflicts(PROCURA, f"quote:{item_code}:", task_id=task_id, client=active_client)}
        by_supplier: Dict[str, List[Dict[str, Any]]] = {}
        for claim in list_claims(item_code, client=active_client):
            supplier = (claim.get("structured_fact") or {}).get("supplier")
            if supplier in eligible and claim_value(claim) is not None:
                by_supplier.setdefault(supplier, []).append(claim)
                result.claims_seen += 1

        for supplier in eligible:
            claims = by_supplier.get(supplier)
            if not claims:
                continue
            report = reports.get(subject_for(item_code, supplier))
            if report is not None and report.status == "needs_clarification":
                result.conflicts.append(conflict_view(report, item_code=item_code))
                continue
            if report is not None and report.status == "resolved":
                result.resolved.append(conflict_view(report, item_code=item_code))
                chosen = report.winner
            else:  # no conflict: if several agree, prefer the ERPNext document
                chosen = sorted(claims, key=lambda c: (c.get("source_type") != "erpnext_doctype",))[0]
            row = _row_from_claim(chosen, by_ref)
            if chosen.get("source_type") != "erpnext_doctype" and supplier not in {r.get("supplier") for r in erpnext_rows}:
                result.external_only.append(supplier)
            result.rows.append(row)
    except Exception as exc:  # memory is a safeguard, not a dependency of the quotation data itself
        note = f"claim reconciliation unavailable ({type(exc).__name__}: {str(exc)[:160]}); used the ERPNext quotations as they are"
        progress.emit("procura", note)
        return Reconciliation(rows=[r for r in erpnext_rows if r.get("supplier") in eligible], note=note)
    return result
