"""LangChain tools wrapping the memory layer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from app.memory.context import get_relevant_context
from app.memory.embeddings import embed_or_none
from app.memory.propagation import get_ancestry, get_descendants, share_memory
from app.memory.store import get_agent_memories, get_memory, save_memory
from app.memory.versioning import update_memory


@tool
def get_context(agent_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get relevant context for an agent (summary-first with freshness filtering)."""
    return get_relevant_context(agent_id, limit=limit)


@tool
def list_memories(agent_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """List active memories for an agent."""
    return get_agent_memories(agent_id, limit=limit)


@tool
def save_agent_memory(agent_id: str, memory_key: str, content: str) -> Dict[str, Any]:
    """Save or update a memory for an agent."""
    return save_memory(agent_id, memory_key, content)


@tool
def update_agent_memory(agent_id: str, memory_key: str, new_content: str) -> Dict[str, Any]:
    """Update an existing memory creating a new version."""
    return update_memory(agent_id, memory_key, new_content)


@tool
def share_agent_memory(source_memory_id: str, target_agent_id: str) -> Dict[str, Any]:
    """Share a memory to another agent with policy enforcement."""
    return share_memory(source_memory_id, target_agent_id)


@tool
def get_memory_ancestry(memory_id: str, max_depth: int = 10) -> List[Dict[str, Any]]:
    """Get provenance ancestry of a memory."""
    return get_ancestry(memory_id, max_depth=max_depth)


@tool
def get_memory_descendants(memory_id: str, max_depth: int = 10) -> List[Dict[str, Any]]:
    """Get memories derived from this memory."""
    return get_descendants(memory_id, max_depth=max_depth)


# ---------------------------------------------------------------------------
# Identity-scoped tools for the workflow agents
# ---------------------------------------------------------------------------

AGENT_IDS = ("supervisor", "planner", "researcher", "executor", "validator")


class NotAuthorized(PermissionError):
    """An agent tried to act on memory it does not own."""


def _require_owner(agent_id: str, memory_id: str) -> None:
    row = get_memory(memory_id)
    if row is None:
        raise NotAuthorized(f"memory {memory_id} not found")
    if row.get("agent_id") != agent_id:
        raise NotAuthorized(f"memory {memory_id} is not owned by {agent_id}")


def build_agent_tools(agent_id: str, *, valid_ids: tuple[str, ...] = AGENT_IDS) -> list:
    """Return memory tools bound to ``agent_id``.

    The identity is fixed here and is never an argument the LLM can set, so an
    agent cannot read or write another agent's memory. Sharing and provenance
    lookups additionally require that the agent owns the memory in question;
    the sharing policy itself is still enforced in the database function.

    ``valid_ids`` defaults to the five demo-workflow agents (``AGENT_IDS``);
    pass a different registry (e.g. the procurement systems' identities) to
    reuse this exact factory for an unrelated set of memory-owning identities
    without touching their tool logic.
    """
    if agent_id not in valid_ids:
        raise ValueError(f"unknown agent identity: {agent_id!r}")

    @tool
    def get_context(limit: int = 10) -> List[Dict[str, Any]]:
        """Get your relevant context (summary-first, stale items excluded)."""
        return get_relevant_context(agent_id, limit=limit)

    @tool
    def list_memories(limit: int = 20) -> List[Dict[str, Any]]:
        """List your active memories."""
        return get_agent_memories(agent_id, limit=limit)

    @tool
    def save_memory_entry(
        memory_key: str,
        content: str,
        source_reference: Optional[str] = None,
        subject_key: Optional[str] = None,
        valid_until: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a memory under a key in your own memory space (new version if it changed).

        Optional: ``source_reference`` (the exact document it came from),
        ``subject_key`` (what it is about, so conflicting claims can be found),
        ``valid_until`` (ISO time after which it is no longer current) and
        ``observed_at`` (ISO time the fact was observed/dated)."""
        extra = {
            name: value
            for name, value in (
                ("source_reference", source_reference),
                ("subject_key", subject_key),
                ("valid_until", valid_until),
                ("observed_at", observed_at),
            )
            if value is not None
        }
        return save_memory(
            agent_id,
            memory_key,
            content,
            source_agent_id=agent_id,
            embedding=embed_or_none(content),
            **extra,
        )

    @tool
    def update_memory_entry(memory_key: str, new_content: str) -> Dict[str, Any]:
        """Create a new version of one of your existing memories."""
        return update_memory(agent_id, memory_key, new_content, embedding=embed_or_none(new_content))

    @tool
    def share_memory_entry(source_memory_id: str, target_agent_id: str) -> Dict[str, Any]:
        """Share one of your memories with another agent (policy enforced)."""
        if target_agent_id not in valid_ids or target_agent_id == agent_id:
            raise NotAuthorized(f"invalid share target: {target_agent_id!r}")
        _require_owner(agent_id, source_memory_id)
        return share_memory(source_memory_id, target_agent_id)

    @tool
    def memory_ancestry(memory_id: str, max_depth: int = 10) -> List[Dict[str, Any]]:
        """Provenance ancestry of one of your memories."""
        _require_owner(agent_id, memory_id)
        return get_ancestry(memory_id, max_depth=max_depth)

    @tool
    def memory_descendants(memory_id: str, max_depth: int = 10) -> List[Dict[str, Any]]:
        """Memories derived from one of your memories."""
        _require_owner(agent_id, memory_id)
        return get_descendants(memory_id, max_depth=max_depth)

    return [
        get_context,
        list_memories,
        save_memory_entry,
        update_memory_entry,
        share_memory_entry,
        memory_ancestry,
        memory_descendants,
    ]
