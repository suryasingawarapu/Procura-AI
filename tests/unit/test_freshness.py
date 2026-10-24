"""Unit tests for freshness/staleness checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.memory.freshness import check_freshness, is_fresh, is_stale


def test_fresh_active_memory_no_expiry() -> None:
    memory = {"status": "active", "id": "mem-1"}
    assert check_freshness(memory) == "fresh"
    assert is_fresh(memory) is True
    assert is_stale(memory) is False


def test_stale_superseded_memory() -> None:
    memory = {"status": "superseded", "id": "mem-1"}
    assert check_freshness(memory) == "stale"
    assert is_stale(memory) is True


def test_stale_archived_memory() -> None:
    memory = {"status": "archived", "id": "mem-1"}
    assert check_freshness(memory) == "stale"


def test_stale_expired_memory() -> None:
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=1)).isoformat()
    memory = {"status": "active", "valid_until": past}
    assert check_freshness(memory, now=now) == "stale"


def test_fresh_not_yet_expired() -> None:
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    memory = {"status": "active", "valid_until": future}
    assert check_freshness(memory, now=now) == "fresh"


def test_unknown_missing_status_and_validity() -> None:
    memory = {"id": "mem-1"}
    assert check_freshness(memory) == "unknown"


def test_unknown_invalid_datetime() -> None:
    memory = {"status": "active", "valid_until": "not-a-date"}
    assert check_freshness(memory) == "unknown"
