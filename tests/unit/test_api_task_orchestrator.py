"""Unit tests for the one-instruction task entry point's HTTP layer.
app.procurement.task_orchestrator itself (real Supabase + real
coordinator run) is covered by
tests/integration/test_dbwrite_task_orchestrator.py -- these tests are
about routing, auth gating, and error-code mapping.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement import workflow

client = TestClient(app)
ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}


def _sample_pr(**overrides):
    defaults = dict(
        pr_id="PR-TASK0001", requested_by="jane@example.com", item_description="laptops", quantity=10,
        estimated_budget=600000.0, delivery_location="Mumbai office", required_date="2026-12-01",
        business_justification="Purchase 10 laptops...", status="Pending Approval",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        mode="demo", item_code="DEMO-LAPTOP-14", workflow_state="Waiting For Approval", blockers=[],
        coordinator_task_id="task-abc", coordinator_result={"requires_approval": True}, idempotency_key=None,
    )
    defaults.update(overrides)
    return workflow.PurchaseRequest(**defaults)


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.start_procurement_task")
def test_start_task_requires_admin_token(mock_start):
    response = client.post(
        "/procurement/tasks/start",
        json={"instruction": "Purchase 10 laptops", "requested_by": "jane@example.com"},
        headers={"X-API-Token": "vendra-token"},
    )
    assert response.status_code == 403
    mock_start.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.start_procurement_task")
def test_start_task_success_returns_the_full_task_state(mock_start):
    mock_start.return_value = _sample_pr()
    response = client.post(
        "/procurement/tasks/start",
        json={
            "instruction": "Purchase 10 laptops with 16 GB RAM, within ₹60 lakh, delivered to our Mumbai office within 30 days.",
            "requested_by": "jane@example.com", "mode": "demo",
        },
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["workflow_state"] == "Waiting For Approval"
    assert data["mode"] == "demo"
    assert data["coordinator_result"]["requires_approval"] is True


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.resume_procurement_task")
def test_resume_task_requires_auth(mock_resume):
    response = client.post("/procurement/tasks/PR-TASK0001/resume")
    assert response.status_code == 401
    mock_resume.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.resume_procurement_task")
def test_resume_missing_task_is_404(mock_resume):
    mock_resume.side_effect = workflow.PurchaseRequestError("no such task")
    response = client.post("/procurement/tasks/PR-NOPE/resume", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 404


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.update_purchase_request")
def test_patch_purchase_request_fills_in_missing_fields(mock_update):
    mock_update.return_value = _sample_pr(workflow_state="Waiting For Information")
    response = client.patch(
        "/procurement/requests/PR-TASK0001",
        json={"item_code": "DEMO-LAPTOP-14"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    mock_update.assert_called_once_with("PR-TASK0001", item_code="DEMO-LAPTOP-14")
