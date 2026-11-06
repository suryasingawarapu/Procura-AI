"""Unit tests for the Purchase Request / approval HTTP layer. The
workflow module itself (real Supabase read/write, versioning, approval
limits) is covered by tests/integration/test_dbwrite_workflow.py -- these
tests are about routing, auth gating, and error-code mapping, with
app.procurement.workflow mocked (matching this project's existing
convention in test_api_procurement.py).
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement import workflow

client = TestClient(app)
ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}


@patch.dict(os.environ, ENV, clear=False)
def test_approval_limits_route_translates_infinity_to_null():
    response = client.get("/procurement/approval-limits", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 200
    limits = response.json()["limits"]
    assert limits["manager"] == 500000.0
    assert limits["cfo"] is None  # float("inf") has no JSON representation -- must come through as null


def _sample_pr(status="Draft"):
    return workflow.PurchaseRequest(
        pr_id="PR-TEST0001",
        requested_by="jane@example.com",
        item_description="14-inch laptop",
        quantity=100,
        estimated_budget=1_000_000.0,
        delivery_location="HQ",
        required_date="2026-12-01",
        business_justification="refresh",
        status=status,
    )


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.create_purchase_request")
def test_create_purchase_request_requires_auth(mock_create):
    response = client.post("/procurement/requests", json={
        "requested_by": "jane@example.com", "item_description": "laptop", "quantity": 10,
        "estimated_budget": 100000, "delivery_location": "HQ", "required_date": "2026-12-01",
        "business_justification": "refresh",
    })
    assert response.status_code == 401
    mock_create.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.create_purchase_request")
def test_create_purchase_request_success(mock_create):
    mock_create.return_value = _sample_pr()
    response = client.post(
        "/procurement/requests",
        json={
            "requested_by": "jane@example.com", "item_description": "laptop", "quantity": 100,
            "estimated_budget": 1000000, "delivery_location": "HQ", "required_date": "2026-12-01",
            "business_justification": "refresh",
        },
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 201
    assert response.json()["pr_id"] == "PR-TEST0001"
    assert response.json()["status"] == "Draft"


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.get_purchase_request", return_value=None)
def test_get_missing_purchase_request_is_404(mock_get):
    response = client.get("/procurement/requests/PR-NOPE", headers={"X-API-Token": "admin-token"})
    assert response.status_code == 404


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.transition_purchase_request")
def test_invalid_transition_is_400_not_500(mock_transition):
    mock_transition.side_effect = workflow.PurchaseRequestError("invalid transition")
    response = client.post(
        "/procurement/requests/PR-TEST0001/transition",
        json={"new_status": "Approved"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 400


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.record_approval_decision")
def test_approval_requires_admin_token_not_a_scoped_one(mock_decide):
    response = client.post(
        "/procurement/requests/PR-TEST0001/approve",
        json={"approver_id": "mgr-1", "approver_role": "manager", "decision": "Approved"},
        headers={"X-API-Token": "vendra-token"},
    )
    assert response.status_code == 403
    mock_decide.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.record_approval_decision")
def test_stale_approval_is_409_not_silently_approved(mock_decide):
    mock_decide.side_effect = workflow.StaleApprovalError("request changed since review")
    response = client.post(
        "/procurement/requests/PR-TEST0001/approve",
        json={"approver_id": "mgr-1", "approver_role": "manager", "decision": "Approved", "reviewed_updated_at": "stale"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 409


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.record_approval_decision")
def test_approval_over_the_limit_is_403_not_silently_approved(mock_decide):
    mock_decide.side_effect = workflow.ApprovalLimitExceeded("over the manager's limit")
    response = client.post(
        "/procurement/requests/PR-TEST0001/approve",
        json={"approver_id": "mgr-1", "approver_role": "manager", "decision": "Approved"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 403
    assert "limit" in response.json()["detail"]


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.submit_approved_purchase_order")
@patch("app.api.procurement_routes.workflow.record_approval_decision")
def test_approval_success(mock_decide, _mock_submit):
    mock_decide.return_value = workflow.ApprovalDecision(
        pr_id="PR-TEST0001", approver_id="mgr-1", approver_role="manager",
        decision="Approved", comment=None, evaluation_version=None,
    )
    response = client.post(
        "/procurement/requests/PR-TEST0001/approve",
        json={"approver_id": "mgr-1", "approver_role": "manager", "decision": "Approved"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "Approved"
