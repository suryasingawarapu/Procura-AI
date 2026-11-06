"""Langfuse tracing helper and the observability dashboard routes (no network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import observability_routes as obs
from app.api.main import app
from app.observability import langfuse_tracing as tracing


def test_disabled_by_default_in_unit_tests_and_a_noop(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert tracing.configured() and not tracing.enabled()  # conftest sets LANGFUSE_ENABLED=0
    with tracing.trace("Chat", user_id="a@b.c", input="x") as t:
        t.update(output="y")
        t.error("boom")
    assert tracing.llm_config("Router") == {}
    assert tracing.llm_kwargs("Router") == {}
    tracing.mark("ERROR", "nothing happens")
    tracing.flush()


def test_enabled_needs_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "1")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert not tracing.enabled()


def test_status_never_contains_secrets(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-supersecret")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-public")
    assert "supersecret" not in str(tracing.status()) and "pk-lf-public" not in str(tracing.status())


def test_names_use_the_display_name_dash_step_format():
    assert " - " not in tracing.DISPLAY
    assert tracing._agent_of if hasattr(tracing, "_agent_of") else True
    assert obs._agent_of("Procurement Coordinator - Router") == tracing.DISPLAY


def test_exception_in_a_traced_block_propagates(monkeypatch):
    with pytest.raises(ValueError):
        with tracing.span("Vendra"):
            raise ValueError("x")


class _AdminClient:
    """TestClient that always sends the admin token."""

    def __init__(self):
        self._c = TestClient(app)

    def get(self, url):
        return self._c.get(url, headers={"X-Api-Token": "admin-token"})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MEMORY_API_TOKEN", "admin-token")
    obs._cache.clear(); obs._call_cache.clear()
    return _AdminClient()


def test_summary_aggregates_observations_without_the_metrics_api(client, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    rows = [
        {"name": "Procurement Coordinator - Router", "type": "GENERATION", "level": "ERROR", "userId": "a@b.c",
         "startTime": "2026-11-03T01:00:00Z", "totalCost": 0.5, "usageDetails": {"total": 100}, "latency": 2.0},
        {"name": "Procurement Coordinator - Router", "type": "GENERATION", "level": "DEFAULT", "userId": None,
         "startTime": "2026-11-03T02:00:00Z", "totalCost": 0.25, "usageDetails": {"total": 50}, "latency": 1.0},
        {"name": "Procurement Coordinator - Vendra", "type": "SPAN", "level": "WARNING", "userId": "a@b.c",
         "startTime": "2026-11-03T03:00:00Z", "totalCost": None, "usageDetails": None, "latency": 0.0},
    ]
    paths = []

    def fake_get(path, params=None):
        paths.append(path)
        return {"data": rows, "meta": {}}

    with patch.object(obs, "_get", side_effect=fake_get):
        body = client.get("/observability/summary?days=1").json()
    assert set(paths) == {"/v2/observations"}  # never the quota-limited metrics endpoint
    assert body["totals"] == {"observations": 3, "cost": 0.75, "tokens": 150, "avg_latency_s": 1.0, "errors": 1, "warnings": 1}
    assert body["agents"][0] == {"name": "Procurement Coordinator", "count": 3, "cost": 0.75, "tokens": 150}
    assert body["steps"][0]["name"].endswith("Router") and body["steps"][0]["avg_latency_s"] == 1.5
    assert {u["user_id"] for u in body["users"]} == {"a@b.c", "(anonymous)"}
    assert not body["truncated"]


def test_recent_drops_metadata_and_filters(client):
    rows = [
        {"id": "1", "name": "Procurement Coordinator - Chat", "type": "SPAN", "level": "DEFAULT", "isRootObservation": True,
         "userId": "a@b.c", "sessionId": "s", "metadata": {"public_key": "pk-secret"}, "input": "x" * 500},
        {"id": "2", "name": "Procurement Coordinator - Router", "type": "GENERATION", "level": "ERROR",
         "parentObservationId": "1", "statusMessage": "429"},
    ]
    with patch.object(obs, "_get", return_value={"data": rows}):
        traces = client.get("/observability/recent?kind=traces").json()["items"]
        errors = client.get("/observability/recent?kind=errors").json()["items"]
    assert [r["id"] for r in traces] == ["1"] and [r["id"] for r in errors] == ["2"]
    assert "pk-secret" not in str(traces) and len(traces[0]["input"]) < 200


def test_not_configured_is_a_clear_503(client, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    r = client.get("/observability/summary")
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


def test_langfuse_failure_is_reported_not_leaked(client, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    resp = MagicMock(status_code=401, is_success=False)
    with patch.object(obs.httpx, "get", return_value=resp):
        r = client.get("/observability/summary")
    assert r.status_code == 502 and "rejected" in r.json()["detail"]


def test_sessions_are_grouped_by_session_id(client):
    rows = [{"sessionId": "c1", "totalCost": 0.1, "usageDetails": {"total": 90}, "latency": 0.5},
            {"sessionId": "c1", "totalCost": 0.0, "usageDetails": {"total": 0}, "latency": 1.5},
            {"sessionId": None, "latency": 9}]
    with patch.object(obs, "_get", return_value={"data": rows, "meta": {}}):
        items = client.get("/observability/recent?kind=sessions").json()["items"]
    assert items == [{"session_id": "c1", "observations": 2, "cost": 0.1, "tokens": 90, "avg_latency_s": 1.0}]


def test_rate_limit_is_retried_and_responses_are_cached(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    obs._call_cache.clear()
    limited = MagicMock(status_code=429, is_success=False, headers={"Retry-After": "1"})
    ok = MagicMock(status_code=200, is_success=True)
    ok.json.return_value = {"data": [1]}
    with patch.object(obs.httpx, "get", side_effect=[limited, ok]) as get, patch.object(obs.time, "sleep"):
        assert obs._get("/x", {"a": 1}) == {"data": [1]}
        assert obs._get("/x", {"a": 1}) == {"data": [1]}  # second call is served from the cache
    assert get.call_count == 2
