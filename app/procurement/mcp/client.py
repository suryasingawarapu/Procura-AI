"""ERPNext access behind one narrow, auditable interface.

MCP SERVER VERIFICATION (done before writing any of this, per spec):
  No official ERPNext MCP server exists. Checked two real candidates via
  the GitHub API and their READMEs:

  * frappe/mcp (official Frappe org, MIT, Python, 170 stars, pushed within
    the last few months) -- turns a Frappe/ERPNext site itself into an MCP
    server. Requires installing an app ON the ERPNext instance and OAuth2
    (only works smoothly on Frappe versions with frappe#33188 merged).
    README calls it "highly experimental," tools-only (no resources/
    prompts yet). More invasive to set up, less mature.
  * rakeshgangwar/erpnext-mcp-server (MIT, TypeScript/Node, 127 stars,
    pushed within the last few months) -- a standalone server that calls
    ERPNext's existing REST API from outside, authenticating with a plain
    API key/secret (ERPNEXT_URL / ERPNEXT_API_KEY / ERPNEXT_API_SECRET).
    Exposes 11 tools: get_doctypes, get_doctype_fields, get_documents,
    get_document, create_document, update_document, delete_document,
    submit_document, cancel_document, call_method, run_report. No
    documented read-only mode or permission scoping -- delete_document is
    permanent and call_method can invoke any whitelisted server method,
    with no built-in way to disable either. Lower-friction to adopt (no
    app install on the ERPNext side), but its write/delete tools must be
    kept out of what any system here is allowed to call, and "restricted
    API account" / "read-only initially" (per spec) has to be enforced on
    the ERPNext side (a dedicated user whose role permissions are
    read-only), since the server itself does not enforce it.

  Neither was installed or run: there is no live ERPNext instance, no
  Node.js MCP transport here, and no credentials to test either server
  against. Writing an untested MCP-protocol client against a library that
  was never even installed would be exactly the kind of "invented
  integration" the spec says not to produce.

WHAT'S IMPLEMENTED INSTEAD
  ERPNextClient (below) is the one interface every procurement system
  talks to -- intentionally narrower than either MCP server's raw tool
  list: no delete, no submit/cancel, no arbitrary call_method, and
  `allow_writes` defaults to False (read-only first, per spec). Two
  implementations exist behind it:

  * SeededERPNextClient -- fully real, in-memory, clearly-labeled seed
    data. This is what the systems actually run against and what the
    tests exercise, until a real ERPNext instance exists.
  * ERPNextRestClient -- calls ERPNext's own documented REST API directly
    (GET/POST /api/resource/<doctype>, token auth), not through either MCP
    server. This is a real, correctly-written client I can verify against
    ERPNext's public API docs and test with mocked HTTP, which an
    untested MCP-SDK client would not be. Swapping in the verified MCP
    server later is a drop-in replacement behind this same interface, once
    Node.js and a live ERPNext instance exist to actually test it against.

  Both implementations satisfy the same ERPNextClient Protocol, so no
  calling code (Vendra/Procura/Finora) needs to know which one it has.

UPDATE -- the "no official server" conclusion above was correct when
written, but has since changed: Frappe Core 16 ships a BUILT-IN MCP
endpoint (`/api/v2/mcp`, see frappe/mcp/README.md in frappe/frappe),
verified live against a real hosted site (not assumed): `initialize` and
`tools/list` both succeeded, returning the same user a plain REST
`frappe.auth.get_logged_user` call had already confirmed. This supersedes
the `frappe/mcp` PyPI-app / `rakeshgangwar` evaluation above for any
hosted or SaaS Frappe Cloud site (no app install, no Docker, no local
ERPNext needed) -- see FrappeMCPClient below, and mcp_client.py's
MCPERPNextClient (the rakeshgangwar-based client) is accordingly
deprecated, not deleted.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ERPNextClient(Protocol):
    """The only ERPNext surface any procurement system may call.

    Deliberately excludes delete/submit/cancel/call_method: per spec,
    "avoid unrestricted delete tools" and give each system only the tools
    it needs. Write operations are the narrow, explicit set actually
    needed -- create_supplier/create_item/create_supplier_quotation (vendor
    master seeding) plus create_rfq/create_purchase_order (the demo
    workflow) -- all gated by `allow_writes`, purchase orders additionally
    by an explicit approval flag, never a generic "create any document" tool.
    """

    def list_suppliers(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]: ...

    def get_supplier(self, supplier_name: str) -> Optional[Dict[str, Any]]: ...

    def list_supplier_documents(self, supplier_name: str) -> List[Dict[str, Any]]: ...

    def list_items(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]: ...

    def create_supplier(self, *, supplier_name: str, country: str, **fields: Any) -> Dict[str, Any]: ...

    def update_supplier(self, *, supplier_name: str, **fields: Any) -> Dict[str, Any]: ...

    def create_supplier_document(self, *, supplier_name: str, file_name: str, content: bytes = b"") -> Dict[str, Any]: ...

    def create_item(self, *, item_code: str, item_name: str, item_group: str, **fields: Any) -> Dict[str, Any]: ...

    def create_supplier_quotation(
        self, *, supplier: str, items: List[Dict[str, Any]], **fields: Any
    ) -> Dict[str, Any]: ...

    def create_rfq(
        self, *, items: List[str], supplier_names: List[str], source_reference: Optional[str] = None, qty: int = 1
    ) -> Dict[str, Any]: ...

    def list_supplier_quotations(
        self, *, rfq_name: Optional[str] = None, item_code: Optional[str] = None
    ) -> List[Dict[str, Any]]: ...

    def create_purchase_order(
        self, *, supplier: str, items: List[Dict[str, Any]], approved: bool
    ) -> Dict[str, Any]: ...

    def get_budget(self, *, cost_center: Optional[str] = None) -> Optional[float]: ...

    def list_invoices(self, *, supplier: Optional[str] = None) -> List[Dict[str, Any]]: ...


def _days_between(start: Any, end: Any) -> Optional[int]:
    """Best-effort day delta between two date-ish values (ERPNext returns
    dates as "YYYY-MM-DD" strings). None if either side is missing or
    unparseable -- never raises, never guesses a number."""
    if not start or not end:
        return None
    try:
        from datetime import date

        d1 = date.fromisoformat(str(start)[:10])
        d2 = date.fromisoformat(str(end)[:10])
        return (d2 - d1).days
    except Exception:
        return None


class ERPNextUnavailable(RuntimeError):
    """The configured ERPNext/MCP backend could not be reached or is not configured."""


# This live site's one real Company record (confirmed via list_documents,
# not guessed) -- the default every write below needs for a required
# "company" field, overridable via the matching **fields kwarg.
_DEFAULT_COMPANY = "Procurement AI Demo"
# Likewise this site's one real stock Warehouse (confirmed via
# list_documents) -- required on an RFQ item line for any stock Item.
_DEFAULT_WAREHOUSE = "Stores - PAD"


# ---------------------------------------------------------------------------
# Seeded, in-memory implementation -- real code, clearly-labeled fake data.
# ---------------------------------------------------------------------------


def _default_seed() -> Dict[str, Any]:
    """Clearly-labeled seed data for the demo workflow. Every record's
    `source_reference` looks like a real ERPNext doctype/name so the
    provenance fields stay meaningful even though the data is seeded."""
    return {
        "suppliers": [
            {
                "name": "SUP-0001",
                "supplier_name": "Northbridge Computing Ltd",
                "country": "US",
                "disabled": 0,
                "on_hold": 0,
                "source_reference": "Supplier/SUP-0001",
                "_seeded": True,
            },
            {
                "name": "SUP-0002",
                "supplier_name": "Clearline Hardware Supply",
                "country": "US",
                "disabled": 0,
                "on_hold": 0,
                "source_reference": "Supplier/SUP-0002",
                "_seeded": True,
            },
            {
                "name": "SUP-0003",
                "supplier_name": "OnHold Traders Inc",
                "country": "US",
                "disabled": 0,
                "on_hold": 1,  # deliberately ineligible -- tests exclusion logic
                "source_reference": "Supplier/SUP-0003",
                "_seeded": True,
            },
        ],
        "supplier_documents": {
            "SUP-0001": [
                {"doctype": "Supplier Document", "title": "W-9 Tax Form", "status": "verified"},
                {"doctype": "Supplier Document", "title": "Business License", "status": "verified"},
            ],
            "SUP-0002": [
                {"doctype": "Supplier Document", "title": "W-9 Tax Form", "status": "verified"},
                # deliberately incomplete: no business license on file
            ],
        },
        "items": [
            {
                "name": "ITEM-LAPTOP-14",
                "item_name": "14-inch Business Laptop",
                "item_group": "Computers",
                "source_reference": "Item/ITEM-LAPTOP-14",
                "_seeded": True,
            }
        ],
        "quotations": [
            {
                "name": "SQ-0001",
                "supplier": "SUP-0001",
                "item_code": "ITEM-LAPTOP-14",
                "rate": 950.0,
                "qty": 20,
                "currency": "USD",
                "delivery_days": 10,
                "payment_terms_days": 30,
                "source_reference": "Supplier Quotation/SQ-0001",
                "_seeded": True,
            },
            {
                "name": "SQ-0002",
                "supplier": "SUP-0002",
                "item_code": "ITEM-LAPTOP-14",
                "rate": 985.0,
                "qty": 20,
                "currency": "USD",
                "delivery_days": 4,
                "payment_terms_days": 15,
                "source_reference": "Supplier Quotation/SQ-0002",
                "_seeded": True,
            },
        ],
        "budget": {
            "default": 25000.0,  # covers the cheaper quote (950*20=19000), not an arbitrary huge number
        },
        "invoices": [],
        "rfqs": [],
        "purchase_orders": [],
    }


class SeededERPNextClient:
    """In-memory ERPNext stand-in with clearly-labeled seed data.

    Every returned record carries `"_seeded": True` and a plausible
    `source_reference` (e.g. "Supplier/SUP-0001"), so provenance written
    from this backend is honestly traceable to "seeded test data," never
    confusable with a real ERPNext record.
    """

    def __init__(self, seed: Optional[Dict[str, Any]] = None):
        self._data = copy.deepcopy(seed if seed is not None else _default_seed())

    def list_suppliers(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows = self._data["suppliers"]
        if filters:
            for key, value in filters.items():
                rows = [r for r in rows if r.get(key) == value]
        return copy.deepcopy(rows)

    def get_supplier(self, supplier_name: str) -> Optional[Dict[str, Any]]:
        for row in self._data["suppliers"]:
            if row["name"] == supplier_name:
                return copy.deepcopy(row)
        return None

    def list_supplier_documents(self, supplier_name: str) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._data["supplier_documents"].get(supplier_name, []))

    def list_items(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows = self._data["items"]
        if filters:
            for key, value in filters.items():
                rows = [r for r in rows if r.get(key) == value]
        return copy.deepcopy(rows)

    def create_supplier(self, *, supplier_name: str, country: str, **fields: Any) -> Dict[str, Any]:
        row = {
            "name": supplier_name,
            "supplier_name": supplier_name,
            "country": country,
            "disabled": 0,
            "on_hold": 0,
            "source_reference": f"Supplier/{supplier_name}",
            "_seeded": True,
            **fields,
        }
        self._data["suppliers"].append(row)
        return copy.deepcopy(row)

    def update_supplier(self, *, supplier_name: str, **fields: Any) -> Dict[str, Any]:
        for row in self._data["suppliers"]:
            if row["name"] == supplier_name:
                row.update(fields)
                return copy.deepcopy(row)
        raise ERPNextUnavailable(f"no such seeded supplier: {supplier_name}")

    def create_supplier_document(self, *, supplier_name: str, file_name: str, content: bytes = b"") -> Dict[str, Any]:
        row = {"title": file_name, "file_name": file_name, "_seeded": True}
        self._data["supplier_documents"].setdefault(supplier_name, []).append(row)
        return copy.deepcopy(row)

    def create_item(self, *, item_code: str, item_name: str, item_group: str, **fields: Any) -> Dict[str, Any]:
        row = {
            "name": item_code,
            "item_code": item_code,
            "item_name": item_name,
            "item_group": item_group,
            "source_reference": f"Item/{item_code}",
            "_seeded": True,
            **fields,
        }
        self._data["items"].append(row)
        return copy.deepcopy(row)

    def create_supplier_quotation(self, *, supplier: str, items: List[Dict[str, Any]], **fields: Any) -> Dict[str, Any]:
        name = f"SQ-SEED-{len(self._data['quotations']) + 1:04d}"
        rows = []
        for item in items:
            row = {
                "name": name,
                "supplier": supplier,
                "item_code": item.get("item_code"),
                "rate": item.get("rate"),
                "qty": item.get("qty"),
                "currency": fields.get("currency", "USD"),
                "delivery_days": item.get("delivery_days"),
                "payment_terms_days": item.get("payment_terms_days"),
                "source_reference": f"Supplier Quotation/{name}",
                "_seeded": True,
            }
            self._data["quotations"].append(row)
            rows.append(row)
        return {"name": name, "supplier": supplier, "items": copy.deepcopy(rows)}

    def create_rfq(
        self, *, items: List[str], supplier_names: List[str], source_reference: Optional[str] = None, qty: int = 1
    ) -> Dict[str, Any]:
        rfq = {
            "name": f"RFQ-SEED-{len(self._data['rfqs']) + 1:04d}",
            "items": items,
            "qty": qty,
            "suppliers": supplier_names,
            "source_reference": source_reference or "seeded",
            "_seeded": True,
        }
        self._data["rfqs"].append(rfq)
        return copy.deepcopy(rfq)

    def list_supplier_quotations(
        self, *, rfq_name: Optional[str] = None, item_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        rows = self._data["quotations"]
        if item_code:
            rows = [r for r in rows if r.get("item_code") == item_code]
        return copy.deepcopy(rows)

    def create_purchase_order(
        self, *, supplier: str, items: List[Dict[str, Any]], approved: bool
    ) -> Dict[str, Any]:
        if not approved:
            raise PermissionError("create_purchase_order requires explicit human approval (approved=True)")
        po = {
            "name": f"PO-SEED-{len(self._data['purchase_orders']) + 1:04d}",
            "supplier": supplier,
            "items": items,
            "status": "To Receive and Bill",
            "_seeded": True,
        }
        self._data["purchase_orders"].append(po)
        return copy.deepcopy(po)

    def get_budget(self, *, cost_center: Optional[str] = None) -> Optional[float]:
        return self._data["budget"].get(cost_center or "default")

    def list_invoices(self, *, supplier: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._data["invoices"]
        if supplier:
            rows = [r for r in rows if r.get("supplier") == supplier]
        return copy.deepcopy(rows)


# ---------------------------------------------------------------------------
# Real implementation: ERPNext's own documented REST API, read-first.
# ---------------------------------------------------------------------------


class ERPNextRestClient:
    """Calls ERPNext's own `/api/resource/<doctype>` REST API directly.

    Untested against a live instance (none is configured here), but
    written against ERPNext's own stable, documented API shape, not an
    unverified third-party MCP server's SDK -- see this module's docstring
    for why that trade was made. `allow_writes=False` (the default) makes
    create_rfq/create_purchase_order raise ERPNextUnavailable instead of
    attempting a write, matching the spec's "prefer read-only access
    initially."
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        allow_writes: bool = False,
        timeout: float = 30.0,
        session: Any = None,
    ):
        if not base_url or not api_key or not api_secret:
            raise ERPNextUnavailable("ERPNEXT_URL, ERPNEXT_API_KEY and ERPNEXT_API_SECRET are all required")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"token {api_key}:{api_secret}",
            "Accept": "application/json",
        }
        self._allow_writes = allow_writes
        self._timeout = timeout
        if session is not None:
            self._session = session
        else:
            import requests

            self._session = requests.Session()

    def _get(self, doctype: str, *, filters: Optional[Dict[str, Any]] = None, fields: Optional[List[str]] = None):
        import json as _json

        # ERPNext's REST list endpoint silently caps results at 20 unless
        # told otherwise -- confirmed live: a plain GET /api/resource/Supplier
        # returned 20 of 32 real rows with no error, no "has_next_page"
        # flag, nothing to indicate truncation. This caused a real bug:
        # Vendra/Procura silently stopped seeing some suppliers/quotations
        # once the real data grew past 20 rows, with no visible failure at
        # all (e.g. "no quotations found" for an item whose quotations were
        # all created after the 20th). limit_page_length=0 means "no
        # limit" -- every caller here wants the complete, real result set,
        # never a silently partial one.
        params: Dict[str, Any] = {"limit_page_length": 0}
        if filters:
            params["filters"] = _json.dumps([[k, "=", v] for k, v in filters.items()])
        if fields:
            params["fields"] = _json.dumps(fields)
        try:
            response = self._session.get(
                f"{self._base_url}/api/resource/{doctype}",
                headers=self._headers,
                params=params,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ERPNextUnavailable(f"GET {doctype} failed: {exc}") from exc
        return response.json().get("data", [])

    def _get_one(self, doctype: str, name: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._session.get(
                f"{self._base_url}/api/resource/{doctype}/{name}", headers=self._headers, timeout=self._timeout
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
        except Exception as exc:
            raise ERPNextUnavailable(f"GET {doctype}/{name} failed: {exc}") from exc
        return response.json().get("data")

    def _post(self, doctype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._allow_writes:
            raise ERPNextUnavailable(
                f"writes are disabled (allow_writes=False); refused to create a {doctype} document"
            )
        try:
            response = self._session.post(
                f"{self._base_url}/api/resource/{doctype}", headers=self._headers, json=payload, timeout=self._timeout
            )
            response.raise_for_status()
        except Exception as exc:
            raise ERPNextUnavailable(f"POST {doctype} failed: {exc}") from exc
        return response.json().get("data", {})

    def _put(self, doctype: str, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._allow_writes:
            raise ERPNextUnavailable(
                f"writes are disabled (allow_writes=False); refused to update a {doctype} document"
            )
        try:
            response = self._session.put(
                f"{self._base_url}/api/resource/{doctype}/{name}",
                headers=self._headers,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ERPNextUnavailable(f"PUT {doctype}/{name} failed: {exc}") from exc
        return response.json().get("data", {})

    # ERPNext's /api/resource/<doctype> list endpoint returns only `name`
    # unless `fields` is given explicitly -- confirmed live: without this,
    # on_hold/disabled/etc. all came back as None, silently breaking
    # vendra.discover_suppliers()'s on_hold filter. Every list/get call
    # below requests exactly the fields that domain code actually reads.
    _SUPPLIER_FIELDS = ["name", "supplier_name", "country", "disabled", "on_hold"]
    _ITEM_FIELDS = ["name", "item_name", "item_group"]
    # `budget_amount` is the real field on this project's live Budget
    # doctype -- confirmed live via get_doctype_schema and by reading back
    # a real created record (BUDGET-00000001), not assumed. The earlier
    # `budget_against_total` / `total_budget` names were never confirmed
    # against a live schema and don't exist here; requesting an unknown
    # field 417s the whole call (confirmed live), so only confirmed-real
    # fields are requested -- those two legacy names are still read from
    # the row if present (harmless no-op for this schema, but preserves
    # the previously supported return shape for any other Budget schema
    # variant that does supply them as additional, requested fields).
    _BUDGET_FIELDS = ["name", "budget_amount", "cost_center", "account"]

    def list_suppliers(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows = self._get("Supplier", filters=filters, fields=self._SUPPLIER_FIELDS)
        for row in rows:
            row.setdefault("source_reference", f"Supplier/{row['name']}")
        return rows

    def get_supplier(self, supplier_name: str) -> Optional[Dict[str, Any]]:
        return self._get_one("Supplier", supplier_name)

    def list_supplier_documents(self, supplier_name: str) -> List[Dict[str, Any]]:
        # "Supplier Document" is not a real ERPNext doctype -- confirmed live
        # via search_doctypes returning no match. The real, always-available
        # mechanism for "what's attached to this supplier" is Frappe's core
        # File doctype, filtered by attached_to_doctype/attached_to_name.
        return self._get(
            "File",
            filters={"attached_to_doctype": "Supplier", "attached_to_name": supplier_name},
            fields=["name", "file_name", "attached_to_doctype", "attached_to_name"],
        )

    def list_items(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self._get("Item", filters=filters, fields=self._ITEM_FIELDS)

    def create_supplier(self, *, supplier_name: str, country: str, **fields: Any) -> Dict[str, Any]:
        # supplier_type and gst_category are required on this site's Supplier
        # doctype (confirmed live via get_doctype_schema) -- supplier_group
        # is not required but every real supplier here has one, so default
        # to "Local" rather than leaving it unset.
        payload = {
            "supplier_name": supplier_name,
            "country": country,
            "supplier_type": fields.pop("supplier_type", "Company"),
            "gst_category": fields.pop("gst_category", "Unregistered"),
            "supplier_group": fields.pop("supplier_group", "Local"),
            **fields,
        }
        return self._post("Supplier", payload)

    def update_supplier(self, *, supplier_name: str, **fields: Any) -> Dict[str, Any]:
        return self._put("Supplier", supplier_name, fields)

    def create_supplier_document(self, *, supplier_name: str, file_name: str, content: bytes = b"") -> Dict[str, Any]:
        # Not a plain /api/resource/<doctype> write: Frappe's File doctype
        # needs real bytes, which only its dedicated multipart upload
        # endpoint accepts -- confirmed live: posting a File document via
        # the ordinary doctype-create path raised an unrelated, unhandled
        # server error (no file content to validate against), and a
        # genuinely blank/.pdf-labeled payload separately 500'd on a
        # server-side PDF-parsing hook. A plain .txt placeholder with
        # actual (even tiny, clearly-labeled) content is what's real here.
        if not self._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to upload a Supplier document")
        files = {"file": (file_name, content or b"SIMULATED TEST DOCUMENT -- NOT A REAL FILE", "text/plain")}
        data = {"is_private": "0", "doctype": "Supplier", "docname": supplier_name}
        try:
            response = self._session.post(
                f"{self._base_url}/api/method/upload_file",
                headers={"Authorization": self._headers["Authorization"]},
                files=files,
                data=data,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ERPNextUnavailable(f"upload_file failed: {exc}") from exc
        return response.json().get("message", {})

    def create_item(self, *, item_code: str, item_name: str, item_group: str, **fields: Any) -> Dict[str, Any]:
        # stock_uom is required (confirmed live via get_doctype_schema);
        # "Nos" matches this project's existing DEMO-LAPTOP-14 item.
        # gst_hsn_code isn't marked required by the schema but a live
        # server-side validation hook on this site rejects an Item without
        # one -- confirmed by an actual failed create ("HSN/SAC Code is
        # required"). It must be an existing "GST HSN Code" record (a Link
        # field), not arbitrary text, and must be 6 or 8 digits (a second
        # live validation error, after the first fix); "01011010" is a
        # real one on this site, used here only to satisfy that
        # validation, not as an accurate tax classification for these
        # test items.
        payload = {
            "item_code": item_code,
            "item_name": item_name,
            "item_group": item_group,
            "stock_uom": fields.pop("stock_uom", "Nos"),
            "gst_hsn_code": fields.pop("gst_hsn_code", "01011010"),
            **fields,
        }
        return self._post("Item", payload)

    def create_supplier_quotation(self, *, supplier: str, items: List[Dict[str, Any]], **fields: Any) -> Dict[str, Any]:
        # company/currency/transaction_date/conversion_rate, and each item
        # line's stock_uom/uom/conversion_factor, are required (confirmed
        # live via get_doctype_schema and by reading back an existing real
        # quotation, PUR-SQTN-2026-00001) -- defaults below match that
        # record's own values for this site, not guessed.
        import datetime as _dt

        payload = {
            "supplier": supplier,
            "company": fields.pop("company", _DEFAULT_COMPANY),
            "currency": fields.pop("currency", "INR"),
            "conversion_rate": fields.pop("conversion_rate", 1.0),
            "transaction_date": fields.pop("transaction_date", _dt.date.today().isoformat()),
            "items": [
                {
                    "item_code": item["item_code"],
                    "qty": item["qty"],
                    "rate": item["rate"],
                    "stock_uom": item.get("stock_uom", "Nos"),
                    "uom": item.get("uom", "Nos"),
                    "conversion_factor": item.get("conversion_factor", 1.0),
                    **({"schedule_date": item["schedule_date"]} if item.get("schedule_date") else {}),
                }
                for item in items
            ],
            **fields,
        }
        return self._post("Supplier Quotation", payload)

    def create_rfq(
        self, *, items: List[str], supplier_names: List[str], source_reference: Optional[str] = None, qty: int = 1
    ) -> Dict[str, Any]:
        # company/transaction_date/subject (top-level) and each item
        # line's schedule_date/qty/stock_uom/uom/conversion_factor are
        # required (confirmed live via get_doctype_schema) -- the original
        # payload here only ever sent item_code, which a live write
        # correctly rejected ("Quantity ... cannot be zero"), and a second
        # live attempt surfaced a further requirement not in the schema's
        # own reqd flags: a stock Item's RFQ line also needs a warehouse
        # ("Warehouse is mandatory for stock Item ...").
        import datetime as _dt

        schedule_date = (_dt.date.today() + _dt.timedelta(days=14)).isoformat()
        return self._post(
            "Request for Quotation",
            {
                "company": _DEFAULT_COMPANY,
                "transaction_date": _dt.date.today().isoformat(),
                "subject": f"RFQ for {', '.join(items)}",
                "items": [
                    {
                        "item_code": i,
                        "qty": qty,
                        "schedule_date": schedule_date,
                        "stock_uom": "Nos",
                        "uom": "Nos",
                        "conversion_factor": 1.0,
                        "warehouse": _DEFAULT_WAREHOUSE,
                    }
                    for i in items
                ],
                "suppliers": [{"supplier": s} for s in supplier_names],
            },
        )

    def list_supplier_quotations(
        self, *, rfq_name: Optional[str] = None, item_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """ERPNext's REST filter syntax only supports top-level doctype
        fields; item_code lives on the "Supplier Quotation Item" child
        table -- confirmed live: filtering on it directly returns HTTP 417
        Expectation Failed. So this fetches the (optionally rfq-filtered)
        quotation names, then each full document, and flattens to one row
        per matching item line -- the shape
        app.procurement.calculations/procura.py already expect (rate, qty,
        item_code at the top level, like the seeded client's rows).
        """
        filters: Dict[str, Any] = {}
        if rfq_name:
            filters["request_for_quotation"] = rfq_name
        names = self._get("Supplier Quotation", filters=filters or None, fields=["name"])

        rows: List[Dict[str, Any]] = []
        for row in names:
            doc = self._get_one("Supplier Quotation", row["name"])
            if not doc:
                continue
            for item in doc.get("items", []) or []:
                if item_code and item.get("item_code") != item_code:
                    continue
                rows.append(
                    {
                        "name": doc["name"],
                        "supplier": doc.get("supplier"),
                        "item_code": item.get("item_code"),
                        "rate": item.get("rate"),
                        "qty": item.get("qty"),
                        "currency": doc.get("currency", "USD"),
                        "delivery_days": _days_between(doc.get("transaction_date"), item.get("schedule_date")),
                        # ERPNext ties payment terms to a Payment Terms
                        # Template, not a plain "net N days" number; not
                        # derived in this pass rather than invented.
                        "payment_terms_days": None,
                        "source_reference": f"Supplier Quotation/{doc['name']}",
                        "document_date": doc.get("transaction_date"),
                    }
                )
        return rows

    def create_purchase_order(
        self, *, supplier: str, items: List[Dict[str, Any]], approved: bool
    ) -> Dict[str, Any]:
        if not approved:
            raise PermissionError("create_purchase_order requires explicit human approval (approved=True)")
        # Same required fields as create_rfq/create_supplier_quotation
        # (confirmed live via get_doctype_schema): company/transaction_date/
        # currency/conversion_rate at top level, and each item line needs
        # item_name/schedule_date/stock_uom/uom/conversion_factor/warehouse
        # -- the original payload here only ever sent item_code/qty.
        import datetime as _dt

        schedule_date = (_dt.date.today() + _dt.timedelta(days=14)).isoformat()
        return self._post(
            "Purchase Order",
            {
                "supplier": supplier,
                "company": _DEFAULT_COMPANY,
                "transaction_date": _dt.date.today().isoformat(),
                "currency": "INR",
                "conversion_rate": 1.0,
                "items": [
                    {
                        "item_code": item["item_code"],
                        "item_name": self._item_name(item["item_code"]),
                        "qty": item["qty"],
                        "rate": item.get("rate", 0.0),
                        "schedule_date": schedule_date,
                        "stock_uom": "Nos",
                        "uom": "Nos",
                        "conversion_factor": 1.0,
                        "warehouse": _DEFAULT_WAREHOUSE,
                    }
                    for item in items
                ],
            },
        )

    def _item_name(self, item_code: str) -> str:
        """Best-effort real item_name lookup (required on a PO line) --
        falls back to the item_code itself rather than failing the whole
        PO submission over a cosmetic label."""
        try:
            doc = self._get_one("Item", item_code)
            return (doc or {}).get("item_name") or item_code
        except Exception:
            return item_code

    def get_budget(self, *, cost_center: Optional[str] = None) -> Optional[float]:
        rows = self._get(
            "Budget", filters={"cost_center": cost_center} if cost_center else None, fields=self._BUDGET_FIELDS
        )
        if not rows:
            return None
        row = rows[0]
        return row.get("budget_amount") or row.get("budget_against_total") or row.get("total_budget")

    def list_invoices(self, *, supplier: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._get("Purchase Invoice", filters={"supplier": supplier} if supplier else None)


class FrappeMCPClient:
    """Implements ERPNextClient via Frappe's own BUILT-IN MCP endpoint
    (`/api/v2/mcp`, core Frappe 16+ -- frappe/mcp/README.md in the
    frappe/frappe repo), not a third-party MCP server.

    Verified live against the hosted site before this was written, not
    assumed:
      * `frappe/api/v2.py` (version-16 branch, matching this site's
        reported Frappe Core 16.50.0) registers
        `Rule("/mcp", methods=["POST","GET","DELETE"], endpoint=handle_mcp)`.
      * A real `initialize` call returned HTTP 200 with
        `serverInfo.version "16.50.0"` and confirmed the authenticated
        user (the same user the plain REST check returned).
      * A real `tools/list` call returned 13 tools, matching
        frappe/mcp/README.md's table.
      * The endpoint is stateless per request: `tools/call` succeeded
        with no prior `initialize` in the same process and no session
        header -- no session management is implemented here because none
        was needed.
      * A real error (an unknown doctype) came back as HTTP 200 with
        `result.isError: true` and a short, already-sanitized message in
        `result.content[0].text` (e.g. "PermissionError") -- not a
        top-level JSON-RPC `"error"` key. Handled accordingly below.

    Auth is identical to ERPNextRestClient's (`token <key>:<secret>`), so
    this class wraps one instead of re-implementing session/header/error
    plumbing -- composition, not a parallel implementation.
    """

    def __init__(self, rest_client: "ERPNextRestClient"):
        self._rest = rest_client

    # -- transport ------------------------------------------------------

    def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """``_call_tool_raw`` as a traced ERPNext tool call (name and doctype only, never the payload)."""
        from app.observability import langfuse_tracing as tracing

        doctype = arguments.get("doctype") if isinstance(arguments, dict) else None
        with tracing.tool_span(f"ERPNext {name}", input={"doctype": doctype, "name": (arguments or {}).get("name")}) as t:
            result = self._call_tool_raw(name, arguments)
            t.update(output="ok")
            return result

    def _call_tool_raw(self, name: str, arguments: Dict[str, Any]) -> Any:
        """One JSON-RPC `tools/call` POST. Never raises a raw/unwrapped
        exception: network errors, HTTP errors, a JSON-RPC-level error, an
        `isError` tool result, and a malformed/non-JSON body are all
        normalised to ERPNextUnavailable (or PermissionError, for the one
        case callers are meant to catch specifically)."""
        headers = dict(self._rest._headers)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}

        try:
            response = self._rest._session.post(
                f"{self._rest._base_url}/api/v2/mcp", headers=headers, json=body, timeout=self._rest._timeout
            )
            response.raise_for_status()
        except Exception as exc:
            raise ERPNextUnavailable(f"MCP call to {name} failed: {exc}") from exc

        try:
            envelope = response.json()
        except Exception as exc:
            raise ERPNextUnavailable(f"{name} returned a non-JSON response: {response.text[:200]!r}") from exc

        if "error" in envelope:
            raise ERPNextUnavailable(f"{name} failed: {envelope['error']}")

        result = envelope.get("result")
        if not isinstance(result, dict):
            raise ERPNextUnavailable(f"{name} returned an unexpected shape: {envelope!r}")

        content = result.get("content") or []
        text = content[0].get("text", "") if content and isinstance(content[0], dict) else ""

        if result.get("isError"):
            raise ERPNextUnavailable(f"{name} failed: {text or 'unknown error'}")

        try:
            return json.loads(text) if text else None
        except (json.JSONDecodeError, TypeError) as exc:
            raise ERPNextUnavailable(f"{name} returned a non-JSON tool result: {text!r}") from exc

    def _list_documents(self, doctype: str, *, filters: Optional[Dict[str, Any]] = None, fields=None, limit=None):
        # The MCP list_documents tool silently caps results at a small
        # default page (confirmed live: an unfiltered Supplier/UOM listing
        # with no limit returned far fewer than the real total, no error,
        # no visible truncation signal) -- same real bug class as
        # ERPNextRestClient._get's own limit_page_length fix. Every caller
        # here wants the complete result set; 500 is comfortably above
        # anything this project seeds or expects to see live.
        args: Dict[str, Any] = {"doctype": doctype, "limit": limit if limit else 500}
        if filters:
            args["filters"] = filters
        if fields:
            args["fields"] = fields
        data = self._call_tool("list_documents", args)
        return (data or {}).get("documents", [])

    def _get_document(self, doctype: str, name: str) -> Optional[Dict[str, Any]]:
        try:
            return self._call_tool("get_document", {"doctype": doctype, "name": name})
        except ERPNextUnavailable as exc:
            if "not found" in str(exc).lower() or "does not exist" in str(exc).lower():
                return None
            raise

    # -- ERPNextClient protocol ------------------------------------------

    def list_suppliers(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows = self._list_documents("Supplier", filters=filters, fields=ERPNextRestClient._SUPPLIER_FIELDS)
        for row in rows:
            row.setdefault("source_reference", f"Supplier/{row['name']}")
        return rows

    def get_supplier(self, supplier_name: str) -> Optional[Dict[str, Any]]:
        return self._get_document("Supplier", supplier_name)

    def list_supplier_documents(self, supplier_name: str) -> List[Dict[str, Any]]:
        # Same correction as ERPNextRestClient: "Supplier Document" isn't a
        # real doctype (confirmed live via search_doctypes); Frappe's core
        # File doctype is the real, always-available attachment mechanism.
        return self._list_documents(
            "File",
            filters={"attached_to_doctype": "Supplier", "attached_to_name": supplier_name},
            fields=["name", "file_name", "attached_to_doctype", "attached_to_name"],
        )

    def list_items(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self._list_documents("Item", filters=filters, fields=ERPNextRestClient._ITEM_FIELDS)

    def create_supplier(self, *, supplier_name: str, country: str, **fields: Any) -> Dict[str, Any]:
        if not self._rest._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create a Supplier")
        data = {
            "supplier_name": supplier_name,
            "country": country,
            "supplier_type": fields.pop("supplier_type", "Company"),
            "gst_category": fields.pop("gst_category", "Unregistered"),
            "supplier_group": fields.pop("supplier_group", "Local"),
            **fields,
        }
        return self._call_tool("create_document", {"doctype": "Supplier", "data": data})

    def update_supplier(self, *, supplier_name: str, **fields: Any) -> Dict[str, Any]:
        if not self._rest._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to update a Supplier")
        return self._call_tool("update_document", {"doctype": "Supplier", "name": supplier_name, "data": fields})

    def create_supplier_document(self, *, supplier_name: str, file_name: str, content: bytes = b"") -> Dict[str, Any]:
        # The generic create_document MCP tool can't do this (confirmed
        # live: it has no multipart/file-content support and errors
        # unhelpfully on a File doctype) -- delegate to the wrapped REST
        # client's dedicated upload_file call instead. Composition, same
        # as this class's own docstring already describes for auth/
        # transport.
        return self._rest.create_supplier_document(supplier_name=supplier_name, file_name=file_name, content=content)

    def create_item(self, *, item_code: str, item_name: str, item_group: str, **fields: Any) -> Dict[str, Any]:
        if not self._rest._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create an Item")
        # See ERPNextRestClient.create_item -- gst_hsn_code is required by a
        # live server-side validation hook on this site (confirmed by an
        # actual failed create), not by the doctype schema itself.
        data = {
            "item_code": item_code,
            "item_name": item_name,
            "item_group": item_group,
            "stock_uom": fields.pop("stock_uom", "Nos"),
            "gst_hsn_code": fields.pop("gst_hsn_code", "01011010"),
            **fields,
        }
        return self._call_tool("create_document", {"doctype": "Item", "data": data})

    def create_supplier_quotation(self, *, supplier: str, items: List[Dict[str, Any]], **fields: Any) -> Dict[str, Any]:
        if not self._rest._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create a Supplier Quotation")
        import datetime as _dt

        data = {
            "supplier": supplier,
            "company": fields.pop("company", _DEFAULT_COMPANY),
            "currency": fields.pop("currency", "INR"),
            "conversion_rate": fields.pop("conversion_rate", 1.0),
            "transaction_date": fields.pop("transaction_date", _dt.date.today().isoformat()),
            "items": [
                {
                    "item_code": item["item_code"],
                    "qty": item["qty"],
                    "rate": item["rate"],
                    "stock_uom": item.get("stock_uom", "Nos"),
                    "uom": item.get("uom", "Nos"),
                    "conversion_factor": item.get("conversion_factor", 1.0),
                    **({"schedule_date": item["schedule_date"]} if item.get("schedule_date") else {}),
                }
                for item in items
            ],
            **fields,
        }
        return self._call_tool("create_document", {"doctype": "Supplier Quotation", "data": data})

    def create_rfq(
        self, *, items: List[str], supplier_names: List[str], source_reference: Optional[str] = None, qty: int = 1
    ) -> Dict[str, Any]:
        if not self._rest._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create an RFQ")
        # See ERPNextRestClient.create_rfq -- company/transaction_date/
        # subject and each item line's schedule_date/qty/stock_uom/uom/
        # conversion_factor/warehouse are required (confirmed live via
        # get_doctype_schema and an actual failed write); the original
        # payload only sent item_code.
        import datetime as _dt

        schedule_date = (_dt.date.today() + _dt.timedelta(days=14)).isoformat()
        data = {
            "company": _DEFAULT_COMPANY,
            "transaction_date": _dt.date.today().isoformat(),
            "subject": f"RFQ for {', '.join(items)}",
            "items": [
                {
                    "item_code": i,
                    "qty": qty,
                    "schedule_date": schedule_date,
                    "stock_uom": "Nos",
                    "uom": "Nos",
                    "conversion_factor": 1.0,
                    "warehouse": _DEFAULT_WAREHOUSE,
                }
                for i in items
            ],
            "suppliers": [{"supplier": s} for s in supplier_names],
        }
        return self._call_tool("create_document", {"doctype": "Request for Quotation", "data": data})

    def list_supplier_quotations(
        self, *, rfq_name: Optional[str] = None, item_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        # Same child-table filter limitation as ERPNextRestClient (tool
        # arguments mirror the REST v2 list endpoint, per
        # frappe/mcp/README.md) -- item_code lives on the "items" child
        # table, so this fetches names, then each full document, and
        # flattens to one row per matching item line.
        filters = {"request_for_quotation": rfq_name} if rfq_name else None
        names = self._list_documents("Supplier Quotation", filters=filters, fields=["name"])

        rows: List[Dict[str, Any]] = []
        for row in names:
            doc = self._get_document("Supplier Quotation", row["name"])
            if not doc:
                continue
            for item in doc.get("items", []) or []:
                if item_code and item.get("item_code") != item_code:
                    continue
                rows.append(
                    {
                        "name": doc["name"],
                        "supplier": doc.get("supplier"),
                        "item_code": item.get("item_code"),
                        "rate": item.get("rate"),
                        "qty": item.get("qty"),
                        "currency": doc.get("currency", "USD"),
                        "delivery_days": _days_between(doc.get("transaction_date"), item.get("schedule_date")),
                        "payment_terms_days": None,
                        "source_reference": f"Supplier Quotation/{doc['name']}",
                        "document_date": doc.get("transaction_date"),
                    }
                )
        return rows

    def create_purchase_order(
        self, *, supplier: str, items: List[Dict[str, Any]], approved: bool
    ) -> Dict[str, Any]:
        if not approved:
            raise PermissionError("create_purchase_order requires explicit human approval (approved=True)")
        if not self._rest._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create a purchase order")
        # See ERPNextRestClient.create_purchase_order -- same required
        # fields (confirmed live via get_doctype_schema): the original
        # payload here only sent item_code/qty.
        import datetime as _dt

        schedule_date = (_dt.date.today() + _dt.timedelta(days=14)).isoformat()
        data = {
            "supplier": supplier,
            "company": _DEFAULT_COMPANY,
            "transaction_date": _dt.date.today().isoformat(),
            "currency": "INR",
            "conversion_rate": 1.0,
            "items": [
                {
                    "item_code": item["item_code"],
                    "item_name": self._item_name(item["item_code"]),
                    "qty": item["qty"],
                    "rate": item.get("rate", 0.0),
                    "schedule_date": schedule_date,
                    "stock_uom": "Nos",
                    "uom": "Nos",
                    "conversion_factor": 1.0,
                    "warehouse": _DEFAULT_WAREHOUSE,
                }
                for item in items
            ],
        }
        return self._call_tool("create_document", {"doctype": "Purchase Order", "data": data})

    def _item_name(self, item_code: str) -> str:
        """Best-effort real item_name lookup (required on a PO line) --
        falls back to the item_code itself rather than failing the whole
        PO submission over a cosmetic label."""
        try:
            doc = self._get_document("Item", item_code)
            return (doc or {}).get("item_name") or item_code
        except Exception:
            return item_code

    def get_budget(self, *, cost_center: Optional[str] = None) -> Optional[float]:
        filters = {"cost_center": cost_center} if cost_center else None
        rows = self._list_documents("Budget", filters=filters, fields=ERPNextRestClient._BUDGET_FIELDS)
        if not rows:
            return None
        row = rows[0]
        return row.get("budget_amount") or row.get("budget_against_total") or row.get("total_budget")

    def list_invoices(self, *, supplier: Optional[str] = None) -> List[Dict[str, Any]]:
        filters = {"supplier": supplier} if supplier else None
        return self._list_documents("Purchase Invoice", filters=filters)


def get_configured_client() -> tuple[ERPNextClient, str]:
    """Build the ERPNext client the deployment is actually configured for.

    Returns ``(client, backend_name)`` -- the backend name ("erpnext_mcp",
    "erpnext_rest" or "seeded") is for the Integration Health view and API
    responses, so the UI can label which data is real and which is seeded,
    per spec ("Clearly distinguish seeded data from real ERPNext records").

    Precedence, highest first:
      1. ``ERPNEXT_USE_OFFICIAL_MCP=1`` (plus the three ERPNEXT_*
         credentials) -- Frappe's own built-in MCP endpoint
         (FrappeMCPClient, verified live). Opt-in, not a silent default
         change: current behavior (direct REST) is preserved when unset.
      2. ``ERPNEXT_MCP_SERVER_ENTRY`` (plus the three credentials) -- the
         deprecated third-party MCP server path (rakeshgangwar/
         erpnext-mcp-server, see mcp_client.py). Kept for backward
         compatibility only; prefer (1) for any new setup.
      3. Just the three credentials -- the direct REST client.
      4. Otherwise -- the seeded stand-in.

    ``ERPNEXT_ALLOW_WRITES=1`` additionally enables create_rfq/
    create_purchase_order against a real instance; without it, those calls
    raise ERPNextUnavailable even when ERPNext itself is configured --
    "prefer read-only access initially" stays the default for a real
    backend until explicitly opted into.
    """
    url = os.getenv("ERPNEXT_URL")
    api_key = os.getenv("ERPNEXT_API_KEY")
    api_secret = os.getenv("ERPNEXT_API_SECRET")
    mcp_server_entry = os.getenv("ERPNEXT_MCP_SERVER_ENTRY")
    use_official_mcp = os.getenv("ERPNEXT_USE_OFFICIAL_MCP", "").strip().lower() in {"1", "true", "yes", "on"}
    allow_writes = os.getenv("ERPNEXT_ALLOW_WRITES", "").strip().lower() in {"1", "true", "yes", "on"}

    if url and api_key and api_secret and use_official_mcp:
        rest = ERPNextRestClient(url, api_key, api_secret, allow_writes=allow_writes)
        return FrappeMCPClient(rest), "erpnext_official_mcp"
    if url and api_key and api_secret and mcp_server_entry:
        from .mcp_client import MCPERPNextClient

        return (
            MCPERPNextClient(
                server_entry=mcp_server_entry,
                erpnext_url=url,
                api_key=api_key,
                api_secret=api_secret,
                allow_writes=allow_writes,
            ),
            "erpnext_mcp",
        )
    if url and api_key and api_secret:
        return ERPNextRestClient(url, api_key, api_secret, allow_writes=allow_writes), "erpnext_rest"
    return SeededERPNextClient(), "seeded"
