"""Summaries (fresh / partial / stale), summary-first retrieval with a delta,
and stale shared-copy detection across a multi-hop path."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.memory.context import get_relevant_context
from app.memory.staleness import drop_stale_copies, find_stale_copies, refresh_stale_copies
from app.memory.summaries import (
    classify_summary,
    get_active_summary,
    refresh_summary,
    save_summary,
    summarize_text,
    summary_state,
)
from tests.memfake import MemFake

T = lambda n: f"2026-03-01T10:00:{n:02d}+00:00"  # noqa: E731


def _mem(db, id, key, content, observed, *, agent="vendra", status="active", **extra):
    fields = dict(
        id=id, agent_id=agent, memory_key=key, content=content, status=status,
        observed_at=T(observed), version=1, hop_depth=0,
    )
    fields.update(extra)
    return db.add("agent_memories", **fields)


# -- classify_summary (pure) ------------------------------------------------------


def _summary(ids, covered=T(5)):
    return {"id": "s1", "agent_id": "vendra", "summary": "x", "memory_ids": ids, "covered_observed_at": covered}


def test_summary_without_coverage_info_is_unknown_and_usable():
    state = classify_summary({"id": "s", "agent_id": "a", "summary": "legacy"}, [], [])
    assert state.state == "unknown" and state.usable


def test_summary_is_fresh_when_everything_it_covers_is_current():
    covered = [{"id": "m1", "status": "active"}, {"id": "m2", "status": "active"}]
    assert classify_summary(_summary(["m1", "m2"]), covered, []).state == "fresh"


def test_summary_is_partial_when_newer_memories_exist():
    covered = [{"id": "m1", "status": "active"}]
    newer = [{"id": "m9", "status": "active"}]
    state = classify_summary(_summary(["m1"]), covered, newer)
    assert state.state == "partial" and state.usable
    assert [m["id"] for m in state.new_memories] == ["m9"]


def test_summary_is_stale_when_a_covered_memory_was_superseded():
    covered = [{"id": "m1", "status": "active"}, {"id": "m2", "status": "superseded"}]
    state = classify_summary(_summary(["m1", "m2"]), covered, [])
    assert state.state == "stale" and not state.usable
    assert state.stale_ids == ["m2"]


def test_summary_is_stale_when_a_covered_memory_expired():
    covered = [{"id": "m1", "status": "active", "valid_until": "2026-01-01T00:00:00+00:00"}]
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert classify_summary(_summary(["m1"]), covered, [], now).state == "stale"


def test_summary_is_stale_when_a_covered_memory_was_deleted():
    assert classify_summary(_summary(["gone"]), [], []).state == "stale"


# -- summary_state against the (fake) database --------------------------------------


def test_summary_state_reads_the_database_and_finds_the_delta():
    db = MemFake()
    _mem(db, "m1", "k1", "one", 1)
    _mem(db, "m2", "k2", "two", 5)
    _mem(db, "m3", "k3", "three", 9)  # newer than the summary
    state = summary_state(_summary(["m1", "m2"], covered=T(5)), client=db)
    assert state.state == "partial"
    assert [m["id"] for m in state.new_memories] == ["m3"]


def test_summary_state_scope_ignores_unrelated_newer_memories():
    db = MemFake()
    _mem(db, "m1", "chat:A:1", "one", 1)
    _mem(db, "m2", "chat:B:1", "another conversation", 9)
    state = summary_state(_summary(["m1"], covered=T(1)), scope_key_prefix="chat:A:", client=db)
    assert state.state == "fresh"


# -- retrieval uses the summary + delta, and skips stale summaries --------------------


def test_retrieval_returns_summary_then_only_the_newer_memories():
    db = MemFake()
    _mem(db, "m1", "k1", "old fact", 1)
    _mem(db, "m2", "k2", "newer fact", 9)
    db.add("agent_memory_summaries", id="s1", agent_id="vendra", summary="old fact summary",
           memory_ids=["m1"], covered_observed_at=T(1), status="active", created_at=T(2))
    result = get_relevant_context("vendra", client=db)
    assert [r.get("summary") or r["content"] for r in result] == ["old fact summary", "newer fact"]


def test_retrieval_skips_a_stale_summary_and_falls_back_to_active_memories():
    db = MemFake()
    _mem(db, "m1", "k1", "old version", 1, status="superseded")
    _mem(db, "m1b", "k1", "new version", 2)
    db.add("agent_memory_summaries", id="s1", agent_id="vendra", summary="mentions the old version",
           memory_ids=["m1"], covered_observed_at=T(1), status="active", created_at=T(3))
    result = get_relevant_context("vendra", client=db)
    assert [r["content"] for r in result] == ["new version"]


# -- summary lifecycle ---------------------------------------------------------------------


def test_refresh_creates_then_reuses_then_folds_in_new_memories_then_rebuilds_when_stale():
    db = MemFake()
    _mem(db, "m1", "k1", "supplier A is compliant", 1)
    _mem(db, "m2", "k2", "supplier B is disputed", 2)

    first = refresh_summary("vendra", client=db)
    assert first["memory_ids"] == ["m1", "m2"] and first["status"] == "active"
    assert "(extractive summary" in first["summary"]  # no LLM given -> labelled fallback

    assert refresh_summary("vendra", client=db)["id"] == first["id"]  # fresh: nothing rewritten

    _mem(db, "m3", "k3", "supplier C is compliant", 9)
    second = refresh_summary("vendra", client=db)
    assert second["id"] != first["id"] and second["memory_ids"] == ["m1", "m2", "m3"]
    assert "supplier A is compliant" in second["summary"]  # folded the old summary in
    assert [s["status"] for s in db.tables["agent_memory_summaries"]] == ["superseded", "active"]

    for row in db.tables["agent_memories"]:  # a covered fact changes
        if row["id"] == "m1":
            row["status"] = "superseded"
    _mem(db, "m1b", "k1", "supplier A is now disputed", 10)
    third = refresh_summary("vendra", client=db)
    assert third["id"] != second["id"]
    assert "m1" not in third["memory_ids"] and "m1b" in third["memory_ids"]
    assert "now disputed" in third["summary"] and "A is compliant" not in third["summary"]


def test_llm_is_used_and_told_to_use_only_the_given_entries():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="Supplier A is compliant.")
    text = summarize_text([{"memory_key": "k", "content": "A is compliant"}], llm=llm)
    assert text == "Supplier A is compliant."
    system = llm.invoke.call_args[0][0][0].content
    assert "ONLY what the memory entries below state" in system


def test_llm_failure_falls_back_to_a_labelled_extractive_summary():
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("provider down")
    assert summarize_text([{"memory_key": "k", "content": "A is compliant"}], llm=llm).startswith("(extractive summary")


def test_saving_a_summary_retires_the_previous_one_for_the_same_scope():
    db = MemFake()
    old = save_summary("coordinator", "old", ["t1", "t2"], T(2), client=db)
    new = save_summary("coordinator", "new", ["t1", "t2", "t3"], T(3), client=db)
    assert get_active_summary("coordinator", containing_memory_id="t1", client=db)["id"] == new["id"]
    assert [s["status"] for s in db.tables["agent_memory_summaries"]] == ["superseded", "active"]
    assert old["id"] != new["id"]


# -- stale shared copies ---------------------------------------------------------------------


SRC = "00000000-0000-0000-0000-000000000001"
COPY_P = "11111111-1111-1111-1111-111111111111"
COPY_F = "22222222-2222-2222-2222-222222222222"


def _chain(db):
    """vendra source -> procura copy (hop 1) -> finora copy (hop 2)."""
    _mem(db, SRC, "supplier_eligibility:X", "X: disputed", 1, agent="vendra")
    _mem(db, COPY_P, "shared_supplier_eligibility:X", "X: disputed", 2, agent="procura",
         hop_depth=1, source_reference=SRC)
    _mem(db, COPY_F, "shared_shared_supplier_eligibility:X", "X: disputed", 3, agent="finora",
         hop_depth=2, source_reference=COPY_P)


def _supersede_source(db):
    for row in db.tables["agent_memories"]:
        if row["id"] == SRC:
            row["status"] = "superseded"


def test_copies_are_not_stale_while_the_whole_chain_is_active():
    db = MemFake()
    _chain(db)
    assert find_stale_copies("procura", client=db) == []
    assert find_stale_copies("finora", client=db) == []


def test_superseding_the_source_makes_both_copies_stale_one_direct_one_upstream():
    db = MemFake()
    _chain(db)
    _supersede_source(db)
    _mem(db, "new-src", "supplier_eligibility:X", "X: compliant", 5, agent="vendra")

    procura = find_stale_copies("procura", client=db)
    finora = find_stale_copies("finora", client=db)
    assert [(i["via"], i["latest_source_id"]) for i in procura] == [("direct", "new-src")]
    assert [i["via"] for i in finora] == ["upstream"]


def test_stale_copies_are_dropped_from_a_list_of_rows_and_other_rows_kept():
    db = MemFake()
    _chain(db)
    _supersede_source(db)
    rows = [r for r in db.tables["agent_memories"] if r["status"] == "active"]
    kept = drop_stale_copies(rows, client=db)
    assert kept == []  # both copies stale; nothing else in the list


def test_a_row_that_is_not_a_copy_is_never_dropped():
    db = MemFake()
    row = _mem(db, "plain", "k", "x", 1)
    assert drop_stale_copies([row], client=db) == [row]


def test_refresh_only_acts_on_direct_stale_copies_and_reshares_the_latest_source():
    db = MemFake()
    _chain(db)
    _supersede_source(db)
    _mem(db, "new-src", "supplier_eligibility:X", "X: compliant", 5, agent="vendra")

    with patch("app.memory.staleness.share_memory", return_value={"id": "new-copy"}) as share:
        assert refresh_stale_copies("procura", client=db) == [{"id": "new-copy"}]
        share.assert_called_once_with("new-src", "procura", memory_key="shared_supplier_eligibility:X", client=db)
        share.reset_mock()
        assert refresh_stale_copies("finora", client=db) == []  # upstream only: refresh procura first
        share.assert_not_called()
