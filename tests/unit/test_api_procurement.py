"""Unit tests for the procurement coordinator's API surface: auth gating,
the admin-only task/purchase-order endpoints, the explicit confirm gate,
and the integration health check. The coordinator itself is mocked here
(it's already covered end to end in test_procurement_coordinator.py) --
these tests are about the HTTP layer: status codes and gating.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement.messages import TaskResult

client = TestClient(app)

ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}


# -- /procurement/tasks ---------------------------------------------------


@patch.dict(os.environ, {}, clear=False)
def test_create_task_auth_not_configured_fails_closed():
    # A real deployment's .env (loaded at import time by app.memory.client)
    # may configure an admin token and/or per-agent scoped tokens -- pop
    # every one of them so "not configured" is genuinely simulated, not
    # just the admin token while a real scoped token silently remains.
    for key in ("MEMORY_API_TOKEN", "MEMORY_API_TOKEN_VENDRA", "MEMORY_API_TOKEN_PROCURA", "MEMORY_API_TOKEN_FINORA"):
        os.environ.pop(key, None)
    response = client.post("/procurement/tasks", json={"description": "x", "item_code": "ITEM-1"})
    assert response.status_code == 503


@patch.dict(os.environ, ENV, clear=False)
def test_create_task_rejects_scoped_non_admin_token():
    response = client.post(
        "/procurement/tasks",
        json={"description": "x", "item_code": "ITEM-1"},
        headers={"X-API-Token": "vendra-token"},
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.get_configured_client")
@patch("app.api.procurement_routes.run_coordinator")
def test_create_task_success_with_admin_token(mock_run, mock_get_client):
    mock_get_client.return_value = (MagicMock(), "seeded")
    mock_run.return_value = TaskResult(
        task_id="task-1",
        correlation_id="corr-1",
        systems_invoked=["vendra", "procura", "finora"],
        requires_approval=True,
        approval_summary="Recommend X",
        result={"recommended_supplier": "SUP-0001"},
    )

    response = client.post(
        "/procurement/tasks",
        json={"description": "Find eligible suppliers for 20 laptops", "item_code": "ITEM-LAPTOP-14", "quantity": 20},
        headers={"X-API-Token": "admin-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["systems_invoked"] == ["vendra", "procura", "finora"]
    assert data["requires_approval"] is True
    assert data["failed"] is False


@patch.dict(os.environ, ENV, clear=False)
def test_create_task_validates_request_body():
    response = client.post(
        "/procurement/tasks", json={"description": "", "item_code": "ITEM-1"}, headers={"X-API-Token": "admin-token"}
    )
    assert response.status_code == 422  # description min_length=1


# -- /procurement/purchase-orders -----------------------------------------


@patch.dict(os.environ, ENV, clear=False)
def test_purchase_order_requires_explicit_confirm():
    response = client.post(
        "/procurement/purchase-orders",
        json={"supplier": "SUP-0001", "item_code": "ITEM-LAPTOP-14", "quantity": 20, "confirm": False},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 400


@patch.dict(os.environ, ENV, clear=False)
def test_purchase_order_rejects_scoped_token():
    response = client.post(
        "/procurement/purchase-orders",
        json={"supplier": "SUP-0001", "item_code": "ITEM-LAPTOP-14", "quantity": 20, "confirm": True},
        headers={"X-API-Token": "vendra-token"},
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.get_configured_client")
def test_purchase_order_success_with_confirm_true(mock_get_client):
    from app.procurement.mcp.client import SeededERPNextClient

    mock_get_client.return_value = (SeededERPNextClient(), "seeded")

    response = client.post(
        "/procurement/purchase-orders",
        json={"supplier": "SUP-0001", "item_code": "ITEM-LAPTOP-14", "quantity": 20, "confirm": True},
        headers={"X-API-Token": "admin-token"},
    )

    assert response.status_code == 201
    assert response.json()["purchase_order"]["supplier"] == "SUP-0001"
    assert response.json()["backend"] == "seeded"


# -- /procurement/health ---------------------------------------------------


@patch.dict(
    os.environ,
    {
        **ENV,
        # `app.memory.client` loads the real `.env` at import time, so the
        # real ERPNEXT_*/backend-selection vars are already sitting in
        # os.environ by the time this test runs; overriding just the keys
        # above (clear=False) would leave them in place and this health
        # check would report whatever backend the real deployment actually
        # has configured -- never "seeded". Explicitly blanking the ones
        # get_configured_client() checks reproduces "not configured" for
        # this test without clearing os.environ wholesale (which would
        # also drop PATH/etc. the test client's HTTP stack needs).
        "ERPNEXT_URL": "",
        "ERPNEXT_API_KEY": "",
        "ERPNEXT_API_SECRET": "",
        "ERPNEXT_USE_OFFICIAL_MCP": "",
        "ERPNEXT_MCP_SERVER_ENTRY": "",
    },
    clear=False,
)
def test_integration_health_never_exposes_secret_values():
    response = client.get("/procurement/health", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
    data = response.json()
    assert data["erpnext_backend"] == "seeded"  # no ERPNEXT_URL configured in ENV
    assert "admin-token" not in response.text
    assert "vendra-token" not in response.text
