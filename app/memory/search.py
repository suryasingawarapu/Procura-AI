"""Vector similarity search over agent memories.

Uses the match_agent_memories RPC function.
Read-only operations.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .rpc import call_rpc

__all__ = ["search_memories"]


def search_memories(
    agent_id: str,
    query_embedding: str | List[float],
    *,
    match_threshold: float = 0.75,
    match_count: int = 10,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Search for similar memories using vector similarity.
    
    Returns active memories for the agent ordered by similarity.
    """
    if isinstance(query_embedding, list):
        query_embedding_str = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
    elif isinstance(query_embedding, str):
        query_embedding_str = query_embedding
    else:
        return []
    
    return call_rpc(
        "match_agent_memories",
        {
            "p_agent_id": agent_id,
            "p_query_embedding": query_embedding_str,
            "p_match_threshold": match_threshold,
            "p_match_count": match_count,
        },
        client=client,
    )
