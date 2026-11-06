"""Unit tests for Procura's specialist subagents, run through the real
shared framework against the real SeededERPNextClient."""

from __future__ import annotations

from app.procurement.mcp.client import SeededERPNextClient
from app.procurement.subagents.base import run_specialists
from app.procurement.subagents.procura import SPECIALISTS

ORDER = ["rfq_management", "quotation_processing", "quotation_comparison", "supplier_matching", "delivery_schedule_estimation"]


def _run(eligible, item_code="ITEM-LAPTOP-14", quantity=20, client=None):
    client = client or SeededERPNextClient()
    context = {"client": client, "item_code": item_code, "quantity": quantity, "eligible_supplier_names": eligible}
    return run_specialists(SPECIALISTS, context, needed=ORDER)


def test_rfq_management_and_quotation_processing_skip_cleanly_with_zero_eligible():
    results = _run(eligible=[])
    assert results["rfq_management"].status == "skipped"
    assert results["rfq_management"].data["rfq"] is None
    assert results["quotation_processing"].status == "skipped"
    assert results["quotation_processing"].data["quote_rows"] == []
    client = SeededERPNextClient()
    # never actually attempted the write
    assert client._data["rfqs"] == []


def test_quotation_comparison_still_runs_cleanly_over_zero_quotations():
    results = _run(eligible=[])
    assert results["quotation_comparison"].data["comparison"]["recommended_supplier"] is None
    assert results["quotation_comparison"].data["comparison"]["rationale"] == "No quotations to compare."


def test_rfq_management_creates_an_rfq_when_eligible():
    client = SeededERPNextClient()
    results = _run(eligible=["SUP-0001", "SUP-0002"], client=client)
    assert results["rfq_management"].status == "ok"
    assert results["rfq_management"].data["rfq"]["name"].startswith("RFQ-SEED-")
    assert len(client._data["rfqs"]) == 1


def test_quotation_processing_filters_to_eligible_suppliers_only():
    results = _run(eligible=["SUP-0002"])
    rows = results["quotation_processing"].data["quote_rows"]
    assert {r["supplier"] for r in rows} == {"SUP-0002"}


def test_quotation_comparison_recommends_cheapest_when_eligible():
    results = _run(eligible=["SUP-0001", "SUP-0002"])
    assert results["quotation_comparison"].data["comparison"]["recommended_supplier"] == "SUP-0001"


def test_supplier_matching_finds_the_real_gap():
    """SUP-0003 is deliberately not seeded with a quotation."""
    client = SeededERPNextClient()
    client._data["suppliers"].append(
        {"name": "SUP-0004", "supplier_name": "No Quote Supplier", "disabled": 0, "on_hold": 0}
    )
    results = _run(eligible=["SUP-0001", "SUP-0004"], client=client)
    matching = results["supplier_matching"].data
    assert matching["matched_suppliers"] == ["SUP-0001"]
    assert matching["eligible_suppliers_without_a_quotation"] == ["SUP-0004"]


def test_supplier_matching_skips_cleanly_with_zero_eligible():
    results = _run(eligible=[])
    assert results["supplier_matching"].status == "skipped"


def test_delivery_schedule_estimation_reports_real_promised_delivery_days():
    results = _run(eligible=["SUP-0001", "SUP-0002"])
    estimates = {e["supplier"]: e["promised_delivery_days"] for e in results["delivery_schedule_estimation"].data["estimates"]}
    assert estimates == {"SUP-0001": 10, "SUP-0002": 4}  # from the seeded fixture, not invented


def test_delivery_schedule_estimation_skips_cleanly_with_no_quotations():
    results = _run(eligible=[])
    assert results["delivery_schedule_estimation"].status == "skipped"


def test_a_backend_failure_in_rfq_management_is_reported_not_swallowed():
    class _BrokenClient(SeededERPNextClient):
        def create_rfq(self, *, items, supplier_names, source_reference=None, qty=1):
            raise RuntimeError("write endpoint unavailable")

    results = _run(eligible=["SUP-0001"], client=_BrokenClient())
    assert results["rfq_management"].status == "error"
    assert "write endpoint unavailable" in results["rfq_management"].error
    # quotation_processing is independent of rfq_management and still runs
    assert results["quotation_processing"].status == "ok"
