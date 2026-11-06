"""Document expiry: reading validity from file names and recorded
verifications, treating a lapsed document as non-compliant, and a verdict
that goes stale when the first document behind it lapses."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.memory.freshness import check_freshness
from app.procurement.document_validity import load_recorded_validity, record_validity
from app.procurement.expiry import end_of_day_utc, parse_expiry
from app.procurement.subagents.vendra import (
    check_compliance,
    vendor_onboarding_readiness,
    vendor_risk_flagging,
)
from app.procurement.systems import vendra as vendra_system
from tests.memfake import MemFake

TODAY = date(2026, 6, 15)


class _Client:
    def __init__(self, docs, country="India"):
        self.docs, self.country = docs, country

    def get_supplier(self, name):
        return {"name": name, "country": self.country}

    def list_suppliers(self):
        return [{"name": "ACME", "country": self.country}]

    def list_supplier_documents(self, name):
        return [{"name": f"F{i}", "file_name": f} for i, f in enumerate(self.docs)]


def _check(docs, **kw):
    return check_compliance(_Client(docs), "ACME", country="India", today=TODAY, **kw)


PAN = "PAN_Card_ABCDE1234F.pdf"
GST = "GST_Certificate.pdf"


@pytest.mark.parametrize("label,expected", [
    ("GST_Certificate_valid_until_2027-03-31.pdf", date(2027, 3, 31)),
    ("gst cert valid till 2027-03-31.pdf", date(2027, 3, 31)),
    ("GST_valid_upto_2027-03-31.pdf", date(2027, 3, 31)),
    ("gst-certificate-expires-2027-03-31.pdf", date(2027, 3, 31)),
    ("GST_cert_expiry_2027.03.31.pdf", date(2027, 3, 31)),
    ("GST_cert_exp_2027-03-31.pdf", date(2027, 3, 31)),
    ("GST_Certificate.pdf", None),
    ("PAN_Card_ABCDE1234F_valid_until_2027-13-45.pdf", None),
    ("", None),
])
def test_parse_expiry(label, expected):
    assert parse_expiry(label) == expected


def test_the_end_of_the_expiry_day_is_still_valid():
    assert end_of_day_utc(date(2026, 6, 15)) == datetime(2026, 6, 15, 23, 59, 59, tzinfo=timezone.utc)


def test_documents_with_no_expiry_behave_exactly_as_before():
    result = _check([PAN, GST])
    assert result["compliant"] and result["expired_documents"] == [] and result["earliest_expiry"] is None


def test_a_document_past_its_file_name_expiry_is_expired_and_the_supplier_not_compliant():
    result = _check([PAN, "GST_Certificate_valid_until_2026-01-31.pdf"])
    assert not result["compliant"]
    assert result["missing_documents"] == []
    assert result["expired_documents"] == [
        {"document": "GST Registration Certificate", "file": "GST_Certificate_valid_until_2026-01-31.pdf", "expired_on": "2026-01-31"}
    ]


def test_a_document_valid_through_today_is_still_valid():
    assert _check([PAN, "GST_Certificate_valid_until_2026-06-15.pdf"])["compliant"]


def test_a_renewed_document_replaces_an_expired_one():
    result = _check([PAN, "GST_Certificate_valid_until_2025-01-01.pdf", "GST_Certificate_renewed_valid_until_2027-01-01.pdf"])
    assert result["compliant"] and result["expired_documents"] == []


def test_the_earliest_expiry_among_valid_documents_is_reported():
    result = _check(["PAN_Card_ABCDE1234F_valid_until_2027-12-31.pdf", "GST_Certificate_valid_until_2026-09-30.pdf"])
    assert result["compliant"] and result["earliest_expiry"] == "2026-09-30"


def test_a_missing_document_is_missing_not_expired():
    result = _check([PAN])
    assert result["missing_documents"] == ["GST Registration Certificate"] and result["expired_documents"] == []


def test_a_recorded_lapsed_validity_makes_a_file_with_no_date_expired():
    result = _check([PAN, GST], recorded_expiries={"GST Registration Certificate": date(2026, 1, 1)})
    assert not result["compliant"] and result["expired_documents"][0]["expired_on"] == "2026-01-01"


def test_a_recorded_future_validity_is_used_for_the_expiry():
    result = _check([PAN, GST], recorded_expiries={"GST Registration Certificate": date(2027, 1, 1)})
    assert result["compliant"] and result["earliest_expiry"] == "2027-01-01"


def test_a_recorded_validity_wins_over_the_file_name():
    result = _check([PAN, "GST_Certificate_valid_until_2020-01-01.pdf"], recorded_expiries={"GST Registration Certificate": date(2027, 1, 1)})
    assert result["compliant"]


def test_file_references_are_kept_as_provenance():
    assert _check([PAN, GST])["document_refs"] == ["File/F0", "File/F1"]


# -- the other specialists understand "expired" -------------------------------------------


def _doc_results(compliance):
    return {
        "vendor_discovery": {"data": {"candidates": [{"name": "ACME"}]}},
        "document_verification": {"data": {"compliance_by_supplier": {"ACME": compliance}}},
    }


def test_readiness_says_expired_not_missing():
    expired = _check([PAN, "GST_Certificate_valid_until_2026-01-01.pdf"])
    out = vendor_onboarding_readiness({"client": _Client([])}, _doc_results(expired))
    assert out["readiness_by_supplier"]["ACME"] == "blocked: expired GST Registration Certificate"


def test_risk_flag_distinguishes_expired_documents_from_missing_ones():
    expired = _check([PAN, "GST_Certificate_valid_until_2026-01-01.pdf"])
    missing = _check([PAN])
    assert vendor_risk_flagging({"client": _Client([])}, _doc_results(expired))["risk_by_supplier"]["ACME"] == "documents_expired"
    assert vendor_risk_flagging({"client": _Client([])}, _doc_results(missing))["risk_by_supplier"]["ACME"] == "non_compliant"


# -- the verdict memory ------------------------------------------------------------------------


def _run_vendra(docs, *, recorded=None, today_patch=None):
    tool = MagicMock()
    tool.invoke.return_value = {"id": "mem-1"}
    with patch.object(vendra_system, "_tool", return_value=tool), patch.object(vendra_system, "enrich_memory") as enrich, patch(
        "app.procurement.document_validity.load_recorded_validity", return_value=recorded or {}
    ), patch("app.procurement.subagents.vendra.today_utc", return_value=TODAY):
        msg = vendra_system.run_vendor_eligibility_check(
            task_id="t", correlation_id="c", client=_Client(docs), use_memory=recorded is not None
        )
    return msg, tool.invoke.call_args.args[0], enrich.call_args.kwargs


def test_a_compliant_verdict_records_its_source_subject_expiry_and_a_content_hash():
    msg, saved, enrich = _run_vendra([PAN, "GST_Certificate_valid_until_2026-09-30.pdf"])
    assert saved["content"].endswith("compliant, documents valid until 2026-09-30")
    assert saved["source_reference"] == "Supplier/ACME" and saved["subject_key"] == "eligibility:ACME"
    assert saved["valid_until"] == "2026-09-30T23:59:59+00:00"
    assert len(enrich["source_hash"]) == 64 and enrich["verification_status"] == "verified"
    assert enrich["structured_fact"]["compliance"]["document_refs"]


def test_a_verdict_with_no_known_expiry_has_no_valid_until():
    _msg, saved, _enrich = _run_vendra([PAN, GST])
    assert "valid_until" not in saved and saved["content"].endswith(": compliant")


def test_an_expired_document_makes_the_verdict_disputed_and_says_what_expired():
    msg, saved, enrich = _run_vendra([PAN, "GST_Certificate_valid_until_2026-01-31.pdf"])
    assert saved["content"].endswith("expired GST Registration Certificate (on 2026-01-31)")
    assert "valid_until" not in saved and enrich["verification_status"] == "disputed"
    assert msg.payload["eligible_suppliers"] == []


def test_recorded_validity_is_used_in_live_mode():
    msg, saved, _enrich = _run_vendra([PAN, GST], recorded={"ACME": {"GST Registration Certificate": date(2026, 1, 1)}})
    assert msg.payload["eligible_suppliers"] == [] and "expired" in saved["content"]


def test_if_recorded_validity_cannot_be_read_the_check_still_runs():
    tool = MagicMock()
    tool.invoke.return_value = {"id": "m"}
    with patch.object(vendra_system, "_tool", return_value=tool), patch.object(vendra_system, "enrich_memory"), patch(
        "app.procurement.document_validity.load_recorded_validity", side_effect=RuntimeError("db down")
    ):
        msg = vendra_system.run_vendor_eligibility_check(
            task_id="t", correlation_id="c", client=_Client([PAN, GST]), use_memory=True
        )
    assert len(msg.payload["eligible_suppliers"]) == 1


def test_demo_mode_never_reads_recorded_validity():
    tool = MagicMock()
    tool.invoke.return_value = {"id": "m"}
    with patch.object(vendra_system, "_tool", return_value=tool), patch.object(vendra_system, "enrich_memory"), patch(
        "app.procurement.document_validity.load_recorded_validity"
    ) as load:
        vendra_system.run_vendor_eligibility_check(task_id="t", correlation_id="c", client=_Client([PAN, GST]))
    load.assert_not_called()


def test_a_verdict_goes_stale_when_its_first_document_lapses():
    verdict = {"status": "active", "valid_until": end_of_day_utc(date(2026, 9, 30)).isoformat()}
    assert check_freshness(verdict, datetime(2026, 9, 30, 12, tzinfo=timezone.utc)) == "fresh"
    assert check_freshness(verdict, datetime(2026, 10, 1, 0, 0, 1, tzinfo=timezone.utc)) == "stale"


# -- recorded validity ----------------------------------------------------------------------------


@pytest.fixture
def validity_db():
    db = MemFake()

    def save(agent, key, content, **kw):
        return db.add("agent_memories", agent_id=agent, memory_key=key, content=content, status="active",
                      valid_until=str(kw.get("valid_until")), subject_key=kw.get("subject_key"))

    def enrich(memory_id, **fields):
        fields.pop("client", None)
        for row in db.tables["agent_memories"]:
            if row["id"] == memory_id:
                row.update(fields)

    with patch("app.procurement.document_validity.save_memory", side_effect=save), patch(
        "app.procurement.document_validity.enrich_memory", side_effect=enrich
    ), patch("app.procurement.document_validity.embed_or_none", return_value=None):
        yield db


def test_recording_a_validity_stores_who_verified_it_until_when_and_how_to_find_it(validity_db):
    saved = record_validity(
        supplier="ACME", document="GST Registration Certificate", valid_until=date(2027, 3, 31),
        verified_by="priya", source_reference="File/F9", client=validity_db,
    )
    row = validity_db.tables["agent_memories"][0]
    assert row["memory_key"] == "document_validity:ACME:GST Registration Certificate" and row["agent_id"] == "vendra"
    assert "verified valid until 2027-03-31 by priya" in row["content"]
    assert row["valid_until"].startswith("2027-03-31") and "23:59:59" in row["valid_until"]
    assert row["structured_fact"]["verified_by"] == "priya" and row["verification_status"] == "verified"
    assert row["subject_key"] == "validity:ACME:GST Registration Certificate" and saved["id"] == row["id"]


def test_loading_returns_expiries_by_supplier_and_document_even_when_they_have_lapsed(validity_db):
    record_validity(supplier="ACME", document="PAN Card", valid_until=date(2020, 1, 1), verified_by="p", client=validity_db)
    record_validity(supplier="ACME", document="GST Registration Certificate", valid_until=date(2027, 1, 1), verified_by="p", client=validity_db)
    record_validity(supplier="OTHER", document="PAN Card", valid_until=date(2028, 1, 1), verified_by="p", client=validity_db)
    loaded = load_recorded_validity(client=validity_db)
    assert loaded == {
        "ACME": {"PAN Card": date(2020, 1, 1), "GST Registration Certificate": date(2027, 1, 1)},
        "OTHER": {"PAN Card": date(2028, 1, 1)},
    }  # the lapsed 2020 record is still returned, so it is seen as EXPIRED, not forgotten


def test_loading_ignores_rows_it_cannot_read(validity_db):
    validity_db.add("agent_memories", agent_id="vendra", memory_key="document_validity:X:Y", status="active", structured_fact={"junk": 1})
    assert load_recorded_validity(client=validity_db) == {}
