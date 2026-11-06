"""Unit tests for the memory write/provenance endpoints (save, update, share,
versions, ancestry, descendants): auth scoping, ownership, and error mapping.
Everything below the FastAPI layer is mocked -- no Supabase access.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.memory import errors

client = TestClient(app)

ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_RESEARCHER": "researcher-token"}
HDR = {"X-API-Token": "admin-token"}


# -- save ---------------------------------------------------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.embed_or_none", return_value=[0.1, 0.2])
@patch("app.api.main.save_memory")
def test_create_memory_success(mock_save, mock_embed):
    mock_save.return_value = {"id": "m1", "agent_id": "researcher", "version": 1}

    response = client.post(
        "/agents/researcher/memories",
        json={"memory_key": "price", "content": "The price is 55000."},
        headers=HDR,
    )

    assert response.status_code == 201
    assert response.json()["memory"]["id"] == "m1"
    assert mock_save.call_args.args[0] == "researcher"
    assert mock_save.call_args.kwargs["source_agent_id"] == "researcher"
    assert mock_save.call_args.kwargs["embedding"] == [0.1, 0.2]
    mock_embed.assert_called_once_with("The price is 55000.")


@patch.dict(os.environ, ENV, clear=False)
def test_create_memory_rejects_scoped_token_for_other_agent():
    response = client.post(
        "/agents/executor/memories",
        json={"memory_key": "k", "content": "c"},
        headers={"X-API-Token": "researcher-token"},
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
def test_create_memory_requires_auth():
    response = client.post(
        "/agents/researcher/memories", json={"memory_key": "k", "content": "c"}
    )
    assert response.status_code == 401


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.save_memory", side_effect=errors.AgentNotRegistered("nope"))
def test_create_memory_maps_agent_not_registered_to_400(mock_save):
    response = client.post(
        "/agents/researcher/memories", json={"memory_key": "k", "content": "c"}, headers=HDR
    )
    assert response.status_code == 400


# -- update ---------------------------------------------------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.embed_or_none", return_value=None)
@patch("app.api.main.update_memory")
def test_update_memory_success(mock_update, mock_embed):
    mock_update.return_value = {"id": "m2", "agent_id": "researcher", "version": 2}

    response = client.patch(
        "/agents/researcher/memories/price",
        json={"new_content": "The price is 58000."},
        headers=HDR,
    )

    assert response.status_code == 200
    assert response.json()["memory"]["version"] == 2
    assert mock_update.call_args.kwargs["embedding"] is None


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.update_memory", side_effect=errors.MemoryNotFound("no active memory"))
def test_update_memory_not_found_is_404(mock_update):
    response = client.patch(
        "/agents/researcher/memories/missing-key",
        json={"new_content": "x"},
        headers=HDR,
    )
    assert response.status_code == 404


# -- versions ---------------------------------------------------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.get_version_history", return_value=[{"version": 1}, {"version": 2}])
def test_memory_versions(mock_history):
    response = client.get("/agents/researcher/memories/price/versions", headers=HDR)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "researcher"
    assert len(data["versions"]) == 2


# -- share ------------------------------------------------------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.share_memory")
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "researcher"})
def test_share_memory_success(mock_get_memory, mock_share):
    mock_share.return_value = {"id": "m3", "agent_id": "executor"}

    response = client.post(
        "/agents/researcher/memories/m1/share",
        json={"target_agent_id": "executor"},
        headers=HDR,
    )

    assert response.status_code == 201
    assert response.json()["memory"]["agent_id"] == "executor"
    mock_share.assert_called_once_with("m1", "executor", memory_key=None, max_hops=None, client=mock_share.call_args.kwargs["client"])


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.share_memory")
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "executor"})
def test_share_memory_rejects_non_owner(mock_get_memory, mock_share):
    """researcher tries to share a memory that actually belongs to executor."""
    response = client.post(
        "/agents/researcher/memories/m1/share",
        json={"target_agent_id": "validator"},
        headers=HDR,
    )
    assert response.status_code == 403
    mock_share.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.get_memory", return_value=None)
def test_share_memory_missing_source_is_404(mock_get_memory):
    response = client.post(
        "/agents/researcher/memories/does-not-exist/share",
        json={"target_agent_id": "executor"},
        headers=HDR,
    )
    assert response.status_code == 404


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.share_memory", side_effect=errors.ShareNotAllowed("denied by policy"))
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "researcher"})
def test_share_memory_policy_denial_is_403(mock_get_memory, mock_share):
    response = client.post(
        "/agents/researcher/memories/m1/share",
        json={"target_agent_id": "supervisor"},
        headers=HDR,
    )
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.share_memory", side_effect=errors.HopLimitExceeded("too many hops"))
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "researcher"})
def test_share_memory_hop_limit_is_403(mock_get_memory, mock_share):
    response = client.post(
        "/agents/researcher/memories/m1/share",
        json={"target_agent_id": "executor"},
        headers=HDR,
    )
    assert response.status_code == 403


# -- ancestry / descendants ---------------------------------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.get_ancestry", return_value=[{"depth": 0}, {"depth": 1}])
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "researcher"})
def test_memory_ancestry_success(mock_get_memory, mock_ancestry):
    response = client.get("/agents/researcher/memories/m1/ancestry", headers=HDR)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "executor"})
def test_memory_ancestry_rejects_non_owner(mock_get_memory):
    response = client.get("/agents/researcher/memories/m1/ancestry", headers=HDR)
    assert response.status_code == 403


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.get_descendants", return_value=[])
@patch("app.api.main.get_memory", return_value={"id": "m1", "agent_id": "researcher"})
def test_memory_descendants_success(mock_get_memory, mock_descendants):
    response = client.get("/agents/researcher/memories/m1/descendants", headers=HDR)
    assert response.status_code == 200
    assert response.json()["items"] == []


@patch.dict(os.environ, ENV, clear=False)
def test_memory_descendants_requires_auth():
    response = client.get("/agents/researcher/memories/m1/descendants")
    assert response.status_code == 401
