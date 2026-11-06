"""Persistent conversation memory for the chat front door.

Every chat turn is stored as one immutable memory under the ``coordinator``
identity (key ``chat:<conversation_id>:<turn>:<suffix>``). Once a few turns
have accumulated they are folded into a rolling summary
(``agent_memory_summaries``). When the router needs context it gets the
summary plus the turns that came after it -- not just whatever the browser
happened to send -- so a long conversation is compressed instead of cut off,
and it survives a closed tab or a restarted server.

Nothing here is used in demo mode (the chat route skips it), so demo data
never reaches the real database.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from app.memory.client import supabase
from app.memory.embeddings import embed_or_none
from app.memory.provenance_fields import enrich_memory
from app.memory.store import save_memory
from app.memory.summaries import refresh_summary, scoped_context

from . import progress
from .identities import COORDINATOR

__all__ = [
    "valid_conversation_id",
    "record_turn",
    "conversation_context",
    "format_for_prompt",
    "maybe_summarize",
    "delete_conversation",
    "SUMMARY_EVERY",
]

SUMMARY_EVERY = 4  # fold turns into the summary once this many are uncovered
RECENT_TURNS = 6  # newest turns shown verbatim next to the summary
MAX_TURN_CHARS = 2000

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def valid_conversation_id(value: Optional[str]) -> bool:
    return bool(value and _ID_RE.match(value))


def _prefix(conversation_id: str) -> str:
    return f"chat:{conversation_id}:"


def _turns(conversation_id: str, client: Any) -> List[Dict[str, Any]]:
    return (
        client.table("agent_memories")
        .select("*")
        .eq("agent_id", COORDINATOR)
        .eq("status", "active")
        .like("memory_key", _prefix(conversation_id) + "%")
        .order("observed_at")
        .execute()
        .data
        or []
    )


def record_turn(
    conversation_id: str,
    user_message: str,
    assistant_reply: str,
    *,
    task_id: Optional[str] = None,
    client: Any = None,
) -> Dict[str, Any]:
    """Store one user/assistant exchange as a memory, tagged with its source
    (``chat``), the task it concerned (if any) and its verification status
    (``unverified``: it is what was said, not something checked)."""
    active_client = client if client is not None else supabase
    number = len(_turns(conversation_id, active_client)) + 1
    key = f"{_prefix(conversation_id)}{number:04d}:{uuid.uuid4().hex[:6]}"
    content = f"User: {user_message.strip()}\nAssistant: {assistant_reply.strip()}"[:MAX_TURN_CHARS]
    saved = save_memory(
        COORDINATOR,
        key,
        content,
        source_agent_id=COORDINATOR,
        embedding=embed_or_none(content),
        client=active_client,
    )
    enrich_memory(
        saved["id"],
        client=active_client,
        source_type="chat",
        task_id=task_id,
        change_reason="chat turn",
        verification_status="unverified",
        sharing_classification="internal",
    )
    progress.emit("memory", f"Conversation turn {number} saved to persistent memory ({key.split(':', 2)[2]})")
    return saved


def conversation_context(conversation_id: str, *, client: Any = None) -> Dict[str, Any]:
    """What the router should know about this conversation so far:
    ``{"summary": text | None, "summary_state": ..., "recent": [turn texts]}``.

    Summary-first: a usable summary is returned with only the turns written
    after it; if there is no summary, or it has gone stale, the latest turns
    are returned verbatim instead."""
    ctx = scoped_context(COORDINATOR, _prefix(conversation_id), client=client if client is not None else supabase)
    summary, state = ctx["summary"], ctx["state"]
    return {
        "summary": summary["summary"] if summary else None,
        "summary_state": state.state if state else None,
        "recent": [m["content"] for m in ctx["records"]][-RECENT_TURNS:],
    }


def format_for_prompt(context: Dict[str, Any]) -> Optional[str]:
    """The conversation context as the text block the router prompt carries,
    or ``None`` when there is nothing stored yet."""
    if not context.get("summary") and not context.get("recent"):
        return None
    lines: List[str] = []
    if context.get("summary"):
        lines.append(f"Summary of earlier turns: {context['summary']}")
    if context.get("recent"):
        lines.append("Most recent turns" + (" since that summary" if context.get("summary") else "") + ":")
        lines += [f"- {text}" for text in context["recent"]]
    return "\n".join(lines)


def maybe_summarize(conversation_id: str, *, llm: Any = None, every: int = SUMMARY_EVERY, client: Any = None):
    """Fold the conversation's uncovered turns into its summary once at least
    ``every`` of them exist (or the summary went stale). Returns the active
    summary row, or ``None`` when there was nothing to do yet."""
    active_client = client if client is not None else supabase
    turns = _turns(conversation_id, active_client)
    if len(turns) < every:
        return None
    return refresh_summary(
        COORDINATOR,
        scope_key_prefix=_prefix(conversation_id),
        llm=llm,
        min_new=every,
        client=active_client,
    )


def delete_conversation(conversation_id: str, *, client: Any = None) -> Dict[str, int]:
    """Remove everything stored for one conversation: its turns and every
    summary that covers them. Real deletion, not hiding."""
    active_client = client if client is not None else supabase
    rows = (
        active_client.table("agent_memories")
        .select("id")
        .eq("agent_id", COORDINATOR)
        .like("memory_key", _prefix(conversation_id) + "%")
        .order("observed_at")
        .execute()
        .data
        or []
    )
    ids = [r["id"] for r in rows]
    summaries = 0
    if ids:
        # Every summary of a conversation covers its first (oldest) turn.
        found = (
            active_client.table("agent_memory_summaries")
            .select("id")
            .eq("agent_id", COORDINATOR)
            .contains("memory_ids", [ids[0]])
            .execute()
            .data
            or []
        )
        summary_ids = sorted({s["id"] for s in found})
        if summary_ids:
            active_client.table("agent_memory_summaries").delete().in_("id", summary_ids).execute()
            summaries = len(summary_ids)
        for column in ("memory_id", "parent_memory_id"):
            active_client.table("memory_provenance").delete().in_(column, ids).execute()
        active_client.table("agent_memories").delete().in_("id", ids).execute()
    return {"turns": len(ids), "summaries": summaries}
