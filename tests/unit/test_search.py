"""Unit tests for vector search."""

from __future__ import annotations

import pytest
from postgrest.exceptions import APIError

from app.memory import errors
from app.memory.search import search_memories
from tests.fakes import FakeClient


def test_search_calls_match_agent_memories() -> None:
    client = FakeClient(rows=[{"id": "mem-1", "similarity": 0.9}])
    
    search_memories("researcher", [0.1, 0.2], client=client)
    
    call = client.last_rpc()
    assert call["function"] == "match_agent_memories"
    assert call["params"]["p_agent_id"] == "researcher"
    assert call["params"]["p_query_embedding"] == "[0.1,0.2]"


def test_search_passes_threshold_and_count() -> None:
    client = FakeClient(rows=[])
    
    search_memories("researcher", "[0.1]", match_threshold=0.8, match_count=5, client=client)
    
    call = client.last_rpc()
    assert call["params"]["p_match_threshold"] == 0.8
    assert call["params"]["p_match_count"] == 5


def test_search_accepts_string_embedding() -> None:
    client = FakeClient(rows=[])
    
    search_memories("researcher", "[0.5,0.6]", client=client)
    
    assert client.last_rpc()["params"]["p_query_embedding"] == "[0.5,0.6]"


def test_search_returns_empty_for_invalid_embedding() -> None:
    client = FakeClient(rows=[])
    
    result = search_memories("researcher", None, client=client)  # type: ignore
    
    assert result == []
    assert len(client.rpc_calls) == 0


def test_search_maps_errors() -> None:
    client = FakeClient(error=APIError({"message": "bad", "code": "P0002", "hint": None, "details": None}))
    
    with pytest.raises(errors.MemoryNotFound):
        search_memories("researcher", [0.1], client=client)
