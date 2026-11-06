"""Quotation-claim, conflict and document-validity endpoints."""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.memory.conflicts import ConflictReport

ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}
ADMIN = {"X-API-Token": "admin-token"}
SCOPED = {"X-API-Token": "vendra-token"}
R = "app.api.procurement_routes"

CLAIM = {"item_code": "LAPTOP-14IN", "supplier": "ACME", "unit_price": 53000, "source_type": "pdf",
         "source_reference": "acme_quote_v2.pdf", "document_date": "2026-03-05", "recorded_by": "priya"}


def _client():
    return TestClient(app)


def _report(status="none", **kw):
    claims = [{"id": "m1", "source_type": "email", "source_reference": "mail", "verification_status": "unverified",
               "structured_fact": {"value": 50000.0, "currency": "INR", "supplier": "ACME", "document_date": "2026-03-01"}}]
    return ConflictReport("quote:LAPTOP-14IN:ACME", status, claims, **kw)


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.memory_conflicts.detect_conflicts")
@patch(f"{R}.quotation_claims.record_claim", return_value={"id": "new-claim"})
def test_recording_a_claim_stores_it_and_returns_the_conflict_outcome(mock_record, mock_detect):
    winner = {"id": "pdf-1", "source_type": "pdf"}
    mock_detect.return_value = _report("resolved", winner=winner, losers=[], reason="pdf outranks email")
    response = _client().post("/procurement/quotation-claims", json=CLAIM, headers=ADMIN)

    assert response.status_code == 201
    body = response.json()
    assert body["claim"]["memory_id"] == "new-claim" and body["claim"]["subject_key"] == "quote:LAPTOP-14IN:ACME"
    assert body["conflict"]["status"] == "resolved" and body["conflict"]["winner_memory_id"] == "pdf-1"
    kwargs = mock_record.call_args.kwargs
    assert kwargs["source_type"] == "pdf" and kwargs["document_date"] == date(2026, 3, 5) and kwargs["recorded_by"] == "priya"
    mock_detect.assert_called_once_with("procura", "quote:LAPTOP-14IN:ACME")


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.memory_conflicts.detect_conflicts", return_value=_report("needs_clarification", reason="same tier, same date"))
@patch(f"{R}.quotation_claims.record_claim", return_value={"id": "c"})
def test_a_claim_that_needs_a_decision_says_so(_record, _detect):
    body = _client().post("/procurement/quotation-claims", json=CLAIM, headers=ADMIN).json()
    assert body["conflict"]["status"] == "needs_clarification" and body["conflict"]["reason"] == "same tier, same date"


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.quotation_claims.record_claim")
def test_invalid_claims_are_rejected_before_anything_is_stored(mock_record):
    c = _client()
    for bad in (dict(CLAIM, unit_price=0), dict(CLAIM, unit_price=-5), dict(CLAIM, source_type="carrier-pigeon"),
                dict(CLAIM, content_sha256="xyz"), dict(CLAIM, currency="RUPEES"), dict(CLAIM, document_date="not-a-date"),
                {k: v for k, v in CLAIM.items() if k != "supplier"}):
        assert c.post("/procurement/quotation-claims", json=bad, headers=ADMIN).status_code == 422
    mock_record.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.quotation_claims.record_claim", side_effect=ValueError("unknown source_type"))
def test_a_value_error_from_recording_is_a_400(_record):
    assert _client().post("/procurement/quotation-claims", json=CLAIM, headers=ADMIN).status_code == 400


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.quotation_claims.record_claim")
@patch(f"{R}.quotation_claims.list_claims")
@patch(f"{R}.memory_conflicts.scan_conflicts")
@patch(f"{R}.memory_conflicts.resolve_subject")
@patch(f"{R}.document_validity.record_validity")
def test_every_route_refuses_a_scoped_token(mock_validity, mock_resolve, mock_scan, mock_list, mock_record):
    c = _client()
    assert c.post("/procurement/quotation-claims", json=CLAIM, headers=SCOPED).status_code == 403
    assert c.get("/procurement/quotation-claims?item_code=X", headers=SCOPED).status_code == 403
    assert c.get("/procurement/conflicts?item_code=X", headers=SCOPED).status_code == 403
    resolve = {"subject_key": "quote:X:Y", "winner_memory_id": "m", "resolved_by": "jane"}
    assert c.post("/procurement/conflicts/resolve", json=resolve, headers=SCOPED).status_code == 403
    validity = {"document": "PAN Card", "valid_until": "2027-01-01", "verified_by": "p"}
    assert c.post("/procurement/vendors/ACME/document-validity", json=validity, headers=SCOPED).status_code == 403
    for mock in (mock_validity, mock_resolve, mock_scan, mock_list, mock_record):
        mock.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.quotation_claims.list_claims")
def test_claims_can_be_listed_for_an_item_and_a_supplier(mock_list):
    mock_list.return_value = [{"id": "m1", "source_type": "pdf", "source_reference": "q.pdf", "verification_status": "unverified",
                               "structured_fact": {"value": 53000.0, "currency": "INR", "supplier": "ACME", "document_date": "2026-03-05"}}]
    body = _client().get("/procurement/quotation-claims?item_code=LAPTOP-14IN&supplier=ACME", headers=ADMIN).json()
    assert body["claims"][0]["supplier"] == "ACME" and body["claims"][0]["value"] == 53000.0 and body["claims"][0]["memory_id"] == "m1"
    assert mock_list.call_args.args == ("LAPTOP-14IN", "ACME")


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.memory_conflicts.scan_conflicts")
def test_conflicts_are_listed_for_an_item(mock_scan):
    mock_scan.return_value = [_report("needs_clarification", reason="r")]
    body = _client().get("/procurement/conflicts?item_code=LAPTOP-14IN", headers=ADMIN).json()
    assert body["conflicts"][0]["supplier"] == "ACME" and body["conflicts"][0]["status"] == "needs_clarification"
    assert mock_scan.call_args.args == ("procura", "quote:LAPTOP-14IN:")


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.memory_conflicts.resolve_subject")
def test_a_person_resolves_a_conflict(mock_resolve):
    winner = {"id": "m1", "source_type": "pdf"}
    mock_resolve.return_value = _report("resolved", winner=winner, losers=[], reason="decided by jane", manual=True)
    body = _client().post(
        "/procurement/conflicts/resolve", headers=ADMIN,
        json={"subject_key": "quote:LAPTOP-14IN:ACME", "winner_memory_id": "m1", "resolved_by": "jane", "note": "signed copy"},
    ).json()
    assert body["status"] == "resolved" and body["manual"] is True and body["item_code"] == "LAPTOP-14IN"
    assert mock_resolve.call_args.kwargs == {"resolved_by": "jane", "note": "signed copy"}


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.memory_conflicts.resolve_subject", side_effect=ValueError("m9 is not an active claim for quote:X:Y"))
def test_resolving_with_a_claim_that_is_not_part_of_the_subject_is_a_400(_resolve):
    response = _client().post("/procurement/conflicts/resolve", headers=ADMIN,
                              json={"subject_key": "quote:X:Y", "winner_memory_id": "m9", "resolved_by": "jane"})
    assert response.status_code == 400 and "not an active claim" in response.json()["detail"]


@patch.dict(os.environ, ENV, clear=False)
@patch(f"{R}.document_validity.record_validity", return_value={"id": "val-1"})
def test_recording_a_documents_validity(mock_record):
    response = _client().post(
        "/procurement/vendors/ACME/document-validity", headers=ADMIN,
        json={"document": "GST Registration Certificate", "valid_until": "2027-03-31", "verified_by": "priya"},
    )
    assert response.status_code == 201
    assert response.json() == {"memory_id": "val-1", "supplier": "ACME", "document": "GST Registration Certificate",
                               "valid_until": "2027-03-31", "verified_by": "priya"}
    assert mock_record.call_args.kwargs["valid_until"] == date(2027, 3, 31) and mock_record.call_args.kwargs["supplier"] == "ACME"


@patch.dict(os.environ, ENV, clear=False)
def test_an_invalid_validity_request_is_rejected():
    c = _client()
    assert c.post("/procurement/vendors/ACME/document-validity", headers=ADMIN,
                  json={"document": "PAN Card", "valid_until": "soon", "verified_by": "p"}).status_code == 422
