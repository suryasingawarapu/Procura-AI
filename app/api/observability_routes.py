"""Read-only Langfuse dashboard data for the observability page.

The browser never sees the Langfuse keys: this router calls the Langfuse
public API (v2 ``metrics`` and ``observations``) server-side and returns only
the numbers and fields the page shows. Raw observation ``metadata`` is never
returned (the SDK stores the public key there).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.observability import langfuse_tracing as tracing

from .auth import authorize_admin, require_auth

router = APIRouter(prefix="/observability", tags=["observability"])

_CACHE_SECONDS = 20
_cache: Dict[Any, Any] = {}


def _auth() -> httpx.BasicAuth:
    import os

    pk, sk = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        raise HTTPException(status_code=503, detail="Langfuse is not configured (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY).")
    return httpx.BasicAuth(pk, sk)


_CALL_CACHE_SECONDS = 45
_call_cache: Dict[Any, Any] = {}


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """One Langfuse API call, cached briefly and retried when Langfuse says to slow down (429)."""
    key = (path, json.dumps(params, sort_keys=True, default=str))
    hit = _call_cache.get(key)
    if hit and time.time() - hit[0] < _CALL_CACHE_SECONDS:
        return hit[1]
    data = _fetch(path, params)
    _call_cache[key] = (time.time(), data)
    return data


def _retry_after(response: Any) -> float:
    try:
        return float(response.headers.get("Retry-After", 2))
    except (TypeError, ValueError):
        return 2.0


def _fetch(path: str, params: Optional[Dict[str, Any]]) -> Any:
    base = tracing.status()["base_url"].rstrip("/")
    for attempt in range(3):
        try:
            response = httpx.get(f"{base}/api/public{path}", params=params, auth=_auth(), timeout=30)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach Langfuse: {type(exc).__name__}")
        if response.status_code != 429 or attempt == 2 or _retry_after(response) > 10:
            break
        time.sleep(min(max(_retry_after(response), 1.0), 5.0))
    if response.status_code in (401, 403):
        raise HTTPException(status_code=502, detail="Langfuse rejected the configured keys.")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="Langfuse is rate limiting this project's API calls; try again later.")
    if not response.is_success:
        raise HTTPException(status_code=502, detail=f"Langfuse returned {response.status_code}.")
    return response.json()


def _agent_of(name: str) -> str:
    """Cost is attributed by the part of the name before ' - ' (the guide's naming rule)."""
    return name.split(" - ", 1)[0] if " - " in name else name


def _num(row: Dict[str, Any], key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _clip(value: Any, limit: int = 160) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def _recent(from_ts: str, root_only: bool, level: Optional[str], limit: int) -> List[Dict[str, Any]]:
    params = {
        "fromStartTime": from_ts,
        "limit": 200,
        "fields": "core,basic,time,io,model,usage",
    }
    rows = _get("/v2/observations", params).get("data", [])
    out = []
    for r in rows:
        if root_only and not r.get("isRootObservation") and r.get("parentObservationId"):
            continue
        if level and r.get("level") not in level.split(","):
            continue
        out.append(
            {
                "id": r.get("id"),
                "trace_id": r.get("traceId"),
                "name": r.get("name"),
                "type": r.get("type"),
                "level": r.get("level"),
                "status_message": _clip(r.get("statusMessage")),
                "user_id": r.get("userId"),
                "session_id": r.get("sessionId"),
                "environment": r.get("environment"),
                "model": r.get("model") or r.get("providedModelName"),
                "latency_s": r.get("latency"),
                "total_cost": r.get("totalCost"),
                "tokens": (r.get("usageDetails") or {}).get("total"),
                "start_time": r.get("startTime"),
                "input": _clip(r.get("input")),
                "output": _clip(r.get("output")),
            }
        )
        if len(out) >= limit:
            break
    return out


@router.get("/status")
def observability_status(auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    info = tracing.status()
    reachable = False
    project = None
    if info["configured"]:
        try:
            _get("/health")
            reachable = True
            projects = _get("/projects").get("data") or []
            project = projects[0].get("name") if projects else None
        except HTTPException:
            reachable = False
    return {**info, "reachable": reachable, "project": project}


_PAGE = 1000
_MAX_PAGES = 5  # up to 5,000 observations per range; the page says so when it is truncated


def _observations(days: int) -> tuple:
    """Every observation in the range from the observations API (the metrics API is quota-limited
    to 100 calls on Langfuse's free plan, the observations API is not). Returns (rows, truncated)."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).replace(second=0, microsecond=0)
    rows: List[Dict[str, Any]] = []
    cursor = None
    for _ in range(_MAX_PAGES):
        params = {"fromStartTime": start.isoformat(), "limit": _PAGE, "fields": "core,basic,time,usage"}
        if cursor:
            params["cursor"] = cursor
        body = _get("/v2/observations", params)
        rows.extend(body.get("data", []))
        cursor = (body.get("meta") or {}).get("cursor")
        if not cursor:
            return rows, False
    return rows, True


def _bucket() -> Dict[str, Any]:
    return {"count": 0, "cost": 0.0, "tokens": 0, "latency": 0.0}


def _add(bucket: Dict[str, Any], row: Dict[str, Any]) -> None:
    bucket["count"] += 1
    bucket["cost"] += float(row.get("totalCost") or 0)
    bucket["tokens"] += int((row.get("usageDetails") or {}).get("total") or 0)
    bucket["latency"] += float(row.get("latency") or 0)


def _avg(bucket: Dict[str, Any]) -> float:
    return bucket["latency"] / bucket["count"] if bucket["count"] else 0.0


@router.get("/summary")
def observability_summary(days: int = Query(7, ge=1, le=90), auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    now = datetime.now(timezone.utc)
    rows, truncated = _observations(days)

    total, by_name, by_type, by_user, by_day = _bucket(), {}, {}, {}, {}
    agents: Dict[str, Dict[str, Any]] = {}
    levels: Dict[str, int] = {}
    for r in rows:
        name = r.get("name") or "(unnamed)"
        _add(total, r)
        _add(by_name.setdefault(name, _bucket()), r)
        _add(agents.setdefault(_agent_of(name), _bucket()), r)
        _add(by_type.setdefault(r.get("type") or "?", _bucket()), r)
        _add(by_user.setdefault(r.get("userId") or "(anonymous)", _bucket()), r)
        _add(by_day.setdefault((r.get("startTime") or "")[:10], _bucket()), r)
        levels[r.get("level") or "DEFAULT"] = levels.get(r.get("level") or "DEFAULT", 0) + 1
    for offset in range(days + 1):  # days with no activity still appear, as zero
        by_day.setdefault((now - timedelta(days=offset)).strftime("%Y-%m-%d"), _bucket())

    steps = [
        {"name": n, "count": b["count"], "cost": b["cost"], "tokens": b["tokens"], "avg_latency_s": _avg(b)}
        for n, b in by_name.items()
    ]
    steps.sort(key=lambda x: (x["cost"], x["count"]), reverse=True)
    return {
        "days": days,
        "truncated": truncated,
        "totals": {
            "observations": total["count"],
            "cost": total["cost"],
            "tokens": total["tokens"],
            "avg_latency_s": _avg(total),
            "errors": levels.get("ERROR", 0),
            "warnings": levels.get("WARNING", 0),
        },
        "levels": levels,
        "types": [{"type": k, "count": b["count"], "cost": b["cost"], "tokens": b["tokens"]} for k, b in by_type.items()],
        "agents": sorted(
            [{"name": k, "count": b["count"], "cost": b["cost"], "tokens": b["tokens"]} for k, b in agents.items()],
            key=lambda x: x["cost"], reverse=True,
        ),
        "steps": steps,
        "daily": [
            {"day": d, "count": b["count"], "cost": b["cost"], "tokens": b["tokens"]}
            for d, b in sorted(by_day.items()) if d
        ],
        "users": sorted(
            [{"user_id": k, "count": b["count"], "cost": b["cost"]} for k, b in by_user.items()],
            key=lambda x: x["count"], reverse=True,
        )[:20],
    }


@router.get("/recent")
def observability_recent(
    days: int = Query(7, ge=1, le=90),
    kind: str = Query("traces", pattern="^(traces|errors|generations|sessions)$"),
    limit: int = Query(50, ge=1, le=200),
    auth_token: str = Depends(require_auth),
):
    """``traces`` = top-level requests; ``errors`` = ERROR/WARNING observations; ``generations`` = model calls."""
    authorize_admin(auth_token)
    now = datetime.now(timezone.utc)
    from_ts = (now - timedelta(days=days)).isoformat()
    if kind == "sessions":
        # Langfuse retired its sessions endpoint; a session is every observation sharing a sessionId.
        sessions: Dict[str, Dict[str, Any]] = {}
        for r in _observations(days)[0]:
            if r.get("sessionId"):
                _add(sessions.setdefault(r["sessionId"], _bucket()), r)
        ranked = sorted(sessions.items(), key=lambda kv: kv[1]["count"], reverse=True)[:limit]
        return {"kind": kind, "items": [
            {"session_id": k, "observations": b["count"], "cost": b["cost"], "tokens": b["tokens"], "avg_latency_s": _avg(b)}
            for k, b in ranked
        ]}
    if kind == "errors":
        rows = _recent(from_ts, False, "ERROR,WARNING", limit)
    elif kind == "generations":
        rows = [r for r in _recent(from_ts, False, None, 200) if r["type"] == "GENERATION"][:limit]
    else:
        rows = _recent(from_ts, True, None, limit)
    return {"kind": kind, "items": rows}
