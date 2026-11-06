"""Unit tests for the cross-system coordinator: routing, the full required
demo workflow end to end against the seeded ERPNext client, partial
routing, and failure handling. Memory writes are mocked throughout (real
Supabase otherwise); ERPNext is the real SeededERPNextClient except where
a test specifically wants to simulate ERPNext being unavailable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import app.procurement.coordinator as coordinator_module
from app.procurement.coordinator import _ai_summary_llm, classify_systems_needed, run_coordinator
from app.procurement.identities import FINORA, PROCURA, VENDRA
from app.procurement.mcp.client import ERPNextUnavailable, SeededERPNextClient, _default_seed
from app.procurement.messages import TaskRequest

DEMO_TASK = (
    "Find eligible suppliers for 20 laptops, collect/compare quotations, "
    "check the available budget, and prepare the purchase for approval."
)


# -- routing --------------------------------------------------------------


def test_classify_full_demo_task_needs_all_three_in_order():
    assert classify_systems_needed(DEMO_TASK) == [VENDRA, PROCURA, FINORA]


def test_classify_budget_only_task_needs_only_finora():
    assert classify_systems_needed("What is our current budget for office supplies?") == [FINORA]


def test_classify_vendor_only_task_needs_only_vendra():
    assert classify_systems_needed("Check vendor compliance documents for all suppliers.") == [VENDRA]


def test_classify_unrelated_task_routes_nowhere():
    assert classify_systems_needed("What's the weather like today?") == []


# -- full demo workflow, end to end ---------------------------------------


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_full_demo_workflow_runs_all_three_systems_and_requires_approval(mock_save, _ctx, _supabase):
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description=DEMO_TASK)

    result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14", quantity=20)

    assert result.failed is False, result.error
    assert result.systems_invoked == [VENDRA, PROCURA, FINORA]
    assert [m.sender for m in result.messages] == [VENDRA, PROCURA, FINORA]
    assert result.result["recommended_supplier"] == "SUP-0001"
    assert result.result["recommended_total_cost"] == 19000.0
    assert result.requires_approval is True
    assert "awaiting explicit approval" in result.approval_summary.lower()
    # no purchase order was created by the coordinator itself
    assert client._data["purchase_orders"] == []


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_budget_only_task_runs_only_finora(mock_save, _ctx, _supabase):
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description="What is our current budget?")

    # Finora alone has no recommended_total to validate against -- this is
    # the honest failure the coordinator reports instead of inventing one.
    result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14")

    assert result.systems_invoked == [FINORA]
    assert result.failed is True
    assert "no recommended supplier/total" in result.error


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_vendor_only_task_runs_only_vendra(mock_save, _ctx, _supabase):
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description="Check vendor compliance documents for all suppliers.")

    result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14")

    assert result.systems_invoked == [VENDRA]
    assert result.failed is False
    assert result.requires_approval is False  # Procura never ran, nothing to approve


def test_unrelated_task_fails_cleanly_without_touching_memory_or_erpnext():
    client = SeededERPNextClient()
    task = TaskRequest(description="What's the weather like today?")
    result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14")
    assert result.failed is True
    assert result.systems_invoked == []
    assert "nothing was routed" in result.error


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_coordinator_blocks_cleanly_when_no_suppliers_are_compliant(mock_save, _ctx, _supabase):
    """The real safety gap this test guards: if Vendra finds zero eligible
    suppliers, Procura must never create an RFQ or fall back to comparing
    quotations from the (non-compliant) suppliers that do have one."""
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    seed = _default_seed()
    seed["supplier_documents"] = {}  # no supplier has any document on file
    client = SeededERPNextClient(seed)
    task = TaskRequest(description=DEMO_TASK)

    result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14", quantity=20)

    assert result.result["eligible_supplier_names"] == []
    assert result.result["recommended_supplier"] is None
    # no RFQ was created even though ITEM-LAPTOP-14 has quotations on file
    assert client._data["rfqs"] == []
    assert client._data["purchase_orders"] == []
    # a compliance block is a clean, honest outcome -- never a pipeline
    # failure, and never something requiring purchase-order approval
    assert result.blocked is True
    assert result.failed is False
    assert result.requires_approval is False
    assert result.approval_summary is None
    assert "no eligible" in result.error.lower()
    # Finora never ran -- there is nothing to validate, and its message
    # must not misreport this as "Procura did not run first"
    assert [m.sender for m in result.messages] == [VENDRA, PROCURA]
    assert "did not run first" not in (result.error or "")


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_coordinator_blocks_cleanly_when_eligible_suppliers_have_no_quotations(mock_save, _ctx, _supabase):
    """A second way Procura can cleanly produce nothing to recommend:
    suppliers are compliant, but no quotation exists for the item. This
    must also surface as `blocked`, not `failed`."""
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description=DEMO_TASK)

    result = run_coordinator(task, client, item_code="ITEM-DOES-NOT-EXIST", quantity=1)

    assert result.blocked is True
    assert result.failed is False
    assert result.requires_approval is False
    assert "no quotations" in result.error.lower()


# -- optional NVIDIA Nemotron narration subagent -----------------------------


def test_ai_summary_llm_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PROCUREMENT_AI_SUMMARIES", raising=False)
    assert _ai_summary_llm("vendra") is None


def test_ai_summary_llm_enabled_via_env_flag(monkeypatch):
    monkeypatch.setenv("PROCUREMENT_AI_SUMMARIES", "1")
    coordinator_module._ai_summary_llm_cache.clear()  # isolate from any other test's cached client
    sentinel = object()
    with patch("app.agents.workflow.get_llm", return_value=sentinel) as mock_get_llm:
        assert _ai_summary_llm("vendra") is sentinel
        mock_get_llm.assert_called_once_with("vendra")


def test_ai_summary_llm_falls_back_to_none_if_get_llm_errors(monkeypatch):
    monkeypatch.setenv("PROCUREMENT_AI_SUMMARIES", "1")
    coordinator_module._ai_summary_llm_cache.clear()
    with patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no provider")):
        assert _ai_summary_llm("vendra") is None


def test_ai_summary_llm_caches_per_role(monkeypatch):
    monkeypatch.setenv("PROCUREMENT_AI_SUMMARIES", "1")
    coordinator_module._ai_summary_llm_cache.clear()
    with patch("app.agents.workflow.get_llm", return_value=object()) as mock_get_llm:
        first = _ai_summary_llm("vendra")
        second = _ai_summary_llm("vendra")
    assert first is second
    mock_get_llm.assert_called_once_with("vendra")


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_coordinator_passes_ai_summary_llm_through_to_each_system(mock_save, _ctx, _supabase, monkeypatch):
    """End to end: when the flag is on, each system actually receives an
    llm and its ai_summary is populated -- when it's off (the default,
    covered by every other coordinator test), no system makes any LLM call."""
    monkeypatch.setenv("PROCUREMENT_AI_SUMMARIES", "1")
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description=DEMO_TASK)

    class _FakeLLM:
        def invoke(self, messages):
            class _Reply:
                content = "narrated"

            return _Reply()

    with patch("app.agents.workflow.get_llm", return_value=_FakeLLM()):
        result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14", quantity=20)

    assert result.failed is False
    vendra_msg = next(m for m in result.messages if m.sender == "vendra")
    procura_msg = next(m for m in result.messages if m.sender == "procura")
    finora_msg = next(m for m in result.messages if m.sender == "finora")
    assert vendra_msg.payload["ai_summary"] == "narrated"
    assert procura_msg.payload["ai_summary"] == "narrated"
    assert finora_msg.payload["ai_summary"] == "narrated"
    # narration never changes the actual recommendation
    assert result.result["recommended_supplier"] == "SUP-0001"


# -- quantity derivation via Procura's NLP subagent --------------------------


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_coordinator_derives_quantity_from_description_when_not_given(mock_save, _ctx, _supabase):
    """Activates Procura's previously-unreachable interpret_purchase_request
    (mocked here to avoid a real network call) only when the caller didn't
    already supply an explicit quantity."""
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description="Find eligible suppliers for 20 laptops and compare quotations")

    with patch(
        "app.procurement.coordinator.procura_system.interpret_purchase_request",
        return_value={"quantity": 20, "item_description": "laptops"},
    ) as mock_interpret:
        result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14")

    mock_interpret.assert_called_once()
    assert result.result["recommended_total_cost"] == 19000.0  # 950 * 20, not 950 * 1


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_coordinator_never_calls_interpret_purchase_request_when_quantity_is_explicit(mock_save, _ctx, _supabase):
    """An explicit quantity always wins -- the NLP subagent is never
    invoked (and never makes a network call) when it isn't needed."""
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    task = TaskRequest(description=DEMO_TASK)

    with patch("app.procurement.coordinator.procura_system.interpret_purchase_request") as mock_interpret:
        run_coordinator(task, client, item_code="ITEM-LAPTOP-14", quantity=20)

    mock_interpret.assert_not_called()


# -- failure handling -------------------------------------------------------


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_erpnext_unavailable_is_reported_not_crashed(mock_save, _ctx, _supabase):
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    client = SeededERPNextClient()
    client.list_suppliers = MagicMock(side_effect=ERPNextUnavailable("connection refused"))
    task = TaskRequest(description=DEMO_TASK)

    import app.procurement.coordinator as coordinator_module

    with patch.object(
        coordinator_module.vendra_system,
        "run_vendor_eligibility_check",
        wraps=coordinator_module.vendra_system.run_vendor_eligibility_check,
    ) as spy:
        result = run_coordinator(task, client, item_code="ITEM-LAPTOP-14", quantity=20)

    assert result.failed is True
    assert "vendra error" in result.error
    assert "connection refused" in result.error
    # one retry, per the coordinator's retry policy -- asserted at the
    # orchestrator boundary (the thing _call_with_one_retry actually
    # retries), not via a proxy count on one internal ERPNext call, since
    # Vendra's six specialists may legitimately call list_suppliers() more
    # than once per attempt (e.g. Vendor Discovery and Vendor Risk
    # Flagging both need it).
    assert spy.call_count == 2
