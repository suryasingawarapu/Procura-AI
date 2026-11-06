"""Recorded verification of a supplier document, with the date it stays valid.

Example: "Silverline's GST Registration Certificate was checked by Priya and is
valid until 2027-03-31". It is stored as a Vendra memory whose ``valid_until``
is that date, so the record itself goes stale when the validity ends, and the
compliance check treats a document with a lapsed recorded validity as expired
(it does not fall back to "no expiry known").
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Dict, Optional

from app.memory.client import supabase
from app.memory.embeddings import embed_or_none
from app.memory.provenance_fields import enrich_memory
from app.memory.store import save_memory

from .expiry import end_of_day_utc, today_utc
from .identities import VENDRA

__all__ = ["record_validity", "load_recorded_validity"]

KEY_PREFIX = "document_validity:"


def _key(supplier: str, document: str) -> str:
    return f"{KEY_PREFIX}{supplier}:{document}"


def record_validity(
    *,
    supplier: str,
    document: str,
    valid_until: date,
    verified_by: str,
    source_reference: Optional[str] = None,
    note: Optional[str] = None,
    source_type: str = "manual",
    client: Any = None,
) -> Dict[str, Any]:
    """Record that ``document`` of ``supplier`` was verified valid until
    ``valid_until``. A renewed document with a new date becomes a new version;
    the old validity stays in history."""
    active_client = client if client is not None else supabase
    content = f"{document} of {supplier} verified valid until {valid_until.isoformat()} by {verified_by}"
    fact = {
        "supplier": supplier, "document": document, "expires_on": valid_until.isoformat(),
        "verified_by": verified_by, "note": note,
    }
    saved = save_memory(
        VENDRA, _key(supplier, document), content,
        source_agent_id=VENDRA, source_reference=source_reference or f"Supplier/{supplier}",
        subject_key=f"validity:{supplier}:{document}", valid_until=end_of_day_utc(valid_until),
        embedding=embed_or_none(content), client=active_client,
    )
    enrich_memory(
        saved["id"], client=active_client, source_type=source_type, change_reason=f"validity recorded by {verified_by}",
        verification_status="verified", sharing_classification="internal", structured_fact=fact,
        source_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    return saved


def load_recorded_validity(*, client: Any = None) -> Dict[str, Dict[str, date]]:
    """``{supplier: {document: expiry date}}`` from every recorded validity.

    Reads the rows directly rather than through retrieval on purpose:
    retrieval drops a memory whose ``valid_until`` has passed, but an expired
    recorded validity must be seen as *expired*, not silently disappear."""
    active_client = client if client is not None else supabase
    rows = (
        active_client.table("agent_memories").select("structured_fact")
        .eq("agent_id", VENDRA).eq("status", "active").like("memory_key", KEY_PREFIX + "%")
        .execute().data
        or []
    )
    out: Dict[str, Dict[str, date]] = {}
    for row in rows:
        fact = row.get("structured_fact") or {}
        try:
            out.setdefault(fact["supplier"], {})[fact["document"]] = date.fromisoformat(fact["expires_on"])
        except (KeyError, ValueError, TypeError):
            continue
    return out


def is_lapsed(expires_on: date, today: Optional[date] = None) -> bool:
    return expires_on < (today or today_utc())
