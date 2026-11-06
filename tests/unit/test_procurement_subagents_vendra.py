"""Unit tests for Vendra's six specialist subagents, run through the real
shared framework (app.procurement.subagents.base.run_specialists) against
the real SeededERPNextClient -- the same fixture app.procurement.systems.
vendra's own tests use."""

from __future__ import annotations

from app.procurement.mcp.client import SeededERPNextClient
from app.procurement.subagents.base import run_specialists
from app.procurement.subagents.vendra import SPECIALISTS


def _run(item_code="ITEM-LAPTOP-14", client=None):
    client = client or SeededERPNextClient()
    return run_specialists(SPECIALISTS, {"client": client, "item_code": item_code})


def test_vendor_discovery_excludes_on_hold():
    results = _run()
    candidates = results["vendor_discovery"].data["candidates"]
    assert {c["name"] for c in candidates} == {"SUP-0001", "SUP-0002"}  # SUP-0003 is on_hold


def test_document_verification_flags_the_known_incomplete_supplier():
    results = _run()
    compliance = results["document_verification"].data["compliance_by_supplier"]
    assert compliance["SUP-0001"]["compliant"] is True
    assert compliance["SUP-0002"]["compliant"] is False
    assert results["document_verification"].data["eligible_names"] == ["SUP-0001"]


def test_vendor_onboarding_readiness_distinguishes_ready_from_blocked():
    results = _run()
    readiness = results["vendor_onboarding_readiness"].data["readiness_by_supplier"]
    assert readiness["SUP-0001"] == "ready"
    assert "Business License" in readiness["SUP-0002"]


def test_vendor_profile_counts_real_submitted_quotations():
    results = _run()
    profiles = results["vendor_profile"].data["profiles"]
    assert profiles["SUP-0001"]["quotations_submitted_for_item"] == 1
    assert profiles["SUP-0002"]["quotations_submitted_for_item"] == 1
    assert profiles["SUP-0001"]["compliant"] is True


def test_vendor_performance_snapshot_ranks_by_real_price_for_eligible_suppliers_only():
    results = _run()
    # Only SUP-0001 is eligible (compliant); SUP-0002's quote must not appear
    # even though it exists and is cheaper-or-not -- performance is scoped
    # to eligible suppliers, matching Procura's own eligibility filter.
    snapshot = results["vendor_performance_snapshot"].data
    assert snapshot["item_code"] == "ITEM-LAPTOP-14"
    assert [r["supplier"] for r in snapshot["price_ranking"]] == ["SUP-0001"]


def test_vendor_performance_snapshot_skips_cleanly_without_an_item_code():
    results = _run(item_code=None)
    assert results["vendor_performance_snapshot"].status == "skipped"


def test_vendor_risk_flagging_covers_every_supplier_including_excluded_ones():
    results = _run()
    flags = results["vendor_risk_flagging"].data["risk_by_supplier"]
    assert flags["SUP-0001"] == "eligible"
    assert flags["SUP-0002"] == "non_compliant"
    assert flags["SUP-0003"] == "on_hold"  # excluded before Vendor Discovery even ran


def test_one_broken_specialist_does_not_take_down_the_others():
    class _BrokenClient(SeededERPNextClient):
        def list_supplier_quotations(self, *, rfq_name=None, item_code=None):
            raise RuntimeError("ERPNext timeout")

    results = _run(client=_BrokenClient())
    assert results["vendor_discovery"].status == "ok"
    assert results["document_verification"].status == "ok"
    # vendor_profile and vendor_performance_snapshot both call
    # list_supplier_quotations -- both must report a typed error, not crash
    assert results["vendor_profile"].status == "error"
    assert results["vendor_performance_snapshot"].status == "error"
    assert results["vendor_risk_flagging"].status == "ok"  # unaffected, doesn't call that method
