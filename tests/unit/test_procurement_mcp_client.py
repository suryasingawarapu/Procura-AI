"""Unit tests for MCPERPNextClient: the MCP stdio transport and SDK calls
are mocked throughout (no real subprocess, no Node, no live server) --
these test this wrapper's own logic (tool allowlisting, write gating,
isError/JSON-response handling), not the third-party server or SDK.
Real, live verification is tracked separately (see the project README).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.procurement.mcp.client import ERPNextUnavailable
from app.procurement.mcp.mcp_client import MCPERPNextClient


def _client(**kw):
    return MCPERPNextClient(
        server_entry="build/index.js",
        erpnext_url="https://erp.example.com",
        api_key="key",
        api_secret="secret",
        **kw,
    )


def test_requires_all_credentials():
    with pytest.raises(ERPNextUnavailable):
        MCPERPNextClient(server_entry="x", erpnext_url="", api_key="k", api_secret="s")


def _mock_mcp_chain(result_content_text: str, is_error: bool = False):
    """Patches mcp.ClientSession.call_tool to return one text content block,
    exactly like the real server's response shape, and stdio_client/
    ClientSession as working async context managers around it."""
    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = result_content_text
    mock_result.content = [block]
    mock_result.isError = is_error

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    stdio_cm = AsyncMock()
    stdio_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    stdio_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_session, session_cm, stdio_cm


def test_list_suppliers_calls_get_documents_and_parses_json():
    _session, session_cm, stdio_cm = _mock_mcp_chain(json.dumps([{"name": "SUP-0001"}]))
    with patch("mcp.client.stdio.stdio_client", return_value=stdio_cm), patch(
        "mcp.ClientSession", return_value=session_cm
    ):
        client = _client()
        result = client.list_suppliers()
    assert result == [{"name": "SUP-0001"}]
    _session.call_tool.assert_called_once_with("get_documents", {"doctype": "Supplier", "filters": {}})


def test_isError_response_raises_erpnext_unavailable():
    _session, session_cm, stdio_cm = _mock_mcp_chain("Failed to get Supplier documents: no permission", is_error=True)
    with patch("mcp.client.stdio.stdio_client", return_value=stdio_cm), patch(
        "mcp.ClientSession", return_value=session_cm
    ):
        client = _client()
        with pytest.raises(ERPNextUnavailable, match="no permission"):
            client.list_suppliers()


def test_non_json_response_raises_erpnext_unavailable_not_a_raw_exception():
    _session, session_cm, stdio_cm = _mock_mcp_chain("not json at all")
    with patch("mcp.client.stdio.stdio_client", return_value=stdio_cm), patch(
        "mcp.ClientSession", return_value=session_cm
    ):
        client = _client()
        with pytest.raises(ERPNextUnavailable):
            client.list_suppliers()


def test_writes_disabled_by_default_never_calls_the_tool():
    client = _client()
    with pytest.raises(ERPNextUnavailable):
        client.create_rfq(items=["ITEM-1"], supplier_names=["SUP-0001"])
    # no mcp call attempted -- mcp isn't even patched here, so a real call
    # attempt would raise a *different*, unmocked error if this didn't
    # short-circuit first.


def test_purchase_order_requires_approval_even_with_writes_enabled():
    client = _client(allow_writes=True)
    with pytest.raises(PermissionError):
        client.create_purchase_order(supplier="SUP-0001", items=[], approved=False)


def test_allowed_tools_allowlist_rejects_unlisted_tool_names():
    client = _client()
    with pytest.raises(ERPNextUnavailable):
        # not a real public method -- directly exercising the allowlist
        # enforcement inside _call_tool/_call_tool_async
        client._call_tool("delete_document", {"doctype": "Supplier", "name": "SUP-0001"})


def test_get_supplier_missing_returns_none():
    _session, session_cm, stdio_cm = _mock_mcp_chain(
        "Failed to get Supplier SUP-9999: Document SUP-9999 does not exist", is_error=True
    )
    with patch("mcp.client.stdio.stdio_client", return_value=stdio_cm), patch(
        "mcp.ClientSession", return_value=session_cm
    ):
        client = _client()
        assert client.get_supplier("SUP-9999") is None


def test_create_purchase_order_calls_create_document_when_approved_and_writes_allowed():
    _session, session_cm, stdio_cm = _mock_mcp_chain(json.dumps({"name": "PO-0001", "docstatus": 0}))
    with patch("mcp.client.stdio.stdio_client", return_value=stdio_cm), patch(
        "mcp.ClientSession", return_value=session_cm
    ):
        client = _client(allow_writes=True)
        result = client.create_purchase_order(supplier="SUP-0001", items=[{"item_code": "ITEM-1", "qty": 1}], approved=True)
    assert result["name"] == "PO-0001"
    _session.call_tool.assert_called_once()
    assert _session.call_tool.call_args.args[0] == "create_document"
    assert _session.call_tool.call_args.args[1]["doctype"] == "Purchase Order"
