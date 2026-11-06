"""Enrichment for the procurement provenance fields added in migration 006.

These columns (source_type, verification_status, confidence, task_id,
message_id, sharing_classification, structured_fact, change_reason,
source_hash on agent_memories; task_id/message_id/purpose/accepted on
memory_provenance) are deliberately NOT written by the atomic SQL functions
in 002 -- see migrations/006_procurement_provenance_fields.sql for why. They
are set here, in a second call right after the atomic write succeeds.

This means they are enrichment, not part of the core write's atomicity
guarantee: a crash between the atomic call and this one leaves the memory
itself (and its version chain / provenance edge) fully correct, but these
fields unset on that one row. That is an accepted, explicitly documented
tradeoff -- these are audit/classification fields, not fields the
version-chain or sharing-policy correctness depends on.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .client import supabase

__all__ = ["enrich_memory", "enrich_share_edge"]

_MEMORY_FIELDS = (
    "source_type",
    "source_hash",
    "change_reason",
    "verification_status",
    "confidence",
    "task_id",
    "message_id",
    "sharing_classification",
    "structured_fact",
)

_EDGE_FIELDS = ("task_id", "message_id", "purpose", "accepted")


def _only_given(fields: tuple, values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in values.items() if k in fields and v is not None}


def enrich_memory(memory_id: str, *, client: Any = None, **fields: Any) -> Optional[Dict[str, Any]]:
    """Set any of the migration-006 columns on one ``agent_memories`` row.

    Unknown keyword arguments are ignored rather than raising, so a caller
    can pass through a loosely-typed dict of "whatever we know" without
    checking it against this module's exact field list first. Returns the
    updated row, or ``None`` if nothing was given to set.
    """
    updates = _only_given(_MEMORY_FIELDS, fields)
    if not updates:
        return None
    active_client = client if client is not None else supabase
    rows = active_client.table("agent_memories").update(updates).eq("id", memory_id).execute().data or []
    return rows[0] if rows else None


def enrich_share_edge(
    memory_id: str,
    parent_memory_id: str,
    *,
    client: Any = None,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    """Set share-event metadata on the ``derived_from`` provenance edge that
    ``share_memory_atomic`` already created for ``(memory_id,
    parent_memory_id)``. Looks the edge up rather than requiring its id,
    since the atomic function's return value is the copied memory row, not
    the edge itself."""
    updates = _only_given(_EDGE_FIELDS, fields)
    if not updates:
        return None
    active_client = client if client is not None else supabase
    rows = (
        active_client.table("memory_provenance")
        .update(updates)
        .eq("memory_id", memory_id)
        .eq("parent_memory_id", parent_memory_id)
        .eq("relationship", "derived_from")
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None
