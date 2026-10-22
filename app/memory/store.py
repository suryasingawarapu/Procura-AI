"""Storage operations for agent memories.

Writes go through the ``save_memory_atomic`` SQL function: creating a memory
and superseding its previous version happen in one transaction together with
the provenance edge, so a partially written version chain can no longer
occur.

Reads use PostgREST directly and are strictly read-only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .client import supabase
from .formatting import format_embedding, format_timestamp
from .rpc import call_rpc_one

__all__ = [
    "supabase",
    "save_memory",
    "get_agent_memories",
    "get_memory",
    "get_memory_by_key",
    "count_agent_memories",
]


def save_memory(
    agent_id: str,
    memory_key: str,
    content: str,
    source_agent_id: str | None = None,
    source_reference: str | None = None,
    *,
    embedding: Any = None,
    observed_at: Any = None,
    subject_key: str | None = None,
    valid_until: Any = None,
    client: Any = None,
) -> Dict[str, Any]:
    """Save a memory into one agent's memory space.

    The write is atomic:

    * no active memory for ``(agent_id, memory_key)`` -> version 1 is created
    * an active memory with identical content          -> it is returned as is
    * an active memory with different content           -> it is superseded and
      the next version is created, both in one transaction

    Returns the stored row as a dictionary.
    """
    row = call_rpc_one(
        "save_memory_atomic",
        {
            "p_agent_id": agent_id,
            "p_memory_key": memory_key,
            "p_content": content,
            "p_source_agent_id": source_agent_id,
            "p_source_reference": source_reference,
            "p_embedding": format_embedding(embedding),
            "p_observed_at": format_timestamp(observed_at),
            "p_subject_key": subject_key,
            "p_valid_until": format_timestamp(valid_until),
        },
        client=client,
    )
    return row


def get_agent_memories(
    agent_id: str,
    limit: int | None = None,
    offset: int = 0,
    *,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Retrieve the active memories of one agent, newest first.

    ``limit`` is optional; without it every active memory is returned. Pass a
    limit for anything that runs in a request path.
    """
    active_client = client if client is not None else supabase

    query = (
        active_client.table("agent_memories")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("status", "active")
        .order("created_at", desc=True)
    )

    if limit is not None:
        query = query.limit(limit)
    if offset:
        query = query.offset(offset)

    return query.execute().data or []


def get_memory(memory_id: str, *, client: Any = None) -> Dict[str, Any] | None:
    """Return one memory by id, or ``None`` when it does not exist."""
    active_client = client if client is not None else supabase

    rows = (
        active_client.table("agent_memories")
        .select("*")
        .eq("id", memory_id)
        .limit(1)
        .execute()
    ).data or []

    return rows[0] if rows else None


def get_memory_by_key(
    agent_id: str,
    memory_key: str,
    *,
    include_superseded: bool = False,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Return every version stored under ``(agent_id, memory_key)``."""
    active_client = client if client is not None else supabase

    query = (
        active_client.table("agent_memories")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("memory_key", memory_key)
        .order("version")
    )

    if not include_superseded:
        query = query.eq("status", "active")

    return query.execute().data or []


def count_agent_memories(
    agent_id: str,
    *,
    include_superseded: bool = False,
    client: Any = None,
) -> int:
    """Return how many memories an agent has."""
    active_client = client if client is not None else supabase

    query = (
        active_client.table("agent_memories")
        .select("id", count="exact")
        .eq("agent_id", agent_id)
    )

    if not include_superseded:
        query = query.eq("status", "active")

    response = query.execute()
    count = getattr(response, "count", None)
    if count is not None:
        return int(count)
    return len(response.data or [])
