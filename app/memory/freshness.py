"""Freshness and staleness checks for memories.

Distinguishes fresh, stale, and unknown based on available schema fields.
Assumptions (documented, not invented):
- A memory with status 'superseded' is stale (has been replaced by newer version).
- A memory with status 'archived' is stale.
- A memory with valid_until set and in the past (relative to current time) is stale.
- An active memory with no valid_until or valid_until in the future is fresh.
- If required fields are missing or cannot be evaluated, freshness is unknown.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


__all__ = ["check_freshness", "is_fresh", "is_stale"]


def check_freshness(memory: Dict[str, Any], now: datetime | None = None) -> str:
    """Check freshness of a memory.
    
    Returns: 'fresh', 'stale', or 'unknown'
    """
    if not isinstance(memory, dict):
        return "unknown"
    
    status = memory.get("status")
    if status == "superseded" or status == "archived":
        return "stale"
    if status == "stale":
        return "stale"
    
    valid_until = memory.get("valid_until")
    if valid_until is not None:
        try:
            if isinstance(valid_until, str):
                # Try to parse ISO format
                # Handle Z suffix
                if valid_until.endswith("Z"):
                    valid_until_dt = datetime.fromisoformat(valid_until[:-1] + "+00:00")
                else:
                    valid_until_dt = datetime.fromisoformat(valid_until.replace("Z", "+00:00") if "+" not in valid_until and valid_until.count("-") >= 3 else valid_until)
            elif isinstance(valid_until, datetime):
                valid_until_dt = valid_until
            else:
                return "unknown"
            
            if valid_until_dt.tzinfo is None:
                valid_until_dt = valid_until_dt.replace(tzinfo=timezone.utc)
            
            check_time = now if now is not None else datetime.now(timezone.utc)
            if check_time.tzinfo is None:
                check_time = check_time.replace(tzinfo=timezone.utc)
            
            if valid_until_dt < check_time:
                return "stale"
            return "fresh"
        except Exception:
            return "unknown"
    
    if status == "active":
        return "fresh"
    
    return "unknown"


def is_fresh(memory: Dict[str, Any], now: datetime | None = None) -> bool:
    """Return True if memory is fresh."""
    return check_freshness(memory, now) == "fresh"


def is_stale(memory: Dict[str, Any], now: datetime | None = None) -> bool:
    """Return True if memory is stale."""
    return check_freshness(memory, now) == "stale"
