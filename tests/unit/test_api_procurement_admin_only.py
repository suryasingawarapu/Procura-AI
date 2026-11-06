"""Regression tests for a real security gap found in a full code audit: a
scoped per-agent token (e.g. MEMORY_API_TOKEN_VENDRA, meant to authorize
only that agent's own resources -- see app/api/auth.py's own docstrings)
could read and write ANY Purchase Request or vendor record, because those
routes only checked require_auth (any valid token) instead of
authorize_admin (admin token only), unlike /tasks/start, /chat, /approve,
and /purchase-orders, which already did this correctly.

POST /requests (create) is deliberately excluded here -- its own docstring
documents that any authenticated caller creating a Purchase Request is
intentional ("per spec, authorized employees"), unlike reading/modifying
an existing one.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement import vendor_master, workflow

client = TestClient(app)
ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}


def _sample_pr(**overrides):
    base = dict(
        pr_id="PR-TEST0001", requested_by="jane@example.com", item_description="laptops", quantity=10,
        estimated_budget=600000.0, delivery_location="Mumbai", required_date="2026-12-01",
        business_justification="refresh", status="Draft",
    )
    base.update(overrides)
    return workflow.PurchaseRequest(**base)


def _sample_vendor(**overrides):
    defaults = dict(
        vendor_code="TEST-0001", legal_name="Test Vendor Co", business_category="IT Hardware",
        products_services="Laptops", country="India", address="1 Test St", authorized_contacts=[],
        email=None, phone=None, website=None, currency="USD", payment_terms_days=None,
        delivery_capabilities=None, warranty_info=None, registration_status="Unverified", notes=None,
        status="Active", is_demo=False, source="manual", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return vendor_master.VendorProfile(**defaults)


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.get_purchase_request")
def test_scoped_token_cannot_read_an_arbitrary_purchase_request(mock_get):
    mock_get.return_value = _sample_pr()
    response = client.get("/procurement/requests/PR-TEST0001", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_get.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.update_purchase_request")
def test_scoped_token_cannot_patch_an_arbitrary_purchase_request(mock_update):
    response = client.patch(
        "/procurement/requests/PR-TEST0001", json={"item_code": "X"}, headers={"X-API-Token": "vendra-token"}
    )
    assert response.status_code == 403
    mock_update.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.transition_purchase_request")
def test_scoped_token_cannot_transition_an_arbitrary_purchase_request(mock_transition):
    response = client.post(
        "/procurement/requests/PR-TEST0001/transition",
        json={"new_status": "Cancelled"},
        headers={"X-API-Token": "vendra-token"},
    )
    assert response.status_code == 403
    mock_transition.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.get_approval_history")
def test_scoped_token_cannot_read_approval_history(mock_history):
    response = client.get("/procurement/requests/PR-TEST0001/approvals", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_history.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.create_vendor")
def test_scoped_token_cannot_create_a_vendor(mock_create):
    response = client.post(
        "/procurement/vendors",
        json={
            "vendor_code": "X-0001", "legal_name": "X", "business_category": "IT", "products_services": "X",
            "country": "India", "address": "1 St",
        },
        headers={"X-API-Token": "vendra-token"},
    )
    assert response.status_code == 403
    mock_create.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.get_vendor")
def test_scoped_token_cannot_read_an_arbitrary_vendor(mock_get):
    mock_get.return_value = _sample_vendor()
    response = client.get("/procurement/vendors/TEST-0001", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_get.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.search_vendors")
def test_scoped_token_cannot_search_vendors(mock_search):
    response = client.get("/procurement/vendors", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_search.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.update_vendor")
def test_scoped_token_cannot_update_a_vendor(mock_update):
    response = client.patch(
        "/procurement/vendors/TEST-0001", json={"notes": "hijacked"}, headers={"X-API-Token": "vendra-token"}
    )
    assert response.status_code == 403
    mock_update.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.seed_demo_vendors")
def test_scoped_token_cannot_seed_demo_vendors(mock_seed):
    response = client.post(
        "/procurement/vendors/demo-seed", json={"count": 1}, headers={"X-API-Token": "vendra-token"}
    )
    assert response.status_code == 403
    mock_seed.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.remove_demo_vendors")
def test_scoped_token_cannot_reset_demo_vendors(mock_reset):
    response = client.post("/procurement/vendors/demo-reset", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_reset.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.import_vendors")
def test_scoped_token_cannot_import_vendors(mock_import):
    response = client.post(
        "/procurement/vendors/import", json={"rows": [], "commit": False}, headers={"X-API-Token": "vendra-token"}
    )
    assert response.status_code == 403
    mock_import.assert_not_called()


# -- the admin token must still work on every one of these ------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.get_purchase_request")
def test_admin_token_can_still_read_a_purchase_request(mock_get):
    mock_get.return_value = _sample_pr()
    response = client.get("/procurement/requests/PR-TEST0001", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200


@patch.dict(os.environ, ENV, clear=False)
@patch("app.procurement.vendor_master.search_vendors", return_value=[])
def test_admin_token_can_still_search_vendors(mock_search):
    response = client.get("/procurement/vendors", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
