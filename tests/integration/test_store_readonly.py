"""Read-only checks of the memory store's read path.

These replace the ad-hoc ``test_memory.py`` script. They never insert, update
or delete a row; the ``assert_no_writes`` fixture in ``conftest.py`` enforces
that at runtime.
"""

from __future__ import annotations

import pytest

from app.memory.store import get_agent_memories

KNOWN_AGENT_IDS = [
    "supervisor",
    "planner",
    "researcher",
    "executor",
    "validator",
]
UNKNOWN_AGENT_ID = "agent-that-does-not-exist"


@pytest.mark.integration
@pytest.mark.parametrize("agent_id", KNOWN_AGENT_IDS)
def test_memories_are_scoped_to_the_requested_agent(supabase_client, agent_id):
    """The agent filter must never leak another agent's memories."""
    memories = get_agent_memories(agent_id)

    assert isinstance(memories, list)
    assert all(row["agent_id"] == agent_id for row in memories)


@pytest.mark.integration
def test_only_active_versions_are_returned(supabase_client):
    """Superseded versions must not appear in the active view."""
    for agent_id in KNOWN_AGENT_IDS:
        memories = get_agent_memories(agent_id)
        assert all(row["status"] == "active" for row in memories)


@pytest.mark.integration
def test_unknown_agent_has_no_memories(supabase_client):
    assert get_agent_memories(UNKNOWN_AGENT_ID) == []


@pytest.mark.integration
def test_results_are_ordered_newest_first(supabase_client):
    """`get_agent_memories` orders by ``created_at`` descending."""
    for agent_id in KNOWN_AGENT_IDS:
        memories = get_agent_memories(agent_id)
        timestamps = [row["created_at"] for row in memories]
        assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.integration
def test_two_agents_do_not_see_the_same_rows(supabase_client):
    """Cross-agent overlap would break the isolation guarantee."""
    researcher = {row["id"] for row in get_agent_memories("researcher")}
    executor = {row["id"] for row in get_agent_memories("executor")}

    assert researcher.isdisjoint(executor)
