"""Supervisor-controlled multi-agent workflow driven by LLM calls.

The supervisor decides which worker runs next (or finishes); every worker is
an LLM with memory tools bound to its own identity. Models are configurable
per role through environment variables:

* ``AGENT_LLM_MODEL``            default for all roles (``provider:model``)
* ``AGENT_LLM_MODEL_<ROLE>``     override for one role, e.g. ``..._PLANNER``

The default model is NVIDIA's Nemotron 3.5 Lightning, served through
``langchain-nvidia-ai-endpoints`` (``ChatNVIDIA``), which ``init_chat_model``
already resolves for the ``nvidia:`` prefix. It reads ``NVIDIA_API_KEY`` from
the environment itself -- no key handling lives in this module. Any role can
still be pointed at a different provider via the env vars above.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

ROLES = ["supervisor", "planner", "researcher", "executor", "validator"]
WORKERS = ["planner", "researcher", "executor", "validator"]

DEFAULT_MODEL = "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b"
MAX_STEPS = 8
MAX_TOOL_ROUNDS = 4

# Speed-oriented defaults applied only when a role resolves to an NVIDIA
# model. Nemotron 3.5 Lightning is a hybrid reasoning model whose "thinking"
# tokens and final-answer tokens share one completion budget
# (max_completion_tokens) -- confirmed empirically: with thinking enabled
# and a 512-token cap, the model spent the entire budget on
# reasoning_content and returned finish_reason="length" with an EMPTY
# content, so the supervisor/validator's required JSON was never produced.
# Thinking is disabled so the budget goes to the actual output every time;
# this is also what "speed-oriented" actually means for a model called
# Lightning. The installed SDK only exposes this as a boolean
# (chat_template_kwargs.enable_thinking), not a numeric reasoning budget.
# Real bug found live: 512 was tuned for the original supervisor/planner/
# researcher/executor JSON routing replies (short, fixed-shape). The
# chat_router's "answer" intent (app.procurement.chat_router) can
# legitimately need a much longer narrated reply (e.g. describing all 18
# specialists across Vendra/Procura/Finora) -- one such real reply
# already used 354 of the 512 tokens, and a follow-up asking for even
# more detail plausibly got cut off mid-JSON, which fails to parse
# silently and falls back to a generic, unhelpful message instead of
# ever surfacing an error. Raised with real headroom for that case.
NVIDIA_MAX_COMPLETION_TOKENS = 2048
NVIDIA_MODEL_KWARGS = {"chat_template_kwargs": {"enable_thinking": False}}

PROMPTS = {
    "supervisor": (
        "You are the Supervisor of a multi-agent team (planner, researcher, executor, "
        "validator). Route to the worker(s) who need to actually do the task (planner, "
        "researcher, or executor) before sending it to the validator -- do not send the "
        "validator a task that no one has worked on yet. Pick the next agent, or finish once "
        "the validator has approved or no progress is possible. Reply with JSON only: "
        '{"next": "planner|researcher|executor|validator|finish", "reason": "...", '
        '"final_answer": "... (when finishing)"}.'
    ),
    "planner": (
        "You are the Planner. Break the task into concise, ordered steps. If the plan is "
        "reusable for similar future tasks, save it with save_memory_entry under a short, "
        "descriptive memory_key."
    ),
    "researcher": (
        "You are the Researcher. Gather the facts needed for the task and note sources. Save "
        "any fact worth remembering for future tasks with save_memory_entry (or "
        "update_memory_entry if you are revising something you already saved), under a short, "
        "descriptive memory_key."
    ),
    "executor": (
        "You are the Executor. Carry out the task using the plan and research so far. If your "
        "result is worth reusing later, save it with save_memory_entry."
    ),
    "validator": (
        "You are the Validator. Base your verdict only on what 'Team progress so far' actually "
        "shows -- if it is empty or does not address the task, reply passed: false; never "
        "approve work you have no evidence was done. Reply with JSON only: "
        '{"passed": true|false, "feedback": "..."}.'
    ),
}


class AgentState(TypedDict, total=False):
    task: str
    history: List[Dict[str, str]]
    current_agent: str
    next: str
    result: str
    step: int
    validation_passed: bool
    failed: bool
    error: str


def get_llm(role: str) -> Any:
    """Build the chat model for ``role`` from the environment.

    The NVIDIA-specific speed/reasoning kwargs are only applied when the
    resolved model is actually an NVIDIA one, so a per-role override to a
    different provider (e.g. ``AGENT_LLM_MODEL_PLANNER=openai:gpt-4o-mini``)
    is never handed a kwarg it doesn't understand.
    """
    from langchain.chat_models import init_chat_model

    model = os.getenv(f"AGENT_LLM_MODEL_{role.upper()}") or os.getenv("AGENT_LLM_MODEL") or DEFAULT_MODEL
    kwargs: Dict[str, Any] = {"temperature": 0}
    if model.split(":", 1)[0] == "nvidia":
        kwargs["max_completion_tokens"] = NVIDIA_MAX_COMPLETION_TOKENS
        kwargs["model_kwargs"] = NVIDIA_MODEL_KWARGS
    return init_chat_model(model, **kwargs)


def _parse_json(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in model reply: {text!r}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("model reply JSON is not an object")
    return data


def _text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def _run_agent(role: str, llm: Any, tools: list, state: AgentState) -> str:
    """One LLM turn for ``role`` with a bounded tool-calling loop."""
    by_name = {t.name: t for t in tools}
    model = llm.bind_tools(tools) if tools else llm

    try:
        memory = by_name["get_context"].invoke({"limit": 10}) if "get_context" in by_name else []
    except Exception as exc:  # memory is advisory; keep the agent running
        memory = f"unavailable ({type(exc).__name__}: {exc})"

    history = "\n".join(f"[{h['agent']}] {h['output']}" for h in state.get("history", []))
    messages = [
        SystemMessage(content=PROMPTS[role]),
        HumanMessage(
            content=f"Task: {state.get('task', '')}\n\nYour memory context: {memory}\n\n"
            f"Team progress so far:\n{history or '(none)'}"
        ),
    ]
    reply = model.invoke(messages)
    for _ in range(MAX_TOOL_ROUNDS):
        calls = getattr(reply, "tool_calls", None) or []
        if not calls:
            break
        messages.append(reply)
        for call in calls:
            tool = by_name.get(call["name"])
            try:
                if tool is None:
                    raise KeyError(f"unknown tool {call['name']}")
                out = tool.invoke(call.get("args", {}))
            except Exception as exc:
                out = f"error: {type(exc).__name__}: {exc}"
            messages.append(ToolMessage(content=str(out), tool_call_id=call["id"]))
        reply = model.invoke(messages)
    return _text(reply)


def build_workflow(
    llms: Dict[str, Any] | None = None,
    tools_factory: Callable[[str], list] | None = None,
    max_steps: int = MAX_STEPS,
):
    """Build the workflow graph.

    ``llms`` maps role -> chat model (defaults to :func:`get_llm`);
    ``tools_factory`` maps an agent identity -> its tools (defaults to
    ``build_agent_tools``). Each node only ever receives the tools built for
    its own role.
    """
    if tools_factory is None:
        from app.tools.memory_tools import build_agent_tools as tools_factory

    llm_cache: Dict[str, Any] = dict(llms or {})
    tool_cache: Dict[str, list] = {}

    def llm_for(role: str) -> Any:
        if role not in llm_cache:
            llm_cache[role] = get_llm(role)
        return llm_cache[role]

    def tools_for(role: str) -> list:
        if role not in tool_cache:
            tool_cache[role] = tools_factory(role)
        return tool_cache[role]

    def supervisor_node(state: AgentState) -> AgentState:
        step = state.get("step", 0) + 1
        if step > max_steps:
            return {"current_agent": "supervisor", "step": step, "next": "finish", "failed": True,
                    "error": "max steps exceeded"}
        try:
            text = _run_agent("supervisor", llm_for("supervisor"), tools_for("supervisor"), state)
            decision = _parse_json(text)
            nxt = str(decision.get("next", "")).lower()
            if nxt not in WORKERS + ["finish"]:
                raise ValueError(f"invalid next agent: {nxt!r}")
        except Exception as exc:
            return {"current_agent": "supervisor", "step": step, "next": "finish", "failed": True,
                    "error": f"supervisor error: {exc}"}
        out: AgentState = {"current_agent": "supervisor", "step": step, "next": nxt}
        if nxt == "finish":
            # Fall back to the last actual work product, not the validator's
            # own verdict -- the validator almost always runs immediately
            # before finishing, so "the last history entry" would otherwise
            # usually be its {"passed": ..., "feedback": ...} JSON rather
            # than anything produced for the task.
            fallback = ""
            for entry in reversed(state.get("history", [])):
                if entry.get("agent") != "validator":
                    fallback = entry.get("output", "")
                    break
            out["result"] = str(decision.get("final_answer") or fallback)
        return out

    def make_worker(role: str):
        def node(state: AgentState) -> AgentState:
            try:
                text = _run_agent(role, llm_for(role), tools_for(role), state)
            except Exception as exc:
                return {"current_agent": role, "failed": True, "error": f"{role} error: {exc}"}
            out: AgentState = {
                "current_agent": role,
                "history": state.get("history", []) + [{"agent": role, "output": text}],
            }
            if role == "validator":
                try:
                    out["validation_passed"] = bool(_parse_json(text).get("passed"))
                except Exception as exc:
                    out["validation_passed"] = False
                    out["error"] = f"validator reply unparseable: {exc}"
            return out

        return node

    def after_supervisor(state: AgentState) -> str:
        nxt = state.get("next", "finish")
        failed = state.get("failed")

        # Two separate things can want to send control to the validator:
        # the model directly asking for it, or `finish` being rejected
        # because nothing has been validated yet (below). Both must go
        # through the SAME "has real work happened yet" check -- routing
        # them through two independent conditions previously let the
        # finish->validator path skip it entirely, so a supervisor that
        # decided "finish" on its very first turn (no worker run at all)
        # still got silently redirected straight to the validator.
        wants_validator = nxt == "validator" or (
            nxt == "finish" and not failed and state.get("validation_passed") is None
        )
        if not failed and wants_validator:
            history_agents = {h.get("agent") for h in state.get("history", [])}
            worker_has_run = bool(history_agents & (set(WORKERS) - {"validator"}))
            if worker_has_run:
                return "validator"
            # Nothing has actually been worked on yet -- don't let the
            # validator (or a bare finish) short-circuit past real work.
            # "researcher" is the default redirect target rather than
            # "planner": its prompt is explicit about using
            # save_memory_entry/update_memory_entry, which is exactly what
            # the gate above exists to make happen, whereas the planner's
            # prompt treats saving as a conditional afterthought. Neither
            # is "correct" for every possible task -- the supervisor
            # regains full control on its very next turn and can route
            # wherever actually fits once there is real progress in
            # history to route from.
            return "researcher"

        return END if nxt == "finish" else nxt

    def after_worker(state: AgentState) -> str:
        return END if state.get("failed") else "supervisor"

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", after_supervisor, {**{w: w for w in WORKERS}, END: END})
    for role in WORKERS:
        graph.add_node(role, make_worker(role))
        graph.add_conditional_edges(role, after_worker, {"supervisor": "supervisor", END: END})
    return graph.compile()
