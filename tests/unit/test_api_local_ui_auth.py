"""The bundled browser UI calls the API without holding a token; the server
uses its own admin token on the UI's behalf, but only when UI_LOCAL_AUTH is
on AND the request genuinely comes from this machine."""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app

ROUTE = "/procurement/approval-limits"
ADMIN_ONLY_ROUTE = "/procurement/requests/PR-NOPE"
ENV_ON = {"MEMORY_API_TOKEN": "admin-token", "UI_LOCAL_AUTH": "1"}


def _client(peer: str = "127.0.0.1", base_url: str = "http://127.0.0.1:8000") -> TestClient:
    return TestClient(app, base_url=base_url, client=(peer, 50000))


@patch.dict(os.environ, ENV_ON, clear=False)
def test_local_ui_request_without_token_is_authorized():
    assert _client().get(ROUTE).status_code == 200


@patch.dict(os.environ, ENV_ON, clear=False)
def test_local_ui_request_counts_as_admin():
    with patch("app.api.procurement_routes.workflow.get_purchase_request", return_value=None):
        response = _client().get(ADMIN_ONLY_ROUTE)
    assert response.status_code == 404  # got past authorize_admin; the PR just doesn't exist


@patch.dict(os.environ, ENV_ON, clear=False)
def test_localhost_host_header_is_accepted():
    assert _client(base_url="http://localhost:8000").get(ROUTE).status_code == 200


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "admin-token", "UI_LOCAL_AUTH": "0"}, clear=False)
def test_disabled_flag_still_requires_a_token():
    assert _client().get(ROUTE).status_code == 401


@patch.dict(os.environ, ENV_ON, clear=False)
def test_non_loopback_peer_is_rejected():
    assert _client(peer="192.168.1.20").get(ROUTE).status_code == 401


@patch.dict(os.environ, ENV_ON, clear=False)
def test_dns_rebinding_host_is_rejected():
    assert _client(base_url="http://evil.example:8000").get(ROUTE).status_code == 401


@patch.dict(os.environ, ENV_ON, clear=False)
def test_foreign_origin_is_rejected():
    response = _client().get(ROUTE, headers={"Origin": "https://evil.example"})
    assert response.status_code == 401


@patch.dict(os.environ, ENV_ON, clear=False)
def test_wrong_explicit_token_is_still_rejected_locally():
    assert _client().get(ROUTE, headers={"X-API-Token": "wrong"}).status_code == 401


@patch.dict(os.environ, {"MEMORY_API_TOKEN": "", "UI_LOCAL_AUTH": "1"}, clear=False)
def test_no_admin_token_configured_fails_closed():
    # 503 if no token of any kind is configured, 401 if only scoped ones are.
    assert _client().get(ROUTE).status_code in (401, 503)
