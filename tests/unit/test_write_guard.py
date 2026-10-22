"""Tests for the database write guard.

The guard keeps the ad-hoc scripts from adding rows to the shared Supabase
project unless writes are explicitly enabled.
"""

from __future__ import annotations

import pytest

from app.utils.writes import WRITE_ENV_VAR, describe_write_policy, writes_enabled


def test_writes_are_disabled_by_default() -> None:
    assert writes_enabled({}) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_truthy_values_enable_writes(value: str) -> None:
    assert writes_enabled({WRITE_ENV_VAR: value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_falsy_values_keep_dry_run(value: str) -> None:
    assert writes_enabled({WRITE_ENV_VAR: value}) is False


def test_describe_write_policy_reports_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)
    message = describe_write_policy()
    assert "DRY RUN" in message
    assert WRITE_ENV_VAR in message


def test_describe_write_policy_reports_write_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    message = describe_write_policy()
    assert "WRITE MODE" in message
