"""Contract tests for the read path.

Reads stay on PostgREST and must keep filtering by agent and status. These
tests assert the exact filter chain that the memory layer sends.
"""

from __future__ import annotations

from app.memory.store import (
    count_agent_memories,
    get_agent_memories,
    get_memory,
    get_memory_by_key,
)
from tests.fakes import FakeClient

ROW = {"id": "mem-1", "agent_id": "researcher", "status": "active"}


def test_memories_are_filtered_by_agent_and_status() -> None:
    client = FakeClient(rows=[dict(ROW)])

    get_agent_memories("researcher", client=client)

    query = client.last_query()
    assert query.table == "agent_memories"
    assert query.filter_values("eq", "agent_id") == ["researcher"]
    assert query.filter_values("eq", "status") == ["active"]


def test_memories_are_ordered_newest_first() -> None:
    client = FakeClient(rows=[dict(ROW)])

    get_agent_memories("researcher", client=client)

    assert client.last_query().order_by == ("created_at", True)


def test_limit_and_offset_are_applied_when_given() -> None:
    client = FakeClient(rows=[])

    get_agent_memories("researcher", limit=5, offset=10, client=client)

    query = client.last_query()
    assert query.limit_value == 5
    assert query.offset_value == 10


def test_no_limit_is_sent_by_default() -> None:
    client = FakeClient(rows=[])

    get_agent_memories("researcher", client=client)

    assert client.last_query().limit_value is None


def test_rows_are_returned_unchanged() -> None:
    client = FakeClient(rows=[dict(ROW)])

    assert get_agent_memories("researcher", client=client) == [dict(ROW)]


def test_get_memory_by_key_hides_superseded_by_default() -> None:
    client = FakeClient(rows=[dict(ROW)])

    get_memory_by_key("researcher", "price", client=client)

    query = client.last_query()
    assert query.filter_values("eq", "memory_key") == ["price"]
    assert query.filter_values("eq", "status") == ["active"]


def test_get_memory_by_key_can_include_superseded_versions() -> None:
    client = FakeClient(rows=[dict(ROW)])

    get_memory_by_key("researcher", "price", include_superseded=True, client=client)

    assert client.last_query().filter_values("eq", "status") == []


def test_get_memory_returns_none_when_the_row_is_missing() -> None:
    client = FakeClient(rows=[])

    assert get_memory("does-not-exist", client=client) is None


def test_count_uses_the_exact_count() -> None:
    client = FakeClient(rows=[], count=7)

    assert count_agent_memories("researcher", client=client) == 7
    assert client.last_query().filter_values("eq", "status") == ["active"]
