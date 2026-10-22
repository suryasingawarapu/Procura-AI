"""Serialisation helpers for values that are sent to Postgres functions.

The SQL functions accept the embedding and the timestamps as text so that
PostgREST never has to coerce a JSON array or object into a custom type.
These helpers turn Python values into that text form.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence


def format_embedding(embedding: Any) -> Optional[str]:
    """Return the embedding in the text form the SQL function expects.

    Accepts a string (``"[0.1,0.2]"``) or a sequence of numbers and returns
    ``"[0.1,0.2]"``. ``None`` stays ``None``.
    """
    if embedding is None:
        return None
    if isinstance(embedding, str):
        return embedding
    if isinstance(embedding, Sequence):
        return "[" + ",".join(str(float(value)) for value in embedding) + "]"
    raise TypeError(f"unsupported embedding type: {type(embedding).__name__}")


def format_timestamp(value: Any) -> Optional[str]:
    """Return a timestamp as an ISO string, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
