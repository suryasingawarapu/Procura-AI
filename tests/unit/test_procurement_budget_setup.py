"""Unit tests for demo Budget creation planning. Pure logic, no ERPNext
call involved -- every case supplies (or omits) already-discovered values."""

from __future__ import annotations

from app.procurement.budget_setup import plan_demo_budget


def test_plan_demo_budget_ready_when_all_real_values_are_known():
    result = plan_demo_budget(
        company="Procurement AI Demo",
        fiscal_year="2026-2027",
        cost_center="Main - PAD",
        account="Miscellaneous Expenses - PAD",
        budget_amount=1_100_000.0,
    )

    assert result["ready"] is True
    payload = result["payload"]
    assert payload["company"] == "Procurement AI Demo"
    assert payload["cost_center"] == "Main - PAD"
    assert payload["account"] == "Miscellaneous Expenses - PAD"
    assert payload["from_fiscal_year"] == payload["to_fiscal_year"] == "2026-2027"
    assert payload["budget_against"] == "Cost Center"
    assert payload["budget_amount"] == 1_100_000.0
    assert payload["distribution_frequency"] == "Yearly"


def test_plan_demo_budget_refuses_to_invent_missing_values():
    """Never fabricates a Company/Fiscal Year/Cost Center/Account/amount --
    reports exactly what's missing instead."""
    result = plan_demo_budget(
        company="Procurement AI Demo",
        fiscal_year=None,
        cost_center="Main - PAD",
        account=None,
        budget_amount=None,
    )

    assert result["ready"] is False
    assert "fiscal_year" in result["missing"]
    assert "account" in result["missing"]
    assert "budget_amount" in result["missing"]
    assert "company" not in result["missing"]
    assert "cost_center" not in result["missing"]
    assert "payload" not in result


def test_plan_demo_budget_rejects_an_invalid_distribution_frequency():
    result = plan_demo_budget(
        company="Procurement AI Demo",
        fiscal_year="2026-2027",
        cost_center="Main - PAD",
        account="Miscellaneous Expenses - PAD",
        budget_amount=1000.0,
        distribution_frequency="Daily",
    )

    assert result["ready"] is False
    assert any("distribution_frequency" in m for m in result["missing"])
