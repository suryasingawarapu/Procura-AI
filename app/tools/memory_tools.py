"""LangChain tools wrapping the memory layer."""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.tools import tool

from app.memory.context import get_relevant_context
from app.memory.propagation import get_ancestry, get_descendants, share_memory
from app.memory.store import get_agent_memories, save_memory
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
