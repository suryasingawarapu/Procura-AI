"""Enrich the 30 vendors already seeded into the LIVE ERPNext instance
(see seed_live_erpnext_vendors.py) with realistic contact/address info --
email, phone, website, and a real linked Address record. They previously
had only the bare minimum to pass validation and compliance checks
(supplier_name, country, supplier_group, supplier_type, gst_category).

Every contact field uses the .invalid TLD (RFC 2606): guaranteed non-
routable, can never reach a real person or mail server -- same convention
already used for vendor_master.py's demo vendors.

Deterministic per-vendor generation (a stable hash of the vendor name, not
randomness), so rerunning this is idempotent in effect -- it just writes
the same values again.

Real write: builds its own client with allow_writes=True explicitly,
rather than touching ERPNEXT_ALLOW_WRITES in .env.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.procurement.mcp.client import ERPNextRestClient, FrappeMCPClient, ERPNextUnavailable  # noqa: E402
from seed_live_erpnext_vendors import VENDORS  # noqa: E402

_CITY_BY_COUNTRY = {
    "India": "Bengaluru",
    "United States": "Austin",
    "Singapore": "Singapore",
    "United Kingdom": "London",
    "Germany": "Berlin",
}
# A real, live-confirmed requirement (not in the doctype schema's own
# "reqd" flags): a server-side validation hook rejects an Indian Address
# with no "state" at all -- Bengaluru's real state is Karnataka.
_STATE_BY_COUNTRY = {"India": "Karnataka"}
_PHONE_FORMAT_BY_COUNTRY = {
    "India": "+91-80-{n:07d}",
    "United States": "+1-512-{n3:03d}-{n4:04d}",
    "Singapore": "+65-6{n3:03d}-{n4:04d}",
    "United Kingdom": "+44-20-{n4:04d}-{n4b:04d}",
    "Germany": "+49-30-{n7:07d}",
}
_STREETS = ["Harbor Industrial Way", "Riverside Business Park", "Commerce Avenue", "Innovation Drive", "Market Street"]


def _hash_int(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _phone_for(name: str, country: str) -> str:
    h = _hash_int(name + "|phone")
    fmt = _PHONE_FORMAT_BY_COUNTRY.get(country, "+1-555-{n4:04d}")
    return fmt.format(n=h % 10_000_000, n3=(h // 10_000) % 1000, n4=h % 10_000, n4b=(h // 10_000_000) % 10_000, n7=h % 10_000_000)


def _client() -> FrappeMCPClient:
    url = os.environ["ERPNEXT_URL"]
    key = os.environ["ERPNEXT_API_KEY"]
    secret = os.environ["ERPNEXT_API_SECRET"]
    rest = ERPNextRestClient(url, key, secret, allow_writes=True)
    return FrappeMCPClient(rest)


def _create_address(client: FrappeMCPClient, *, supplier_name: str, country: str, street: str, city: str) -> str:
    data = {
        "address_title": supplier_name,
        "address_type": "Billing",
        "address_line1": street,
        "city": city,
        "country": country,
        "gst_category": "Unregistered",
        "links": [{"link_doctype": "Supplier", "link_name": supplier_name}],
    }
    if country in _STATE_BY_COUNTRY:
        data["state"] = _STATE_BY_COUNTRY[country]
    doc = client._call_tool("create_document", {"doctype": "Address", "data": data})
    return doc["name"]


def main() -> int:
    client = _client()
    enriched = []
    failed = []

    for category, vendors in VENDORS.items():
        for name, country in vendors:
            slug = _slug(name)
            h = _hash_int(name)
            city = _CITY_BY_COUNTRY.get(country, "Unknown")
            street = f"{1 + h % 200} {_STREETS[h % len(_STREETS)]}"
            email = f"sales@{slug}.invalid"
            phone = _phone_for(name, country)
            website = f"https://{slug}.invalid"

            existing = client.get_supplier(name) or {}
            if existing.get("supplier_primary_address"):
                print(f"already enriched, skipping: {name}")
                continue

            try:
                client.update_supplier(supplier_name=name, email_id=email, mobile_no=phone, website=website)
                address_name = _create_address(client, supplier_name=name, country=country, street=street, city=city)
                client.update_supplier(supplier_name=name, supplier_primary_address=address_name)
                enriched.append(name)
                print(f"enriched: {name} -- {email} | {phone} | {website} | {street}, {city}, {country}")
            except ERPNextUnavailable as exc:
                failed.append((name, str(exc)))
                print(f"FAILED: {name}: {exc}")

    print()
    print(f"Enriched: {len(enriched)}")
    print(f"Failed: {len(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
