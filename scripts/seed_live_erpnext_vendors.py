"""Seed a real, realistic vendor pool into the LIVE ERPNext instance
(erpnext_url from .env) -- Suppliers, a handful of Items, and a Supplier
Quotation per vendor -- so the chat-driven Live mode has real data to
search, filter, and compare, not just the 1-2 fixtures from earlier
manual testing.

Real write, real external system: builds its own client with
allow_writes=True, explicitly, rather than touching ERPNEXT_ALLOW_WRITES
in .env -- so the running API server's own write-gate for RFQ/PO creation
stays exactly as configured (off) after this runs.

Idempotent: skips any Supplier whose supplier_name already exists.

Schema (field names, required-ness, valid Supplier Group / Item Group /
UOM / Country / gst_category values) was confirmed live against this
instance via get_doctype_schema and by reading back its two existing real
records before writing this script -- nothing here is guessed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.procurement.mcp.client import ERPNextRestClient, FrappeMCPClient, ERPNextUnavailable  # noqa: E402

# Matches app.procurement.subagents.vendra's REQUIRED_SUPPLIER_DOCUMENTS_BY_COUNTRY
# exactly (PAN Card/GST Registration Certificate for India, W-9 Tax
# Form/Business License everywhere else) -- without a real, matching
# attachment on file, the real, unmodified compliance check correctly
# reports every one of these suppliers as non-compliant and the whole
# live pipeline blocks before any quotation is even compared.
_DOC_TITLES_INDIA = ["SIMULATED-PAN-CARD-TEST-NOT-A-REAL-DOCUMENT.txt", "SIMULATED-GST-REGISTRATION-CERTIFICATE-TEST-NOT-A-REAL-DOCUMENT.txt"]
_DOC_TITLES_OTHER = ["SIMULATED-W-9-TAX-FORM-TEST-NOT-A-REAL-DOCUMENT.txt", "SIMULATED-BUSINESS-LICENSE-TEST-NOT-A-REAL-DOCUMENT.txt"]

COMPANY = "Procurement AI Demo"
CURRENCY = "INR"

# category -> (item_code, item_name, item_group, supplier_group, unit_rate, qty)
CATEGORIES = {
    "IT Hardware": ("LAPTOP-14IN", "14-inch Business Laptop", "Products", "Hardware", 55_000.0, 10),
    "Office Supplies": ("OFFICE-CHAIR-STD", "Ergonomic Office Chair", "Products", "Local", 8_500.0, 20),
    "Logistics": ("FREIGHT-STD", "Standard Freight Delivery Service", "Services", "Distributor", 12_000.0, 5),
    "Packaging": ("PACK-BOX-MED-50", "Medium Shipping Carton (Pack of 50)", "Products", "Raw Material", 2_200.0, 15),
    "Facilities": ("FACILITY-MAINT-MO", "Facility Maintenance Service (Monthly)", "Services", "Services", 18_000.0, 3),
    "Software Services": ("SOFTWARE-LIC-ANNUAL", "Annual Software License (Per Seat)", "Services", "Services", 6_500.0, 25),
}

VENDORS = {
    "IT Hardware": [
        ("Northbridge Compute Supply", "India"),
        ("Clearline Hardware Partners", "United States"),
        ("Vantage Systems Trading", "Singapore"),
        ("Silverline Technologies Pvt Ltd", "India"),
        ("Apex Micro Distributors", "United Kingdom"),
    ],
    "Office Supplies": [
        ("Brightdesk Office Solutions", "India"),
        ("Meridian Stationery Co", "United States"),
        ("Parkview Office Essentials", "United Kingdom"),
        ("Gracewood Supplies Ltd", "Germany"),
        ("Crestline Business Products", "India"),
    ],
    "Logistics": [
        ("Swiftline Freight Services", "India"),
        ("Oceanic Cargo Movers", "Singapore"),
        ("Rapidway Logistics Pvt Ltd", "India"),
        ("Continental Shipping Partners", "Germany"),
        ("BlueArrow Transport Co", "United States"),
    ],
    "Packaging": [
        ("Durapack Industries", "India"),
        ("Greenfold Packaging Co", "United States"),
        ("Sturdybox Manufacturing", "India"),
        ("Ecotainer Solutions Ltd", "United Kingdom"),
        ("Crateworks Supply", "Germany"),
    ],
    "Facilities": [
        ("Primecare Facility Services", "India"),
        ("Urban Maintenance Group", "United States"),
        ("ClearView Facilities Ltd", "United Kingdom"),
        ("Hearthstone Property Services", "India"),
        ("Summit Facility Partners", "Singapore"),
    ],
    "Software Services": [
        ("CloudPeak Software Solutions", "United States"),
        ("Nimbus Technologies Ltd", "United Kingdom"),
        ("Brightlogic Software Co", "India"),
        ("Datastream Systems Pvt Ltd", "India"),
        ("Vertex Cloud Services", "Singapore"),
    ],
}


def _client() -> FrappeMCPClient:
    url = os.environ["ERPNEXT_URL"]
    key = os.environ["ERPNEXT_API_KEY"]
    secret = os.environ["ERPNEXT_API_SECRET"]
    rest = ERPNextRestClient(url, key, secret, allow_writes=True)
    return FrappeMCPClient(rest)


def main() -> int:
    client = _client()

    # list_suppliers()/list_items() only return the live MCP tool's
    # default page (~20 rows) -- confirmed live: a first run of this
    # script left >20 suppliers, and a plain list_suppliers() call on a
    # second run silently missed some of them, causing spurious
    # "already exists" errors on re-create. Query the underlying tool
    # directly with an explicit limit instead.
    existing = {
        s["supplier_name"] for s in client._list_documents("Supplier", fields=["supplier_name"], limit=500)
    }
    existing_items = {i["name"] for i in client._list_documents("Item", fields=["name"], limit=500)}

    created_items = []
    for category, (item_code, item_name, item_group, _group, _rate, _qty) in CATEGORIES.items():
        if item_code in existing_items:
            print(f"item exists, skipping: {item_code}")
            continue
        client.create_item(item_code=item_code, item_name=item_name, item_group=item_group)
        created_items.append(item_code)
        print(f"created item: {item_code} ({item_name})")

    created_suppliers = []
    created_quotations = []
    created_documents = []
    for category, vendors in VENDORS.items():
        item_code, _item_name, _item_group, supplier_group, unit_rate, qty = CATEGORIES[category]
        for i, (name, country) in enumerate(vendors):
            is_new_supplier = name not in existing
            if is_new_supplier:
                try:
                    client.create_supplier(
                        supplier_name=name,
                        country=country,
                        supplier_group=supplier_group,
                        supplier_type="Company",
                        gst_category="Unregistered",
                    )
                    created_suppliers.append(name)
                    print(f"created supplier: {name} ({country}, {category})")
                except ERPNextUnavailable as exc:
                    if "Duplicate entry" in str(exc):
                        # Belt-and-suspenders against the same pagination
                        # gap the upfront existence check now avoids --
                        # treat it as already existing rather than
                        # skipping its quotation/compliance-document step.
                        print(f"supplier already existed: {name}")
                        is_new_supplier = False
                    else:
                        print(f"FAILED to create supplier {name}: {exc}")
                        continue
            else:
                print(f"supplier exists, skipping: {name}")

            # Deterministic per-vendor variation -- same idea as the demo
            # vendor-pool seed: 0.80x..1.20x of the category's base rate,
            # a spread of delivery lead times via schedule_date, and
            # ~2 of every 3 vendors carrying compliant evidence (so the
            # real compliance check has a genuine mix to filter, not an
            # all-pass or all-fail scripted outcome).
            h = sum(ord(c) for c in name)
            rate = round(unit_rate * (0.80 + (h % 41) / 100.0), 2)
            delivery_days = 3 + (h % 20)
            import datetime as _dt

            schedule_date = (_dt.date.today() + _dt.timedelta(days=delivery_days)).isoformat()

            if is_new_supplier:
                try:
                    quote = client.create_supplier_quotation(
                        supplier=name,
                        company=COMPANY,
                        currency=CURRENCY,
                        items=[{"item_code": item_code, "qty": qty, "rate": rate, "schedule_date": schedule_date}],
                    )
                    created_quotations.append(quote.get("name") if isinstance(quote, dict) else None)
                    print(f"  quoted {name}: {rate}/unit x {qty}, delivery in {delivery_days}d")
                except ERPNextUnavailable as exc:
                    print(f"  FAILED to create quotation for {name}: {exc}")

            has_docs = bool(client.list_supplier_documents(name))
            if not has_docs and h % 3 != 0:
                titles = _DOC_TITLES_INDIA if country == "India" else _DOC_TITLES_OTHER
                for title in titles:
                    try:
                        client.create_supplier_document(supplier_name=name, file_name=title)
                        created_documents.append((name, title))
                    except ERPNextUnavailable as exc:
                        print(f"  FAILED to attach document for {name}: {exc}")
                print(f"  attached compliance documents for {name}")
            elif not has_docs:
                print(f"  left {name} without compliance documents (deliberately -- a real mix, not all-pass)")

    print()
    print(f"Items created: {len(created_items)}")
    print(f"Suppliers created: {len(created_suppliers)}")
    print(f"Quotations created: {len(created_quotations)}")
    print(f"Compliance documents attached: {len(created_documents)}")
    print()
    print("Item codes to reference in a Live-mode chat query:")
    for category, (item_code, item_name, *_rest) in CATEGORIES.items():
        print(f"  {item_code}  --  {item_name}  ({category})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
