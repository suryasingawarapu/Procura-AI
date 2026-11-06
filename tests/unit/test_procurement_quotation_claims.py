"""Quotation claims: recording with provenance, versioning of a changed price,
and reconciling sources that disagree before anything is compared."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.memory.conflicts import resolve_subject
from app.procurement.calculations import ComparisonResult, QuotationScore
from app.procurement.quotation_claims import list_claims, reconcile, record_claim, subject_for
from app.procurement.subagents.procura import blocking_conflicts, quotation_processing
from tests.memfake import MemFake

Q = "app.procurement.quotation_claims"
ITEM = "LAPTOP-14IN"


@pytest.fixture
def db():
    fake = MemFake()

    def save(agent, key, content, **kw):
        """Faithful enough: identical content is a no-op, changed content supersedes and bumps the version."""
        active = [r for r in fake.tables.get("agent_memories", []) if r["agent_id"] == agent and r["memory_key"] == key and r["status"] == "active"]
        if active and active[0]["content"] == content:
            return dict(active[0])
        version = 1
        if active:
            active[0]["status"] = "superseded"
            version = active[0]["version"] + 1
        row = fake.add(
            "agent_memories", agent_id=agent, memory_key=key, content=content, status="active", version=version,
            subject_key=kw.get("subject_key"), source_reference=kw.get("source_reference"),
            observed_at=kw.get("observed_at"), source_type=None, hop_depth=0,
        )
        return dict(row)

    def enrich(memory_id, client=None, **fields):
        for row in fake.tables["agent_memories"]:
            if row["id"] == memory_id:
                row.update({k: v for k, v in fields.items()})

    with patch(f"{Q}.save_memory", side_effect=save), patch(f"{Q}.enrich_memory", side_effect=enrich), patch(
        f"{Q}.embed_or_none", return_value=None
    ), patch(f"{Q}.supabase", fake):
        yield fake


def _erp(supplier, rate, name, doc_date="2026-03-01", **extra):
    row = {"name": name, "supplier": supplier, "item_code": ITEM, "rate": rate, "currency": "INR", "delivery_days": 10,
           "payment_terms_days": 30, "source_reference": f"Supplier Quotation/{name}", "document_date": doc_date}
    row.update(extra)
    return row


def _claims(db):
    return [r for r in db.tables["agent_memories"] if r["agent_id"] == "procura"]


# -- recording -------------------------------------------------------------------------------------------------


def test_a_claim_records_its_source_document_date_hash_and_who_recorded_it(db):
    saved = record_claim(
        item_code=ITEM, supplier="ACME", unit_price=53000, source_type="pdf", source_reference="acme_quote_v2.pdf",
        document_date=date(2026, 3, 5), delivery_days=12, recorded_by="priya", task_id="PR-1", content_sha256="ab" * 32,
        client=db,
    )
    row = _claims(db)[0]
    assert row["memory_key"] == "quote:LAPTOP-14IN:ACME:pdf:acme_quote_v2.pdf"
    assert row["subject_key"] == subject_for(ITEM, "ACME") == "quote:LAPTOP-14IN:ACME"
    assert row["source_reference"] == "acme_quote_v2.pdf" and row["source_type"] == "pdf" and row["source_hash"] == "ab" * 32
    assert row["observed_at"] == "2026-03-05" and row["task_id"] == "PR-1" and row["verification_status"] == "unverified"
    fact = row["structured_fact"]
    assert fact["value"] == 53000.0 and fact["document_date"] == "2026-03-05" and fact["recorded_by"] == "priya"
    assert saved["structured_fact"]["delivery_days"] == 12


def test_an_erpnext_claim_is_born_verified(db):
    record_claim(item_code=ITEM, supplier="ACME", unit_price=50600, source_type="erpnext_doctype",
                 source_reference="Supplier Quotation/PUR-SQTN-1", client=db)
    assert _claims(db)[0]["verification_status"] == "verified"


def test_the_same_claim_twice_changes_nothing(db):
    args = dict(item_code=ITEM, supplier="ACME", unit_price=50600, source_type="erpnext_doctype",
                source_reference="Supplier Quotation/PUR-SQTN-1", client=db)
    record_claim(**args)
    with patch(f"{Q}.enrich_memory") as enrich:
        record_claim(**args)
    assert len(_claims(db)) == 1 and _claims(db)[0]["version"] == 1
    enrich.assert_not_called()  # an unchanged claim is already tagged


def test_a_changed_price_on_the_same_document_is_a_new_version_and_the_old_price_stays_in_history(db):
    for price in (50000, 53000):
        record_claim(item_code=ITEM, supplier="ACME", unit_price=price, source_type="erpnext_doctype",
                     source_reference="Supplier Quotation/PUR-SQTN-1", client=db)
    rows = _claims(db)
    assert [(r["version"], r["status"], r["structured_fact"]["value"]) for r in rows] == [(1, "superseded", 50000.0), (2, "active", 53000.0)]


def test_a_delivery_time_change_with_the_same_price_is_a_new_version_too(db):
    """15 days -> 25 days must be remembered as a change, not silently ignored."""
    for days in (15, 25):
        record_claim(item_code=ITEM, supplier="ACME", unit_price=50000, source_type="erpnext_doctype",
                     source_reference="Supplier Quotation/PUR-SQTN-1", delivery_days=days, client=db)
    rows = _claims(db)
    assert [(r["version"], r["status"], r["structured_fact"]["delivery_days"]) for r in rows] == [(1, "superseded", 15), (2, "active", 25)]


def test_a_payment_terms_change_is_a_new_version_too(db):
    for terms in (30, 45):
        record_claim(item_code=ITEM, supplier="ACME", unit_price=50000, source_type="pdf", source_reference="q.pdf",
                     payment_terms_days=terms, client=db)
    assert [r["version"] for r in _claims(db)] == [1, 2]


def test_an_unknown_source_type_is_refused(db):
    with pytest.raises(ValueError, match="unknown source_type"):
        record_claim(item_code=ITEM, supplier="A", unit_price=1, source_type="carrier-pigeon", source_reference="x", client=db)


def test_list_claims_filters_by_supplier(db):
    record_claim(item_code=ITEM, supplier="A", unit_price=1, source_type="pdf", source_reference="a.pdf", client=db)
    record_claim(item_code=ITEM, supplier="B", unit_price=2, source_type="pdf", source_reference="b.pdf", client=db)
    assert [c["structured_fact"]["supplier"] for c in list_claims(ITEM, "B", client=db)] == ["B"]
    assert len(list_claims(ITEM, client=db)) == 2


# -- reconciliation -----------------------------------------------------------------------------------------------


def test_quotations_with_no_conflict_are_returned_unchanged(db):
    rows = [_erp("A", 100, "Q1"), _erp("B", 90, "Q2")]
    rec = reconcile(item_code=ITEM, eligible=["A", "B"], erpnext_rows=rows, client=db)
    assert sorted(r["supplier"] for r in rec.rows) == ["A", "B"]
    assert rec.rows[0] in rows and rec.conflicts == [] and rec.resolved == [] and rec.claims_seen == 2
    assert len(_claims(db)) == 2 and all(c["source_type"] == "erpnext_doctype" for c in _claims(db))


def test_only_eligible_suppliers_quotations_are_recorded_and_priced(db):
    rec = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[_erp("A", 100, "Q1"), _erp("X", 1, "Q9")], client=db)
    assert [r["supplier"] for r in rec.rows] == ["A"] and len(_claims(db)) == 1


def test_an_older_email_that_disagrees_with_the_erpnext_quotation_loses_and_is_marked_disputed(db):
    record_claim(item_code=ITEM, supplier="A", unit_price=50000, source_type="email", source_reference="re: quote",
                 document_date=date(2026, 2, 20), client=db)
    rec = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[_erp("A", 53000, "Q1", "2026-03-05")], client=db)

    assert rec.rows[0]["rate"] == 53000 and rec.rows[0]["name"] == "Q1"
    assert rec.conflicts == [] and len(rec.resolved) == 1
    assert rec.resolved[0]["status"] == "resolved" and "outranks email" in rec.resolved[0]["reason"]
    status = {c["source_type"]: c["verification_status"] for c in _claims(db)}
    assert status == {"email": "disputed", "erpnext_doctype": "verified"}
    edges = {e["relationship"] for e in db.tables["memory_provenance"]}
    assert edges == {"conflicts_with", "resolved_by"}


def test_a_pdf_only_supplier_is_priced_from_the_authoritative_claim_and_flagged_as_external(db):
    record_claim(item_code=ITEM, supplier="NEW", unit_price=50000, source_type="email", source_reference="mail",
                 document_date=date(2026, 3, 1), delivery_days=20, client=db)
    record_claim(item_code=ITEM, supplier="NEW", unit_price=53000, source_type="pdf", source_reference="quote.pdf",
                 document_date=date(2026, 3, 5), delivery_days=12, client=db)
    rec = reconcile(item_code=ITEM, eligible=["NEW"], erpnext_rows=[], client=db)
    assert len(rec.rows) == 1
    row = rec.rows[0]
    assert (row["supplier"], row["rate"], row["delivery_days"], row["source_reference"]) == ("NEW", 53000.0, 12, "quote.pdf")
    assert rec.external_only == ["NEW"]


def test_equally_authoritative_sources_with_no_date_order_need_clarification_and_are_held_out(db):
    rec = reconcile(item_code=ITEM, eligible=["A", "B"],
                    erpnext_rows=[_erp("A", 100, "Q1", "2026-03-01"), _erp("A", 120, "Q2", "2026-03-01"), _erp("B", 90, "Q3")], client=db)
    assert [r["supplier"] for r in rec.rows] == ["B"]
    assert len(rec.conflicts) == 1
    conflict = rec.conflicts[0]
    assert conflict["supplier"] == "A" and conflict["status"] == "needs_clarification"
    assert sorted(c["value"] for c in conflict["claims"]) == [100.0, 120.0]
    assert all(c["memory_id"] for c in conflict["claims"])


def test_the_newer_of_two_equal_authority_documents_wins(db):
    rec = reconcile(item_code=ITEM, eligible=["A"],
                    erpnext_rows=[_erp("A", 100, "Q1", "2026-03-01"), _erp("A", 120, "Q2", "2026-04-01")], client=db)
    assert [r["name"] for r in rec.rows] == ["Q2"] and rec.conflicts == []


def test_a_persons_decision_is_used_on_the_next_run(db):
    rows = [_erp("A", 100, "Q1", "2026-03-01"), _erp("A", 120, "Q2", "2026-03-01")]
    first = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=rows, client=db)
    winner = next(c for c in first.conflicts[0]["claims"] if c["value"] == 120.0)["memory_id"]
    resolve_subject("procura", first.conflicts[0]["subject_key"], winner, resolved_by="jane", client=db)

    second = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=rows, client=db)
    assert second.conflicts == [] and [r["name"] for r in second.rows] == ["Q2"]
    assert second.resolved[0]["manual"] is True


def test_a_claim_whose_erpnext_document_no_longer_exists_is_archived_not_priced(db):
    reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[_erp("A", 100, "Q1")], client=db)
    rec = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[], client=db)
    assert rec.rows == [] and rec.archived == ["Supplier Quotation/Q1"]
    assert _claims(db)[0]["status"] == "archived"


def test_if_memory_is_unavailable_the_erpnext_quotations_are_used_as_they_are(db):
    with patch(f"{Q}.save_memory", side_effect=ConnectionError("db down")):
        rec = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[_erp("A", 100, "Q1"), _erp("X", 5, "Q2")], client=db)
    assert [r["supplier"] for r in rec.rows] == ["A"] and "unavailable" in rec.note


# -- Procura's quotation_processing and the blocking rule ---------------------------------------------------------------------


class _Client:
    def list_supplier_quotations(self, item_code=None, rfq_name=None):
        return [_erp("A", 100, "Q1"), _erp("X", 5, "Q9")]


def test_quotation_processing_without_memory_is_the_old_behaviour():
    out = quotation_processing({"client": _Client(), "item_code": ITEM, "eligible_supplier_names": ["A"]}, {})
    assert [r["supplier"] for r in out["quote_rows"]] == ["A"] and "reconciliation" not in out


def test_quotation_processing_with_memory_reconciles_and_reports():
    from app.procurement.quotation_claims import Reconciliation

    rec = Reconciliation(rows=[_erp("A", 100, "Q1")], conflicts=[{"supplier": "B"}], claims_seen=3, archived=["x"])
    with patch(f"{Q}.reconcile", return_value=rec) as mock:
        out = quotation_processing({"client": _Client(), "item_code": ITEM, "eligible_supplier_names": ["A", "B"], "use_memory": True, "task_id": "PR-1"}, {})
    assert out["quote_rows"] == rec.rows and out["reconciliation"]["conflicts"] == [{"supplier": "B"}]
    assert mock.call_args.kwargs["task_id"] == "PR-1" and len(mock.call_args.kwargs["erpnext_rows"]) == 2  # all rows, to spot orphans


def _comparison(best_total):
    scores = [QuotationScore(supplier="B", total_cost=best_total, delivery_days=1, payment_terms_days=1, specification_match=None, rank=1, reasons=[])] if best_total else []
    return ComparisonResult(recommended_supplier="B" if best_total else None, scores=scores, rationale="r")


def _conflict(*values):
    return {"supplier": "A", "claims": [{"value": v} for v in values]}


def test_a_conflict_that_could_win_blocks_the_task():
    assert blocking_conflicts([_conflict(100, 120)], _comparison(1500), quantity=10) != []  # 100*10 < 1500


def test_a_conflict_that_cannot_win_even_at_its_lowest_price_does_not_block():
    assert blocking_conflicts([_conflict(200, 220)], _comparison(1500), quantity=10) == []  # 200*10 > 1500


def test_with_nothing_else_to_compare_any_conflict_blocks():
    assert blocking_conflicts([_conflict(200, 220)], _comparison(None), quantity=10) != []


def test_no_conflicts_never_block():
    assert blocking_conflicts([], _comparison(1500), quantity=10) == []


def test_a_degraded_reconciliation_says_so_in_the_live_trace(db):
    from app.procurement import progress

    seen = []
    with patch(f"{Q}.save_memory", side_effect=ConnectionError("db down")), progress.capture(lambda s, t: seen.append((s, t))):
        rec = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[_erp("A", 100, "Q1")], client=db)
    assert rec.note and seen == [("procura", rec.note)]


def test_a_write_that_fails_once_is_retried_instead_of_degrading_the_reconciliation(db):
    from app.procurement import quotation_claims as qc

    real = qc.record_claim
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("dropped connection")
        return real(**kw)

    with patch(f"{Q}.record_claim", side_effect=flaky), patch(f"{Q}.time.sleep"):
        rec = reconcile(item_code=ITEM, eligible=["A"], erpnext_rows=[_erp("A", 100, "Q1")], client=db)
    assert rec.note is None and [r["supplier"] for r in rec.rows] == ["A"] and calls["n"] == 2
