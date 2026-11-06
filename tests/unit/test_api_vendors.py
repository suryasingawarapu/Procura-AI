"""Unit tests for the Vendor Master HTTP layer. app.procurement.vendor_master
itself (real Supabase read/write) is covered by
tests/integration/test_dbwrite_vendor_master.py -- these tests are about
routing, auth gating, and error-code mapping.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement import vendor_master as vm

client = TestClient(app)
ENV = {"MEMORY_API_TOKEN": "admin-token"}


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
    return vm.VendorProfile(**defaults)


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.create_vendor")
def test_create_vendor_requires_auth(mock_create):
    response = client.post("/procurement/vendors", json={
        "vendor_code": "V1", "legal_name": "X", "business_category": "x",
        "products_services": "x", "country": "x", "address": "x",
    })
    assert response.status_code == 401
    mock_create.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.create_vendor")
def test_create_vendor_success(mock_create):
    mock_create.return_value = _sample_vendor()
    response = client.post(
        "/procurement/vendors",
        json={"vendor_code": "TEST-0001", "legal_name": "Test Vendor Co", "business_category": "IT Hardware",
              "products_services": "Laptops", "country": "India", "address": "1 Test St"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 201
    assert response.json()["vendor_code"] == "TEST-0001"


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.create_vendor")
def test_duplicate_vendor_is_400_not_500(mock_create):
    mock_create.side_effect = vm.DuplicateVendorError("already exists")
    response = client.post(
        "/procurement/vendors",
        json={"vendor_code": "TEST-0001", "legal_name": "X", "business_category": "x",
              "products_services": "x", "country": "x", "address": "x"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 400


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.get_vendor", return_value=None)
def test_get_missing_vendor_is_404(mock_get):
    response = client.get("/procurement/vendors/NOPE", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 404


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.search_vendors")
def test_search_vendors_passes_filters_through(mock_search):
    mock_search.return_value = [_sample_vendor()]
    response = client.get(
        "/procurement/vendors?business_category=IT+Hardware&country=India",
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    assert len(response.json()["vendors"]) == 1
    mock_search.assert_called_once_with(
        query=None, business_category="IT Hardware", country="India", status=None, is_demo=None
    )


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.import_vendors")
def test_import_vendors_defaults_to_preview_not_committed(mock_import):
    mock_import.return_value = vm.ImportReport(total_rows=1, created=["V1"], skipped_duplicates=[], errors=[], committed=False)
    response = client.post(
        "/procurement/vendors/import",
        json={"rows": [{"vendor_code": "V1"}]},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    assert response.json()["committed"] is False
    mock_import.assert_called_once_with([{"vendor_code": "V1"}], commit=False)


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.seed_demo_vendors")
def test_demo_seed_vendors_route(mock_seed):
    mock_seed.return_value = [_sample_vendor(vendor_code="DEMO-VENDOR-0001", is_demo=True)]
    response = client.post(
        "/procurement/vendors/demo-seed", json={"count": 1}, headers={"X-API-Token": "admin-token"}
    )
    assert response.status_code == 200
    assert response.json()["simulated"] is True
    assert response.json()["created_vendor_codes"] == ["DEMO-VENDOR-0001"]


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.vendor_master.remove_demo_vendors", return_value=7)
def test_demo_reset_vendors_route(mock_remove):
    response = client.post("/procurement/vendors/demo-reset", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
    assert response.json()["removed_count"] == 7


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.get_configured_client")
@patch("app.api.procurement_routes.vendor_master.get_vendor")
@patch("app.api.procurement_routes.vendor_master.check_erpnext_sync_status")
def test_erpnext_sync_route_is_read_only(mock_check, mock_get, mock_client):
    mock_get.return_value = _sample_vendor()
    mock_client.return_value = ("fake-client", "seeded")
    mock_check.return_value = {"linked": False, "reason": "not found"}
    response = client.get("/procurement/vendors/TEST-0001/erpnext-sync", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
    assert response.json()["linked"] is False
    mock_check.assert_called_once()
