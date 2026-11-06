"""Summary-first context retrieval for agents.

Order of preference:

1. A usable summary, followed by any memories newer than it (the "delta"), so
   an agent gets the compressed past plus what changed since. A summary that
   covers a memory which was later superseded, archived or expired is *stale*
   and is skipped (see :mod:`app.memory.summaries`).
2. Vector search, when a query embedding is given.
3. Active memories.

Shared copies whose source has since been superseded are treated as stale and
left out (see :mod:`app.memory.staleness`). Read-only operations only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .client import supabase
from .freshness import check_freshness
from .search import search_memories
from .staleness import drop_stale_copies
from .store import get_agent_memories
from .summaries import summary_state

__all__ = ["get_relevant_context"]


def get_relevant_context(
    agent_id: str,
    limit: int | None = None,
    *,
    client: Any = None,
    memory_limit: int | None = None,
    exclude_stale: bool = True,
    now: datetime | None = None,
    query_embedding: Any = None,
    match_threshold: float = 0.75,
    match_count: int | None = None,
) -> List[Dict[str, Any]]:
    """Retrieve relevant context for an agent.

    Prefers summaries when available, falls back to vector search if query_embedding provided,
    otherwise falls back to active memories. Excludes stale items by default;
    unknown freshness is preserved conservatively.
    """
    active_client = client if client is not None else supabase

    # Try to get summaries first
    query = (
        active_client.table("agent_memory_summaries")
        .select("*")
        .eq("agent_id", agent_id)
        .order("created_at", desc=True)
    )

    if limit is not None:
        query = query.limit(limit)

    summaries = query.execute().data or []

    if summaries:
        if not exclude_stale:
            return summaries
        candidates = [s for s in summaries if check_freshness(s, now) != "stale"]
        usable: List[Dict[str, Any]] = []
        delta: List[Dict[str, Any]] = []
        seen = set()
        for summary in candidates:
            state = summary_state(summary, client=active_client, now=now)
            if not state.usable:
                continue
            usable.append(summary)
            for memory in state.new_memories:
                if memory.get("id") not in seen:
                    seen.add(memory.get("id"))
                    delta.append(memory)
        if usable:
            return usable + drop_stale_copies(delta, client=active_client)
        # every summary is stale: do not use them, fall through to the memories

    # Try vector search if embedding provided
    if query_embedding is not None:
        try:
            count = match_count if match_count is not None else (limit if limit is not None else 10)
            results = search_memories(
                agent_id,
                query_embedding,
                match_threshold=match_threshold,
                match_count=count,
                client=active_client,
            )
            if exclude_stale:
                filtered = [r for r in results if check_freshness(r, now) != "stale"]
                return drop_stale_copies(filtered, client=active_client)
            return results
        except Exception:
            # Fall back to memories on search failure
            pass

    # Fall back to active memories
    memories = get_agent_memories(agent_id, limit=memory_limit, client=active_client)
    if exclude_stale:
        filtered = [m for m in memories if check_freshness(m, now) != "stale"]
        return drop_stale_copies(filtered, client=active_client)
    return memories
