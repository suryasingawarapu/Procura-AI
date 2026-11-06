"""Unit tests for LangChain tools and LangGraph workflow."""

from __future__ import annotations

from unittest.mock import patch

from app.agents.workflow import ROLES
from app.tools import memory_tools


def test_roles_defined():
    assert "supervisor" in ROLES
    assert "researcher" in ROLES
    assert len(ROLES) == 5


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
