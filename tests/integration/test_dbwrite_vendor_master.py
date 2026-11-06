"""Acceptance tests for the Vendor Master (app.procurement.vendor_master).
Writes real rows under the real "vendra" identity -- opt-in:

    $env:MEMORY_ALLOW_WRITE = "1"
    .\\.venv\\Scripts\\python.exe -m pytest -m dbwrite -v

Each test's own vendor(s) are cleaned up immediately after that test via
the ``make_vendor``/``demo_seed`` fixtures below -- per-test teardown, not
a single end-of-module cleanup.
"""

from __future__ import annotations

import os

import pytest

from app.memory.client import supabase
from app.procurement import vendor_master as vm
from app.procurement.identities import VENDRA
from app.procurement.mcp.client import SeededERPNextClient

pytestmark = [
    pytest.mark.dbwrite,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MEMORY_ALLOW_WRITE", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="set MEMORY_ALLOW_WRITE=1 to run the database write tests",
    ),
]


def _delete_all_versions(vendor_code: str) -> None:
    rows = (
        supabase.table("agent_memories")
        .select("id")
        .eq("agent_id", VENDRA)
        .eq("memory_key", f"vendor:{vendor_code}")
        .execute()
        .data
        or []
    )
    for row in rows:
        supabase.table("agent_memories").delete().eq("id", row["id"]).execute()


@pytest.fixture
def make_vendor():
    created_codes: list[str] = []

    def _make(**overrides):
        import uuid

        defaults = dict(
            vendor_code=f"TEST-VENDOR-{uuid.uuid4().hex[:8].upper()}",
            legal_name="Test Vendor Co",
            business_category="IT Hardware",
            products_services="Laptops",
            country="India",
            address="1 Test Street",
        )
        defaults.update(overrides)
        vendor = vm.create_vendor(**defaults)
        created_codes.append(vendor.vendor_code)
        return vendor

    yield _make

    for code in created_codes:
        _delete_all_versions(code)


@pytest.fixture
def demo_seed():
    """Seeds a SMALL demo batch (5, not the full 35) for fast tests, and
    guarantees removal afterward even if the test's own
    remove_demo_vendors() call didn't run (e.g. the test itself failed)."""
    created = vm.seed_demo_vendors(count=5)
    yield created
    vm.remove_demo_vendors()


def test_create_and_retrieve_a_vendor(make_vendor):
    vendor = make_vendor(legal_name="Acme Hardware Pvt Ltd")
    fetched = vm.get_vendor(vendor.vendor_code)
    assert fetched is not None
    assert fetched.legal_name == "Acme Hardware Pvt Ltd"
    assert fetched.status == "Active"
    assert fetched.is_demo is False


def test_duplicate_vendor_code_is_rejected(make_vendor):
    vendor = make_vendor()
    with pytest.raises(vm.DuplicateVendorError):
        vm.create_vendor(
            vendor_code=vendor.vendor_code, legal_name="Someone Else", business_category="x",
            products_services="x", country="x", address="x",
        )


def test_update_vendor_creates_a_new_version(make_vendor):
    vendor = make_vendor()
    updated = vm.update_vendor(vendor.vendor_code, business_category="Software Services")
    assert updated.business_category == "Software Services"
    assert vm.get_vendor(vendor.vendor_code).business_category == "Software Services"


def test_search_vendors_by_category_and_country(make_vendor):
    v1 = make_vendor(business_category="Packaging", country="Germany")
    v2 = make_vendor(business_category="IT Hardware", country="India")
    results = vm.search_vendors(business_category="Packaging")
    codes = {r.vendor_code for r in results}
    assert v1.vendor_code in codes
    assert v2.vendor_code not in codes


def test_import_preview_does_not_write_until_commit(make_vendor):
    import uuid

    code = f"TEST-VENDOR-{uuid.uuid4().hex[:8].upper()}"
    rows = [{
        "vendor_code": code, "legal_name": "Imported Co", "business_category": "Logistics",
        "products_services": "Freight", "country": "Singapore", "address": "1 Import Rd",
    }]
    preview = vm.import_vendors(rows, commit=False)
    assert preview.created == [code]
    assert preview.committed is False
    assert vm.get_vendor(code) is None  # preview only -- nothing written yet

    committed = vm.import_vendors(rows, commit=True)
    assert committed.created == [code]
    try:
        assert vm.get_vendor(code) is not None
    finally:
        _delete_all_versions(code)


def test_import_detects_duplicates_within_batch_and_against_existing(make_vendor):
    existing = make_vendor()
    rows = [
        {"vendor_code": "DUP-1", "legal_name": "A", "business_category": "x", "products_services": "x", "country": "x", "address": "x"},
        {"vendor_code": "DUP-1", "legal_name": "B", "business_category": "x", "products_services": "x", "country": "x", "address": "x"},
        {"vendor_code": existing.vendor_code, "legal_name": "C", "business_category": "x", "products_services": "x", "country": "x", "address": "x"},
    ]
    report = vm.import_vendors(rows, commit=False)
    assert report.created == ["DUP-1"]
    assert len(report.skipped_duplicates) == 2


def test_import_reports_missing_required_fields_as_errors():
    report = vm.import_vendors([{"vendor_code": "BAD-1", "legal_name": "Incomplete Co"}], commit=False)
    assert report.created == []
    assert len(report.errors) == 1
    assert "BAD-1" == report.errors[0]["vendor_code"]


def test_demo_seed_creates_vendors_and_is_idempotent(demo_seed):
    assert len(demo_seed) == 5
    assert all(v.is_demo for v in demo_seed)
    assert all(v.vendor_code.startswith(vm.DEMO_VENDOR_PREFIX) for v in demo_seed)
    assert all(v.email.endswith("@example.invalid") for v in demo_seed)  # safe, non-routable placeholder

    # rerunning with the same count must not create duplicates
    second_pass = vm.seed_demo_vendors(count=5)
    assert second_pass == []  # every DEMO-VENDOR-0001..0005 already existed


def test_remove_demo_vendors_never_touches_a_real_vendor(make_vendor, demo_seed):
    real_vendor = make_vendor(legal_name="Real Company That Must Survive")
    removed_count = vm.remove_demo_vendors()
    assert removed_count >= 5
    assert vm.get_vendor(real_vendor.vendor_code) is not None  # untouched
    assert all(vm.get_vendor(v.vendor_code) is None for v in demo_seed)  # genuinely gone


def test_check_erpnext_sync_status_demo_vendor_never_checked_against_live(demo_seed):
    client = SeededERPNextClient()
    status = vm.check_erpnext_sync_status(demo_seed[0], client)
    assert status["linked"] is False
    assert "demo" in status["reason"].lower()


def test_check_erpnext_sync_status_finds_a_real_match_by_vendor_code(make_vendor):
    client = SeededERPNextClient()
    vendor = make_vendor(vendor_code="SUP-0001", legal_name="whatever")  # matches the seeded fixture's real supplier
    status = vm.check_erpnext_sync_status(vendor, client)
    assert status["linked"] is True
    assert status["match"] == "vendor_code"


def test_check_erpnext_sync_status_reports_no_match_honestly(make_vendor):
    client = SeededERPNextClient()
    vendor = make_vendor(legal_name="Totally Unrelated Co")
    status = vm.check_erpnext_sync_status(vendor, client)
    assert status["linked"] is False
