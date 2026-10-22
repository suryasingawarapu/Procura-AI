"""Acceptance test for the atomic memory functions.

This test *writes* to the database and is therefore opt-in:

    $env:MEMORY_ALLOW_WRITE = "1"
    .\\.venv\\Scripts\\python.exe -m pytest -m dbwrite -v

It registers its own throwaway agents, exercises the full write path and
removes everything it created again. It is skipped while the M1 migrations
are not applied.
"""

from __future__ import annotations

import os

import pytest

from app.memory import errors
from app.memory.propagation import get_ancestry, get_descendants, share_memory
from app.memory.store import save_memory
from app.memory.versioning import update_memory

SOURCE_AGENT = "pytest_source"
TARGET_AGENT = "pytest_target"
MEMORY_KEY = "pytest_key"

pytestmark = [
    pytest.mark.dbwrite,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MEMORY_ALLOW_WRITE", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="set MEMORY_ALLOW_WRITE=1 to run the database write tests",
    ),
]


@pytest.fixture
def registered_agents(supabase_client):
    """Register the throwaway agents and remove them afterwards."""
    for agent_id in (SOURCE_AGENT, TARGET_AGENT):
        supabase_client.table("memory_agents").upsert(
            {"agent_id": agent_id, "role": "agent"},
            on_conflict="agent_id",
        ).execute()

    supabase_client.table("memory_share_policy").upsert(
        {
            "from_agent_id": SOURCE_AGENT,
            "to_agent_id": TARGET_AGENT,
            "allowed": True,
            "max_hops": 3,
        },
        on_conflict="from_agent_id,to_agent_id",
    ).execute()

    yield

    supabase_client.table("agent_memories").delete().in_(
        "agent_id", [SOURCE_AGENT, TARGET_AGENT]
    ).execute()
    supabase_client.table("memory_share_policy").delete().in_(
        "from_agent_id", [SOURCE_AGENT]
    ).execute()
    supabase_client.table("memory_agents").delete().in_(
        "agent_id", [SOURCE_AGENT, TARGET_AGENT]
    ).execute()


def test_save_is_atomic_and_idempotent(supabase_client, registered_agents) -> None:
    first = save_memory(SOURCE_AGENT, MEMORY_KEY, "first content")
    assert first["version"] == 1
    assert first["status"] == "active"

    again = save_memory(SOURCE_AGENT, MEMORY_KEY, "first content")
    assert again["id"] == first["id"], "saving the same content must not create a new version"
    assert again["version"] == 1


def test_changed_content_supersedes_in_one_step(supabase_client, registered_agents) -> None:
    first = save_memory(SOURCE_AGENT, MEMORY_KEY, "first content")
    second = save_memory(SOURCE_AGENT, MEMORY_KEY, "second content")

    assert second["version"] == 2
    assert second["id"] != first["id"]

    old = (
        supabase_client.table("agent_memories")
        .select("status")
        .eq("id", first["id"])
        .single()
        .execute()
        .data
    )
    assert old["status"] == "superseded"

    edge = (
        supabase_client.table("memory_provenance")
        .select("*")
        .eq("memory_id", second["id"])
        .eq("parent_memory_id", first["id"])
        .eq("relationship", "supersedes")
        .execute()
        .data
    )
    assert len(edge) == 1, "the supersedes edge is missing"


def test_update_creates_the_next_version(supabase_client, registered_agents) -> None:
    save_memory(SOURCE_AGENT, MEMORY_KEY, "first content")

    updated = update_memory(SOURCE_AGENT, MEMORY_KEY, "second content")

    assert updated["version"] == 2
    assert updated["status"] == "active"


def test_update_of_an_unknown_key_is_reported(supabase_client, registered_agents) -> None:
    with pytest.raises(errors.MemoryNotFound):
        update_memory(SOURCE_AGENT, "does-not-exist", "content")


def test_share_creates_a_copy_with_provenance(supabase_client, registered_agents) -> None:
    source = save_memory(SOURCE_AGENT, MEMORY_KEY, "shared content")

    copy = share_memory(source["id"], TARGET_AGENT)

    assert copy["agent_id"] == TARGET_AGENT
    assert copy["content"] == source["content"]
    assert copy["hop_depth"] == 1
    assert copy["source_version"] == source["version"]

    edge = (
        supabase_client.table("memory_provenance")
        .select("*")
        .eq("memory_id", copy["id"])
        .eq("parent_memory_id", source["id"])
        .eq("relationship", "derived_from")
        .execute()
        .data
    )
    assert len(edge) == 1, "the derived_from edge is missing"


def test_sharing_twice_does_not_duplicate(supabase_client, registered_agents) -> None:
    source = save_memory(SOURCE_AGENT, MEMORY_KEY, "shared content")

    first = share_memory(source["id"], TARGET_AGENT)
    second = share_memory(source["id"], TARGET_AGENT)

    assert first["id"] == second["id"]


def test_sharing_an_unlisted_pair_is_denied(supabase_client, registered_agents) -> None:
    source = save_memory(SOURCE_AGENT, MEMORY_KEY, "shared content")
    copy = share_memory(source["id"], TARGET_AGENT)

    # the reverse direction has no policy row -> denied
    with pytest.raises(errors.ShareNotAllowed):
        share_memory(copy["id"], SOURCE_AGENT)

    # an agent cannot share a memory with itself
    with pytest.raises(errors.SelfShareNotAllowed):
        share_memory(source["id"], SOURCE_AGENT)


def test_ancestry_and_descendants_are_multi_hop(supabase_client, registered_agents) -> None:
    source = save_memory(SOURCE_AGENT, MEMORY_KEY, "shared content")
    copy = share_memory(source["id"], TARGET_AGENT)

    ancestry = get_ancestry(copy["id"])
    assert len(ancestry) >= 2
    assert ancestry[0]["memory_id"] == copy["id"]
    assert ancestry[0]["depth"] == 0
    assert ancestry[1]["relationship"] == "derived_from"

    descendants = get_descendants(source["id"])
    assert len(descendants) >= 2
    assert descendants[1]["memory_id"] == copy["id"]


def test_unregistered_agents_are_rejected(supabase_client) -> None:
    with pytest.raises(errors.AgentNotRegistered):
        save_memory("pytest_unknown_agent", MEMORY_KEY, "content")
