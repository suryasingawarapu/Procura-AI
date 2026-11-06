"""ERPNext access via rakeshgangwar/erpnext-mcp-server, over the real MCP
protocol (stdio transport, the official `mcp` Python SDK).

VERIFIED before writing this (not assumed):
  * Repo: https://github.com/rakeshgangwar/erpnext-mcp-server, MIT license,
    127 stars, not archived, pushed within the last few months (checked via
    the GitHub API). Built on the official `@modelcontextprotocol/sdk`.
  * Source read directly (src/index.ts, ~1000 lines): confirms the exact 11
    tool names and JSON input schemas used below (get_documents, get_document,
    create_document, update_document, run_report, get_doctypes,
    get_doctype_fields -- and, NOT used here, delete_document,
    submit_document, cancel_document, call_method). Auth is a single
    `Authorization: token <key>:<secret>` header, same scheme
    ERPNextRestClient already uses. Tool results come back as
    `content: [{type: "text", text: <JSON or a plain error string>}]`,
    with `isError: true` set on failure.
  * No permission scoping in the server itself -- confirmed by reading the
    source, matching what the README said. The restriction to a safe tool
    subset below is enforced in this wrapper, not by the server.

NOT YET LIVE-TESTED: building the cloned server (`npm install` / `npm run
build`) needs explicit approval to run a fresh external repo's build
scripts, which was not given when this was written -- see the project
README for the exact pending step. This class is written correctly against
the SDK's actual installed API and the server's actual source, but has not
made a real call against a running server yet.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from .client import ERPNextUnavailable

# Tools this wrapper will ever call. delete_document, submit_document,
# cancel_document and call_method are deliberately never referenced here --
# per spec, "avoid unrestricted delete tools" and give each system only the
# tools it needs. The server does not enforce this itself; this allowlist
# is the enforcement.
_ALLOWED_TOOLS = {
    "get_doctypes",
    "get_doctype_fields",
    "get_documents",
    "get_document",
    "create_document",
    "update_document",
    "run_report",
}


class MCPERPNextClient:
    """Implements the ERPNextClient protocol by calling the MCP server's
    tools over stdio. Spawns a fresh server subprocess per call (simple and
    correct for this system's call volume -- a handful of calls per
    procurement task -- rather than managing a long-lived async session
    across this codebase's synchronous call sites)."""

    def __init__(
        self,
        *,
        node_command: str = "node",
        server_entry: str,
        erpnext_url: str,
        api_key: str,
        api_secret: str,
        allow_writes: bool = False,
    ):
        if not erpnext_url or not api_key or not api_secret:
            raise ERPNextUnavailable("ERPNEXT_URL, ERPNEXT_API_KEY and ERPNEXT_API_SECRET are all required")
        self._node_command = node_command
        self._server_entry = server_entry
        self._env = {
            "ERPNEXT_URL": erpnext_url,
            "ERPNEXT_API_KEY": api_key,
            "ERPNEXT_API_SECRET": api_secret,
        }
        self._allow_writes = allow_writes

    async def _call_tool_async(self, name: str, arguments: Dict[str, Any]) -> Any:
        if name not in _ALLOWED_TOOLS:
            raise ERPNextUnavailable(f"tool {name!r} is not in this client's allowed tool set")

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._node_command, args=[self._server_entry], env=self._env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)

        text = ""
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = block.text
                break

        if getattr(result, "isError", False):
            raise ERPNextUnavailable(f"{name} failed: {text}")
        try:
            return json.loads(text) if text else None
        except (json.JSONDecodeError, TypeError) as exc:
            raise ERPNextUnavailable(f"{name} returned a non-JSON response: {text!r}") from exc

    def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        try:
            return asyncio.run(self._call_tool_async(name, arguments))
        except ERPNextUnavailable:
            raise
        except Exception as exc:
            raise ERPNextUnavailable(f"MCP call to {name} failed: {exc}") from exc

    # -- ERPNextClient protocol ------------------------------------------

    def list_suppliers(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self._call_tool("get_documents", {"doctype": "Supplier", "filters": filters or {}}) or []

    def get_supplier(self, supplier_name: str) -> Optional[Dict[str, Any]]:
        try:
            return self._call_tool("get_document", {"doctype": "Supplier", "name": supplier_name})
        except ERPNextUnavailable as exc:
            if "does not exist" in str(exc).lower() or "not found" in str(exc).lower():
                return None
            raise

    def list_supplier_documents(self, supplier_name: str) -> List[Dict[str, Any]]:
        return (
            self._call_tool(
                "get_documents", {"doctype": "Supplier Document", "filters": {"supplier": supplier_name}}
            )
            or []
        )

    def list_items(self, *, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self._call_tool("get_documents", {"doctype": "Item", "filters": filters or {}}) or []

    def create_rfq(
        self, *, items: List[str], supplier_names: List[str], source_reference: Optional[str] = None
    ) -> Dict[str, Any]:
        if not self._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create an RFQ")
        data = {
            "items": [{"item_code": i} for i in items],
            "suppliers": [{"supplier": s} for s in supplier_names],
        }
        return self._call_tool(
            "create_document", {"doctype": "Request for Quotation", "data": data, "verbose": True}
        )

    def list_supplier_quotations(
        self, *, rfq_name: Optional[str] = None, item_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        filters: Dict[str, Any] = {}
        if rfq_name:
            filters["request_for_quotation"] = rfq_name
        if item_code:
            filters["item_code"] = item_code
        return self._call_tool("get_documents", {"doctype": "Supplier Quotation", "filters": filters}) or []

    def create_purchase_order(
        self, *, supplier: str, items: List[Dict[str, Any]], approved: bool
    ) -> Dict[str, Any]:
        if not approved:
            raise PermissionError("create_purchase_order requires explicit human approval (approved=True)")
        if not self._allow_writes:
            raise ERPNextUnavailable("writes are disabled (allow_writes=False); refused to create a purchase order")
        return self._call_tool(
            "create_document",
            {"doctype": "Purchase Order", "data": {"supplier": supplier, "items": items}, "verbose": True},
        )

    def get_budget(self, *, cost_center: Optional[str] = None) -> Optional[float]:
        filters = {"cost_center": cost_center} if cost_center else {}
        rows = self._call_tool("get_documents", {"doctype": "Budget", "filters": filters}) or []
        if not rows:
            return None
        return rows[0].get("budget_against_total") or rows[0].get("total_budget")

    def list_invoices(self, *, supplier: Optional[str] = None) -> List[Dict[str, Any]]:
        filters = {"supplier": supplier} if supplier else {}
        return self._call_tool("get_documents", {"doctype": "Purchase Invoice", "filters": filters}) or []
