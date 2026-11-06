"""Unit tests must never touch the real database.

A unit test that forgets to patch one memory call would otherwise quietly read
or WRITE your real Supabase project (it has happened: a test once shared a
real memory by accident). This makes any such call fail loudly instead.

Tests that genuinely need the real client belong in ``tests/integration``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _unit_tests_never_send_traces(monkeypatch):
    """Tracing is off in unit tests (the real keys in .env would otherwise ship test data to Langfuse)."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "0")


@pytest.fixture(autouse=True)
def _unit_tests_never_reach_the_real_supabase(monkeypatch):
    try:
        from app.memory.client import supabase
    except Exception:  # no credentials configured: there is nothing to protect
        return

    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "a unit test tried to use the REAL Supabase client -- patch the memory call it reaches, "
            "or use tests.memfake.MemFake"
        )

    monkeypatch.setattr(supabase, "table", refuse)
    monkeypatch.setattr(supabase, "rpc", refuse)
