"""Streaming chat: the reply streamer, the router's streaming path, live
progress events from the real coordinator, and the SSE endpoint."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, TypedDict
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph

from app.api.main import app
from app.procurement import chat_router, progress, workflow
from app.procurement.chat_router import ChatDecision, _ReplyStreamer, route_chat_message
from app.procurement.coordinator import run_coordinator
from app.procurement.mcp.client import SeededERPNextClient
from app.procurement.messages import TaskRequest

ENV = {"MEMORY_API_TOKEN": "admin-token"}
HEADERS = {"X-API-Token": "admin-token"}


# -- _ReplyStreamer -----------------------------------------------------------


def _stream(raw: str, size: int) -> str:
    out: List[str] = []
    streamer = _ReplyStreamer(out.append)
    for i in range(0, len(raw), size):
        streamer.feed(raw[i : i + size])
    return "".join(out)


def test_reply_streamer_decodes_escapes_split_across_chunks():
    raw = json.dumps({"intent": "answer", "reply": 'Say "hi"\nthen \\ done — é'})
    for size in (1, 2, 3, 7):
        assert _stream(raw, size) == 'Say "hi"\nthen \\ done — é'


def test_reply_streamer_handles_unicode_escapes():
    raw = '{"intent": "answer", "reply": "caf\\u00e9"}'
    assert _stream(raw, 2) == "café"


def test_reply_streamer_stays_silent_for_task_intents():
    assert _stream(json.dumps({"intent": "new_task"}), 3) == ""
    assert _stream(json.dumps({"intent": "provide_info", "reply": "Got it"}), 3) == ""


def test_reply_streamer_waits_for_intent_even_if_reply_comes_first():
    raw = '{"reply": "hello there", "intent": "answer"}'
    assert _stream(raw, 4) == "hello there"


def test_reply_streamer_ignores_text_after_the_closing_quote():
    assert _stream('{"intent":"answer","reply":"ok"} trailing "junk"', 5) == "ok"


# -- router streaming path ------------------------------------------------------


class _Chunk:
    def __init__(self, content: str):
        self.content = content


def test_router_streams_reply_tokens_and_returns_the_same_decision():
    raw = json.dumps({"intent": "answer", "reply": "Vendra checks documents.\n- one\n- two"})
    llm = MagicMock()
    llm.stream.return_value = [_Chunk(raw[i : i + 6]) for i in range(0, len(raw), 6)]
    tokens: List[str] = []

    decision = route_chat_message(message="what does vendra check?", llm=llm, on_token=tokens.append)

    assert decision.intent == "answer"
    assert "".join(tokens) == decision.reply == "Vendra checks documents.\n- one\n- two"
    llm.invoke.assert_not_called()


def test_router_without_on_token_still_uses_invoke():
    llm = MagicMock()
    llm.invoke.return_value = _Chunk(json.dumps({"intent": "answer", "reply": "hi"}))
    assert route_chat_message(message="hello", llm=llm).reply == "hi"
    llm.stream.assert_not_called()


def test_router_stream_failure_falls_back_like_before():
    llm = MagicMock()
    llm.stream.side_effect = RuntimeError("connection dropped")
    decision = route_chat_message(message="please buy 5 chairs", llm=llm, on_token=lambda _t: None)
    assert decision.intent == "new_task"


PROSE = "Finora handles budget verification, cost analysis and approval routing."


def test_plain_prose_reply_is_kept_as_the_answer_instead_of_discarded():
    """Regression: the model sometimes ignores the JSON instruction and
    answers in prose; that used to be replaced by a generic "I'm not sure"."""
    llm = MagicMock()
    llm.invoke.return_value = _Chunk(PROSE)
    decision = route_chat_message(message="In two sentences, what does Finora do?", llm=llm)
    assert decision.intent == "answer"
    assert decision.reply == PROSE


def test_plain_prose_is_streamed_and_matches_the_final_answer():
    llm = MagicMock()
    llm.stream.return_value = [_Chunk(PROSE[i : i + 9]) for i in range(0, len(PROSE), 9)]
    tokens: List[str] = []
    decision = route_chat_message(message="what does Finora do?", llm=llm, on_token=tokens.append)
    assert "".join(tokens) == decision.reply == PROSE


def test_prose_for_a_purchase_message_still_starts_a_task_and_is_not_streamed():
    llm = MagicMock()
    llm.stream.return_value = [_Chunk("Sure, I can help you buy laptops.")]
    tokens: List[str] = []
    decision = route_chat_message(message="Purchase 10 laptops", llm=llm, on_token=tokens.append)
    assert decision.intent == "new_task"
    assert tokens == []


# -- progress events ----------------------------------------------------------------


def test_emit_is_a_noop_without_a_listener():
    progress.emit("x", "nobody is listening")  # must not raise


def test_a_broken_listener_never_breaks_the_caller():
    def boom(_stage, _text):
        raise RuntimeError("listener bug")

    with progress.capture(boom):
        progress.emit("x", "still fine")


def test_progress_sink_follows_the_request_into_langgraph_nodes():
    class S(TypedDict, total=False):
        n: int

    def node(state: S) -> S:
        progress.emit("node", "inside the graph")
        return {"n": 1}

    graph = StateGraph(S)
    graph.add_node("a", node)
    graph.add_edge(START, "a")
    graph.add_edge("a", END)
    seen: List[tuple] = []
    with progress.capture(lambda stage, text: seen.append((stage, text))):
        graph.compile().invoke({})
    assert seen == [("node", "inside the graph")]


@patch("app.memory.provenance_fields.supabase", new_callable=MagicMock)
@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.tools.memory_tools.save_memory")
def test_real_coordinator_reports_each_agent_starting_and_finishing(mock_save, _ctx, _supabase):
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{a[1]}"}
    seen: List[tuple] = []
    task = TaskRequest(description="Find eligible suppliers for 20 laptops, compare quotations, check the budget.")

    with progress.capture(lambda stage, text: seen.append((stage, text))):
        run_coordinator(task, SeededERPNextClient(), item_code="ITEM-LAPTOP-14", quantity=20)

    stages = [stage for stage, _ in seen]
    assert stages == ["vendra", "vendra", "procura", "procura", "finora", "finora"]
    assert seen[0][1].startswith("Vendra started")
    assert "eligible" in seen[1][1]
    assert "SUP-0001" in seen[3][1]  # the real recommendation, not a placeholder
    assert seen[5][1].startswith("Finora finished")


# -- SSE endpoint ------------------------------------------------------------------


def _events(response) -> List[Dict[str, Any]]:
    out = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append({"event": lines["event"], "data": json.loads(lines["data"])})
    return out


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_router.route_chat_message")
def test_stream_endpoint_sends_tokens_then_the_final_answer(mock_route):
    def fake_route(**kwargs):
        for piece in ("Hello", ", ", "world"):
            kwargs["on_token"](piece)
        return ChatDecision(intent="answer", reply="Hello, world")

    mock_route.side_effect = fake_route
    response = TestClient(app).post("/procurement/chat/stream", json={"message": "hi", "requested_by": "a@b.c"}, headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert [e["data"]["text"] for e in events if e["event"] == "token"] == ["Hello", ", ", "world"]
    assert events[-1] == {"event": "final", "data": {"reply": "Hello, world", "pr": None, "mail": None}}


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.task_orchestrator.start_procurement_task")
@patch("app.api.procurement_routes.chat_router.route_chat_message", return_value=ChatDecision(intent="new_task"))
def test_stream_endpoint_forwards_pipeline_progress_then_the_task(_route, mock_start):
    pr = workflow.PurchaseRequest(
        pr_id="PR-STREAM1", requested_by="a@b.c", item_description="laptops", quantity=10,
        estimated_budget=600000.0, delivery_location="Mumbai", required_date="2026-12-01",
        business_justification="x", status="Draft", workflow_state="Waiting For Approval",
    )

    def fake_start(**_kwargs):
        progress.emit("memory", "Task PR-STREAM1 saved to persistent memory (version 1)")
        progress.emit("vendra", "Vendra started")
        return pr

    mock_start.side_effect = fake_start
    response = TestClient(app).post(
        "/procurement/chat/stream", json={"message": "Purchase 10 laptops", "requested_by": "a@b.c"}, headers=HEADERS
    )

    events = _events(response)
    statuses = [e["data"]["text"] for e in events if e["event"] == "status"]
    assert statuses == [
        "Router: reading your message",
        "Router: new purchase request",
        "Task PR-STREAM1 saved to persistent memory (version 1)",
        "Vendra started",
    ]
    assert events[-1]["event"] == "final"
    assert events[-1]["data"]["pr"]["pr_id"] == "PR-STREAM1"


@patch.dict(os.environ, ENV, clear=False)
@patch("app.api.procurement_routes.chat_router.route_chat_message", side_effect=RuntimeError("boom"))
def test_stream_endpoint_reports_failures_as_an_error_event(_route):
    events = _events(TestClient(app).post("/procurement/chat/stream", json={"message": "hi", "requested_by": "a@b.c"}, headers=HEADERS))
    assert events[-1]["event"] == "error"
    assert "boom" in events[-1]["data"]["detail"]


@patch.dict(os.environ, {**ENV, "MEMORY_API_TOKEN_VENDRA": "vendra-token"}, clear=False)
def test_stream_endpoint_requires_the_admin_token():
    response = TestClient(app).post(
        "/procurement/chat/stream", json={"message": "hi", "requested_by": "a@b.c"}, headers={"X-API-Token": "vendra-token"}
    )
    assert response.status_code == 403
