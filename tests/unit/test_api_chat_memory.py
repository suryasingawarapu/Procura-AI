"""The chat routes' use of conversation memory: live mode only, never a reason
for chat to fail, and a delete endpoint that really deletes (admin only)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.procurement.chat_router import ChatDecision

ENV = {"MEMORY_API_TOKEN": "admin-token", "MEMORY_API_TOKEN_VENDRA": "vendra-token"}
HEADERS = {"X-API-Token": "admin-token"}
CONV = "conv-123-abc"


def _body(**over):
    base = {"message": "What does Vendra check?", "requested_by": "a@b.c", "mode": "live", "conversation_id": CONV}
    base.update(over)
    return base


class _Spy:
    """Patches every memory touchpoint of the routes and records the calls."""

    def __init__(self, context="Summary of earlier turns: prefers Gracewood", record_error=None, context_error=None):
        self.calls: List[tuple] = []
        self.router_kwargs: Dict[str, Any] = {}
        self.context, self.record_error, self.context_error = context, record_error, context_error

    def __enter__(self):
        spy = self

        def route(**kwargs):
            spy.router_kwargs = kwargs
            return ChatDecision(intent="answer", reply="Vendra checks documents.")

        def conversation_context(cid):
            if spy.context_error:
                raise spy.context_error
            spy.calls.append(("context", cid))
            return {"summary": "x"}

        def record_turn(cid, msg, reply, task_id=None):
            if spy.record_error:
                raise spy.record_error
            spy.calls.append(("record", cid, msg, reply, task_id))

        self.patches = [
            patch.dict(os.environ, ENV, clear=False),
            patch("app.api.procurement_routes.chat_router.route_chat_message", side_effect=route),
            patch("app.api.procurement_routes.chat_memory.conversation_context", side_effect=conversation_context),
            patch("app.api.procurement_routes.chat_memory.format_for_prompt", return_value=self.context),
            patch("app.api.procurement_routes.chat_memory.record_turn", side_effect=record_turn),
            patch("app.api.procurement_routes._spawn", side_effect=lambda fn, *a: spy.calls.append(("spawn", a))),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def test_live_chat_with_a_conversation_id_recalls_memory_stores_the_turn_and_schedules_a_summary():
    with _Spy() as spy:
        response = TestClient(app).post("/procurement/chat", json=_body(), headers=HEADERS)

    assert response.status_code == 200 and response.json()["reply"] == "Vendra checks documents."
    assert spy.router_kwargs["memory_context"] == "Summary of earlier turns: prefers Gracewood"
    assert ("record", CONV, "What does Vendra check?", "Vendra checks documents.", None) in spy.calls
    assert ("spawn", (CONV,)) in spy.calls


def test_demo_mode_never_touches_conversation_memory():
    with _Spy() as spy:
        response = TestClient(app).post("/procurement/chat", json=_body(mode="demo"), headers=HEADERS)
    assert response.status_code == 200
    assert spy.calls == [] and "memory_context" not in spy.router_kwargs


def test_no_conversation_id_means_no_memory():
    with _Spy() as spy:
        response = TestClient(app).post("/procurement/chat", json=_body(conversation_id=None), headers=HEADERS)
    assert response.status_code == 200 and spy.calls == []


def test_a_malformed_conversation_id_is_rejected():
    with _Spy():
        response = TestClient(app).post("/procurement/chat", json=_body(conversation_id="bad id/../"), headers=HEADERS)
    assert response.status_code == 422


def test_a_memory_read_failure_never_breaks_the_chat():
    with _Spy(context_error=RuntimeError("supabase down")) as spy:
        response = TestClient(app).post("/procurement/chat", json=_body(), headers=HEADERS)
    assert response.status_code == 200 and "memory_context" not in spy.router_kwargs


def test_a_memory_write_failure_never_breaks_the_chat_and_schedules_no_summary():
    with _Spy(record_error=RuntimeError("supabase down")) as spy:
        response = TestClient(app).post("/procurement/chat", json=_body(), headers=HEADERS)
    assert response.status_code == 200 and not any(c[0] == "spawn" for c in spy.calls)


def test_a_task_reply_is_stored_as_a_compact_description_not_the_raw_payload():
    from app.procurement import workflow

    pr = workflow.PurchaseRequest(
        pr_id="PR-MEM1", requested_by="a@b.c", item_description="laptops", quantity=10, estimated_budget=600000.0,
        delivery_location="Mumbai", required_date="2026-12-01", business_justification="x", status="Draft",
        workflow_state="Waiting For Approval",
        coordinator_result={"result": {"recommended_supplier": "ACME", "recommended_total_cost": 506000}},
    )
    with _Spy() as spy, patch(
        "app.api.procurement_routes.chat_router.route_chat_message", return_value=ChatDecision(intent="new_task")
    ), patch("app.api.procurement_routes.task_orchestrator.start_procurement_task", return_value=pr):
        TestClient(app).post("/procurement/chat", json=_body(message="Purchase 10 laptops"), headers=HEADERS)
    record = next(c for c in spy.calls if c[0] == "record")
    assert record[3] == "[task PR-MEM1 is Waiting For Approval] recommended ACME at 506000"
    assert record[4] == "PR-MEM1"  # the turn is linked to the task it concerned


def test_stream_route_recalls_memory_stores_the_turn_before_the_final_event():
    with _Spy() as spy:
        response = TestClient(app).post("/procurement/chat/stream", json=_body(), headers=HEADERS)
    blocks = [b for b in response.text.strip().split("\n\n")]
    events = [(b.split("\n")[0][7:], json.loads(b.split("\n")[1][6:])) for b in blocks]
    statuses = [d["text"] for e, d in events if e == "status"]
    assert "Conversation memory recalled (summary + recent turns)" in statuses
    assert events[-1][0] == "final"
    assert ("record", CONV, "What does Vendra check?", "Vendra checks documents.", None) in spy.calls


# -- DELETE /chat/conversations/{id} ------------------------------------------------------


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_memory.delete_conversation", return_value={"turns": 6, "summaries": 1})
def test_delete_endpoint_deletes_for_the_admin(mock_delete):
    response = TestClient(app).delete(f"/procurement/chat/conversations/{CONV}", headers=HEADERS)
    assert response.status_code == 200 and response.json() == {"turns": 6, "summaries": 1}
    mock_delete.assert_called_once_with(CONV)


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_memory.delete_conversation")
def test_delete_endpoint_refuses_a_scoped_token(mock_delete):
    response = TestClient(app).delete(f"/procurement/chat/conversations/{CONV}", headers={"X-API-Token": "vendra-token"})
    assert response.status_code == 403
    mock_delete.assert_not_called()


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_memory.delete_conversation")
def test_delete_endpoint_rejects_a_malformed_id(mock_delete):
    response = TestClient(app).delete("/procurement/chat/conversations/bad$id", headers=HEADERS)
    assert response.status_code == 400
    mock_delete.assert_not_called()
