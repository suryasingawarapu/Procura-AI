"""Gaps that are still open after milestone M1.

Milestone M1 hardens the schema and makes the writes atomic. Embeddings are
still missing, so this file records what is still outstanding. Each test has
to be removed or inverted in the change that closes the gap.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_no_embeddings_are_populated(supabase_client):
    """Open gap: closed by M3 (embedding generation)."""
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


@pytest.mark.integration
def test_summary_table_is_still_empty(supabase_client):
    """Open gap: closed by M2/M3 (summary generation and summary-first reads)."""
    response = supabase_client.table("agent_memory_summaries").select("id", count="exact").execute()
    count = getattr(response, "count", None)
    if count is None:
        count = len(response.data or [])

    assert count == 0, "summaries were created; update this baseline."
