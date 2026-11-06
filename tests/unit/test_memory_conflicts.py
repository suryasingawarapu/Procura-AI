"""Conflict detection and resolution: the rules, the recorded edges, and the
override by a person's decision."""

from __future__ import annotations

from app.memory.conflicts import (
    claim_value,
    detect_conflicts,
    resolve_rows,
    resolve_subject,
    scan_conflicts,
)
from tests.memfake import MemFake

SUBJECT = "quote:LAPTOP:ACME"


def _claim(db, id, source_type, price, doc_date, *, currency="INR", subject=SUBJECT, status="active"):
    return db.add(
        "agent_memories", id=id, agent_id="procura", memory_key=f"{subject}:{id}", subject_key=subject,
        status=status, source_type=source_type, verification_status="unverified", content=f"{id} {price}",
        structured_fact={"value": price, "currency": currency, "document_date": doc_date},
    )


def _rows(*specs):
    db = MemFake()
    return db, [_claim(db, *spec) for spec in specs]


# -- the rules (pure) ----------------------------------------------------------------------


def test_the_example_an_updated_pdf_beats_an_older_email():
    """Email says 50,000; the updated quotation PDF says 53,000."""
    _db, rows = _rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    report = resolve_rows(rows)
    assert report.status == "resolved" and report.winner["id"] == "pdf"
    assert [r["id"] for r in report.losers] == ["email"]
    assert "outranks email" in report.reason


def test_agreeing_claims_are_not_a_conflict():
    _db, rows = _rows(("a", "email", 50000, "2026-03-01"), ("b", "pdf", 50000, "2026-03-05"))
    assert resolve_rows(rows).status == "none"


def test_a_single_claim_is_not_a_conflict():
    _db, rows = _rows(("a", "pdf", 50000, "2026-03-01"))
    assert resolve_rows(rows).status == "none"


def test_values_that_differ_only_in_rounding_noise_agree():
    _db, rows = _rows(("a", "pdf", 50000.001, "2026-03-01"), ("b", "pdf", 50000.0, "2026-03-02"))
    assert resolve_rows(rows).status == "none"


def test_the_same_number_in_two_currencies_is_a_conflict():
    _db, rows = _rows(("a", "pdf", 500, "2026-03-01"), ("b", "pdf", 500, "2026-03-02"))
    rows[1]["structured_fact"]["currency"] = "USD"
    assert resolve_rows(rows).status in ("resolved", "needs_clarification")
    assert claim_value(rows[0]) != claim_value(rows[1])


def test_equal_tier_the_newer_document_wins():
    _db, rows = _rows(("old", "pdf", 50000, "2026-03-01"), ("new", "document", 53000, "2026-03-09"))
    report = resolve_rows(rows)
    assert report.winner["id"] == "new" and "newer of equally authoritative" in report.reason


def test_equal_tier_with_the_same_date_needs_clarification():
    _db, rows = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    report = resolve_rows(rows)
    assert report.status == "needs_clarification" and report.winner is None
    assert "no document is clearly the newer one" in report.reason


def test_equal_tier_with_an_unknown_date_needs_clarification():
    _db, rows = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, None))
    assert resolve_rows(rows).status == "needs_clarification"


def test_a_newer_but_less_authoritative_claim_needs_clarification_not_a_silent_win_for_either():
    _db, rows = _rows(("pdf", "pdf", 53000, "2026-03-01"), ("email", "email", 50000, "2026-03-09"))
    report = resolve_rows(rows)
    assert report.status == "needs_clarification"
    assert "newer email" in report.reason and "older pdf" in report.reason


def test_the_two_newest_claims_agreeing_is_not_a_tie():
    _db, rows = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-05"), ("c", "pdf", 53000, "2026-03-05"))
    report = resolve_rows(rows)
    assert report.status == "resolved" and claim_value(report.winner)[0] == 53000.0 and [r["id"] for r in report.losers] == ["a"]


def test_an_unknown_source_type_ranks_lowest():
    _db, rows = _rows(("mystery", "carrier-pigeon", 1, "2026-03-09"), ("pdf", "pdf", 2, "2026-03-01"))
    assert resolve_rows(rows).status == "needs_clarification"  # lower tier but newer


def test_claims_without_a_value_are_ignored():
    db = MemFake()
    rows = [_claim(db, "a", "pdf", 5, "2026-03-01"), db.add("agent_memories", id="n", agent_id="procura", status="active", subject_key=SUBJECT, structured_fact={})]
    assert resolve_rows(rows).status == "none"


# -- recording ---------------------------------------------------------------------------------


def _edges(db, relationship=None):
    return [e for e in db.tables.get("memory_provenance", []) if relationship in (None, e["relationship"])]


def test_an_automatic_resolution_is_recorded_as_edges_and_statuses_and_the_loser_is_kept():
    db, rows = _rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    report = detect_conflicts("procura", SUBJECT, client=db, task_id="PR-1")

    assert report.status == "resolved" and report.winner["id"] == "pdf"
    conflict = _edges(db, "conflicts_with")[0]
    assert (conflict["memory_id"], conflict["parent_memory_id"]) == ("email", "pdf")
    assert conflict["resolved_at"] and "50000" in conflict["note"] and "53000" in conflict["note"]
    resolved = _edges(db, "resolved_by")[0]
    assert (resolved["memory_id"], resolved["parent_memory_id"]) == ("email", "pdf")
    assert resolved["accepted"] is True and resolved["task_id"] == "PR-1" and "automatic" in resolved["purpose"]
    status = {r["id"]: r["verification_status"] for r in db.tables["agent_memories"]}
    assert status == {"email": "disputed", "pdf": "verified"}
    assert all(r["status"] == "active" for r in db.tables["agent_memories"])  # nothing deleted or superseded


def test_detection_is_idempotent():
    db, _rows_ = _rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    detect_conflicts("procura", SUBJECT, client=db)
    detect_conflicts("procura", SUBJECT, client=db)
    assert len(_edges(db, "conflicts_with")) == 1 and len(_edges(db, "resolved_by")) == 1


def test_a_needs_clarification_conflict_leaves_an_unresolved_edge_and_disputes_both():
    db, _rows_ = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    report = detect_conflicts("procura", SUBJECT, client=db)
    assert report.status == "needs_clarification"
    edge = _edges(db, "conflicts_with")[0]
    assert not edge.get("resolved_at")
    assert _edges(db, "resolved_by") == []
    assert {r["verification_status"] for r in db.tables["agent_memories"]} == {"disputed"}


def test_apply_false_reports_without_writing():
    db, _rows_ = _rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    assert detect_conflicts("procura", SUBJECT, apply=False, client=db).status == "resolved"
    assert _edges(db) == []


def test_superseded_claims_are_not_part_of_a_conflict():
    db, _rows_ = _rows(("old", "email", 40000, "2026-02-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    db.tables["agent_memories"][0]["status"] = "superseded"
    assert detect_conflicts("procura", SUBJECT, client=db).status == "none"


def test_a_person_decides_a_clarification_and_it_sticks():
    db, _rows_ = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    assert detect_conflicts("procura", SUBJECT, client=db).status == "needs_clarification"

    report = resolve_subject("procura", SUBJECT, "a", resolved_by="jane", note="a is the signed one", client=db)

    assert report.winner["id"] == "a" and report.manual and "decided by jane" in report.reason
    resolved = _edges(db, "resolved_by")[0]
    assert (resolved["memory_id"], resolved["parent_memory_id"]) == ("b", "a") and "manual" in resolved["purpose"]
    again = detect_conflicts("procura", SUBJECT, client=db)  # the next pipeline run
    assert again.status == "resolved" and again.winner["id"] == "a" and again.manual


def test_a_person_can_overrule_the_rules():
    db, _rows_ = _rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    detect_conflicts("procura", SUBJECT, client=db)
    resolve_subject("procura", SUBJECT, "email", resolved_by="jane", client=db)
    assert detect_conflicts("procura", SUBJECT, client=db).winner["id"] == "email"


def test_resolving_with_a_claim_that_is_not_part_of_the_subject_is_refused():
    db, _rows_ = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    try:
        resolve_subject("procura", SUBJECT, "nope", resolved_by="jane", client=db)
    except ValueError as exc:
        assert "not an active claim" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_a_claim_arriving_after_a_decision_competes_under_the_normal_rules():
    db, _rows_ = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    detect_conflicts("procura", SUBJECT, client=db)
    resolve_subject("procura", SUBJECT, "a", resolved_by="jane", client=db)

    _claim(db, "c", "pdf", 57000, "2026-03-20")  # a newer formal document arrives
    report = detect_conflicts("procura", SUBJECT, client=db)
    assert report.winner["id"] == "c" and report.status == "resolved"


def test_a_late_low_authority_claim_does_not_unseat_a_decision():
    db, _rows_ = _rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    detect_conflicts("procura", SUBJECT, client=db)
    resolve_subject("procura", SUBJECT, "a", resolved_by="jane", client=db)

    _claim(db, "chat", "chat", 45000, "2026-02-01")  # an older chat remark
    report = detect_conflicts("procura", SUBJECT, client=db)
    assert report.winner["id"] == "a"


def test_scan_checks_every_subject_under_a_prefix_and_reports_only_conflicts():
    db = MemFake()
    _claim(db, "a1", "email", 1, "2026-03-01", subject="quote:LAPTOP:X")
    _claim(db, "a2", "pdf", 2, "2026-03-05", subject="quote:LAPTOP:X")
    _claim(db, "b1", "pdf", 9, "2026-03-05", subject="quote:LAPTOP:Y")  # a single claim: fine
    _claim(db, "c1", "pdf", 1, "2026-03-01", subject="quote:CHAIR:Z")
    _claim(db, "c2", "pdf", 2, "2026-03-01", subject="quote:CHAIR:Z")  # a different item: not scanned

    reports = scan_conflicts("procura", "quote:LAPTOP:", client=db)
    assert [r.subject_key for r in reports] == ["quote:LAPTOP:X"] and reports[0].status == "resolved"


def test_other_agents_claims_are_never_compared():
    db = MemFake()
    _claim(db, "a", "pdf", 1, "2026-03-01")
    other = _claim(db, "b", "pdf", 2, "2026-03-05")
    other["agent_id"] = "vendra"
    assert detect_conflicts("procura", SUBJECT, client=db).status == "none"


# -- when the database does not accept conflict edges (migration 008 not applied) -------------------------------------


class _NoEdgesDB(MemFake):
    """Like the real database before migration 008: inserting a conflicts_with / resolved_by edge is refused."""

    def table(self, name):
        query = super().table(name)
        if name == "memory_provenance":
            real = query.execute

            def execute():
                if query.kind == "insert":
                    raise RuntimeError('new row violates check constraint "memory_provenance_relationship_check"')
                return real()

            query.execute = execute
        return query


def _no_edges_rows(*specs):
    db = _NoEdgesDB()
    return db, [_claim(db, *spec) for spec in specs]


def test_a_resolution_is_recorded_on_the_claims_even_when_edges_cannot_be_written():
    db, _rows_ = _no_edges_rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    report = detect_conflicts("procura", SUBJECT, client=db)

    assert report.status == "resolved" and report.winner["id"] == "pdf" and report.edges_recorded is False
    rows = {r["id"]: r for r in db.tables["agent_memories"]}
    assert rows["email"]["verification_status"] == "disputed" and rows["pdf"]["verification_status"] == "verified"
    assert rows["email"]["structured_fact"]["conflict"]["status"] == "lost" and rows["email"]["structured_fact"]["conflict"]["to"] == "pdf"
    assert rows["pdf"]["structured_fact"]["conflict"]["status"] == "won" and rows["pdf"]["structured_fact"]["conflict"]["against"] == ["email"]
    assert db.tables.get("memory_provenance", []) == []


def test_a_persons_decision_still_sticks_without_edges():
    db, _rows_ = _no_edges_rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    assert detect_conflicts("procura", SUBJECT, client=db).status == "needs_clarification"
    resolve_subject("procura", SUBJECT, "a", resolved_by="jane", client=db)
    again = detect_conflicts("procura", SUBJECT, client=db)
    assert again.status == "resolved" and again.winner["id"] == "a" and again.manual


def test_overruling_an_automatic_result_works_without_edges():
    db, _rows_ = _no_edges_rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    detect_conflicts("procura", SUBJECT, client=db)
    resolve_subject("procura", SUBJECT, "email", resolved_by="jane", client=db)
    assert detect_conflicts("procura", SUBJECT, client=db).winner["id"] == "email"


def test_an_unresolved_conflict_marks_every_claim_as_disputed_with_the_others():
    db, _rows_ = _no_edges_rows(("a", "pdf", 50000, "2026-03-01"), ("b", "pdf", 53000, "2026-03-01"))
    detect_conflicts("procura", SUBJECT, client=db)
    markers = {r["id"]: r["structured_fact"]["conflict"] for r in db.tables["agent_memories"]}
    assert markers["a"]["status"] == "disputed" and markers["a"]["with"] == ["b"] and markers["b"]["with"] == ["a"]


def test_edges_are_reported_as_recorded_when_the_database_accepts_them():
    db, _rows_ = _rows(("email", "email", 50000, "2026-03-01"), ("pdf", "pdf", 53000, "2026-03-05"))
    assert detect_conflicts("procura", SUBJECT, client=db).edges_recorded is True
