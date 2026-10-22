"""Guards that keep ad-hoc scripts from writing to the shared database.

Policy
------
The Supabase project used during development is a shared, long-lived database.
Test scripts must not add rows to it by accident, and ``pytest`` must never
run a script that writes.

Therefore every write-capable script defaults to **dry run**. Writes are only
performed when ``MEMORY_ALLOW_WRITE`` is set to a truthy value in the
environment::

    MEMORY_ALLOW_WRITE=1 .\\.venv\\Scripts\\python.exe test_memory.py

Read-only operations are always allowed. The guard lives outside
``app.memory`` so that the memory layer itself stays free of test concerns.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

WRITE_ENV_VAR = "MEMORY_ALLOW_WRITE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def writes_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Return ``True`` when the environment explicitly allows database writes."""
    env = os.environ if environ is None else environ
    return env.get(WRITE_ENV_VAR, "").strip().lower() in _TRUTHY


def describe_write_policy() -> str:
    """Return a human readable description of the current write policy."""
    if writes_enabled():
        return (
            f"WRITE MODE enabled ({WRITE_ENV_VAR} is set): "
            "statements WILL modify the configured Supabase project."
        )
    return (
        f"DRY RUN: no statements will be written. "
        f"Set {WRITE_ENV_VAR}=1 to allow writes."
    )
