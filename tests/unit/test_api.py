"""Unit tests for FastAPI memory service."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.api.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch.dict("os.environ", {"MEMORY_API_TOKEN": "test"})
@patch("app.api.main.supabase")
def test_get_summaries(mock_supabase):
    mock_query = MagicMock()
    mock_query.execute.return_value.data = [{"id": "sum-1", "agent_id": "researcher"}]
    mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value = mock_query
    
    response = client.get("/agents/researcher/summaries", headers={"X-API-Token": "test"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "researcher"
    assert len(data["summaries"]) == 1


@patch.dict("os.environ", {"MEMORY_API_TOKEN": "test"})
@patch("app.api.main.get_relevant_context")
def test_get_context(mock_get_context):
    mock_get_context.return_value = [{"id": "mem-1", "content": "test"}]
    
    response = client.post("/agents/researcher/context", json={"agent_id": "researcher"}, headers={"X-API-Token": "test"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "researcher"
    assert len(data["items"]) == 1


@patch.dict("os.environ", {"MEMORY_API_TOKEN": "test"})
def test_get_context_agent_mismatch():
    response = client.post("/agents/researcher/context", json={"agent_id": "other"}, headers={"X-API-Token": "test"})
    
    assert response.status_code == 400
