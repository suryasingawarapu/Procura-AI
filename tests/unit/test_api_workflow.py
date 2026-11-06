"""Unit tests for the /workflow/run endpoint: auth, success, and failure paths."""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


class FakeWorkflow:
    """Stand-in for the compiled LangGraph app returned by build_workflow()."""

    def __init__(self, result=None, exc=None):
        self.result = result or {}
        self.exc = exc
        self.received = None

    def invoke(self, state):
        self.received = state
        if self.exc is not None:
            raise self.exc
        return self.result


# -- auth ---------------------------------------------------------------


@patch.dict(os.environ, {}, clear=False)
def test_workflow_run_auth_not_configured_fails_closed():
    # Remove every configured token (admin + per-agent scoped, the latter
    # now a real part of this project's .env) so "not configured" is
    # genuinely simulated.
    for key in ("MEMORY_API_TOKEN", "MEMORY_API_TOKEN_VENDRA", "MEMORY_API_TOKEN_PROCURA", "MEMORY_API_TOKEN_FINORA"):
        os.environ.pop(key, None)
    response = client.post("/workflow/run", json={"task": "do something"})
    assert response.status_code == 503


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
def test_workflow_run_missing_token():
    response = client.post("/workflow/run", json={"task": "do something"})
    assert response.status_code == 401


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
def test_workflow_run_invalid_token():
    response = client.post(
        "/workflow/run", json={"task": "do something"}, headers={"X-API-Token": "wrong"}
    )
    assert response.status_code == 401


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
def test_workflow_run_rejects_empty_task():
    response = client.post(
        "/workflow/run", json={"task": ""}, headers={"X-API-Token": "secret123"}
    )
    assert response.status_code == 422


# -- success --------------------------------------------------------------


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.trace_workflow_run")
@patch("app.api.main.build_workflow")
def test_workflow_run_success(mock_build, mock_trace):
    fake = FakeWorkflow(
        result={
            "result": "done!",
            "validation_passed": True,
            "failed": False,
            "step": 3,
            "history": [
                {"agent": "planner", "output": "1. do it"},
                {"agent": "executor", "output": "did it"},
            ],
        }
    )
    mock_build.return_value = fake

    response = client.post(
        "/workflow/run", json={"task": "ship the feature"}, headers={"X-API-Token": "secret123"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["result"] == "done!"
    assert data["validation_passed"] is True
    assert data["failed"] is False
    assert [h["agent"] for h in data["history"]] == ["planner", "executor"]
    assert fake.received == {"task": "ship the feature"}
    mock_trace.assert_called_once_with("ship the feature", fake.result)


# -- failure paths ----------------------------------------------------------


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.trace_workflow_run")
@patch("app.api.main.build_workflow")
def test_workflow_run_agent_failure_is_200_with_failed_flag(mock_build, mock_trace):
    """A failure produced *by* the workflow (e.g. an LLM error) is not a server error."""
    mock_build.return_value = FakeWorkflow(
        result={"failed": True, "error": "executor error: llm down", "history": []}
    )

    response = client.post(
        "/workflow/run", json={"task": "break things"}, headers={"X-API-Token": "secret123"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["failed"] is True
    assert "llm down" in data["error"]
    mock_trace.assert_called_once()


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.trace_workflow_run")
@patch("app.api.main.build_workflow")
def test_workflow_run_invoke_exception_is_500(mock_build, mock_trace):
    """A failure to even run the graph (not a workflow-level failure) is a 500."""
    mock_build.return_value = FakeWorkflow(exc=RuntimeError("graph exploded"))

    response = client.post(
        "/workflow/run", json={"task": "task"}, headers={"X-API-Token": "secret123"}
    )

    assert response.status_code == 500
    assert "graph exploded" in response.json()["detail"]
    mock_trace.assert_not_called()


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.build_workflow")
def test_workflow_run_build_exception_is_500(mock_build):
    mock_build.side_effect = RuntimeError("no model configured")

    response = client.post(
        "/workflow/run", json={"task": "task"}, headers={"X-API-Token": "secret123"}
    )

    assert response.status_code == 500


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "secret123"})
@patch("app.api.main.trace_workflow_run", side_effect=RuntimeError("langfuse down"))
@patch("app.api.main.build_workflow")
def test_workflow_run_tracing_failure_does_not_break_response(mock_build, mock_trace):
    """Even if trace_workflow_run itself misbehaves, the endpoint still returns the result."""
    mock_build.return_value = FakeWorkflow(result={"result": "ok", "history": []})

    response = client.post(
        "/workflow/run", json={"task": "task"}, headers={"X-API-Token": "secret123"}
    )

    assert response.status_code == 200
    assert response.json()["result"] == "ok"
