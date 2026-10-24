"""Unit tests for API authentication and authorization."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.api.main import app


client = TestClient(app)


def test_health_no_auth_required():
    response = client.get("/health")
    assert response.status_code == 200


@patch.dict(os.environ, {}, clear=False)
def test_summaries_auth_not_configured_fails_closed():
    # Remove token if exists
    os.environ.pop("MEMORY_API_TOKEN", None)
    response = client.get("/agents/researcher/summaries")
    assert response.status_code == 503


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
def test_summaries_missing_token():
    response = client.get("/agents/researcher/summaries")
    assert response.status_code == 401


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
def test_summaries_invalid_token():
    response = client.get("/agents/researcher/summaries", headers={"X-API-Token": "wrong"})
    assert response.status_code == 401


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.supabase")
def test_summaries_authorized(mock_supabase):
    mock_query = MagicMock()
    mock_query.execute.return_value.data = [{"id": "sum-1"}]
    mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value = mock_query
    
    response = client.get("/agents/researcher/summaries", headers={"X-API-Token": "secret123"})
    assert response.status_code == 200


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.get_relevant_context")
def test_context_authorized(mock_get_context):
    mock_get_context.return_value = [{"id": "mem-1"}]
    
    response = client.post(
        "/agents/researcher/context",
        json={"agent_id": "researcher"},
        headers={"X-API-Token": "secret123"},
    )
    assert response.status_code == 200


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
def test_context_cross_agent_mismatch_still_checked():
    response = client.post(
        "/agents/researcher/context",
        json={"agent_id": "other"},
        headers={"X-API-Token": "secret123"},
    )
    assert response.status_code == 400
