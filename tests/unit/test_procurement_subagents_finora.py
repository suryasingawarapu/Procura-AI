"""Unit tests for Finora's specialist subagents, run through the real
shared framework against the real SeededERPNextClient."""

from __future__ import annotations

from app.procurement.mcp.client import SeededERPNextClient
from app.procurement.subagents.base import run_specialists
from app.procurement.subagents.finora import SPECIALISTS

ORDER = ["budget_verification", "cost_analysis", "invoice_matching", "payment_terms_review", "approval_routing", "financial_records"]


def _run(estimated_total, supplier="SUP-0001", cost_center=None, client=None):
    client = client or SeededERPNextClient()
    context = {"client": client, "estimated_total": estimated_total, "supplier": supplier, "cost_center": cost_center}
    return run_specialists(SPECIALISTS, context, needed=ORDER)


def test_budget_verification_within_budget():
    results = _run(19000.0)  # seeded budget is 25000.0
    assert results["budget_verification"].data["check"]["within_budget"] is True


def test_budget_verification_unknown_when_no_budget_configured():
    results = _run(100.0, cost_center="no-such-cost-center")
    assert results["budget_verification"].data["check"]["within_budget"] is None


def test_cost_analysis_computes_real_utilization_percentage():
    results = _run(12500.0)  # half of the seeded 25000.0 budget
    assert results["cost_analysis"].data["budget_utilization_pct"] == 50.0


def test_cost_analysis_skips_cleanly_without_a_budget_figure():
    results = _run(100.0, cost_center="no-such-cost-center")
    assert results["cost_analysis"].status == "skipped"


def test_invoice_matching_reports_none_found_honestly():
    results = _run(100.0)  # seeded invoices list is empty
    assert results["invoice_matching"].data["invoices_matched"] == []
    assert results["invoice_matching"].data["count"] == 0


def test_payment_terms_review_skips_cleanly_with_no_invoices():
    results = _run(100.0)
    assert results["payment_terms_review"].status == "skipped"


def test_payment_terms_review_reads_real_fields_when_present():
    client = SeededERPNextClient()
    client._data["invoices"].append(
        {"name": "PINV-0001", "supplier": "SUP-0001", "status": "Overdue", "outstanding_amount": 500.0}
    )
    results = _run(100.0, client=client)
    reviewed = results["payment_terms_review"].data["reviewed_invoices"]
    assert reviewed == [{"name": "PINV-0001", "status": "Overdue", "outstanding_amount": 500.0}]


def test_approval_routing_always_requires_human_approval():
    for total in (100.0, 999999.0):
        results = _run(total)
        assert results["approval_routing"].data["requires_human_approval"] is True


def test_approval_routing_reason_reflects_the_real_verdict():
    over = _run(999999.0)
    within = _run(100.0)
    assert "OVER budget" in over["approval_routing"].data["reason"]
    assert "within budget" in within["approval_routing"].data["reason"]


def test_financial_records_assembles_the_consolidated_record():
    results = _run(19000.0)
    record = results["financial_records"].data["record"]
    assert record["budget_check"]["within_budget"] is True
    assert record["invoices_matched"] == 0
    assert record["approval_routing"]["requires_human_approval"] is True


def test_a_backend_failure_in_budget_verification_is_reported_not_swallowed():
    class _BrokenClient(SeededERPNextClient):
        def get_budget(self, *, cost_center=None):
            raise RuntimeError("ERPNext timeout")

    results = _run(100.0, client=_BrokenClient())
    assert results["budget_verification"].status == "error"
    # invoice_matching is independent and still runs
    assert results["invoice_matching"].status == "ok"
