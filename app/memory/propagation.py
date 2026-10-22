"""Controlled sharing of memories between agents.

Sharing copies a memory into the receiving agent's own space. The copy is a
new row, so the receiver can supersede or annotate it independently, and the
lineage is recorded through a ``derived_from`` provenance edge.

The policy that decides *who may share with whom* lives in the database
(``memory_share_policy``), not in Python. A pair that is not listed is
denied.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .rpc import call_rpc, call_rpc_one

__all__ = [
    "share_memory",
    "get_ancestry",
    "get_descendants",
]


def share_memory(
    source_memory_id: str,
    target_agent_id: str,
    *,
    memory_key: str | None = None,
    max_hops: int | None = None,
    client: Any = None,
) -> Dict[str, Any]:
    """Share an active memory with another agent.

    The copy and its ``derived_from`` edge are written in one transaction, so
    a copy can never exist without provenance.

    Raises:

    * :class:`app.memory.errors.MemoryNotFound` -- the source does not exist
      or is not active.
    * :class:`app.memory.errors.ShareNotAllowed` -- the sharing policy does
      not allow this hand-over.
    * :class:`app.memory.errors.HopLimitExceeded` -- the memory has already
      travelled too far.
    * :class:`app.memory.errors.SelfShareNotAllowed` -- source and target are
      the same agent.
    """
    row = call_rpc_one(
        "share_memory_atomic",
        {
            "p_source_memory_id": source_memory_id,
            "p_target_agent_id": target_agent_id,
            "p_memory_key": memory_key,
            "p_max_hops": max_hops,
        },
        client=client,
    )
    return row


def get_ancestry(
    memory_id: str,
    max_depth: int = 10,
    *,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Return the ancestry of a memory, closest ancestor first.

    Depth 0 is the memory itself, depth 1 its direct parent, and so on. Each
    row carries the ``relationship`` that connects it to the previous level.
    """
    return call_rpc(
        "get_ancestry",
        {"p_memory_id": memory_id, "p_max_depth": max_depth},
        client=client,
    )


def get_descendants(
    memory_id: str,
    max_depth: int = 10,
    *,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Return every memory that was derived from this one.

    This is the basis for stale detection: when a source memory is
    superseded, its descendants hold outdated copies.
    """
    return call_rpc(
        "get_descendants",
        {"p_memory_id": memory_id, "p_max_depth": max_depth},
        client=client,
    )
