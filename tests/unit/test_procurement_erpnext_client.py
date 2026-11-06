"""Unit tests for the ERPNext client layer: the seeded implementation
(fully real, exercised directly) and the REST implementation (real logic,
HTTP mocked -- no network, no live ERPNext instance needed or assumed).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.procurement.mcp.client import ERPNextRestClient, ERPNextUnavailable, SeededERPNextClient


# -- seeded client ------------------------------------------------------


def test_seeded_suppliers_are_labeled_as_seeded():
    client = SeededERPNextClient()
    suppliers = client.list_suppliers()
    assert len(suppliers) == 3
    assert all(s["_seeded"] is True for s in suppliers)
    assert all(s["source_reference"].startswith("Supplier/") for s in suppliers)


def test_seeded_get_supplier_found_and_missing():
    client = SeededERPNextClient()
    assert client.get_supplier("SUP-0001")["supplier_name"] == "Northbridge Computing Ltd"
    assert client.get_supplier("SUP-9999") is None


def test_seeded_filters_suppliers():
    client = SeededERPNextClient()
    on_hold = client.list_suppliers(filters={"on_hold": 1})
    assert [s["name"] for s in on_hold] == ["SUP-0003"]


def test_seeded_supplier_documents_distinguish_complete_vs_incomplete():
    client = SeededERPNextClient()
    assert len(client.list_supplier_documents("SUP-0001")) == 2  # complete
    assert len(client.list_supplier_documents("SUP-0002")) == 1  # incomplete
    assert client.list_supplier_documents("SUP-0003") == []  # none on file


def test_seeded_create_rfq_is_idempotent_record_and_mutates_internal_state():
    client = SeededERPNextClient()
    rfq1 = client.create_rfq(items=["ITEM-LAPTOP-14"], supplier_names=["SUP-0001", "SUP-0002"])
    rfq2 = client.create_rfq(items=["ITEM-LAPTOP-14"], supplier_names=["SUP-0001"])
    assert rfq1["name"] != rfq2["name"]  # each call creates a new, distinct record
    assert rfq1["_seeded"] is True


def test_seeded_quotations_filtered_by_item_code():
    client = SeededERPNextClient()
    quotes = client.list_supplier_quotations(item_code="ITEM-LAPTOP-14")
    assert {q["supplier"] for q in quotes} == {"SUP-0001", "SUP-0002"}


def test_seeded_purchase_order_requires_explicit_approval():
    client = SeededERPNextClient()
    with pytest.raises(PermissionError):
        client.create_purchase_order(supplier="SUP-0001", items=[{"item_code": "ITEM-LAPTOP-14", "qty": 20}], approved=False)

    po = client.create_purchase_order(
        supplier="SUP-0001", items=[{"item_code": "ITEM-LAPTOP-14", "qty": 20}], approved=True
    )
    assert po["supplier"] == "SUP-0001"
    assert po["_seeded"] is True


def test_seeded_budget_is_a_real_configured_number_not_unlimited():
    client = SeededERPNextClient()
    assert client.get_budget() == 25000.0
    assert client.get_budget(cost_center="nonexistent") is None  # missing, reported as None, not invented


def test_seeded_mutation_does_not_leak_between_instances():
    a = SeededERPNextClient()
    b = SeededERPNextClient()
    a.create_rfq(items=["x"], supplier_names=["SUP-0001"])
    a.create_rfq(items=["y"], supplier_names=["SUP-0001"])
    # b's counter still starts fresh -- it never saw a's two prior creates
    assert b.create_rfq(items=["z"], supplier_names=["SUP-0001"])["name"] == "RFQ-SEED-0001"


# -- REST client (real logic, HTTP mocked) -------------------------------


def _fake_session(get_data=None, get_status=200):
    session = MagicMock()
    response = MagicMock()
    response.status_code = get_status
    response.json.return_value = {"data": get_data if get_data is not None else []}
    response.raise_for_status.return_value = None
    session.get.return_value = response
    session.post.return_value = response
    return session, response


def test_rest_client_requires_all_credentials():
    with pytest.raises(ERPNextUnavailable):
        ERPNextRestClient("", "key", "secret")
    with pytest.raises(ERPNextUnavailable):
        ERPNextRestClient("https://erp.example.com", "", "secret")


def test_rest_client_list_requests_always_disable_the_default_page_cap():
    """Regression test for a real bug found live: ERPNext's REST list
    endpoint silently caps results at 20 rows with no error and no
    "has_next_page" signal -- confirmed live with a real 32-supplier
    account, where a plain, unlimited GET returned only 20. Vendra/Procura
    then silently stopped seeing some suppliers/quotations once real data
    grew past that cap. limit_page_length=0 ("no limit") must be sent on
    every list request, not just the ones someone happened to test."""
    session, _ = _fake_session(get_data=[{"name": "SUP-0001"}])
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)

    client.list_suppliers()

    assert session.get.call_args.kwargs["params"]["limit_page_length"] == 0


def test_rest_client_list_suppliers_sends_token_auth_header():
    session, _ = _fake_session(get_data=[{"name": "SUP-0001"}])
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)

    result = client.list_suppliers()

    assert result[0]["name"] == "SUP-0001"
    assert result[0]["source_reference"] == "Supplier/SUP-0001"  # filled in when ERPNext doesn't send one
    call = session.get.call_args
    assert call.args[0] == "https://erp.example.com/api/resource/Supplier"
    assert call.kwargs["headers"]["Authorization"] == "token key:secret"
    assert "on_hold" in call.kwargs["params"]["fields"]  # explicit fields requested -- confirmed live


def test_rest_client_list_supplier_documents_queries_file_doctype():
    """"Supplier Document" isn't a real doctype (confirmed live); this must
    query Frappe's real File doctype, filtered to this supplier's
    attachments, not the nonexistent one."""
    session, _ = _fake_session(get_data=[{"name": "f1", "file_name": "w9.pdf"}])
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)

    result = client.list_supplier_documents("SUP-0001")

    assert result == [{"name": "f1", "file_name": "w9.pdf"}]
    call = session.get.call_args
    assert call.args[0] == "https://erp.example.com/api/resource/File"
    filters = json.loads(call.kwargs["params"]["filters"])
    assert ["attached_to_doctype", "=", "Supplier"] in filters
    assert ["attached_to_name", "=", "SUP-0001"] in filters


def test_rest_client_get_supplier_missing_returns_none_on_404():
    session, response = _fake_session()
    response.status_code = 404
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)
    assert client.get_supplier("SUP-9999") is None


def test_rest_client_network_error_raises_erpnext_unavailable_not_a_raw_exception():
    session = MagicMock()
    session.get.side_effect = ConnectionError("no route to host")
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)
    with pytest.raises(ERPNextUnavailable):
        client.list_suppliers()


def test_rest_client_writes_disabled_by_default():
    session, _ = _fake_session()
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)
    with pytest.raises(ERPNextUnavailable):
        client.create_rfq(items=["ITEM-1"], supplier_names=["SUP-0001"])
    session.post.assert_not_called()


def test_rest_client_writes_allowed_when_opted_in():
    session, _ = _fake_session(get_data={"name": "RFQ-0001"})
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session, allow_writes=True)
    result = client.create_rfq(items=["ITEM-1"], supplier_names=["SUP-0001"])
    assert result == {"name": "RFQ-0001"}
    session.post.assert_called_once()


def test_rest_client_purchase_order_requires_approval_even_with_writes_enabled():
    session, _ = _fake_session()
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session, allow_writes=True)
    with pytest.raises(PermissionError):
        client.create_purchase_order(supplier="SUP-0001", items=[], approved=False)
    session.post.assert_not_called()


def test_rest_client_get_budget_reads_the_real_schema_field():
    """`budget_amount` is the real, live-confirmed field on this project's
    Budget doctype (see BUDGET-00000001) -- the old `budget_against_total`/
    `total_budget` guesses never existed there."""
    session, _ = _fake_session(get_data=[{"name": "BUDGET-00000001", "budget_amount": 1_100_000.0}])
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)

    assert client.get_budget(cost_center="Main - PAD") == 1_100_000.0
    call = session.get.call_args
    assert call.args[0] == "https://erp.example.com/api/resource/Budget"
    assert "budget_amount" in call.kwargs["params"]["fields"]  # explicit fields requested -- confirmed live


def test_rest_client_get_budget_missing_returns_none_not_invented():
    session, _ = _fake_session(get_data=[])
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)
    assert client.get_budget(cost_center="nonexistent") is None


def test_rest_client_get_budget_falls_back_to_legacy_field_names():
    """Preserves the previously supported return shape for a Budget row
    that supplies the older field names instead of `budget_amount`."""
    session, _ = _fake_session(get_data=[{"name": "BUDGET-0001", "budget_against_total": 5000.0}])
    client = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session)
    assert client.get_budget() == 5000.0


# -- client factory -----------------------------------------------------


def test_get_configured_client_defaults_to_seeded(monkeypatch):
    from app.procurement.mcp.client import SeededERPNextClient, get_configured_client

    monkeypatch.delenv("ERPNEXT_URL", raising=False)
    monkeypatch.delenv("ERPNEXT_API_KEY", raising=False)
    monkeypatch.delenv("ERPNEXT_API_SECRET", raising=False)

    client, backend = get_configured_client()

    assert backend == "seeded"
    assert isinstance(client, SeededERPNextClient)


def test_get_configured_client_uses_rest_client_when_fully_configured(monkeypatch):
    from app.procurement.mcp.client import get_configured_client

    monkeypatch.setenv("ERPNEXT_URL", "https://erp.example.com")
    monkeypatch.setenv("ERPNEXT_API_KEY", "key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "secret")
    monkeypatch.delenv("ERPNEXT_ALLOW_WRITES", raising=False)
    # This project's own .env now defaults ERPNEXT_USE_OFFICIAL_MCP=1
    # (loaded once at process start by app.memory.client's load_dotenv());
    # this test wants the plain-REST precedence path specifically.
    monkeypatch.delenv("ERPNEXT_USE_OFFICIAL_MCP", raising=False)

    client, backend = get_configured_client()

    assert backend == "erpnext_rest"
    assert client._allow_writes is False


def test_get_configured_client_partial_config_falls_back_to_seeded(monkeypatch):
    from app.procurement.mcp.client import SeededERPNextClient, get_configured_client

    monkeypatch.setenv("ERPNEXT_URL", "https://erp.example.com")
    monkeypatch.delenv("ERPNEXT_API_KEY", raising=False)
    monkeypatch.delenv("ERPNEXT_API_SECRET", raising=False)

    client, backend = get_configured_client()

    assert backend == "seeded"
    assert isinstance(client, SeededERPNextClient)


def test_get_configured_client_prefers_mcp_when_server_entry_set(monkeypatch):
    from app.procurement.mcp.client import get_configured_client
    from app.procurement.mcp.mcp_client import MCPERPNextClient

    monkeypatch.setenv("ERPNEXT_URL", "https://erp.example.com")
    monkeypatch.setenv("ERPNEXT_API_KEY", "key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "secret")
    monkeypatch.setenv("ERPNEXT_MCP_SERVER_ENTRY", "/path/to/build/index.js")
    # The official MCP path takes precedence over this deprecated one; this
    # test wants the deprecated-path precedence specifically, so force the
    # official-MCP flag off regardless of this project's own .env default.
    monkeypatch.delenv("ERPNEXT_USE_OFFICIAL_MCP", raising=False)

    client, backend = get_configured_client()

    assert backend == "erpnext_mcp"
    assert isinstance(client, MCPERPNextClient)


def test_get_configured_client_falls_back_to_rest_without_mcp_entry(monkeypatch):
    from app.procurement.mcp.client import ERPNextRestClient, get_configured_client

    monkeypatch.setenv("ERPNEXT_URL", "https://erp.example.com")
    monkeypatch.setenv("ERPNEXT_API_KEY", "key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "secret")
    monkeypatch.delenv("ERPNEXT_MCP_SERVER_ENTRY", raising=False)
    monkeypatch.delenv("ERPNEXT_USE_OFFICIAL_MCP", raising=False)

    client, backend = get_configured_client()

    assert backend == "erpnext_rest"
    assert isinstance(client, ERPNextRestClient)


# -- FrappeMCPClient (official built-in /api/v2/mcp, HTTP mocked) -----------
#
# Response shapes below (isError on HTTP 200, content[0].text as a JSON
# string, a short generic/sanitized error message rather than an exception
# detail) were confirmed against the real hosted site before this client
# was written -- not guessed. See FrappeMCPClient's docstring.


def _mcp_rest(session=None):
    return ERPNextRestClient("https://erp.example.com", "key", "secret", session=session or MagicMock())


def _mcp_ok(text):
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}], "isError": False},
    }
    return response


def _mcp_error(text):
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}], "isError": True},
    }
    return response


def test_frappe_mcp_list_suppliers_parses_documents_and_fills_source_reference():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_ok('{"documents": [{"name": "SUP-0001", "on_hold": 0}], "has_next_page": false}')
    client = FrappeMCPClient(_mcp_rest(session))

    result = client.list_suppliers()

    assert result == [{"name": "SUP-0001", "on_hold": 0, "source_reference": "Supplier/SUP-0001"}]
    call = session.post.call_args
    assert call.args[0] == "https://erp.example.com/api/v2/mcp"
    assert call.kwargs["headers"]["Authorization"] == "token key:secret"
    assert call.kwargs["json"]["method"] == "tools/call"
    assert call.kwargs["json"]["params"]["name"] == "list_documents"
    assert call.kwargs["json"]["params"]["arguments"]["doctype"] == "Supplier"


def test_frappe_mcp_list_requests_always_pass_a_limit_above_the_default_page_cap():
    """Regression test for the same real bug as the REST client's own
    limit_page_length fix: the MCP list_documents tool silently caps
    results at a small default page (confirmed live) unless a limit is
    explicitly passed -- every list call here must always send one, not
    just the ones a caller happened to specify."""
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_ok('{"documents": [], "has_next_page": false}')
    client = FrappeMCPClient(_mcp_rest(session))

    client.list_suppliers()

    assert session.post.call_args.kwargs["json"]["params"]["arguments"]["limit"] == 500


def test_frappe_mcp_isError_result_raises_erpnext_unavailable_with_sanitized_message():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_error("PermissionError")
    client = FrappeMCPClient(_mcp_rest(session))

    with pytest.raises(ERPNextUnavailable, match="PermissionError"):
        client.list_suppliers()


def test_frappe_mcp_json_rpc_error_key_raises_erpnext_unavailable():
    from app.procurement.mcp.client import FrappeMCPClient

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    session = MagicMock()
    session.post.return_value = response
    client = FrappeMCPClient(_mcp_rest(session))

    with pytest.raises(ERPNextUnavailable):
        client.list_suppliers()


def test_frappe_mcp_malformed_non_json_body_raises_erpnext_unavailable():
    from app.procurement.mcp.client import FrappeMCPClient

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")
    response.text = "<html>not json</html>"
    session = MagicMock()
    session.post.return_value = response
    client = FrappeMCPClient(_mcp_rest(session))

    with pytest.raises(ERPNextUnavailable):
        client.list_suppliers()


def test_frappe_mcp_network_error_raises_erpnext_unavailable_not_raw():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.side_effect = ConnectionError("no route to host")
    client = FrappeMCPClient(_mcp_rest(session))

    with pytest.raises(ERPNextUnavailable):
        client.list_suppliers()


def test_frappe_mcp_get_supplier_missing_returns_none():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_error("Document SUP-9999 does not exist")
    client = FrappeMCPClient(_mcp_rest(session))

    assert client.get_supplier("SUP-9999") is None


def test_frappe_mcp_list_supplier_documents_queries_file_doctype():
    """Same real-doctype correction as the REST client: "Supplier Document"
    doesn't exist on the live site (confirmed via search_doctypes); this
    must query Frappe's real File doctype instead."""
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_ok('{"documents": [{"name": "f1", "file_name": "w9.pdf"}], "has_next_page": false}')
    client = FrappeMCPClient(_mcp_rest(session))

    result = client.list_supplier_documents("SUP-0001")

    assert result == [{"name": "f1", "file_name": "w9.pdf"}]
    args = session.post.call_args.kwargs["json"]["params"]["arguments"]
    assert args["doctype"] == "File"
    assert args["filters"] == {"attached_to_doctype": "Supplier", "attached_to_name": "SUP-0001"}


def test_frappe_mcp_get_budget_reads_the_real_schema_field():
    """Same live-confirmed field as the REST client's equivalent test --
    reads the real BUDGET-00000001 shape, not the old unconfirmed names."""
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_ok(
        '{"documents": [{"name": "BUDGET-00000001", "budget_amount": 1100000.0, '
        '"cost_center": "Main - PAD", "account": "Miscellaneous Expenses - PAD"}], "has_next_page": false}'
    )
    client = FrappeMCPClient(_mcp_rest(session))

    assert client.get_budget(cost_center="Main - PAD") == 1_100_000.0
    args = session.post.call_args.kwargs["json"]["params"]["arguments"]
    assert args["doctype"] == "Budget"
    assert "budget_amount" in args["fields"]


def test_frappe_mcp_get_budget_missing_returns_none_not_invented():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_ok('{"documents": [], "has_next_page": false}')
    client = FrappeMCPClient(_mcp_rest(session))

    assert client.get_budget(cost_center="nonexistent") is None


def test_frappe_mcp_get_budget_falls_back_to_legacy_field_names():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    session.post.return_value = _mcp_ok('{"documents": [{"name": "BUDGET-0001", "total_budget": 2500.0}], "has_next_page": false}')
    client = FrappeMCPClient(_mcp_rest(session))

    assert client.get_budget() == 2500.0


def test_frappe_mcp_create_rfq_blocked_without_allow_writes():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    client = FrappeMCPClient(_mcp_rest(session))

    with pytest.raises(ERPNextUnavailable):
        client.create_rfq(items=["ITEM-1"], supplier_names=["SUP-0001"])
    session.post.assert_not_called()


def test_frappe_mcp_purchase_order_requires_approval_even_with_writes_enabled():
    from app.procurement.mcp.client import FrappeMCPClient

    session = MagicMock()
    rest = ERPNextRestClient("https://erp.example.com", "key", "secret", session=session, allow_writes=True)
    client = FrappeMCPClient(rest)

    with pytest.raises(PermissionError):
        client.create_purchase_order(supplier="SUP-0001", items=[], approved=False)
    session.post.assert_not_called()


def test_frappe_mcp_list_supplier_quotations_flattens_matching_item_rows():
    from app.procurement.mcp.client import FrappeMCPClient

    def side_effect(*args, **kwargs):
        tool = kwargs["json"]["params"]["name"]
        if tool == "list_documents":
            return _mcp_ok('{"documents": [{"name": "SQ-1"}], "has_next_page": false}')
        doc = {
            "name": "SQ-1",
            "supplier": "SUP-0001",
            "currency": "USD",
            "transaction_date": "2026-01-01",
            "items": [
                {"item_code": "ITEM-LAPTOP-14", "rate": 950.0, "qty": 20, "schedule_date": "2026-01-11"},
                {"item_code": "ITEM-OTHER", "rate": 10.0, "qty": 1, "schedule_date": "2026-01-05"},
            ],
        }
        return _mcp_ok(json.dumps(doc))

    session = MagicMock()
    session.post.side_effect = side_effect
    client = FrappeMCPClient(_mcp_rest(session))

    rows = client.list_supplier_quotations(item_code="ITEM-LAPTOP-14")

    assert len(rows) == 1
    assert rows[0]["supplier"] == "SUP-0001"
    assert rows[0]["rate"] == 950.0
    assert rows[0]["delivery_days"] == 10
    assert rows[0]["source_reference"] == "Supplier Quotation/SQ-1"


# -- get_configured_client: the new official-MCP opt-in ----------------------


def test_get_configured_client_official_mcp_opt_in(monkeypatch):
    from app.procurement.mcp.client import FrappeMCPClient, get_configured_client

    monkeypatch.setenv("ERPNEXT_URL", "https://erp.example.com")
    monkeypatch.setenv("ERPNEXT_API_KEY", "key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "secret")
    monkeypatch.setenv("ERPNEXT_USE_OFFICIAL_MCP", "1")
    monkeypatch.delenv("ERPNEXT_MCP_SERVER_ENTRY", raising=False)

    client, backend = get_configured_client()

    assert backend == "erpnext_official_mcp"
    assert isinstance(client, FrappeMCPClient)


def test_get_configured_client_preserves_rest_default_when_official_mcp_unset(monkeypatch):
    """Explicit regression: current behavior must not change when the new
    flag is unset -- same assertion as before this flag existed."""
    monkeypatch.setenv("ERPNEXT_URL", "https://erp.example.com")
    monkeypatch.setenv("ERPNEXT_API_KEY", "key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "secret")
    monkeypatch.delenv("ERPNEXT_USE_OFFICIAL_MCP", raising=False)
    monkeypatch.delenv("ERPNEXT_MCP_SERVER_ENTRY", raising=False)

    from app.procurement.mcp.client import get_configured_client

    client, backend = get_configured_client()

    assert backend == "erpnext_rest"
    assert isinstance(client, ERPNextRestClient)
