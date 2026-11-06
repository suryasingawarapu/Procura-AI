"""GET /agents/{id}/memory-health, POST /agents/{id}/summaries/refresh and
POST /agents/{id}/stale-copies/refresh."""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from tests.memfake import MemFake

ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token", "MEMORY_API_TOKEN_PROCURA": "procura-token"}
ADMIN = {"X-API-Token": "admin-token"}
SRC = "00000000-0000-0000-0000-000000000001"
COPY = "11111111-1111-1111-1111-111111111111"


def _mem(db, id, agent, key, observed, **extra):
    row = dict(id=id, agent_id=agent, memory_key=key, content=key, status="active", version=1,
               observed_at=f"2026-03-01T10:00:{observed:02d}+00:00", hop_depth=0)
    row.update(extra)
    return db.add("agent_memories", **row)


def _get(agent, db, headers=ADMIN):
    with patch.dict(os.environ, ENV, clear=False), patch("app.api.main.supabase", db):
        return TestClient(app).get(f"/agents/{agent}/memory-health", headers=headers)


def test_health_of_an_agent_with_nothing_stored():
    body = _get("vendra", MemFake()).json()
    assert body["memories"] == {"active": 0, "superseded": 0, "shared_copies": 0}
    assert body["summary"] is None and body["stale_copies"] == []


def test_health_counts_summary_state_and_stale_copies():
    db = MemFake()
    _mem(db, SRC, "vendra", "supplier_eligibility:X", 1, status="superseded")
    _mem(db, "src2", "vendra", "supplier_eligibility:X", 2, version=2)
    _mem(db, "m3", "procura", "own fact", 3)
    _mem(db, COPY, "procura", "shared_supplier_eligibility:X", 4, hop_depth=1, source_reference=SRC)
    db.add("agent_memory_summaries", id="s1", agent_id="procura", summary="s", memory_ids=["m3"],
           covered_observed_at="2026-03-01T10:00:03+00:00", status="active", created_at="2026-03-01T10:00:09+00:00")

    body = _get("procura", db).json()

    assert body["memories"] == {"active": 2, "superseded": 0, "shared_copies": 1}
    assert body["summary"]["state"] == "partial" and body["summary"]["newer_memories"] == 1
    assert body["stale_copies"] == [{
        "copy_key": "shared_supplier_eligibility:X", "copy_version": 1,
        "outdated_source": "vendra/supplier_eligibility:X v1", "found": "direct", "can_refresh": True,
    }]


def test_health_reports_a_stale_summary():
    db = MemFake()
    _mem(db, "m1", "vendra", "k", 1, status="superseded")
    _mem(db, "m1b", "vendra", "k", 2, version=2)
    db.add("agent_memory_summaries", id="s1", agent_id="vendra", summary="s", memory_ids=["m1"],
           covered_observed_at="2026-03-01T10:00:01+00:00", status="active", created_at="2026-03-01T10:00:09+00:00")
    assert _get("vendra", db).json()["summary"]["state"] == "stale"


def test_a_token_scoped_to_another_agent_is_refused():
    assert _get("procura", MemFake(), headers={"X-API-Token": "vendra-token"}).status_code == 403


def test_an_agents_own_scoped_token_works():
    assert _get("vendra", MemFake(), headers={"X-API-Token": "vendra-token"}).status_code == 200


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.refresh_summary", return_value={"id": "s1", "summary": "A summary", "memory_ids": ["m1"]})
def test_refresh_summary_returns_the_summary(mock_refresh):
    with patch("app.agents.workflow.get_llm", side_effect=RuntimeError("no llm")):
        response = TestClient(app).post("/agents/vendra/summaries/refresh", headers=ADMIN)
    assert response.status_code == 200 and response.json()["summary"]["summary"] == "A summary"
    assert mock_refresh.call_args.kwargs["llm"] is None  # no LLM -> the labelled extractive fallback is used


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.refresh_summary", return_value=None)
def test_refresh_summary_with_nothing_to_summarize_says_so(_refresh):
    body = TestClient(app).post("/agents/vendra/summaries/refresh", headers=ADMIN).json()
    assert body["summary"] is None and "no current memories" in body["detail"]


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.refresh_summary")
def test_refresh_summary_is_scoped_per_agent(mock_refresh):
    response = TestClient(app).post("/agents/procura/summaries/refresh", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_refresh.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.main.refresh_stale_copies", return_value=[{"memory_key": "shared_x", "version": 2}])
def test_refresh_stale_copies_reports_what_was_refreshed(mock_refresh):
    body = TestClient(app).post("/agents/procura/stale-copies/refresh", headers=ADMIN).json()
    assert body["refreshed"] == [{"memory_key": "shared_x", "version": 2}]
    assert mock_refresh.call_args.args == ("procura",)
