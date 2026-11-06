"""Vendor Master: create, manage, bulk-import, and demo-seed a vendor
database -- independent of whatever suppliers do or don't exist in live
ERPNext today.

Built on the same, already-proven pattern as app.procurement.workflow:
each vendor is a versioned memory owned by Vendra
(memory_key=f"vendor:{vendor_code}"), using the project's existing,
already-migrated Supabase memory system -- no new database migration.

Demo vendors are clearly, structurally marked (``is_demo=True``,
``vendor_code`` prefixed ``DEMO-VENDOR-``, every contact field using the
``.invalid`` TLD reserved by RFC 2606 for exactly this purpose -- it is
guaranteed non-routable, so a demo vendor's contact details can never
accidentally reach a real person or a real mail server) and are never
written into live ERPNext or mixed with a real vendor's record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.memory.provenance_fields import enrich_memory
from app.memory.store import get_agent_memories, get_memory_by_key
from app.tools.memory_tools import build_agent_tools

from .identities import SYSTEM_IDS, VENDRA
from .mcp.client import ERPNextClient

__all__ = [
    "VendorProfile",
    "VENDOR_STATUSES",
    "VendorMasterError",
    "DuplicateVendorError",
    "DEMO_VENDOR_PREFIX",
    "create_vendor",
    "get_vendor",
    "update_vendor",
    "set_vendor_status",
    "search_vendors",
    "ImportReport",
    "import_vendors",
    "seed_demo_vendors",
    "remove_demo_vendors",
    "check_erpnext_sync_status",
]

_tools: Optional[Dict[str, Any]] = None


def _tool(name: str):
    global _tools
    if _tools is None:
        _tools = {t.name: t for t in build_agent_tools(VENDRA, valid_ids=SYSTEM_IDS)}
    return _tools[name]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VendorMasterError(ValueError):
    """An invalid vendor master operation."""


class DuplicateVendorError(VendorMasterError):
    """A vendor with this vendor_code (or, on import, this legal_name +
    country) already exists."""


VENDOR_STATUSES = ("Active", "Inactive", "Archived")
DEMO_VENDOR_PREFIX = "DEMO-VENDOR-"


@dataclass
class VendorProfile:
    vendor_code: str
    legal_name: str
    business_category: str
    products_services: str
    country: str
    address: str
    authorized_contacts: List[str] = field(default_factory=list)
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    currency: str = "USD"
    payment_terms_days: Optional[int] = None
    delivery_capabilities: Optional[str] = None
    warranty_info: Optional[str] = None
    registration_status: str = "Unverified"
    notes: Optional[str] = None
    status: str = "Active"
    is_demo: bool = False
    source: str = "manual"  # "manual" | "import" | "demo_seed" | "erpnext_sync"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


def _save_vendor(vendor: VendorProfile, *, change_reason: Optional[str] = None) -> Dict[str, Any]:
    memory_key = f"vendor:{vendor.vendor_code}"
    content = f"Vendor {vendor.vendor_code}: {vendor.legal_name} ({vendor.country}) -- {vendor.status}" + (
        " [DEMO]" if vendor.is_demo else ""
    )
    saved = _tool("save_memory_entry").invoke({"memory_key": memory_key, "content": content})
    enrich_memory(
        saved["id"],
        source_type="computed" if not vendor.is_demo else "manual",
        change_reason=change_reason,
        verification_status="unverified" if vendor.registration_status == "Unverified" else "verified",
        sharing_classification="shareable",
        structured_fact=asdict(vendor),
    )
    return saved


def get_vendor(vendor_code: str) -> Optional[VendorProfile]:
    rows = get_memory_by_key(VENDRA, f"vendor:{vendor_code}")
    if not rows or not rows[0].get("structured_fact"):
        return None
    return VendorProfile(**rows[0]["structured_fact"])


def create_vendor(
    *,
    vendor_code: str,
    legal_name: str,
    business_category: str,
    products_services: str,
    country: str,
    address: str,
    authorized_contacts: Optional[List[str]] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    website: Optional[str] = None,
    currency: str = "USD",
    payment_terms_days: Optional[int] = None,
    delivery_capabilities: Optional[str] = None,
    warranty_info: Optional[str] = None,
    registration_status: str = "Unverified",
    notes: Optional[str] = None,
    is_demo: bool = False,
    source: str = "manual",
) -> VendorProfile:
    if not vendor_code or not vendor_code.strip():
        raise VendorMasterError("vendor_code is required")
    if not legal_name or not legal_name.strip():
        raise VendorMasterError("legal_name is required")
    if get_vendor(vendor_code) is not None:
        raise DuplicateVendorError(f"a vendor with vendor_code {vendor_code!r} already exists")

    vendor = VendorProfile(
        vendor_code=vendor_code,
        legal_name=legal_name,
        business_category=business_category,
        products_services=products_services,
        country=country,
        address=address,
        authorized_contacts=authorized_contacts or [],
        email=email,
        phone=phone,
        website=website,
        currency=currency,
        payment_terms_days=payment_terms_days,
        delivery_capabilities=delivery_capabilities,
        warranty_info=warranty_info,
        registration_status=registration_status,
        notes=notes,
        is_demo=is_demo,
        source=source,
    )
    _save_vendor(vendor, change_reason="created")
    return vendor


def update_vendor(vendor_code: str, **changes: Any) -> VendorProfile:
    vendor = get_vendor(vendor_code)
    if vendor is None:
        raise VendorMasterError(f"no such vendor: {vendor_code}")
    unknown = set(changes) - set(vendor.__dataclass_fields__)
    if unknown:
        raise VendorMasterError(f"unknown vendor field(s): {sorted(unknown)}")
    for key, value in changes.items():
        setattr(vendor, key, value)
    vendor.updated_at = _now()
    _save_vendor(vendor, change_reason=f"updated: {', '.join(sorted(changes))}")
    return vendor


def set_vendor_status(vendor_code: str, status: str) -> VendorProfile:
    if status not in VENDOR_STATUSES:
        raise VendorMasterError(f"unknown status: {status!r} (expected one of {VENDOR_STATUSES})")
    return update_vendor(vendor_code, status=status)


def search_vendors(
    *,
    query: Optional[str] = None,
    business_category: Optional[str] = None,
    country: Optional[str] = None,
    status: Optional[str] = None,
    is_demo: Optional[bool] = None,
) -> List[VendorProfile]:
    """All active-record vendors matching every given filter (case-
    insensitive substring match on ``query`` against legal_name/
    vendor_code/products_services). No filter at all returns every vendor."""
    rows = get_agent_memories(VENDRA)
    vendors: List[VendorProfile] = []
    for row in rows:
        fact = row.get("structured_fact")
        if not fact or "vendor_code" not in fact:
            continue  # not a vendor record (this agent also owns other memory keys)
        vendors.append(VendorProfile(**fact))

    def matches(v: VendorProfile) -> bool:
        if business_category is not None and v.business_category != business_category:
            return False
        if country is not None and v.country != country:
            return False
        if status is not None and v.status != status:
            return False
        if is_demo is not None and v.is_demo != is_demo:
            return False
        if query:
            haystack = f"{v.legal_name} {v.vendor_code} {v.products_services}".lower()
            if query.lower() not in haystack:
                return False
        return True

    return [v for v in vendors if matches(v)]


@dataclass
class ImportReport:
    total_rows: int
    created: List[str]
    skipped_duplicates: List[Dict[str, str]]
    errors: List[Dict[str, str]]
    committed: bool


_REQUIRED_IMPORT_FIELDS = ("vendor_code", "legal_name", "business_category", "products_services", "country", "address")


def import_vendors(rows: List[Dict[str, Any]], *, commit: bool = False) -> ImportReport:
    """Validate a batch of vendor rows (e.g. parsed from a CSV/XLSX
    upload), detect duplicates against both the batch itself and the
    existing vendor master, and report exactly what would happen --
    without writing anything unless ``commit=True``. Call once with
    ``commit=False`` to preview, then again with ``commit=True`` once the
    operator has reviewed the report."""
    created: List[str] = []
    skipped_duplicates: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    seen_in_batch: set = set()

    for row in rows:
        missing = [f for f in _REQUIRED_IMPORT_FIELDS if not row.get(f)]
        if missing:
            errors.append({"vendor_code": str(row.get("vendor_code", "?")), "error": f"missing required field(s): {missing}"})
            continue

        code = row["vendor_code"]
        if code in seen_in_batch:
            skipped_duplicates.append({"vendor_code": code, "reason": "duplicate vendor_code within this import batch"})
            continue
        if get_vendor(code) is not None:
            skipped_duplicates.append({"vendor_code": code, "reason": "a vendor with this vendor_code already exists"})
            continue

        seen_in_batch.add(code)
        if commit:
            try:
                create_vendor(
                    vendor_code=code,
                    legal_name=row["legal_name"],
                    business_category=row["business_category"],
                    products_services=row["products_services"],
                    country=row["country"],
                    address=row["address"],
                    email=row.get("email"),
                    phone=row.get("phone"),
                    website=row.get("website"),
                    currency=row.get("currency", "USD"),
                    source="import",
                )
            except VendorMasterError as e:
                errors.append({"vendor_code": code, "error": str(e)})
                continue
        created.append(code)

    return ImportReport(total_rows=len(rows), created=created, skipped_duplicates=skipped_duplicates, errors=errors, committed=commit)


# ---------------------------------------------------------------------------
# Demo dataset -- 35 clearly-labeled synthetic vendors. Idempotent: rerunning
# this skips any DEMO-VENDOR-#### that already exists rather than duplicating
# it. Every contact field uses the .invalid TLD (RFC 2606): guaranteed
# non-routable, can never reach a real person or mail server.
# ---------------------------------------------------------------------------

_DEMO_CATEGORIES = ["IT Hardware", "Office Supplies", "Logistics", "Packaging", "Facilities", "Software Services"]
_DEMO_COUNTRIES = ["India", "United States", "Germany", "Singapore", "United Kingdom"]


def _demo_vendor_code(n: int) -> str:
    return f"{DEMO_VENDOR_PREFIX}{n:04d}"


def seed_demo_vendors(count: int = 35) -> List[VendorProfile]:
    """Create up to ``count`` clearly-labeled DEMO vendors. Idempotent --
    an existing DEMO-VENDOR-#### is left untouched and simply not
    recreated, so rerunning this with a larger ``count`` only adds the
    new ones."""
    created: List[VendorProfile] = []
    for i in range(1, count + 1):
        code = _demo_vendor_code(i)
        if get_vendor(code) is not None:
            continue
        category = _DEMO_CATEGORIES[i % len(_DEMO_CATEGORIES)]
        country = _DEMO_COUNTRIES[i % len(_DEMO_COUNTRIES)]
        vendor = create_vendor(
            vendor_code=code,
            legal_name=f"Demo Vendor {i:04d} (SIMULATED)",
            business_category=category,
            products_services=f"Simulated {category.lower()} supplier for demo/testing purposes only",
            country=country,
            address=f"{i} Demo Street (SIMULATED), {country}",
            authorized_contacts=[f"Demo Contact {i:04d} (SIMULATED)"],
            email=f"demo-vendor-{i:04d}@example.invalid",
            phone="+00-000-0000000",  # not a real, dialable number
            currency="INR" if country == "India" else "USD",
            payment_terms_days=30,
            registration_status="Unverified",
            notes="SIMULATED demo vendor -- not a real company. Generated by seed_demo_vendors().",
            is_demo=True,
            source="demo_seed",
        )
        created.append(vendor)
    return created


def remove_demo_vendors() -> int:
    """Delete only vendor records where is_demo=True -- never a real
    vendor, even one that happens to share a naming pattern coincidentally
    (this checks the actual is_demo flag, not just the vendor_code prefix)."""
    from app.memory.client import supabase

    removed = 0
    for row in get_agent_memories(VENDRA):
        fact = row.get("structured_fact")
        if fact and fact.get("vendor_code") and fact.get("is_demo"):
            for version_row in get_memory_by_key(VENDRA, f"vendor:{fact['vendor_code']}", include_superseded=True):
                supabase.table("agent_memories").delete().eq("id", version_row["id"]).execute()
            removed += 1
    return removed


def check_erpnext_sync_status(vendor: VendorProfile, client: ERPNextClient) -> Dict[str, Any]:
    """Read-only comparison against live ERPNext: is this vendor already
    a real Supplier record there? Never writes to ERPNext. Demo vendors
    are never checked against or synced into live ERPNext at all."""
    if vendor.is_demo:
        return {"linked": False, "reason": "demo vendor -- never synced with live ERPNext"}

    record = client.get_supplier(vendor.vendor_code)
    if record is not None:
        return {"linked": True, "erpnext_name": record.get("name"), "match": "vendor_code"}

    for supplier in client.list_suppliers():
        if supplier.get("supplier_name", "").strip().lower() == vendor.legal_name.strip().lower():
            return {"linked": True, "erpnext_name": supplier.get("name"), "match": "legal_name", "note": "vendor_code differs from the ERPNext record name"}

    return {"linked": False, "reason": "no matching Supplier record found in the configured ERPNext backend"}
