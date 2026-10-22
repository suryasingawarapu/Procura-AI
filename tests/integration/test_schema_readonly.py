"""Read-only schema checks.

The schema is owned by Supabase and is not managed by application code yet.
These tests document the contract the application relies on, so that an
accidental schema change is noticed early instead of surfacing as a runtime
error deep inside a workflow.
"""

from __future__ import annotations

import pytest

EXPECTED_COLUMNS = {
    "agent_memories": {
        "id",
        "agent_id",
        "memory_key",
        "content",
        "created_at",
        "updated_at",
        "source_agent_id",
        "source_reference",
        "version",
        "status",
        "embedding",
    },
    "memory_provenance": {
        "id",
        "memory_id",
        "parent_memory_id",
        "from_agent_id",
        "to_agent_id",
        "relationship",
        "created_at",
    },
    "agent_memory_summaries": {
        "id",
        "agent_id",
        "summary",
        "memory_ids",
        "created_at",
        "embedding",
    },
}


@pytest.mark.integration
@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_is_exposed_by_postgrest(openapi_schema, table):
    definitions = openapi_schema.get("definitions", {})
    assert table in definitions


@pytest.mark.integration
@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_expected_columns_exist(openapi_schema, table):
    definitions = openapi_schema.get("definitions", {})
    columns = set(definitions[table].get("properties", {}).keys())
    missing = EXPECTED_COLUMNS[table] - columns
    assert not missing, f"{table} is missing columns: {sorted(missing)}"


@pytest.mark.integration
def test_version_and_status_are_required_columns(openapi_schema):
    """The application relies on these two columns always being present."""
    columns = openapi_schema["definitions"]["agent_memories"]
    required = set(columns.get("required", []))
    assert {"agent_id", "memory_key", "content", "version", "status"} <= required


@pytest.mark.integration
def test_provenance_edge_columns_are_nullable_parent(openapi_schema):
    """``parent_memory_id`` must stay nullable for root memories."""
    columns = openapi_schema["definitions"]["memory_provenance"]
    required = set(columns.get("required", []))
    assert "parent_memory_id" not in required
    assert {"memory_id", "to_agent_id", "relationship"} <= required
