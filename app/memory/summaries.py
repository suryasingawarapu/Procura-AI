"""Summaries of an agent's memories, and the rules for trusting them.

A summary row (``agent_memory_summaries``) records which memories it was
written from (``memory_ids``) and how far in time it reaches
(``covered_observed_at``). That is enough to classify it at read time:

* ``fresh``   -- every covered memory is still active and nothing newer exists.
* ``partial`` -- still correct for what it covers, but newer memories exist.
  Use it together with those newer memories (the "delta").
* ``stale``   -- a covered memory was superseded, archived or has expired, so
  the summary may state something that is no longer true. Do not use it.
* ``unknown`` -- a legacy row with no coverage information; used as-is.

Nothing here invents content: a summary is written by an LLM instructed to use
only the memories it is given, or, when no LLM is available, by a plain
extractive fallback that is labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.observability import langfuse_tracing as tracing

from .client import supabase
from .embeddings import embed_or_none
from .formatting import format_embedding, format_timestamp
from .freshness import check_freshness

__all__ = [
    "SummaryState",
    "classify_summary",
    "summary_state",
    "get_active_summary",
    "save_summary",
    "summarize_text",
    "refresh_summary",
    "scoped_context",
]

MAX_SUMMARY_WORDS = 150

_SYSTEM_PROMPT = (
    "You maintain the long-term memory summary of an AI agent. Summarize ONLY what the "
    "memory entries below state, in at most {max_words} words of plain prose. Keep every "
    "concrete identifier, name, number and decision exactly as written. Never add, infer or "
    "guess anything that is not in the entries. If a previous summary is given, fold it "
    "together with the new entries into one summary."
)


@dataclass
class SummaryState:
    state: str  # fresh | partial | stale | unknown
    reason: str = ""
    stale_ids: List[str] = field(default_factory=list)
    new_memories: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.state != "stale"


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def classify_summary(
    summary: Dict[str, Any],
    covered_rows: List[Dict[str, Any]],
    newer_rows: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> SummaryState:
    """Pure classification: no database access. ``covered_rows`` are the
    current rows for ``summary['memory_ids']``; ``newer_rows`` are active
    memories observed after ``covered_observed_at``."""
    covered_ids = list(summary.get("memory_ids") or [])
    if not covered_ids and summary.get("covered_observed_at") is None:
        return SummaryState("unknown", "no coverage information on this summary")

    by_id = {row["id"]: row for row in covered_rows}
    stale_ids: List[str] = []
    for memory_id in covered_ids:
        row = by_id.get(memory_id)
        if row is None:
            stale_ids.append(memory_id)  # deleted: the summary mentions something that is gone
        elif check_freshness(row, now) == "stale":
            stale_ids.append(memory_id)
    if stale_ids:
        return SummaryState(
            "stale",
            f"{len(stale_ids)} of {len(covered_ids)} covered memories are no longer current",
            stale_ids=stale_ids,
        )

    covered_ids_set = set(covered_ids)
    new_memories = [row for row in newer_rows if row.get("id") not in covered_ids_set]
    if new_memories:
        return SummaryState("partial", f"{len(new_memories)} newer memories are not covered", new_memories=new_memories)
    return SummaryState("fresh", "covers every current memory")


def summary_state(
    summary: Dict[str, Any],
    *,
    scope_key_prefix: Optional[str] = None,
    client: Any = None,
    now: Optional[datetime] = None,
) -> SummaryState:
    """Look up what ``classify_summary`` needs and classify ``summary``.
    ``scope_key_prefix`` limits "newer memories" to one scope (for example a
    single conversation), so unrelated memories never make a summary look
    partial."""
    covered_ids = list(summary.get("memory_ids") or [])
    covered_at = summary.get("covered_observed_at")
    if not covered_ids and covered_at is None:
        return classify_summary(summary, [], [], now)

    active_client = client if client is not None else supabase

    covered_rows: List[Dict[str, Any]] = []
    if covered_ids:
        covered_rows = (
            active_client.table("agent_memories")
            .select("id,status,valid_until")
            .in_("id", covered_ids)
            .execute()
            .data
            or []
        )

    newer_rows: List[Dict[str, Any]] = []
    if covered_at is not None:
        query = (
            active_client.table("agent_memories")
            .select("*")
            .eq("agent_id", summary["agent_id"])
            .eq("status", "active")
            .gt("observed_at", format_timestamp(covered_at))
            .order("observed_at")
        )
        if scope_key_prefix:
            query = query.like("memory_key", scope_key_prefix + "%")
        newer_rows = query.execute().data or []

    return classify_summary(summary, covered_rows, newer_rows, now)


def get_active_summary(
    agent_id: str,
    *,
    containing_memory_id: Optional[str] = None,
    client: Any = None,
) -> Optional[Dict[str, Any]]:
    """The newest active summary of an agent, or of the scope that includes
    ``containing_memory_id`` (for example one conversation's first turn)."""
    active_client = client if client is not None else supabase
    query = (
        active_client.table("agent_memory_summaries")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
    )
    if containing_memory_id:
        query = query.contains("memory_ids", [containing_memory_id])
    rows = query.execute().data or []
    return rows[0] if rows else None


def save_summary(
    agent_id: str,
    summary_text: str,
    memory_ids: List[str],
    covered_observed_at: Any,
    *,
    embedding: Any = None,
    client: Any = None,
) -> Dict[str, Any]:
    """Store a summary and retire the previous active one for the same scope.

    The new row is written first, so a failure part-way leaves two active
    summaries (the newest wins) rather than none."""
    active_client = client if client is not None else supabase
    row = {
        "agent_id": agent_id,
        "summary": summary_text,
        "memory_ids": list(memory_ids),
        "covered_observed_at": format_timestamp(covered_observed_at),
        "status": "active",
    }
    vector = format_embedding(embedding)
    if vector is not None:
        row["embedding"] = vector
    saved = (active_client.table("agent_memory_summaries").insert(row).execute().data or [row])[0]

    if memory_ids and saved.get("id"):
        previous = (
            active_client.table("agent_memory_summaries")
            .select("id")
            .eq("agent_id", agent_id)
            .eq("status", "active")
            .contains("memory_ids", [memory_ids[0]])
            .execute()
            .data
            or []
        )
        old_ids = [r["id"] for r in previous if r["id"] != saved["id"]]
        if old_ids:
            active_client.table("agent_memory_summaries").update({"status": "superseded"}).in_("id", old_ids).execute()
    return saved


def _extractive(memories: List[Dict[str, Any]], previous: Optional[str]) -> str:
    parts = [previous] if previous else []
    parts += [" ".join(str(m.get("content", "")).split())[:240] for m in memories]
    text = " | ".join(p for p in parts if p)
    return "(extractive summary, no LLM available) " + text[:1200]


def summarize_text(
    memories: List[Dict[str, Any]],
    *,
    previous_summary: Optional[str] = None,
    llm: Any = None,
    max_words: int = MAX_SUMMARY_WORDS,
) -> str:
    """Summary text for ``memories``. Uses ``llm`` when given; any failure
    (or an empty reply) falls back to a labelled extractive summary."""
    if llm is not None:
        try:
            from langchain_core.messages import HumanMessage
            from langchain_core.messages import SystemMessage as LCSystemMessage

            entries = "\n".join(f"- [{m.get('memory_key')}] {m.get('content')}" for m in memories)
            user = (f"Previous summary:\n{previous_summary}\n\n" if previous_summary else "") + f"Memory entries:\n{entries}"
            reply = llm.invoke(
                [LCSystemMessage(content=_SYSTEM_PROMPT.format(max_words=max_words)), HumanMessage(content=user)],
                **tracing.llm_kwargs("Memory Summary"),
            )
            text = str(getattr(reply, "content", "") or "").strip()
            if text:
                return text
        except Exception:
            pass
    return _extractive(memories, previous_summary)


def _scope_memories(agent_id: str, scope_key_prefix: Optional[str], limit: int, client: Any) -> List[Dict[str, Any]]:
    query = (
        client.table("agent_memories")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("status", "active")
        .order("observed_at")
    )
    if scope_key_prefix:
        query = query.like("memory_key", scope_key_prefix + "%")
    rows = query.execute().data or []
    rows = [r for r in rows if check_freshness(r) != "stale"]
    return rows[-limit:]


def refresh_summary(
    agent_id: str,
    *,
    scope_key_prefix: Optional[str] = None,
    llm: Any = None,
    min_new: int = 1,
    limit: int = 60,
    client: Any = None,
) -> Optional[Dict[str, Any]]:
    """Create the summary for an agent (or one scope of it), or bring it up
    to date, and return the active summary row.

    * no summary yet            -> summarize the current memories
    * fresh                     -> nothing to do
    * partial (newer memories)  -> fold the new ones into the existing text
    * stale                     -> rebuild from the current memories
    """
    active_client = client if client is not None else supabase
    memories = _scope_memories(agent_id, scope_key_prefix, limit, active_client)
    if not memories:
        return None

    current = get_active_summary(
        agent_id, containing_memory_id=memories[0]["id"] if scope_key_prefix else None, client=active_client
    )
    state = summary_state(current, scope_key_prefix=scope_key_prefix, client=active_client) if current else None

    if current and state.state in ("fresh", "unknown"):
        return current
    if current and state.state == "partial" and len(state.new_memories) < min_new:
        return current

    if current and state.state == "partial":
        previous_text = current["summary"]
        new = state.new_memories
        text = summarize_text(new, previous_summary=previous_text, llm=llm)
        ids = list(current.get("memory_ids") or []) + [m["id"] for m in new]
        covered = max(_parse_ts(m.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc) for m in new)
    else:
        text = summarize_text(memories, llm=llm)
        ids = [m["id"] for m in memories]
        covered = max(_parse_ts(m.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc) for m in memories)

    return save_summary(agent_id, text, ids, covered, embedding=embed_or_none(text), client=active_client)


def scoped_context(
    agent_id: str,
    scope_key_prefix: str,
    *,
    limit: int = 60,
    client: Any = None,
) -> Dict[str, Any]:
    """Summary-first retrieval for one scope of an agent's memory (for example
    one conversation, or one item's quotation history).

    Returns the usable summary (if any) together with the *records still to be
    read*: only those newer than the summary, so the caller reads a compact
    summary plus what changed instead of everything. With no summary, or a
    stale one (never used), the records are all the current memories in scope.

    ``{"summary": row | None, "state": SummaryState | None, "records": [...],
    "scope_size": n}``"""
    active_client = client if client is not None else supabase
    memories = _scope_memories(agent_id, scope_key_prefix, limit, active_client)
    if not memories:
        return {"summary": None, "state": None, "records": [], "scope_size": 0}
    summary = get_active_summary(agent_id, containing_memory_id=memories[0]["id"], client=active_client)
    if summary is None:
        return {"summary": None, "state": None, "records": memories, "scope_size": len(memories)}
    state = summary_state(summary, scope_key_prefix=scope_key_prefix, client=active_client)
    if state.usable:
        return {"summary": summary, "state": state, "records": state.new_memories, "scope_size": len(memories)}
    return {"summary": None, "state": state, "records": memories, "scope_size": len(memories)}
