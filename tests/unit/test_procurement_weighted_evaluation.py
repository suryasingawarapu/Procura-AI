"""Unit tests for the configurable, multi-criteria weighted evaluation
(score_quotations_weighted) -- additive to compare_quotations, which these
tests also confirm is completely unaffected."""

from __future__ import annotations

import pytest

from app.procurement.calculations import (
    DEFAULT_EVALUATION_WEIGHTS,
    EvaluationWeights,
    Quotation,
    compare_quotations,
    landed_cost,
    score_quotations_weighted,
)


def test_evaluation_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        EvaluationWeights(price=0.5, delivery=0.5, warranty=0.5, payment_terms=0, vendor_risk=0, specification=0)


def test_default_weights_are_valid_and_sum_to_one():
    assert abs(sum(DEFAULT_EVALUATION_WEIGHTS.model_dump().values()) - 1.0) < 1e-9


def test_landed_cost_includes_tax_and_freight_and_subtracts_discount():
    q = Quotation(supplier="A", unit_price=100, quantity=10, tax_amount=50, freight_amount=20, discount_amount=30)
    assert landed_cost(q) == 1000 + 50 + 20 - 30


def test_landed_cost_defaults_unstated_adjustments_to_zero_not_invented():
    q = Quotation(supplier="A", unit_price=100, quantity=10)
    assert landed_cost(q) == 1000.0


def test_disqualified_vendor_is_reported_with_reason_never_silently_dropped():
    quotations = [
        Quotation(supplier="Cheap-But-Noncompliant", unit_price=10, quantity=1,
                  mandatory_requirements_met=False, disqualification_reason="missing ISO 9001 certificate"),
        Quotation(supplier="Compliant", unit_price=100, quantity=1),
    ]
    result = score_quotations_weighted(quotations)
    assert result.recommended_supplier == "Compliant"
    assert result.disqualified == [{"supplier": "Cheap-But-Noncompliant", "reason": "missing ISO 9001 certificate"}]
    assert "Cheap-But-Noncompliant" not in [s.supplier for s in result.scores]
    assert "missing ISO 9001 certificate" in result.rationale


def test_everyone_disqualified_reports_that_honestly_instead_of_a_fake_winner():
    quotations = [
        Quotation(supplier="A", unit_price=10, quantity=1, mandatory_requirements_met=False, disqualification_reason="expired quotation"),
    ]
    result = score_quotations_weighted(quotations)
    assert result.recommended_supplier is None
    assert result.scores == []
    assert "mandatory requirements" in result.rationale


def test_weighted_ranking_considers_more_than_just_price():
    """A pure price-only ranking (compare_quotations) would pick the
    cheapest; a delivery/warranty-heavy policy can legitimately prefer a
    slightly pricier vendor instead -- and must, to prove this isn't just
    compare_quotations renamed."""
    quotations = [
        Quotation(supplier="Cheapest-Slow", unit_price=100, quantity=1, delivery_days=60, warranty_months=0),
        Quotation(supplier="Pricier-Fast", unit_price=105, quantity=1, delivery_days=2, warranty_months=24),
    ]
    # compare_quotations (price-only) picks the cheapest
    assert compare_quotations(quotations).recommended_supplier == "Cheapest-Slow"

    # a policy that weights delivery+warranty heavily over price can flip it
    delivery_warranty_heavy = EvaluationWeights(
        price=0.1, delivery=0.4, warranty=0.4, payment_terms=0.0, vendor_risk=0.0, specification=0.1
    )
    result = score_quotations_weighted(quotations, delivery_warranty_heavy)
    assert result.recommended_supplier == "Pricier-Fast"


def test_missing_criterion_data_drops_that_criterions_weight_rather_than_guessing():
    quotations = [
        Quotation(supplier="A", unit_price=100, quantity=1),  # no delivery_days, no warranty_months at all
        Quotation(supplier="B", unit_price=100, quantity=1),
    ]
    result = score_quotations_weighted(quotations)
    for score in result.scores:
        assert score.criterion_scores["delivery"] is None
        assert score.criterion_scores["warranty"] is None
    # a tie on every actually-known criterion (price, both equal) -> rank 1 and 2 still assigned, no crash
    assert {s.rank for s in result.scores} == {1, 2}


def test_scores_are_ranked_and_numbered():
    quotations = [
        Quotation(supplier="A", unit_price=300, quantity=1),
        Quotation(supplier="B", unit_price=100, quantity=1),
        Quotation(supplier="C", unit_price=200, quantity=1),
    ]
    result = score_quotations_weighted(quotations)
    assert [s.supplier for s in result.scores] == sorted(
        [s.supplier for s in result.scores], key=lambda name: next(s.rank for s in result.scores if s.supplier == name)
    )
    assert result.scores[0].rank == 1
    assert len(result.scores) == 3
