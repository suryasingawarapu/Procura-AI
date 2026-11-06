"""Unit tests for Safe Demo Mode: the synthetic fixture data itself, the
real coordinator run against it (both scenarios), and the API route --
including an explicit check that no demo call ever reaches the real
Supabase client.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement.demo_fixtures import (
    DEMO_ITEM_CODE,
    DEMO_QUANTITY,
    DEMO_SUPPLIER_EDGE,
    DEMO_SUPPLIER_TECH,
    build_demo_seed,
)
from app.procurement.mcp.client import SeededERPNextClient
from app.procurement.coordinator import run_coordinator
from app.procurement.messages import TaskRequest

client = TestClient(app)
ENV = {"MEMORY_API_TOKEN": "admin-token"}


# -- fixture data itself -----------------------------------------------------


def test_demo_quotation_totals_match_the_agreed_demo_scenario():
    seed = build_demo_seed("eligible")
    rates = {q["supplier"]: q["rate"] for q in seed["quotations"]}
    assert rates[DEMO_SUPPLIER_TECH] * DEMO_QUANTITY == 520_000.0
    assert rates[DEMO_SUPPLIER_EDGE] * DEMO_QUANTITY == 550_000.0
    assert seed["budget"]["default"] == 600_000.0


def test_eligible_scenario_has_synthetic_compliance_documents_ineligible_does_not():
    eligible_seed = build_demo_seed("eligible")
    ineligible_seed = build_demo_seed("ineligible")
    assert eligible_seed["supplier_documents"][DEMO_SUPPLIER_TECH]
    assert eligible_seed["supplier_documents"][DEMO_SUPPLIER_EDGE]
    assert ineligible_seed["supplier_documents"] == {}


def test_demo_documents_are_unmistakably_labeled_as_simulated():
    seed = build_demo_seed("eligible")
    for docs in seed["supplier_documents"].values():
        for doc in docs:
            assert "SIMULATED" in doc["title"] and "NOT-A-REAL-DOCUMENT" in doc["title"]


# -- the real coordinator, run directly against the demo fixture ------------


def _run_demo_coordinator(scenario):
    demo_client = SeededERPNextClient(build_demo_seed(scenario))
    task = TaskRequest(
        description=f"[SIMULATED DEMO -- {scenario}] Find eligible suppliers for 10 laptops, "
        "collect/compare quotations, check the available budget, and prepare the purchase for approval."
    )
    with patch("app.procurement.systems.vendra.enrich_memory"), patch(
        "app.procurement.systems.procura.enrich_memory"
    ), patch("app.procurement.systems.finora.enrich_memory"), patch(
        "app.tools.memory_tools.save_memory", return_value={"id": "demo-mem-stub"}
    ):
        return run_coordinator(task, demo_client, item_code=DEMO_ITEM_CODE, quantity=DEMO_QUANTITY), demo_client


def test_eligible_scenario_recommends_demotech_and_requires_approval():
    result, demo_client = _run_demo_coordinator("eligible")

    assert result.blocked is False
    assert result.failed is False
    assert result.requires_approval is True
    assert result.result["recommended_supplier"] == DEMO_SUPPLIER_TECH
    assert result.result["recommended_total_cost"] == 520_000.0
    # every one of the 19 specialists is genuinely reachable: 18 auto-run
    # across the three systems (Purchase Order Preparation stays separate
    # by design), all "ok" except the ones that legitimately have nothing to
    # do: no invoices on file for a brand-new demo supplier, and Procura's
    # quotation history, which reads memory (demo mode never does)
    specialists_by_sender = {m.sender: m.payload["specialists"] for m in result.messages}
    assert all(r["status"] == "ok" for r in specialists_by_sender["vendra"].values())
    procura_statuses = {name: r["status"] for name, r in specialists_by_sender["procura"].items()}
    assert procura_statuses.pop("quotation_history") == "skipped"
    assert all(status == "ok" for status in procura_statuses.values())
    finora_statuses = {name: r["status"] for name, r in specialists_by_sender["finora"].items()}
    assert finora_statuses["payment_terms_review"] == "skipped"
    assert all(s == "ok" for name, s in finora_statuses.items() if name != "payment_terms_review")
    # no live-looking write escaped the isolated demo client either
    assert demo_client._data["purchase_orders"] == []


def test_ineligible_scenario_blocks_cleanly_with_no_rfq():
    result, demo_client = _run_demo_coordinator("ineligible")

    assert result.blocked is True
    assert result.failed is False
    assert result.requires_approval is False
    assert result.result["recommended_supplier"] is None
    assert demo_client._data["rfqs"] == []
    assert demo_client._data["purchase_orders"] == []


# -- API route ----------------------------------------------------------


@patch.dict(
    os.environ,
    {**ENV, "ERPNEXT_URL": "", "ERPNEXT_API_KEY": "", "ERPNEXT_API_SECRET": "", "ERPNEXT_USE_OFFICIAL_MCP": ""},
    clear=False,
)
def test_demo_route_eligible_matches_the_agreed_scenario_and_never_touches_real_supabase():
    with patch("app.memory.client.supabase", new_callable=MagicMock) as mock_supabase:
        response = client.post("/procurement/demo/run", json={"scenario": "eligible"}, headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
    data = response.json()
    assert data["simulated"] is True
    assert data["scenario"] == "eligible"
    assert data["task"]["result"]["recommended_supplier"] == "DEMO-SUP-TECH"
    assert data["task"]["result"]["recommended_total_cost"] == 520_000.0
    assert data["task"]["requires_approval"] is True
    mock_supabase.table.assert_not_called()


@patch.dict(
    os.environ,
    {**ENV, "ERPNEXT_URL": "", "ERPNEXT_API_KEY": "", "ERPNEXT_API_SECRET": "", "ERPNEXT_USE_OFFICIAL_MCP": ""},
    clear=False,
)
def test_demo_route_ineligible_is_blocked():
    with patch("app.memory.client.supabase", new_callable=MagicMock):
        response = client.post("/procurement/demo/run", json={"scenario": "ineligible"}, headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["blocked"] is True
    assert data["task"]["requires_approval"] is False


def test_demo_route_requires_admin_token():
    response = client.post("/procurement/demo/run", json={"scenario": "eligible"})
    assert response.status_code in (401, 403)
