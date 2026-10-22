"""Bridge between the Python memory layer and the atomic SQL functions.

Every write goes through a Postgres function so that the mutation and its
provenance edge are committed in a single transaction. This module owns the
translation of database errors into the typed exceptions of
:mod:`app.memory.errors`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from postgrest.exceptions import APIError

from . import errors
from .client import supabase as default_client

MIGRATION_HINT = (
    "The memory SQL functions are not installed. Apply "
    "migrations/001_harden_schema.sql and migrations/002_memory_functions.sql "
    "in the Supabase SQL editor (and reload the PostgREST schema cache)."
)

#: SQLSTATE -> exception class. The MEMxx codes are raised by the functions in
#: migrations/002_memory_functions.sql.
_ERROR_MAP = {
    "P0002": errors.MemoryNotFound,
    "MEM01": errors.AgentNotRegistered,
    "MEM02": errors.ShareNotAllowed,
    "MEM03": errors.HopLimitExceeded,
    "MEM04": errors.DuplicateMemory,
    "MEM05": errors.SelfShareNotAllowed,
    "23503": errors.AgentNotRegistered,
    "23505": errors.DuplicateMemory,
    "23514": errors.ConstraintViolation,
}


def translate_error(exc: APIError, function: str) -> errors.MemoryError:
    """Map a PostgREST error onto a typed memory error."""
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc)

    if code in ("PGRST202", "PGRST203"):
        return errors.MemorySchemaMissing(f"{function}: {MIGRATION_HINT}")

    error_type = _ERROR_MAP.get(code)
    if error_type is None:
        return errors.MemoryError(f"{function} failed ({code}): {message}")

    return error_type(f"{function} failed ({code}): {message}")


def call_rpc(
    function: str,
    params: Mapping[str, Any],
    *,
    client: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Call a Postgres function and return its rows.

    Raises a typed :class:`app.memory.errors.MemoryError` subclass on failure.
    """
    active_client = client if client is not None else default_client

    try:
        response = active_client.rpc(function, dict(params)).execute()
    except APIError as exc:
        raise translate_error(exc, function) from exc

    return response.data or []


def call_rpc_one(
    function: str,
    params: Mapping[str, Any],
    *,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Call a Postgres function that returns exactly one row."""
    rows = call_rpc(function, params, client=client)

    if not rows:
        raise errors.MemoryError(f"{function} returned no row")

    return rows[0]
