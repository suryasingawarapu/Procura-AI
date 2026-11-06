"""Conversation memory: storing turns, summarizing, context for the router,
and real deletion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.procurement import chat_memory, progress
from tests.memfake import MemFake

CONV = "conv-test-1"


@pytest.fixture
def db():
    return MemFake()


def _fake_save(db):
    """Stand-in for app.memory.store.save_memory that writes to the fake."""
    counter = {"n": 0}

    def save(agent_id, key, content, **_kw):
        counter["n"] += 1
        return db.add(
            "agent_memories", agent_id=agent_id, memory_key=key, content=content, status="active", version=1,
            hop_depth=0, observed_at=f"2026-03-01T10:00:{counter['n']:02d}+00:00",
        )

    return save


@pytest.fixture
def stored(db):
    with patch("app.procurement.chat_memory.save_memory", side_effect=_fake_save(db)), patch(
        "app.procurement.chat_memory.enrich_memory"
    ) as enrich, patch("app.procurement.chat_memory.embed_or_none", return_value=None):
        yield enrich


def test_valid_conversation_ids():
    assert chat_memory.valid_conversation_id("conv-1730000000-abc123")
    assert not chat_memory.valid_conversation_id("")
    assert not chat_memory.valid_conversation_id(None)
    assert not chat_memory.valid_conversation_id("has space")
    assert not chat_memory.valid_conversation_id("../etc")
    assert not chat_memory.valid_conversation_id("x" * 81)


def test_record_turn_stores_the_exchange_under_the_coordinator_with_provenance_tags(db, stored):
    saved = chat_memory.record_turn(CONV, "Need chairs", "Which item code?", task_id="PR-1", client=db)

    assert saved["agent_id"] == "coordinator"
    assert saved["memory_key"].startswith(f"chat:{CONV}:0001:")
    assert saved["content"] == "User: Need chairs\nAssistant: Which item code?"
    kwargs = stored.call_args.kwargs
    assert kwargs["source_type"] == "chat" and kwargs["task_id"] == "PR-1"
    assert kwargs["verification_status"] == "unverified"  # it is what was said, not something checked


def test_turn_numbers_increase_and_keys_never_collide(db, stored):
    keys = [chat_memory.record_turn(CONV, f"m{i}", "r", client=db)["memory_key"] for i in range(3)]
    assert [k.split(":")[2] for k in keys] == ["0001", "0002", "0003"]
    assert len(set(keys)) == 3


def test_record_turn_reports_progress_for_the_live_trace(db, stored):
    seen = []
    with progress.capture(lambda stage, text: seen.append((stage, text))):
        chat_memory.record_turn(CONV, "hi", "hello", client=db)
    assert seen and seen[0][0] == "memory" and "Conversation turn 1 saved" in seen[0][1]


def test_very_long_turns_are_truncated(db, stored):
    saved = chat_memory.record_turn(CONV, "x" * 5000, "y", client=db)
    assert len(saved["content"]) == chat_memory.MAX_TURN_CHARS


def test_context_is_empty_for_an_unknown_conversation(db):
    ctx = chat_memory.conversation_context("nothing-here", client=db)
    assert ctx == {"summary": None, "summary_state": None, "recent": []}
    assert chat_memory.format_for_prompt(ctx) is None


def test_without_a_summary_context_is_the_latest_turns_verbatim(db, stored):
    for i in range(8):
        chat_memory.record_turn(CONV, f"question {i}", f"answer {i}", client=db)
    ctx = chat_memory.conversation_context(CONV, client=db)
    assert ctx["summary"] is None and len(ctx["recent"]) == chat_memory.RECENT_TURNS
    assert ctx["recent"][-1].startswith("User: question 7")


def test_context_is_other_conversations_proof(db, stored):
    chat_memory.record_turn("other-conv", "secret from another conversation", "ok", client=db)
    chat_memory.record_turn(CONV, "mine", "ok", client=db)
    ctx = chat_memory.conversation_context(CONV, client=db)
    assert len(ctx["recent"]) == 1 and "secret" not in " ".join(ctx["recent"])


def test_maybe_summarize_waits_for_enough_turns_then_summarizes(db, stored):
    for i in range(3):
        chat_memory.record_turn(CONV, f"q{i}", f"a{i}", client=db)
    assert chat_memory.maybe_summarize(CONV, every=4, client=db) is None

    chat_memory.record_turn(CONV, "q3", "a3", client=db)
    with patch("app.memory.summaries.embed_or_none", return_value=None):
        summary = chat_memory.maybe_summarize(CONV, every=4, client=db)
    assert summary is not None and len(summary["memory_ids"]) == 4


def test_context_after_a_summary_is_the_summary_plus_only_newer_turns(db, stored):
    for i in range(4):
        chat_memory.record_turn(CONV, f"q{i}", f"a{i}", client=db)
    with patch("app.memory.summaries.embed_or_none", return_value=None):
        chat_memory.maybe_summarize(CONV, every=4, client=db)

    fresh = chat_memory.conversation_context(CONV, client=db)
    assert fresh["summary"] and fresh["recent"] == [] and fresh["summary_state"] == "fresh"

    chat_memory.record_turn(CONV, "after the summary", "ok", client=db)
    partial = chat_memory.conversation_context(CONV, client=db)
    assert partial["summary_state"] == "partial" and len(partial["recent"]) == 1
    assert "after the summary" in partial["recent"][0]

    block = chat_memory.format_for_prompt(partial)
    assert block.startswith("Summary of earlier turns:") and "Most recent turns since that summary:" in block


def test_a_stale_summary_is_not_used_and_verbatim_turns_are_returned(db, stored):
    for i in range(4):
        chat_memory.record_turn(CONV, f"q{i}", f"a{i}", client=db)
    with patch("app.memory.summaries.embed_or_none", return_value=None):
        chat_memory.maybe_summarize(CONV, every=4, client=db)
    db.tables["agent_memories"][0]["status"] = "superseded"  # something the summary covers changed

    ctx = chat_memory.conversation_context(CONV, client=db)
    assert ctx["summary"] is None and ctx["summary_state"] == "stale"


def test_delete_removes_turns_summaries_and_their_provenance_but_not_other_conversations(db, stored):
    for i in range(4):
        chat_memory.record_turn(CONV, f"q{i}", f"a{i}", client=db)
    chat_memory.record_turn("keep-me", "other", "ok", client=db)
    with patch("app.memory.summaries.embed_or_none", return_value=None):
        chat_memory.maybe_summarize(CONV, every=4, client=db)
    first = db.tables["agent_memories"][0]["id"]
    db.add("memory_provenance", memory_id=first, parent_memory_id="x", relationship="supersedes")

    result = chat_memory.delete_conversation(CONV, client=db)

    assert result == {"turns": 4, "summaries": 1}
    assert [m["memory_key"].split(":")[1] for m in db.tables["agent_memories"]] == ["keep-me"]
    assert db.tables["agent_memory_summaries"] == []
    assert db.tables["memory_provenance"] == []


def test_deleting_an_unknown_conversation_is_a_harmless_no_op(db):
    assert chat_memory.delete_conversation("never-existed", client=db) == {"turns": 0, "summaries": 0}
