"""Unit tests for the deterministic (non-LLM) requirement parser --
app.procurement.task_orchestrator.parse_procurement_requirement. No
database involved."""

from __future__ import annotations

from datetime import date, timedelta

from app.procurement.task_orchestrator import parse_procurement_requirement


def test_parses_the_brief_s_own_example_exactly():
    text = (
        "Purchase 100 laptops with 16 GB RAM and 512 GB SSD, within ₹60 lakh, "
        "delivered to our Mumbai office within 30 days."
    )
    result = parse_procurement_requirement(text)
    assert result["estimated_budget"] == 6_000_000.0
    assert result["currency"] == "INR"
    assert result["delivery_location"] == "Mumbai office"
    assert result["required_date"] == (date.today() + timedelta(days=30)).isoformat()


def test_parses_a_plain_dollar_budget_without_an_indian_unit():
    result = parse_procurement_requirement("Purchase 20 units within $50000, delivered to our Chicago warehouse within 15 days.")
    assert result["estimated_budget"] == 50_000.0
    assert result["currency"] == "USD"
    assert result["delivery_location"] == "Chicago warehouse"


def test_delivery_location_recognizes_for_our_x_office_phrasing():
    """Regression test for a real bug found live: 'for our Chennai office'
    (a perfectly natural way to say it) matched neither the original
    "delivered to" pattern nor anything else, so delivery_location stayed
    None even though a location was clearly given -- the task then got
    stuck in "Waiting For Information" forever."""
    result = parse_procurement_requirement(
        "Purchase 4 months of facility maintenance within ₹70,000 for our Chennai office within 10 days."
    )
    assert result["delivery_location"] == "Chennai office"


def test_delivery_location_for_phrasing_requires_a_real_location_noun():
    """'for our <X>' must not be read as a delivery location unless X is
    actually a place (office/warehouse/etc.) -- 'for our engineering team'
    is a reason, not a destination, and must not be guessed."""
    result = parse_procurement_requirement("Buy 5 desks for our engineering team, within 50000, within 10 days.")
    assert result["delivery_location"] is None


def test_parses_crore_unit():
    result = parse_procurement_requirement("Need servers within ₹2 crore for the Pune data center.")
    assert result["estimated_budget"] == 20_000_000.0
    assert result["currency"] == "INR"


def test_missing_fields_are_none_not_guessed():
    result = parse_procurement_requirement("We need some laptops soon.")
    assert result["estimated_budget"] is None
    assert result["currency"] is None
    assert result["delivery_location"] is None
    assert result["required_date"] is None


def test_budget_phrase_without_the_literal_word_of_still_parses_correctly():
    """Regression test for a real bug found live: an LLM paraphrase of
    "within 200000" into "within budget 200000" (no literal "of") used to
    fail to match the budget pattern at all, and .search() then silently
    fell through to the LATER "within 15 days" deadline, wrongly recording
    a budget of 15. "of" is now optional, so the real, earlier "budget
    200000" phrase is what matches -- not the deadline."""
    result = parse_procurement_requirement(
        "Purchase 20 ergonomic office chairs within budget 200000, delivered to our Mumbai office within 15 days."
    )
    assert result["estimated_budget"] == 200_000.0
    assert result["required_date"] == (date.today() + timedelta(days=15)).isoformat()


def test_a_deadline_in_days_is_never_mistaken_for_the_budget_when_nothing_else_matches():
    """When the text has no recognizable budget phrase at all, the only
    "within <N>" in the text must not be misread as a budget just because
    it structurally fits -- "within 15 days" is a deadline, not money."""
    result = parse_procurement_requirement(
        "Purchase 20 ergonomic office chairs, delivered to our Mumbai office within 15 days."
    )
    assert result["estimated_budget"] is None  # honestly missing, not silently 15
    assert result["required_date"] == (date.today() + timedelta(days=15)).isoformat()
