"""Shared pytest fixtures and configuration.

This module makes the project importable without an install step and forces
UTF-8 on the console so that non-ASCII memory content (for example the rupee
sign) can be printed on Windows without raising ``UnicodeEncodeError``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.console import enable_utf8_console  # noqa: E402

enable_utf8_console()


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Absolute path of the project root."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def supabase_client():
    """A Supabase client for read-only integration tests.

    The client is created by :mod:`app.memory.store`, which loads the
    credentials from the local ``.env`` file. No credentials are ever read,
    printed or asserted on by the tests.
    """
    from app.memory.store import supabase

    return supabase
