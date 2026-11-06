"""Unit tests for the deterministic quotation comparison and budget math."""

from __future__ import annotations

from app.procurement.calculations import (
    Quotation,
    budget_check,
    compare_quotations,
    total_cost,
)


def test_total_cost_is_unit_price_times_quantity():
    q = Quotation(supplier="Acme", unit_price=99.5, quantity=20)
    assert total_cost(q) == 1990.0


def test_compare_quotations_empty_list():
    result = compare_quotations([])
    assert result.recommended_supplier is None
    assert result.scores == []


def test_compare_quotations_ranks_by_cost_first():
    cheap = Quotation(supplier="Cheap Co", unit_price=50, quantity=20, delivery_days=10)
    pricey = Quotation(supplier="Pricey Inc", unit_price=80, quantity=20, delivery_days=2)
    result = compare_quotations([pricey, cheap])

    assert result.recommended_supplier == "Cheap Co"
    assert [s.supplier for s in result.scores] == ["Cheap Co", "Pricey Inc"]
    assert result.scores[0].rank == 1
    assert "1000.00" in result.rationale  # 50*20
    assert "higher" in result.rationale


def test_compare_quotations_breaks_cost_ties_by_delivery_then_spec_match():
    slow = Quotation(supplier="Slow", unit_price=50, quantity=10, delivery_days=15, specification_match=1.0)
    fast = Quotation(supplier="Fast", unit_price=50, quantity=10, delivery_days=3, specification_match=0.8)
    result = compare_quotations([slow, fast])
    assert result.recommended_supplier == "Fast"  # same cost, faster delivery wins


def test_compare_quotations_rationale_only_cites_given_fields():
    q = Quotation(supplier="Only Co", unit_price=10, quantity=5)  # no delivery/terms/spec given
    result = compare_quotations([q])
    assert "delivery" not in result.rationale
    assert "payment terms" not in result.rationale
    assert "specification" not in result.rationale
    assert "50.00" in result.rationale


def test_budget_check_within_budget():
    result = budget_check(estimated_total=900.0, available_budget=1000.0)
    assert result["within_budget"] is True
    assert result["shortfall"] == 0.0
    assert result["note"] is None


def test_budget_check_over_budget():
    result = budget_check(estimated_total=1200.0, available_budget=1000.0)
    assert result["within_budget"] is False
    assert result["shortfall"] == 200.0


def test_budget_check_missing_budget_is_unknown_not_approved():
    """Never invent a budget: missing data must report unknown, not pass."""
    result = budget_check(estimated_total=500.0, available_budget=None)
    assert result["within_budget"] is None
    assert result["shortfall"] is None
    assert "unknown" in result["note"]
