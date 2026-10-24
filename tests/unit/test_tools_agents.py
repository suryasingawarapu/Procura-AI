"""Unit tests for LangChain tools and LangGraph workflow."""

from __future__ import annotations

from unittest.mock import patch

from app.agents.workflow import MAX_RETRIES, ROLES, build_workflow, route_task, should_retry
from app.tools import memory_tools


def test_roles_defined():
    assert "supervisor" in ROLES
    assert "researcher" in ROLES
    assert len(ROLES) == 5


def test_route_to_planner():
    state = {"task": "create a plan for deployment"}
    assert route_task(state) == "planner"


def test_route_to_researcher():
    state = {"task": "research the latest findings"}
    assert route_task(state) == "researcher"


def test_route_to_executor():
    state = {"task": "implement the solution"}
    assert route_task(state) == "executor"


def test_route_to_validator():
    state = {"task": "verify the results"}
    assert route_task(state) == "validator"


def test_route_to_supervisor_default():
    state = {"task": "do something unclear"}
    assert route_task(state) == "supervisor"


@patch("app.tools.memory_tools.get_relevant_context")
def test_get_context_tool(mock_get_context):
    mock_get_context.return_value = [{"id": "1"}]
    result = memory_tools.get_context.invoke({"agent_id": "researcher", "limit": 5})
    assert len(result) == 1


@patch("app.tools.memory_tools.save_memory")
def test_save_memory_tool(mock_save):
    mock_save.return_value = {"id": "1", "version": 1}
    result = memory_tools.save_agent_memory.invoke({"agent_id": "r", "memory_key": "k", "content": "c"})
    assert result["version"] == 1


def test_workflow_compiles():
    app = build_workflow()
    result = app.invoke({"task": "research topic"})
    assert result["current_agent"] in ROLES


def test_validation_retry_bounded():
    app = build_workflow()
    result = app.invoke({"task": "check invalid result"})
    # Should stop after max retries
    assert result.get("retries", 0) <= MAX_RETRIES


def test_failure_handling():
    app = build_workflow()
    result = app.invoke({"task": "fail execution"})
    assert result.get("failed") is True or "failed" in str(result.get("result", "")).lower()


def test_context_passed_between_agents():
    app = build_workflow()
    result = app.invoke({"task": "plan something", "context": []})
    assert isinstance(result.get("context", []), list)
