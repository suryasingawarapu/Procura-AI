"""Contract tests for the atomic write path.

The writes are executed by Postgres functions, so the Python side only has to
do two things: send the right payload and translate the errors that come
back. Both are verified here against a fake client, so no database is
involved.
"""

from __future__ import annotations

import pytest
from postgrest.exceptions import APIError

from app.memory import errors
from app.memory.propagation import get_ancestry, get_descendants, share_memory
from app.memory.rpc import translate_error
from app.memory.store import save_memory
from app.memory.versioning import update_memory
from tests.fakes import FakeClient

ROW = {"id": "mem-1", "agent_id": "researcher", "version": 1, "status": "active"}


def _client(rows=None, error=None) -> FakeClient:
    return FakeClient(rows=rows if rows is not None else [dict(ROW)], error=error)


def test_save_memory_calls_the_atomic_function() -> None:
    client = _client()

    save_memory(
        agent_id="researcher",
        memory_key="price",
        content="The price is 55000.",
        client=client,
    )

    call = client.last_rpc()
    assert call["function"] == "save_memory_atomic"
    assert call["params"]["p_agent_id"] == "researcher"
    assert call["params"]["p_memory_key"] == "price"
    assert call["params"]["p_content"] == "The price is 55000."


def test_save_memory_returns_the_stored_row() -> None:
    client = _client()

    result = save_memory("researcher", "price", "content", client=client)

    assert result == ROW


def test_save_memory_sends_the_embedding_as_text() -> None:
    client = _client()

    save_memory(
        "researcher",
        "price",
        "content",
        embedding=[0.1, 0.25],
        client=client,
    )

    assert client.last_rpc()["params"]["p_embedding"] == "[0.1,0.25]"


def test_save_memory_leaves_optional_values_empty() -> None:
    client = _client()

    save_memory("researcher", "price", "content", client=client)

    params = client.last_rpc()["params"]
    assert params["p_embedding"] is None
    assert params["p_observed_at"] is None
    assert params["p_source_agent_id"] is None


def test_update_memory_calls_the_supersede_function() -> None:
    client = _client()

    update_memory(
        agent_id="researcher",
        memory_key="price",
        new_content="The price is 58000.",
        source_reference="version_test_v2",
        client=client,
    )

    call = client.last_rpc()
    assert call["function"] == "supersede_memory_atomic"
    assert call["params"]["p_new_content"] == "The price is 58000."
    assert call["params"]["p_source_reference"] == "version_test_v2"


def test_share_memory_calls_the_share_function() -> None:
    client = _client()

    share_memory(
        source_memory_id="mem-1",
        target_agent_id="executor",
        client=client,
    )

    call = client.last_rpc()
    assert call["function"] == "share_memory_atomic"
    assert call["params"]["p_source_memory_id"] == "mem-1"
    assert call["params"]["p_target_agent_id"] == "executor"


def test_share_memory_passes_the_optional_hop_limit() -> None:
    client = _client()

    share_memory("mem-1", "executor", max_hops=1, client=client)

    assert client.last_rpc()["params"]["p_max_hops"] == 1


def test_get_ancestry_calls_the_ancestry_function() -> None:
    client = _client(rows=[{"depth": 0, "memory_id": "mem-1"}])

    rows = get_ancestry("mem-1", max_depth=5, client=client)

    call = client.last_rpc()
    assert call["function"] == "get_ancestry"
    assert call["params"]["p_memory_id"] == "mem-1"
    assert call["params"]["p_max_depth"] == 5
    assert rows == [{"depth": 0, "memory_id": "mem-1"}]


def test_get_descendants_calls_the_descendants_function() -> None:
    client = _client(rows=[])

    get_descendants("mem-1", client=client)

    assert client.last_rpc()["function"] == "get_descendants"


@pytest.mark.parametrize(
    "code, expected",
    [
        ("P0002", errors.MemoryNotFound),
        ("MEM01", errors.AgentNotRegistered),
        ("MEM02", errors.ShareNotAllowed),
        ("MEM03", errors.HopLimitExceeded),
        ("MEM05", errors.SelfShareNotAllowed),
        ("23505", errors.DuplicateMemory),
        ("23514", errors.ConstraintViolation),
        ("23503", errors.AgentNotRegistered),
        ("PGRST202", errors.MemorySchemaMissing),
        ("PGRST203", errors.MemorySchemaMissing),
        ("08006", errors.MemoryError),
    ],
)
def test_error_codes_are_translated(code: str, expected: type) -> None:
    exc = APIError({"message": "boom", "code": code, "hint": None, "details": None})

    translated = translate_error(exc, "save_memory_atomic")

    assert isinstance(translated, expected)
    assert "save_memory_atomic" in str(translated)


def test_missing_functions_point_at_the_migrations() -> None:
    exc = APIError(
        {
            "message": "Could not find the function public.save_memory_atomic",
            "code": "PGRST202",
            "hint": None,
            "details": None,
        }
    )

    translated = translate_error(exc, "save_memory_atomic")

    assert isinstance(translated, errors.MemorySchemaMissing)
    assert "001_harden_schema.sql" in str(translated)
    assert "002_memory_functions.sql" in str(translated)


def test_write_errors_are_raised_as_typed_exceptions() -> None:
    client = _client(error=APIError({"message": "no row", "code": "P0002", "hint": None, "details": None}))

    with pytest.raises(errors.MemoryNotFound):
        update_memory("researcher", "price", "new content", client=client)
