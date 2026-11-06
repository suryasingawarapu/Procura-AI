"""Unit tests for the migration-006 provenance enrichment helpers.

Pure mock-based: no real client, no network. Confirms the exact filter
chain sent to Supabase and that unknown/None fields are dropped rather than
sent as a column that doesn't exist or clobbering a column with None.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.memory.provenance_fields import enrich_memory, enrich_share_edge


def test_enrich_memory_sends_only_known_non_none_fields():
    client = MagicMock()
    client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": "m1", "verification_status": "verified"}
    ]

    result = enrich_memory(
        "m1",
        client=client,
        verification_status="verified",
        confidence=0.9,
        not_a_real_column="should be dropped",
        source_hash=None,  # None is dropped, not sent as a column reset
    )

    client.table.assert_called_once_with("agent_memories")
    update_call = client.table.return_value.update
    update_call.assert_called_once_with({"verification_status": "verified", "confidence": 0.9})
    update_call.return_value.eq.assert_called_once_with("id", "m1")
    assert result == {"id": "m1", "verification_status": "verified"}


def test_enrich_memory_returns_none_when_nothing_to_set():
    client = MagicMock()
    assert enrich_memory("m1", client=client) is None
    client.table.assert_not_called()


def test_enrich_memory_returns_none_when_row_not_found():
    client = MagicMock()
    client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
    assert enrich_memory("missing", client=client, task_id="t1") is None


def test_enrich_share_edge_filters_by_memory_and_parent_and_relationship():
    client = MagicMock()
    chain = client.table.return_value.update.return_value.eq.return_value.eq.return_value.eq
    chain.return_value.execute.return_value.data = [{"memory_id": "copy1", "accepted": True}]

    result = enrich_share_edge("copy1", "source1", client=client, task_id="task-9", accepted=True)

    client.table.assert_called_once_with("memory_provenance")
    client.table.return_value.update.assert_called_once_with({"task_id": "task-9", "accepted": True})
    first_eq = client.table.return_value.update.return_value.eq
    first_eq.assert_called_once_with("memory_id", "copy1")
    second_eq = first_eq.return_value.eq
    second_eq.assert_called_once_with("parent_memory_id", "source1")
    chain.assert_called_once_with("relationship", "derived_from")
    assert result == {"memory_id": "copy1", "accepted": True}
