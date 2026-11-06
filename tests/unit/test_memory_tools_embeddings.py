"""Unit tests: the per-agent save/update tools embed content on write.

Semantic search (match_agent_memories) only has something to match against
if memories carry an embedding. The generic save_memory()/update_memory()
stay embedding-free by default (see test_memory_rpc.py); only the identity-
scoped tools used by the live agent workflow opt into embed_or_none().
"""

from __future__ import annotations

from unittest.mock import patch

from app.tools.memory_tools import build_agent_tools


def _tools():
    return {t.name: t for t in build_agent_tools("researcher")}


@patch("app.tools.memory_tools.save_memory", return_value={"id": "m1"})
@patch("app.tools.memory_tools.embed_or_none", return_value=[0.1, 0.2])
def test_save_memory_entry_attaches_embedding(mock_embed, mock_save):
    _tools()["save_memory_entry"].invoke({"memory_key": "k", "content": "hello world"})

    mock_embed.assert_called_once_with("hello world")
    assert mock_save.call_args.kwargs["embedding"] == [0.1, 0.2]
    assert mock_save.call_args.kwargs["source_agent_id"] == "researcher"


@patch("app.tools.memory_tools.update_memory", return_value={"id": "m1", "version": 2})
@patch("app.tools.memory_tools.embed_or_none", return_value=[0.9])
def test_update_memory_entry_attaches_embedding(mock_embed, mock_update):
    _tools()["update_memory_entry"].invoke({"memory_key": "k", "new_content": "new text"})

    mock_embed.assert_called_once_with("new text")
    assert mock_update.call_args.kwargs["embedding"] == [0.9]


@patch("app.tools.memory_tools.save_memory", return_value={"id": "m1"})
@patch("app.tools.memory_tools.embed_or_none", return_value=None)
def test_save_memory_entry_still_works_without_a_configured_embedder(mock_embed, mock_save):
    """embed_or_none() returning None (no OPENAI_API_KEY etc.) must not block the save."""
    result = _tools()["save_memory_entry"].invoke({"memory_key": "k", "content": "hello"})

    assert result == {"id": "m1"}
    assert mock_save.call_args.kwargs["embedding"] is None
