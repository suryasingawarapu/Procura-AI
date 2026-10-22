"""Baseline snapshot of the known schema gaps (state before milestone M1).

These assertions intentionally encode the *current* state of the database so
that an unexpected change is noticed. Each one has to be updated in the same
change that closes the corresponding gap:

* ``test_provenance_has_no_foreign_keys``  -> closed by M1 (FK + constraints)
* ``test_no_sql_functions_are_exposed``    -> closed by M1 (RPC functions)
* ``test_no_embeddings_are_populated``     -> closed by M3 (embedding generation)
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_provenance_has_no_foreign_keys(openapi_schema):
    """Known gap: provenance rows can point at memories that do not exist."""
    for table in ("agent_memories", "memory_provenance", "agent_memory_summaries"):
        relationships = openapi_schema["definitions"][table].get("relationships", {})
        assert not relationships, (
            f"{table} now declares foreign keys; update this baseline and the "
            "M1 checklist."
        )


@pytest.mark.integration
def test_no_sql_functions_are_exposed(openapi_schema):
    """Known gap: vector search and atomic writes have no SQL function yet."""
    rpc_paths = [p for p in openapi_schema.get("paths", {}) if p.startswith("/rpc/")]
    assert not rpc_paths, (
        f"SQL functions are now exposed ({rpc_paths}); update this baseline "
        "and the M1 checklist."
    )


@pytest.mark.integration
def test_no_embeddings_are_populated(supabase_client):
    """Known gap: every embedding column is still NULL."""
    for table in ("agent_memories", "agent_memory_summaries"):
        rows = (
            supabase_client.table(table)
            .select("embedding")
            .not_.is_("embedding", "null")
            .execute()
            .data
            or []
        )
        assert not rows, f"{table} now contains embeddings; update this baseline."
