"""Deterministic arithmetic for quotation comparison and cost analysis.

Per spec: "Use deterministic Python functions for arithmetic and quotation
comparisons where appropriate." No LLM is involved in any function here --
same inputs always produce the same ranking and the same total, and the
rationale is built from the actual numbers, not generated prose.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

__all__ = [
    "Quotation",
    "QuotationScore",
    "ComparisonResult",
    "total_cost",
    "compare_quotations",
    "landed_cost",
    "EvaluationWeights",
    "DEFAULT_EVALUATION_WEIGHTS",
    "WeightedScore",
    "WeightedEvaluationResult",
    "score_quotations_weighted",
]


class Quotation(BaseModel):
    supplier: str
    unit_price: float = Field(..., ge=0)
    quantity: int = Field(..., gt=0)
    currency: str = "USD"
    delivery_days: Optional[int] = Field(default=None, ge=0)
    payment_terms_days: Optional[int] = Field(default=None, ge=0)
    specification_match: Optional[float] = Field(
        default=None, ge=0, le=1, description="0..1 fraction of required spec actually met"
    )
    source_reference: Optional[str] = None  # e.g. the quotation document/doctype reference

    # Optional fields for the weighted enterprise evaluation
    # (score_quotations_weighted) below -- all default to "not provided"
    # rather than an assumed value, so compare_quotations and every
    # existing caller that never sets them is completely unaffected.
    tax_amount: Optional[float] = Field(default=None, ge=0)
    discount_amount: Optional[float] = Field(default=None, ge=0)
    freight_amount: Optional[float] = Field(default=None, ge=0)
    warranty_months: Optional[int] = Field(default=None, ge=0)
    mandatory_requirements_met: bool = Field(
        default=True, description="False disqualifies this quotation outright -- see disqualification_reason"
    )
    disqualification_reason: Optional[str] = None
    vendor_risk_score: Optional[float] = Field(
        default=None, ge=0, le=1, description="0 = lowest known risk, 1 = highest -- from real vendor history only"
    )


def total_cost(quote: Quotation) -> float:
    """Unit price * quantity. The one piece of arithmetic every comparison
    needs, kept as its own function so it's independently testable and
    reused identically everywhere a total is needed."""
    return round(quote.unit_price * quote.quantity, 2)


class QuotationScore(BaseModel):
    supplier: str
    total_cost: float
    delivery_days: Optional[int]
    payment_terms_days: Optional[int]
    specification_match: Optional[float]
    rank: int
    reasons: List[str]


class ComparisonResult(BaseModel):
    recommended_supplier: Optional[str]
    scores: List[QuotationScore]
    rationale: str


def compare_quotations(quotations: List[Quotation]) -> ComparisonResult:
    """Rank quotations by total cost (primary), then delivery time, then
    spec match, and build a rationale that only states what the numbers
    actually show -- no fact not derivable from the input quotations.
    """
    if not quotations:
        return ComparisonResult(recommended_supplier=None, scores=[], rationale="No quotations to compare.")

    def sort_key(q: Quotation):
        return (
            total_cost(q),
            q.delivery_days if q.delivery_days is not None else float("inf"),
            -(q.specification_match if q.specification_match is not None else 0.0),
        )

    ordered = sorted(quotations, key=sort_key)
    scores: List[QuotationScore] = []
    for rank, q in enumerate(ordered, start=1):
        reasons = [f"total cost {total_cost(q):.2f} {q.currency}"]
        if q.delivery_days is not None:
            reasons.append(f"delivery in {q.delivery_days} day(s)")
        if q.payment_terms_days is not None:
            reasons.append(f"payment terms: net {q.payment_terms_days} day(s)")
        if q.specification_match is not None:
            reasons.append(f"meets {q.specification_match * 100:.0f}% of the required specification")
        scores.append(
            QuotationScore(
                supplier=q.supplier,
                total_cost=total_cost(q),
                delivery_days=q.delivery_days,
                payment_terms_days=q.payment_terms_days,
                specification_match=q.specification_match,
                rank=rank,
                reasons=reasons,
            )
        )

    best = scores[0]
    rationale = (
        f"{best.supplier} ranked first out of {len(scores)} quotation(s): " + "; ".join(best.reasons) + "."
    )
    if len(scores) > 1:
        runner_up = scores[1]
        delta = runner_up.total_cost - best.total_cost
        rationale += (
            f" The next lowest total cost ({runner_up.supplier}) is {delta:.2f} {quotations[0].currency} higher."
        )

    return ComparisonResult(recommended_supplier=best.supplier, scores=scores, rationale=rationale)


def budget_check(estimated_total: float, available_budget: Optional[float]) -> Dict[str, Any]:
    """Compare a cost against a budget figure that must come from a real,
    configured source -- never invented. ``available_budget=None`` means
    the budget source had no data, and this must be reported as such, not
    silently treated as "unlimited" or "zero"."""
    if available_budget is None:
        return {
            "within_budget": None,
            "available_budget": None,
            "estimated_total": estimated_total,
            "shortfall": None,
            "note": "No budget data was found at the configured source; budget status is unknown, not approved.",
        }
    shortfall = max(0.0, round(estimated_total - available_budget, 2))
    return {
        "within_budget": estimated_total <= available_budget,
        "available_budget": available_budget,
        "estimated_total": estimated_total,
        "shortfall": shortfall,
        "note": None,
    }


# ---------------------------------------------------------------------------
# Weighted, multi-criteria enterprise evaluation -- additive to, and never a
# replacement for, compare_quotations above (which stays exactly as-is: a
# pure lowest-total-cost ranking). This is for the real-company scenario of
# scoring many vendors on price, delivery, warranty, payment terms and
# vendor risk together, per a company's own configured weights.
# ---------------------------------------------------------------------------


def landed_cost(quote: Quotation) -> float:
    """Total cost actually payable: unit price * quantity, plus tax and
    freight, less any discount -- all only if the quotation actually
    stated them (unstated fields contribute 0, never an assumed amount)."""
    base = quote.unit_price * quote.quantity
    base += quote.tax_amount or 0.0
    base += quote.freight_amount or 0.0
    base -= quote.discount_amount or 0.0
    return round(base, 2)


class EvaluationWeights(BaseModel):
    """How much each criterion counts toward the final weighted score.
    Must sum to 1.0 -- enforced, never silently renormalized, so a caller
    always knows exactly what policy was actually applied."""

    price: float = Field(..., ge=0, le=1)
    delivery: float = Field(..., ge=0, le=1)
    warranty: float = Field(..., ge=0, le=1)
    payment_terms: float = Field(..., ge=0, le=1)
    vendor_risk: float = Field(..., ge=0, le=1)
    specification: float = Field(..., ge=0, le=1)

    def model_post_init(self, __context: Any) -> None:
        total = self.price + self.delivery + self.warranty + self.payment_terms + self.vendor_risk + self.specification
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"evaluation weights must sum to 1.0, got {total}")


# A documented starting point only -- not a real company's actual
# procurement policy, which this project has no access to. Any caller
# should pass its own EvaluationWeights once that policy is configured.
DEFAULT_EVALUATION_WEIGHTS = EvaluationWeights(
    price=0.40, delivery=0.15, warranty=0.10, payment_terms=0.10, vendor_risk=0.15, specification=0.10
)


class WeightedScore(BaseModel):
    supplier: str
    landed_cost: float
    criterion_scores: Dict[str, Optional[float]]  # 0..1 per criterion, None where data was unavailable
    weighted_total: float  # 0..1, higher is better
    rank: int
    reasons: List[str]


class WeightedEvaluationResult(BaseModel):
    weights: EvaluationWeights
    recommended_supplier: Optional[str]
    scores: List[WeightedScore]
    disqualified: List[Dict[str, Any]]  # [{"supplier": ..., "reason": ...}, ...] -- never silently dropped
    rationale: str


def _criterion_scores(quotations: List[Quotation]) -> Dict[str, Dict[str, Optional[float]]]:
    """Normalize each criterion across the eligible quotations to a 0..1
    scale (1 = best in this set) -- relative to these vendors only, never
    an absolute/invented scale. A criterion with no data from any
    quotation scores None for everyone (never assumed as 0 or 1)."""
    costs = {q.supplier: landed_cost(q) for q in quotations}
    min_cost, max_cost = min(costs.values()), max(costs.values())

    def normalize(values: Dict[str, Optional[float]], *, higher_is_better: bool) -> Dict[str, Optional[float]]:
        known = {k: v for k, v in values.items() if v is not None}
        if not known:
            return {k: None for k in values}
        lo, hi = min(known.values()), max(known.values())
        result: Dict[str, Optional[float]] = {}
        for k, v in values.items():
            if v is None:
                result[k] = None
            elif hi == lo:
                result[k] = 1.0
            else:
                frac = (v - lo) / (hi - lo)
                result[k] = frac if higher_is_better else 1.0 - frac
        return result

    price_scores = {
        q.supplier: (1.0 if max_cost == min_cost else 1.0 - (costs[q.supplier] - min_cost) / (max_cost - min_cost))
        for q in quotations
    }
    delivery_scores = normalize({q.supplier: q.delivery_days for q in quotations}, higher_is_better=False)
    warranty_scores = normalize({q.supplier: q.warranty_months for q in quotations}, higher_is_better=True)
    payment_scores = normalize({q.supplier: q.payment_terms_days for q in quotations}, higher_is_better=True)
    risk_scores = normalize({q.supplier: q.vendor_risk_score for q in quotations}, higher_is_better=False)
    spec_scores = normalize({q.supplier: q.specification_match for q in quotations}, higher_is_better=True)

    return {
        q.supplier: {
            "price": price_scores[q.supplier],
            "delivery": delivery_scores[q.supplier],
            "warranty": warranty_scores[q.supplier],
            "payment_terms": payment_scores[q.supplier],
            "vendor_risk": risk_scores[q.supplier],
            "specification": spec_scores[q.supplier],
        }
        for q in quotations
    }


def score_quotations_weighted(
    quotations: List[Quotation], weights: EvaluationWeights = DEFAULT_EVALUATION_WEIGHTS
) -> WeightedEvaluationResult:
    """Rank quotations by a configurable, company-defined weighted policy
    instead of lowest-total-cost alone. A quotation with
    ``mandatory_requirements_met=False`` is disqualified outright --
    reported with its reason, never silently dropped and never ranked.
    A missing (``None``) data point for one criterion simply drops that
    criterion's weight out of this vendor's own score rather than
    guessing a value for it.
    """
    disqualified = [
        {"supplier": q.supplier, "reason": q.disqualification_reason or "mandatory requirements not met"}
        for q in quotations
        if not q.mandatory_requirements_met
    ]
    eligible = [q for q in quotations if q.mandatory_requirements_met]

    if not eligible:
        return WeightedEvaluationResult(
            weights=weights,
            recommended_supplier=None,
            scores=[],
            disqualified=disqualified,
            rationale="No quotation met the mandatory requirements to be evaluated."
            if disqualified
            else "No quotations to evaluate.",
        )

    per_supplier_criteria = _criterion_scores(eligible)
    weight_map = weights.model_dump()

    ranked: List[WeightedScore] = []
    for q in eligible:
        criteria = per_supplier_criteria[q.supplier]
        applicable_weight_total = sum(weight_map[c] for c, v in criteria.items() if v is not None)
        if applicable_weight_total <= 0:
            weighted_total = 0.0
        else:
            weighted_total = (
                sum(weight_map[c] * v for c, v in criteria.items() if v is not None) / applicable_weight_total
            )
        reasons = [f"landed cost {landed_cost(q):.2f} {q.currency}"]
        if q.delivery_days is not None:
            reasons.append(f"delivery in {q.delivery_days} day(s)")
        if q.warranty_months is not None:
            reasons.append(f"{q.warranty_months} month(s) warranty")
        if q.payment_terms_days is not None:
            reasons.append(f"payment terms: net {q.payment_terms_days} day(s)")
        if q.vendor_risk_score is not None:
            reasons.append(f"vendor risk score {q.vendor_risk_score:.2f}")
        ranked.append(
            WeightedScore(
                supplier=q.supplier,
                landed_cost=landed_cost(q),
                criterion_scores=criteria,
                weighted_total=round(weighted_total, 4),
                rank=0,  # filled in below, after sorting
                reasons=reasons,
            )
        )

    ranked.sort(key=lambda s: s.weighted_total, reverse=True)
    for i, s in enumerate(ranked, start=1):
        s.rank = i

    best = ranked[0]
    rationale = (
        f"{best.supplier} ranked first out of {len(ranked)} eligible quotation(s) "
        f"(weighted score {best.weighted_total:.3f}): " + "; ".join(best.reasons) + "."
    )
    if disqualified:
        rationale += " Disqualified: " + "; ".join(f"{d['supplier']} ({d['reason']})" for d in disqualified) + "."
    if len(ranked) > 1:
        runner_up = ranked[1]
        rationale += (
            f" Runner-up: {runner_up.supplier} (weighted score {runner_up.weighted_total:.3f}, "
            f"landed cost {runner_up.landed_cost:.2f} {eligible[0].currency})."
        )

    return WeightedEvaluationResult(
        weights=weights, recommended_supplier=best.supplier, scores=ranked, disqualified=disqualified, rationale=rationale
    )
