"""Unit tests for per-agent scoped API tokens (authorize_agent / authorize_admin).

Before this, any authenticated token could read or write any agent's
resources and could trigger the whole workflow. These tests pin the fix:
a token scoped to one agent via MEMORY_API_TOKEN_<AGENT> must not reach
another agent's resources or the admin-only workflow endpoint.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)

ENV = {
    "MEMORY_API_TOKEN": "admin-token",
    "MEMORY_API_TOKEN_RESEARCHER": "researcher-token",
    "MEMORY_API_TOKEN_EXECUTOR": "executor-token",
}


def _mock_summaries(mock_supabase):
    mock_query = MagicMock()
    mock_query.execute.return_value.data = []
    mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value = mock_query


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.supabase")
def test_scoped_token_authorized_for_own_agent(mock_supabase):
    _mock_summaries(mock_supabase)
    response = client.get(
        "/agents/researcher/summaries", headers={"X-API-Token": "researcher-token"}
    )
    assert response.status_code == 200


@patch.dict(os.environ, ENV, clear=False)
def test_scoped_token_forbidden_for_other_agent():
    response = client.get(
        "/agents/executor/summaries", headers={"X-API-Token": "researcher-token"}
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.get_relevant_context", return_value=[])
def test_scoped_token_forbidden_on_context_endpoint_for_other_agent(_mock):
    response = client.post(
        "/agents/validator/context",
        json={"agent_id": "validator"},
        headers={"X-API-Token": "researcher-token"},
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.supabase")
def test_admin_token_still_authorized_for_any_agent(mock_supabase):
    _mock_summaries(mock_supabase)
    for agent_id in ("researcher", "executor", "validator"):
        response = client.get(
            f"/agents/{agent_id}/summaries", headers={"X-API-Token": "admin-token"}
        )
        assert response.status_code == 200


@patch.dict(os.environ, ENV, clear=False)
def test_unknown_token_still_rejected():
    response = client.get(
        "/agents/researcher/summaries", headers={"X-API-Token": "not-a-real-token"}
    )
    assert response.status_code == 401


@patch.dict(
    os.environ,
    {"MEMORY_API_TOKEN_RESEARCHER": "researcher-token"},
    clear=False,
)
def test_scoped_only_setup_without_admin_token_still_authenticates():
    os.environ.pop("MEMORY_API_TOKEN", None)
    response = client.get(
        "/agents/researcher/summaries", headers={"X-API-Token": "no-such-token"}
    )
    assert response.status_code == 401  # authenticated-but-wrong, not "not configured"


# -- /workflow/run is admin-only ---------------------------------------------


class _FakeWorkflow:
    def invoke(self, state):
        return {"result": "ok", "history": []}


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.build_workflow", return_value=_FakeWorkflow())
def test_workflow_run_rejects_scoped_agent_token(_mock_build):
    response = client.post(
        "/workflow/run", json={"task": "t"}, headers={"X-API-Token": "researcher-token"}
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.build_workflow", return_value=_FakeWorkflow())
def test_workflow_run_allows_admin_token(_mock_build):
    response = client.post(
        "/workflow/run", json={"task": "t"}, headers={"X-API-Token": "admin-token"}
    )
    assert response.status_code == 200
