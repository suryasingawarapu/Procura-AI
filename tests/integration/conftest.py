"""Fixtures for the read-only integration tests.

Every test in this package is guarded: the row counts of all three tables are
recorded before and after the test and compared afterwards. Any accidental
write makes the test fail, which keeps the suite from polluting the shared
Supabase project.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict

import pytest

TABLES = ("agent_memories", "memory_provenance", "agent_memory_summaries")


def row_count(client, table: str) -> int:
    """Return the number of rows in ``table`` (read-only)."""
    response = client.table(table).select("id", count="exact").execute()
    count = getattr(response, "count", None)
    if count is not None:
        return int(count)
    return len(response.data or [])


@pytest.fixture(scope="session")
def openapi_schema() -> Dict[str, Any]:
    """The PostgREST schema of the configured project (read-only GET)."""
    from dotenv import load_dotenv

    load_dotenv()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        pytest.skip("SUPABASE_URL / SUPABASE_KEY are not configured")

    request = urllib.request.Request(
        url.rstrip("/") + "/rest/v1/",
        headers={
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Accept": "application/openapi+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


@pytest.fixture(autouse=True)
def assert_no_writes(supabase_client, request):
    """Fail the test if it changed the number of rows in any table.

    Tests marked ``dbwrite`` are allowed to write; they are opt-in and clean
    up after themselves.
    """
    if request.node.get_closest_marker("dbwrite") is not None:
        yield
        return

    before = {table: row_count(supabase_client, table) for table in TABLES}

    yield

    after = {table: row_count(supabase_client, table) for table in TABLES}
    assert before == after, (
        "an integration test modified the database; "
        f"row counts changed from {before} to {after}"
    )
