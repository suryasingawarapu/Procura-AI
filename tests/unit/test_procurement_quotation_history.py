"""Procura's quotation history: summary first, then the supporting records,
reporting what changed; a stale summary is never used."""

from __future__ import annotations

from unittest.mock import patch

from app.procurement import quotation_history
from app.procurement.quotation_history import build_history, price_and_delivery_changes
from app.procurement.subagents.procura import quotation_history as history_specialist
from tests.memfake import MemFake

ITEM = "LAPTOP-14IN"
H = "app.procurement.quotation_history"


def _claim(db, id, supplier, doc, price, version=1, status="active", delivery=10, observed=1):
    return db.add(
        "agent_memories", id=id, agent_id="procura", memory_key=f"quote:{ITEM}:{supplier}:erpnext_doctype:{doc}",
        subject_key=f"quote:{ITEM}:{supplier}", status=status, version=version, content=f"{supplier} {price}",
        observed_at=f"2026-03-01T10:00:{observed:02d}+00:00", created_at=f"2026-03-0{version}T10:00:00+00:00",
        structured_fact={"value": price, "currency": "INR", "supplier": supplier, "source_reference": f"Supplier Quotation/{doc}",
                         "delivery_days": delivery},
    )


def _rows(*suppliers):
    return [{"supplier": s} for s in suppliers]


# -- what changed ----------------------------------------------------------------------------------------------------


def test_a_price_and_a_delivery_change_between_versions_of_one_document_are_reported():
    db = MemFake()
    _claim(db, "v1", "ACME", "Q1", 50000, version=1, status="superseded", delivery=15)
    _claim(db, "v2", "ACME", "Q1", 56000, version=2, delivery=25)
    changes = price_and_delivery_changes(db.tables["agent_memories"])

    price = next(c for c in changes if c["field"] == "price")
    assert (price["supplier"], price["from"], price["to"], price["change_pct"]) == ("ACME", 50000, 56000, 12.0)
    assert price["source_reference"] == "Supplier Quotation/Q1" and price["currency"] == "INR"
    delivery = next(c for c in changes if c["field"] == "delivery_days")
    assert (delivery["from"], delivery["to"]) == (15, 25)


def test_unchanged_versions_and_single_versions_report_nothing():
    db = MemFake()
    _claim(db, "a", "ACME", "Q1", 50000)
    _claim(db, "b", "OTHER", "Q2", 40000, version=1, status="superseded")
    _claim(db, "c", "OTHER", "Q2", 40000, version=2)
    assert price_and_delivery_changes(db.tables["agent_memories"]) == []


def test_changes_are_listed_newest_first_and_capped():
    db = MemFake()
    for i in range(1, 15):
        _claim(db, f"v{i}", "ACME", "Q1", 1000 + i, version=i, status="superseded" if i < 14 else "active")
    changes = price_and_delivery_changes(db.tables["agent_memories"])
    assert len(changes) == quotation_history.MAX_CHANGES


# -- summary first -------------------------------------------------------------------------------------------------------


def _with_db(db):
    return patch(f"{H}.supabase", db)


def test_with_no_summary_yet_the_claims_in_scope_are_read_and_the_supporting_records_opened():
    db = MemFake()
    _claim(db, "a", "ACME", "Q1", 50000)
    _claim(db, "b", "BETA", "Q2", 51000, observed=2)
    with patch(f"{H}._spawn") as spawn:
        out = build_history(item_code=ITEM, quote_rows=_rows("ACME", "BETA"), client=db)
    assert out["summary_used"] is False and out["summary_state"] is None
    assert out["claims_in_scope"] == 2 and out["records_opened"] == 2 and out["changes"] == []
    spawn.assert_not_called()  # too few claims to be worth a summary


def test_a_fresh_summary_is_used_and_only_claims_newer_than_it_are_read():
    db = MemFake()
    _claim(db, "a", "ACME", "Q1", 50000, observed=1)
    _claim(db, "b", "BETA", "Q2", 51000, observed=2)
    _claim(db, "c", "GAMMA", "Q3", 52000, observed=3)
    db.add("agent_memory_summaries", id="s1", agent_id="procura", summary="ACME 50,000; BETA 51,000; GAMMA 52,000",
           memory_ids=["a", "b", "c"], covered_observed_at="2026-03-01T10:00:03+00:00", status="active",
           created_at="2026-03-02T00:00:00+00:00")
    with patch(f"{H}._spawn"):
        out = build_history(item_code=ITEM, quote_rows=_rows("ACME"), client=db)
    assert out["summary_used"] is True and out["summary_state"] == "fresh"
    assert out["summary"].startswith("ACME 50,000") and out["records_read_after_summary"] == 0
    assert out["records_opened"] == 1  # only ACME's supporting records, not the whole history


def test_claims_newer_than_the_summary_are_returned_next_to_it():
    db = MemFake()
    for i, (s, d, p) in enumerate((("ACME", "Q1", 1), ("BETA", "Q2", 2), ("GAMMA", "Q3", 3)), start=1):
        _claim(db, f"m{i}", s, d, p, observed=i)
    db.add("agent_memory_summaries", id="s1", agent_id="procura", summary="three quotes", memory_ids=["m1", "m2", "m3"],
           covered_observed_at="2026-03-01T10:00:03+00:00", status="active", created_at="2026-03-02T00:00:00+00:00")
    _claim(db, "m4", "DELTA", "Q4", 4, observed=9)
    with patch(f"{H}._spawn"):
        out = build_history(item_code=ITEM, quote_rows=_rows("ACME"), client=db)
    assert out["summary_state"] == "partial" and out["records_read_after_summary"] == 1


def test_a_summary_covering_a_claim_that_changed_is_stale_and_is_not_used():
    db = MemFake()
    _claim(db, "a1", "ACME", "Q1", 50000, version=1, status="superseded", observed=1)
    _claim(db, "a2", "ACME", "Q1", 56000, version=2, observed=2)
    _claim(db, "b", "BETA", "Q2", 51000, observed=3)
    _claim(db, "c", "GAMMA", "Q3", 52000, observed=4)
    db.add("agent_memory_summaries", id="s1", agent_id="procura", summary="ACME quoted 50,000", memory_ids=["a1", "b", "c"],
           covered_observed_at="2026-03-01T10:00:04+00:00", status="active", created_at="2026-03-02T00:00:00+00:00")
    with patch(f"{H}._spawn"):
        out = build_history(item_code=ITEM, quote_rows=_rows("ACME"), client=db)
    assert out["summary_used"] is False  # it would have said 50,000; the price is now 56,000
    assert [c["change_pct"] for c in out["changes"] if c["field"] == "price"] == [12.0]


def test_a_summary_refresh_is_started_in_the_background_once_enough_claims_exist():
    db = MemFake()
    for i, s in enumerate(("A", "B", "C"), start=1):
        _claim(db, f"m{i}", s, f"Q{i}", i, observed=i)
    with patch(f"{H}._spawn") as spawn:
        build_history(item_code=ITEM, quote_rows=_rows("A"), client=db)
    spawn.assert_called_once_with(quotation_history._refresh_in_background, f"quote:{ITEM}:")


def test_the_background_refresh_summarizes_the_items_claims_and_never_raises():
    with patch(f"{H}.refresh_summary", side_effect=RuntimeError("boom")), patch("app.agents.workflow.get_llm", side_effect=RuntimeError):
        quotation_history._refresh_in_background(f"quote:{ITEM}:")  # must not raise
    with patch(f"{H}.refresh_summary") as refresh, patch("app.agents.workflow.get_llm", return_value="llm"):
        quotation_history._refresh_in_background(f"quote:{ITEM}:")
    assert refresh.call_args.args == ("procura",) and refresh.call_args.kwargs["scope_key_prefix"] == f"quote:{ITEM}:"
    assert refresh.call_args.kwargs["llm"] == "llm" and refresh.call_args.kwargs["min_new"] == 3


# -- the specialist -------------------------------------------------------------------------------------------------------


def test_the_specialist_is_skipped_without_memory():
    out = history_specialist({"item_code": ITEM}, {})
    assert out["_status"] == "skipped"


def test_the_specialist_reads_the_quotations_that_were_processed():
    with patch("app.procurement.quotation_history.build_history", return_value={"summary_used": False}) as build:
        out = history_specialist({"item_code": ITEM, "use_memory": True},
                                 {"quotation_processing": {"data": {"quote_rows": [{"supplier": "ACME"}]}}})
    assert out == {"summary_used": False}
    assert build.call_args.kwargs == {"item_code": ITEM, "quote_rows": [{"supplier": "ACME"}]}
