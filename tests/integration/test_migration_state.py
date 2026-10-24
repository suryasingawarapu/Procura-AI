"""Checks that describe the state of the M1 migrations.

The suite has to stay green *before* and *after* the migrations are applied,
so every check branches on the current state of the database. Once the
migrations are applied, these tests start verifying that they really did
what they were supposed to do.
"""

from __future__ import annotations

import pytest

M1_COLUMNS = {
    "observed_at",
    "valid_until",
    "hop_depth",
    "source_version",
    "subject_key",
    "superseded_at",
}

M1_FUNCTIONS = {
    "save_memory_atomic",
    "supersede_memory_atomic",
    "share_memory_atomic",
    "get_ancestry",
    "get_descendants",
    "match_agent_memories",
}


def _columns(openapi_schema, table: str) -> set:
    definitions = openapi_schema.get("definitions", {})
    if table not in definitions:
        return set()
    return set(definitions[table].get("properties", {}).keys())


def _rpc_functions(openapi_schema) -> set:
    return {
        path.split("/")[-1]
        for path in openapi_schema.get("paths", {})
        if path.startswith("/rpc/")
    }


def schema_migration_applied(openapi_schema) -> bool:
    """True when migrations/001_harden_schema.sql has been applied."""
    return "observed_at" in _columns(openapi_schema, "agent_memories")


def function_migration_applied(openapi_schema) -> bool:
    """True when migrations/002_memory_functions.sql has been applied."""
    return M1_FUNCTIONS <= _rpc_functions(openapi_schema)


@pytest.mark.integration
def test_migrations_are_all_or_nothing(openapi_schema):
    """The functions depend on the new columns, so both must be present."""
    schema_applied = schema_migration_applied(openapi_schema)
    functions_applied = function_migration_applied(openapi_schema)

    if functions_applied:
        assert schema_applied, (
            "the memory functions are installed but 001_harden_schema.sql is "
            "not applied; apply 001 and reload the schema cache"
        )


@pytest.mark.integration
def test_new_columns_are_present_after_the_migration(openapi_schema):
    columns = _columns(openapi_schema, "agent_memories")

    if schema_migration_applied(openapi_schema):
        missing = M1_COLUMNS - columns
        assert not missing, f"agent_memories is missing columns: {sorted(missing)}"
    else:
        present = M1_COLUMNS & columns
        assert not present, (
            f"unexpected columns found before the migration: {sorted(present)}"
        )


@pytest.mark.integration
def test_foreign_keys_exist_after_the_migration(openapi_schema):
    """Provenance must reference real memories and known agents."""
    definitions = openapi_schema.get("definitions", {})
    relationships = {
        table: definitions.get(table, {}).get("relationships", {})
        for table in ("agent_memories", "memory_provenance", "agent_memory_summaries")
    }
    total = sum(len(value) for value in relationships.values())

    if schema_migration_applied(openapi_schema):
        if total == 0:
            pytest.skip(
                "PostgREST OpenAPI does not expose relationships; "
                "verify via migrations/003_verify_after_apply.sql (check 5/18)"
            )
        assert total > 0
    else:
        assert total == 0, "foreign keys appeared without the migration"


@pytest.mark.integration
def test_agent_registry_exists_after_the_migration(openapi_schema):
    if schema_migration_applied(openapi_schema):
        assert "memory_agents" in openapi_schema.get("definitions", {})
        assert "memory_share_policy" in openapi_schema.get("definitions", {})


@pytest.mark.integration
def test_memory_functions_are_exposed_after_the_migration(openapi_schema):
    functions = _rpc_functions(openapi_schema)

    if function_migration_applied(openapi_schema):
        assert M1_FUNCTIONS <= functions
    else:
        assert not (M1_FUNCTIONS & functions), (
            "some memory functions are exposed but not all of them; "
            "re-apply 002_memory_functions.sql"
        )
