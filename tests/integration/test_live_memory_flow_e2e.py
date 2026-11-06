"""End-to-end verification of the live memory flow against real Supabase.

This is deliberately separate from test_dbwrite_memory_atomic.py (which
already proves the atomic RPC functions are correct in isolation). This file
answers a different question: does the flow a real agent actually uses --
save, read it back through the real retrieval path, update, and check
provenance/ownership -- work end to end against the live project, with every
step confirmed by an independent read rather than trusting a function's own
return value?

ONE throwaway agent and ONE throwaway memory key are used, both carrying a
fresh uuid that cannot collide with any real agent name or existing memory
key. Every create/read/update/delete in this file is filtered on that exact
id, so it can never touch another row. Cleanup runs both before the test
(in case a previous run crashed before its own cleanup) and after it in a
try/finally (so an assertion failure still cleans up) -- the same two-sided
pattern already used by test_dbwrite_memory_atomic.py's `registered_agents`
fixture.

Opt-in, like every other dbwrite test:
    $env:MEMORY_ALLOW_WRITE = "1"
    .venv\\Scripts\\python.exe -m pytest tests/integration/test_live_memory_flow_e2e.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.memory.context import get_relevant_context
from app.memory.store import save_memory
from app.memory.versioning import update_memory

pytestmark = [
    pytest.mark.dbwrite,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MEMORY_ALLOW_WRITE", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="set MEMORY_ALLOW_WRITE=1 to run the database write tests",
    ),
]

RUN_ID = uuid.uuid4().hex[:12]
TEST_AGENT_ID = f"e2e_verify_{RUN_ID}"
TEST_MEMORY_KEY = f"e2e_verify_key_{RUN_ID}"
CONTENT_V1 = f"AUTOMATED E2E VERIFICATION TEST -- safe to delete -- run {RUN_ID} -- version 1"
CONTENT_V2 = f"AUTOMATED E2E VERIFICATION TEST -- safe to delete -- run {RUN_ID} -- version 2 (updated)"


def _cleanup(client) -> None:
    """Remove only the rows this exact run created, in dependency order.

    Scoped entirely by TEST_AGENT_ID (a fresh uuid each run), so this can
    never match -- and therefore never delete -- any other agent's data.
    """
    memory_ids = [
        row["id"]
        for row in client.table("agent_memories").select("id").eq("agent_id", TEST_AGENT_ID).execute().data
    ]
    if memory_ids:
        client.table("memory_provenance").delete().in_("memory_id", memory_ids).execute()
        client.table("memory_provenance").delete().in_("parent_memory_id", memory_ids).execute()
    client.table("agent_memories").delete().eq("agent_id", TEST_AGENT_ID).execute()
    client.table("memory_agents").delete().eq("agent_id", TEST_AGENT_ID).execute()


def test_live_memory_flow_end_to_end(supabase_client) -> None:
    _cleanup(supabase_client)  # defensive: clean up debris from any crashed prior run

    try:
        # -- 0. arrange: register the throwaway agent (required: memory_agents
        #    is a closed registry -- an unregistered agent_id is rejected) --
        supabase_client.table("memory_agents").upsert(
            {"agent_id": TEST_AGENT_ID, "role": "agent"}, on_conflict="agent_id"
        ).execute()

        # -- 1. save a memory, then CONFIRM it exists via an independent
        #    read -- not by trusting save_memory()'s own return value --
        saved = save_memory(TEST_AGENT_ID, TEST_MEMORY_KEY, CONTENT_V1)
        assert saved["version"] == 1
        assert saved["status"] == "active"

        confirmed = (
            supabase_client.table("agent_memories").select("*").eq("id", saved["id"]).single().execute().data
        )
        assert confirmed is not None, "the saved memory is not readable back from Supabase"
        assert confirmed["content"] == CONTENT_V1
        assert confirmed["agent_id"] == TEST_AGENT_ID  # ownership

        # -- 2. retrieve it through the real retrieval path (the same
        #    function the API's /context endpoint and the agents' get_context
        #    tool use), not a raw table query --
        context_before = get_relevant_context(TEST_AGENT_ID, client=supabase_client)
        assert any(item["id"] == saved["id"] for item in context_before), (
            "get_relevant_context() did not surface the memory that was just saved"
        )

        # -- 3. update it; verify versioning and supersession, each via an
        #    independent read --
        updated = update_memory(TEST_AGENT_ID, TEST_MEMORY_KEY, CONTENT_V2)
        assert updated["version"] == 2
        assert updated["status"] == "active"

        old_row = (
            supabase_client.table("agent_memories").select("status").eq("id", saved["id"]).single().execute().data
        )
        assert old_row["status"] == "superseded", "the old version was not marked superseded"

        # -- 4. verify provenance (the supersedes edge) and ownership --
        edge = (
            supabase_client.table("memory_provenance")
            .select("*")
            .eq("memory_id", updated["id"])
            .eq("parent_memory_id", saved["id"])
            .eq("relationship", "supersedes")
            .execute()
            .data
        )
        assert len(edge) == 1, "the supersedes provenance edge is missing"
        assert updated["agent_id"] == TEST_AGENT_ID  # ownership carried into the new version

        # Retrieval again, post-update: must reflect the new version only.
        context_after = get_relevant_context(TEST_AGENT_ID, client=supabase_client)
        ids_after = {item["id"] for item in context_after}
        assert updated["id"] in ids_after
        assert saved["id"] not in ids_after, "the superseded version is still being surfaced as active"

    finally:
        # -- 5. cleanup: guaranteed even if an assertion above failed --
        _cleanup(supabase_client)
