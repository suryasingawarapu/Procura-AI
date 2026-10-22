"""Typed errors raised by the memory layer.

The SQL functions raise a specific ``ERRCODE`` for every condition the
application has to react to. :mod:`app.memory.rpc` maps those codes onto the
exceptions below, so the rest of the code never has to know about SQLSTATE
codes.
"""

from __future__ import annotations


class MemoryError(Exception):
    """Base class for every error raised by the memory layer."""


class MemorySchemaMissing(MemoryError):
    """The M1 SQL functions are not installed in the database.

    Raised when PostgREST reports that a memory function does not exist
    (``PGRST202`` / ``PGRST203``). Apply ``migrations/001_harden_schema.sql``
    and ``migrations/002_memory_functions.sql``.
    """


class MemoryNotFound(MemoryError):
    """No (active) memory matches the request."""


class AgentNotRegistered(MemoryError):
    """The agent is not part of ``memory_agents``."""


class ShareNotAllowed(MemoryError):
    """The sharing policy does not allow this hand-over."""


class HopLimitExceeded(MemoryError):
    """The memory has already travelled the maximum number of hops."""


class SelfShareNotAllowed(MemoryError):
    """An agent tried to share a memory with itself."""


class DuplicateMemory(MemoryError):
    """A key conflict or a uniqueness constraint rejected the write."""


class ConstraintViolation(MemoryError):
    """A check constraint rejected the write."""
