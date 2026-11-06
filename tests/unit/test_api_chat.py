"""Unit tests for the chat message HTTP route. chat_router's own
classification logic is covered by test_procurement_chat_router.py -- these
tests are about routing the resulting intent to the right real action
(or none at all), with chat_router.route_chat_message and
task_orchestrator mocked (matching this project's existing convention).
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement import workflow
from app.procurement.chat_router import ChatDecision

client = TestClient(app)
ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}


def _sample_pr(**overrides):
    base = dict(
        pr_id="PR-TEST0001",
        requested_by="jane@example.com",
        item_description="14-inch laptop",
        quantity=10,
        estimated_budget=600_000.0,
        delivery_location="Mumbai",
        required_date="2026-12-01",
        business_justification="refresh",
        mode="demo",
        workflow_state="Waiting For Approval",
    )
    base.update(overrides)
    return workflow.PurchaseRequest(**base)


@patch.dict(os.environ, ENV, clear=False)
def test_chat_requires_auth():
    response = client.post("/procurement/chat", json={"message": "hi", "requested_by": "jane@example.com"})
    assert response.status_code == 401


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_router.route_chat_message")
def test_chat_answer_intent_returns_reply_with_no_pr(mock_route):
    mock_route.return_value = ChatDecision(intent="answer", reply="Vendra checks vendor compliance.")
    response = client.post(
        "/procurement/chat",
        json={"message": "what does vendra do", "requested_by": "jane@example.com"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Vendra checks vendor compliance."
    assert data["pr"] is None


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_router.route_chat_message")
def test_chat_unsupported_action_never_touches_a_task(mock_route):
    mock_route.return_value = ChatDecision(intent="unsupported_action", reply="I can't send real email yet.")
    with patch("app.api.procurement_routes.task_orchestrator.start_procurement_task") as mock_start:
        response = client.post(
            "/procurement/chat",
            json={"message": "send an email to the vendor", "requested_by": "jane@example.com"},
            headers={"X-API-Token": "admin-token"},
        )
        mock_start.assert_not_called()
    assert response.status_code == 200
    assert "can't send real email" in response.json()["reply"]


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.start_procurement_task")
@patch("app.api.procurement_routes.chat_router.route_chat_message")
def test_chat_new_task_intent_starts_a_real_task(mock_route, mock_start):
    # The user's own original message is always what gets parsed -- never
    # an LLM rewrite of it (ChatDecision has no "instruction" field at
    # all), so a downstream regex (budget/deadline) can never be silently
    # corrupted by a paraphrase.
    mock_route.return_value = ChatDecision(intent="new_task")
    mock_start.return_value = _sample_pr()
    response = client.post(
        "/procurement/chat",
        json={"message": "I need 10 laptops", "requested_by": "jane@example.com", "mode": "demo"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    assert response.json()["pr"]["pr_id"] == "PR-TEST0001"
    mock_start.assert_called_once_with(instruction="I need 10 laptops", requested_by="jane@example.com", mode="demo")


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.resume_procurement_task")
@patch("app.api.procurement_routes.workflow.update_purchase_request")
@patch("app.api.procurement_routes.workflow.get_purchase_request")
@patch("app.api.procurement_routes.chat_router.route_chat_message")
def test_chat_provide_info_intent_patches_item_code_and_resumes(mock_route, mock_get, mock_update, mock_resume):
    waiting_pr = _sample_pr(workflow_state="Waiting For Information", item_code=None)
    mock_get.return_value = waiting_pr
    mock_route.return_value = ChatDecision(intent="provide_info", reply="Got it.")
    mock_resume.return_value = _sample_pr(workflow_state="Waiting For Approval", item_code="DEMO-LAPTOP-14")

    response = client.post(
        "/procurement/chat",
        json={"message": "DEMO-LAPTOP-14", "requested_by": "jane@example.com", "pr_id": "PR-TEST0001"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    mock_update.assert_called_once_with("PR-TEST0001", item_code="DEMO-LAPTOP-14")
    mock_resume.assert_called_once_with("PR-TEST0001")
    assert response.json()["pr"]["item_code"] == "DEMO-LAPTOP-14"


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.workflow.get_purchase_request", return_value=None)
@patch("app.api.procurement_routes.chat_router.route_chat_message")
def test_chat_provide_info_with_no_active_task_just_answers(mock_route, mock_get):
    mock_route.return_value = ChatDecision(intent="provide_info", reply="Got it.")
    response = client.post(
        "/procurement/chat",
        json={"message": "DEMO-LAPTOP-14", "requested_by": "jane@example.com"},
        headers={"X-API-Token": "admin-token"},
    )
    assert response.status_code == 200
    assert response.json()["pr"] is None
