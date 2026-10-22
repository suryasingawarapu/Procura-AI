"""Version chains and supersession.

The version chain of a memory is the chain of rows that share
``(agent_id, memory_key)``. Exactly one of them is *active*; the previous
ones are marked ``superseded`` and linked through the ``supersedes``
relationship in ``memory_provenance``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .formatting import format_embedding, format_timestamp
from .rpc import call_rpc_one
from .store import get_memory_by_key

__all__ = ["update_memory", "get_version_history"]


def update_memory(
    agent_id: str,
    memory_key: str,
    new_content: str,
    source_reference: str | None = None,
    *,
    embedding: Any = None,
    observed_at: Any = None,
    subject_key: str | None = None,
    valid_until: Any = None,
    client: Any = None,
) -> Dict[str, Any]:
    """Create the next version of a memory.

    The previous active version is marked ``superseded`` and the new version
    is linked to it through a ``supersedes`` provenance edge. Both happen in
    one transaction.

    Raises :class:`app.memory.errors.MemoryNotFound` when there is no active
    memory for ``(agent_id, memory_key)``.
    """
    row = call_rpc_one(
        "supersede_memory_atomic",
        {
            "p_agent_id": agent_id,
            "p_memory_key": memory_key,
            "p_new_content": new_content,
            "p_source_reference": source_reference,
            "p_embedding": format_embedding(embedding),
            "p_observed_at": format_timestamp(observed_at),
            "p_subject_key": subject_key,
            "p_valid_until": format_timestamp(valid_until),
        },
        client=client,
    )
    return row


def get_version_history(
    agent_id: str,
    memory_key: str,
    *,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Return every version of a memory, oldest first."""
    return get_memory_by_key(
        agent_id,
        memory_key,
        include_superseded=True,
        client=client,
    )
