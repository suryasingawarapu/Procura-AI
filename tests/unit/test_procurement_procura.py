"""Unit tests for Procura. ERPNext side is the real SeededERPNextClient;
memory writes are mocked (real Supabase otherwise); the LLM-based purchase
request interpretation path is tested separately from its regex fallback.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.procurement.mcp.client import ERPNextUnavailable, SeededERPNextClient
from app.procurement.systems import procura


@patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no LLM provider configured"))
def test_interpret_purchase_request_regex_fallback_when_no_llm(mock_get_llm):
    """`llm=None` makes the function try to self-resolve one via get_llm()
    -- in an environment where NVIDIA_API_KEY *is* configured, that would
    silently succeed and make this a real, paid network call instead of
    testing the regex fallback at all. Forcing get_llm() to fail is what
    actually exercises the fallback path this test is named for."""
    result = procura.interpret_purchase_request("Find eligible suppliers for 20 laptops", llm=None)
    assert result["quantity"] == 20
    assert result["item_description"] == "laptop"  # the regex's own extraction, not an LLM's
    mock_get_llm.assert_called_once()


def test_interpret_purchase_request_uses_injected_llm_when_available():
    class FakeLLM:
        def invoke(self, messages):
            class R:
                content = '{"quantity": 20, "item_description": "business laptops"}'

            return R()

    result = procura.interpret_purchase_request("20 laptops please", llm=FakeLLM())
    assert result == {"quantity": 20, "item_description": "business laptops"}


def test_interpret_purchase_request_falls_back_if_llm_reply_unparseable():
    class BadLLM:
        def invoke(self, messages):
            class R:
                content = "not json at all"

            return R()

    result = procura.interpret_purchase_request("Find eligible suppliers for 20 laptops", llm=BadLLM())
    assert result["quantity"] == 20  # regex fallback still kicks in


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_recommends_cheapest_total_cost(mock_save, mock_enrich):
    client = SeededERPNextClient()

    msg = procura.run_procurement_task(
        task_id="task-1",
        correlation_id="corr-1",
        client=client,
        item_code="ITEM-LAPTOP-14",
        quantity=20,
        eligible_supplier_names=["SUP-0001", "SUP-0002"],
    )

    assert msg.status == "ok"
    assert msg.payload["recommended_supplier"] == "SUP-0001"  # 950*20=19000 < 985*20=19700
    assert msg.payload["recommended_total_cost"] == 19000.0
    assert mock_save.call_count == 1
    assert mock_enrich.call_args.kwargs["task_id"] == "task-1"


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_reports_error_status_with_no_quotations(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = procura.run_procurement_task(
        task_id="task-1",
        correlation_id="corr-1",
        client=client,
        item_code="ITEM-DOES-NOT-EXIST",
        quantity=1,
        eligible_supplier_names=["SUP-0001"],
    )
    assert msg.status == "error"
    assert "no quotations" in msg.error


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_blocks_cleanly_with_zero_eligible_suppliers(mock_save, mock_enrich):
    """The actual safety-gap scenario: Vendra found nobody compliant.
    Procurement must stop before any write and never recommend a supplier."""
    client = SeededERPNextClient()

    msg = procura.run_procurement_task(
        task_id="task-1",
        correlation_id="corr-1",
        client=client,
        item_code="ITEM-LAPTOP-14",
        quantity=20,
        eligible_supplier_names=[],
    )

    assert msg.status == "error"
    assert "no eligible" in msg.error.lower()
    assert msg.payload["recommended_supplier"] is None
    assert msg.payload["recommended_total_cost"] is None
    assert msg.payload["quotations_considered"] == 0
    assert msg.payload["rfq"] is None
    # no RFQ was created -- the write was never attempted, not just discarded
    assert client._data["rfqs"] == []


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_never_recommends_an_ineligible_supplier_as_fallback(mock_save, mock_enrich):
    """Both seeded suppliers have quotations for ITEM-LAPTOP-14, but only
    SUP-0002 is passed as eligible here -- the comparison must be confined
    to SUP-0002's quote, never silently fall back to comparing everyone's."""
    client = SeededERPNextClient()

    msg = procura.run_procurement_task(
        task_id="task-1",
        correlation_id="corr-1",
        client=client,
        item_code="ITEM-LAPTOP-14",
        quantity=20,
        eligible_supplier_names=["SUP-0002"],
    )

    assert msg.status == "ok"
    assert msg.payload["quotations_considered"] == 1
    assert msg.payload["recommended_supplier"] == "SUP-0002"  # not SUP-0001, despite its lower price


class _FakeLLM:
    def invoke(self, messages):
        class _Reply:
            content = "SUP-0001 offers the lowest total cost for this item."

        return _Reply()


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_without_llm_has_no_ai_summary(mock_save, mock_enrich):
    """Default behavior (no llm passed) -- no narration, no network call."""
    client = SeededERPNextClient()
    msg = procura.run_procurement_task(
        task_id="task-1", correlation_id="corr-1", client=client,
        item_code="ITEM-LAPTOP-14", quantity=20, eligible_supplier_names=["SUP-0001", "SUP-0002"],
    )
    assert msg.payload["ai_summary"] is None
    assert msg.payload["recommended_supplier"] == "SUP-0001"


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_narrates_without_changing_the_recommendation(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = procura.run_procurement_task(
        task_id="task-1", correlation_id="corr-1", client=client,
        item_code="ITEM-LAPTOP-14", quantity=20, eligible_supplier_names=["SUP-0001", "SUP-0002"],
        llm=_FakeLLM(),
    )
    assert msg.payload["ai_summary"] == "SUP-0001 offers the lowest total cost for this item."
    assert msg.payload["recommended_supplier"] == "SUP-0001"  # unaffected by narration


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_exposes_its_six_automatic_specialists(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = procura.run_procurement_task(
        task_id="task-1", correlation_id="corr-1", client=client,
        item_code="ITEM-LAPTOP-14", quantity=20, eligible_supplier_names=["SUP-0001", "SUP-0002"],
    )
    specialists = msg.payload["specialists"]
    assert set(specialists.keys()) == {
        "rfq_management",
        "quotation_processing",
        "quotation_comparison",
        "supplier_matching",
        "delivery_schedule_estimation",
        "quotation_history",
    }
    # the history step reads memory, which demo/test runs never do
    assert specialists["quotation_history"]["status"] == "skipped"
    assert specialists["supplier_matching"]["data"]["matched_suppliers"] == ["SUP-0001", "SUP-0002"]


@patch("app.procurement.systems.procura.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_procurement_task_raises_on_a_core_specialist_backend_failure(mock_save, mock_enrich):
    """RFQ Management/Quotation Processing are load-bearing: a real
    backend failure there (including the write gate itself) must still
    surface to the coordinator as a failure, not be silently absorbed."""
    client = SeededERPNextClient()
    client.list_supplier_quotations = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("backend down"))

    with pytest.raises(ERPNextUnavailable):
        procura.run_procurement_task(
            task_id="task-1", correlation_id="corr-1", client=client,
            item_code="ITEM-LAPTOP-14", quantity=20, eligible_supplier_names=["SUP-0001"],
        )


def test_submit_purchase_order_requires_approval():
    client = SeededERPNextClient()
    with pytest.raises(PermissionError):
        procura.submit_purchase_order(client, supplier="SUP-0001", item_code="ITEM-LAPTOP-14", quantity=20, approved=False)

    po = procura.submit_purchase_order(
        client, supplier="SUP-0001", item_code="ITEM-LAPTOP-14", quantity=20, approved=True
    )
    assert po["supplier"] == "SUP-0001"
