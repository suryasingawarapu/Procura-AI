"""API authentication and authorization."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException


def require_auth(x_api_token: Optional[str] = Header(default=None)) -> str:
    """Require API token authentication. Fail-closed if not configured."""
    expected = os.getenv("MEMORY_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Authentication not configured")
    
    if not x_api_token or x_api_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    return x_api_token


def authorize_agent(agent_id: str, auth_token: str) -> None:
    """Authorize access to agent resources.
    
    Simple implementation - in production would check token claims.
    For now, any authenticated token can access any agent (app-level isolation).
    """
    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=400, detail="Invalid agent_id")
    return
