"""API authentication and authorization."""

from __future__ import annotations

import os
from typing import Dict, Optional

from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

# Kept in sync by hand with AGENT_IDS in app/tools/memory_tools.py and ROLES in
# app/agents/workflow.py -- this module must not import from the agent layer.
# Also covers the procurement systems' identities (app.procurement.identities
# .SYSTEM_IDS) for the same reason: this module stays import-independent of
# both.
AGENT_IDS = (
    "supervisor", "planner", "researcher", "executor", "validator",
    "vendra", "procura", "finora", "coordinator",
)


def _admin_token() -> Optional[str]:
    return os.getenv("MEMORY_API_TOKEN")


def _scoped_tokens() -> Dict[str, str]:
    """Map each configured per-agent token to the agent it is scoped to.

    ``MEMORY_API_TOKEN_<AGENT>`` (e.g. ``MEMORY_API_TOKEN_RESEARCHER``) grants
    access to that agent's own resources only. The admin token,
    ``MEMORY_API_TOKEN``, still grants access to every agent, so an existing
    single-token deployment keeps working unchanged.
    """
    tokens: Dict[str, str] = {}
    for agent_id in AGENT_IDS:
        value = os.getenv(f"MEMORY_API_TOKEN_{agent_id.upper()}")
        if value:
            tokens[value] = agent_id
    return tokens


_LOOPBACK_CLIENTS = {"127.0.0.1", "::1"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _hostname(value: str) -> Optional[str]:
    try:
        return urlsplit(value if "//" in value else f"//{value}").hostname
    except ValueError:
        return None


def _is_trusted_local_ui(request: Request) -> bool:
    """True when the bundled browser UI may call the API without holding a
    token itself, so the token stays server-side.

    Off unless UI_LOCAL_AUTH is set, and then only for a request that really
    comes from this machine: the TCP peer is loopback, the Host header names
    loopback (blocks DNS rebinding, where an outside site resolves its own
    name to 127.0.0.1), and any Origin header is loopback too. Never enable
    this behind a reverse proxy on the same host -- the proxy's own loopback
    connection would make every outside request look local.
    """
    if os.getenv("UI_LOCAL_AUTH", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if not request.client or request.client.host not in _LOOPBACK_CLIENTS:
        return False
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK_HOSTS:
        return False
    origin = request.headers.get("origin")
    if origin and _hostname(origin) not in _LOOPBACK_HOSTS:
        return False
    return True


def require_auth(request: Request, x_api_token: Optional[str] = Header(default=None)) -> str:
    """Require API token authentication. Fail-closed if not configured."""
    admin_token = _admin_token()
    scoped_tokens = _scoped_tokens()
    if not admin_token and not scoped_tokens:
        raise HTTPException(status_code=503, detail="Authentication not configured")

    if not x_api_token and admin_token and _is_trusted_local_ui(request):
        return admin_token

    if not x_api_token or (x_api_token != admin_token and x_api_token not in scoped_tokens):
        raise HTTPException(status_code=401, detail="Unauthorized")

    return x_api_token


def authorize_agent(agent_id: str, auth_token: str) -> None:
    """Authorize access to one agent's resources.

    The admin token authorizes every agent. A per-agent scoped token (see
    :func:`_scoped_tokens`) authorizes only its own agent's resources.
    """
    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    admin_token = _admin_token()
    if admin_token and auth_token == admin_token:
        return

    owner = _scoped_tokens().get(auth_token)
    if owner is not None and owner != agent_id:
        raise HTTPException(
            status_code=403,
            detail=f"token is not authorized for agent '{agent_id}'",
        )


def authorize_admin(auth_token: str) -> None:
    """Authorize an operation that is not scoped to one agent (e.g. running
    the whole workflow). Only the admin token may do this -- a per-agent
    scoped token must not be able to drive every other agent."""
    admin_token = _admin_token()
    if not admin_token or auth_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")
