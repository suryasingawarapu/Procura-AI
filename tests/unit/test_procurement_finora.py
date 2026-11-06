"""Unit tests for Finora. ERPNext side is the real SeededERPNextClient;
memory writes are mocked (real Supabase otherwise)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.procurement.mcp.client import ERPNextUnavailable, SeededERPNextClient
from app.procurement.systems import finora


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_financial_validation_within_budget(mock_save, mock_enrich):
    client = SeededERPNextClient()  # default budget is 25000.0
    msg = finora.run_financial_validation(
        task_id="task-1", correlation_id="corr-1", client=client, estimated_total=19000.0, supplier="SUP-0001"
    )
    assert msg.status == "ok"
    assert msg.payload["budget_check"]["within_budget"] is True
    assert mock_enrich.call_args.kwargs["verification_status"] == "verified"


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_financial_validation_over_budget(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = finora.run_financial_validation(
        task_id="task-1", correlation_id="corr-1", client=client, estimated_total=99000.0, supplier="SUP-0001"
    )
    assert msg.payload["budget_check"]["within_budget"] is False
    assert msg.payload["budget_check"]["shortfall"] > 0


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_financial_validation_missing_budget_is_unverified_not_approved(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = finora.run_financial_validation(
        task_id="task-1",
        correlation_id="corr-1",
        client=client,
        estimated_total=100.0,
        supplier="SUP-0001",
        cost_center="no-such-cost-center",
    )
    assert msg.payload["budget_check"]["within_budget"] is None
    assert "UNKNOWN" in msg.payload["budget_check"]["note"] or msg.payload["budget_check"]["within_budget"] is None
    assert mock_enrich.call_args.kwargs["verification_status"] == "unverified"


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_financial_validation_reports_no_invoices_honestly(mock_save, mock_enrich):
    client = SeededERPNextClient()  # seed has an empty invoices list
    msg = finora.run_financial_validation(
        task_id="task-1", correlation_id="corr-1", client=client, estimated_total=100.0, supplier="SUP-0001"
    )
    assert msg.payload["invoices_matched"] == []


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_financial_validation_without_llm_has_no_ai_summary(mock_save, mock_enrich):
    """Default behavior (no llm passed) -- no narration, no network call."""
    client = SeededERPNextClient()
    msg = finora.run_financial_validation(
        task_id="task-1", correlation_id="corr-1", client=client, estimated_total=19000.0, supplier="SUP-0001"
    )
    assert msg.payload["ai_summary"] is None
    assert msg.payload["budget_check"]["within_budget"] is True


class _FakeLLM:
    def invoke(self, messages):
        class _Reply:
            content = "This purchase is within the available budget."

        return _Reply()


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_financial_validation_narrates_without_changing_the_verdict(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = finora.run_financial_validation(
        task_id="task-1", correlation_id="corr-1", client=client, estimated_total=19000.0, supplier="SUP-0001",
        llm=_FakeLLM(),
    )
    assert msg.payload["ai_summary"] == "This purchase is within the available budget."
    assert msg.payload["budget_check"]["within_budget"] is True  # unaffected by narration


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_financial_validation_exposes_all_six_specialists(mock_save, mock_enrich):
    client = SeededERPNextClient()
    msg = finora.run_financial_validation(
        task_id="task-1", correlation_id="corr-1", client=client, estimated_total=19000.0, supplier="SUP-0001"
    )
    specialists = msg.payload["specialists"]
    assert set(specialists.keys()) == {
        "budget_verification",
        "cost_analysis",
        "invoice_matching",
        "payment_terms_review",
        "approval_routing",
        "financial_records",
    }
    assert specialists["approval_routing"]["data"]["requires_human_approval"] is True


@patch("app.procurement.systems.finora.enrich_memory")
@patch("app.tools.memory_tools.save_memory", return_value={"id": "mem-1"})
def test_run_financial_validation_raises_on_a_core_specialist_backend_failure(mock_save, mock_enrich):
    client = SeededERPNextClient()
    client.get_budget = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("backend down"))

    with pytest.raises(ERPNextUnavailable):
        finora.run_financial_validation(
            task_id="task-1", correlation_id="corr-1", client=client, estimated_total=100.0, supplier="SUP-0001"
        )
