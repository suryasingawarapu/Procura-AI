"""Stale shared copies.

When an agent shares a memory, the receiver gets its own copy (a new row,
``hop_depth`` > 0, ``source_reference`` = the id of the row it was copied
from). If the source is later superseded, the copy still says what the old
version said -- and so does a copy *of that copy*, further down the path.
These helpers find such copies and bring them up to date.

Detection is computed at read time by walking the source chain; nothing is
written to the copy, so the existing "refresh" path in ``share_memory_atomic``
(which supersedes an *active* copy) keeps working unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from .client import supabase
from .propagation import share_memory

__all__ = ["find_stale_copies", "drop_stale_copies", "refresh_stale_copies"]

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_MAX_CHAIN = 6
_SOURCE_COLUMNS = "id,agent_id,memory_key,version,status,hop_depth,source_reference"


def _source_id(row: Dict[str, Any]) -> Optional[str]:
    ref = row.get("source_reference")
    return ref if isinstance(ref, str) and _UUID_RE.match(ref) else None


def _fetch(ids: Iterable[str], client: Any) -> Dict[str, Dict[str, Any]]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = client.table("agent_memories").select(_SOURCE_COLUMNS).in_("id", wanted).execute().data or []
    return {row["id"]: row for row in rows}


def _first_stale_ancestor(copies: List[Dict[str, Any]], client: Any) -> Dict[str, Dict[str, Any]]:
    """For each copy, the nearest ancestor that is no longer active (``via``
    says whether it is the copy's direct source or one further up). Copies
    whose whole chain is active are absent from the result."""
    pending = {c["id"]: (c, _source_id(c), 1) for c in copies if _source_id(c)}
    found: Dict[str, Dict[str, Any]] = {}
    for _ in range(_MAX_CHAIN):
        if not pending:
            break
        rows = _fetch((src for _c, src, _d in pending.values()), client)
        next_pending = {}
        for copy_id, (copy, src, depth) in pending.items():
            row = rows.get(src)
            if row is None:
                continue
            if row.get("status") != "active":
                found[copy_id] = {"source": row, "via": "direct" if depth == 1 else "upstream"}
            elif (row.get("hop_depth") or 0) > 0 and _source_id(row):
                next_pending[copy_id] = (copy, _source_id(row), depth + 1)
        pending = next_pending
    return found


def drop_stale_copies(rows: List[Dict[str, Any]], *, client: Any = None) -> List[Dict[str, Any]]:
    """``rows`` without any shared copy that is outdated (at any depth of its
    source chain). Rows that are not copies are kept."""
    copies = [r for r in rows if (r.get("hop_depth") or 0) > 0 and _source_id(r)]
    if not copies:
        return rows
    stale = _first_stale_ancestor(copies, client if client is not None else supabase)
    return [r for r in rows if r.get("id") not in stale]


def find_stale_copies(agent_id: str, *, client: Any = None) -> List[Dict[str, Any]]:
    """Active shared copies in ``agent_id``'s space that are outdated. Each
    item has the ``copy`` row, the superseded ``source`` it was found at,
    ``via`` ("direct" or "upstream") and ``latest_source_id`` (that source
    key's current active row, if any)."""
    active_client = client if client is not None else supabase
    copies = (
        active_client.table("agent_memories")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("status", "active")
        .gt("hop_depth", 0)
        .execute()
        .data
        or []
    )
    stale: List[Dict[str, Any]] = []
    for copy_id, info in _first_stale_ancestor(copies, active_client).items():
        source = info["source"]
        latest = (
            active_client.table("agent_memories")
            .select("id")
            .eq("agent_id", source["agent_id"])
            .eq("memory_key", source["memory_key"])
            .eq("status", "active")
            .limit(1)
            .execute()
            .data
            or []
        )
        stale.append(
            {
                "copy": next(c for c in copies if c["id"] == copy_id),
                "source": source,
                "via": info["via"],
                "latest_source_id": latest[0]["id"] if latest else None,
            }
        )
    return stale


def refresh_stale_copies(agent_id: str, *, client: Any = None) -> List[Dict[str, Any]]:
    """Re-share the current version of every stale copy's *direct* source.
    The database records each refresh as a new version of the copy, with a
    ``supersedes`` edge and a fresh ``derived_from`` edge. Copies that are
    only stale because something further upstream changed are skipped: refresh
    the upstream agent's copy first, then call this again for the next agent.
    Copies whose source key has no active row are left alone."""
    refreshed: List[Dict[str, Any]] = []
    for item in find_stale_copies(agent_id, client=client):
        if item["via"] != "direct" or not item["latest_source_id"]:
            continue
        refreshed.append(
            share_memory(
                item["latest_source_id"],
                agent_id,
                memory_key=item["copy"]["memory_key"],
                client=client,
            )
        )
    return refreshed
