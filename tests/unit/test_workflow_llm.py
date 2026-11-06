"""LLM-driven workflow and identity-scoped tools, with fake models (no network, no DB)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.workflow import NVIDIA_MAX_COMPLETION_TOKENS, ROLES, build_workflow, get_llm
from app.tools import memory_tools
from app.tools.memory_tools import NotAuthorized, build_agent_tools


class ScriptedLLM(BaseChatModel):
    """Replays scripted AIMessages and records the prompts it receives."""

    script: list
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        msg = self.script.pop(0) if self.script else AIMessage(content="{}")
        return ChatResult(generations=[ChatGeneration(message=msg)])


class BoomLLM(ScriptedLLM):
    def _generate(self, *args, **kwargs):
        raise RuntimeError("llm down")


def _ai(text="", tool_calls=None):
    return AIMessage(content=text, tool_calls=tool_calls or [])


def _decide(nxt, answer=""):
    return _ai(json.dumps({"next": nxt, "reason": "r", "final_answer": answer}))


def _llms(**scripts):
    return {r: ScriptedLLM(script=list(scripts.get(r, [])), calls=[]) for r in ROLES}


def _no_tools(role):
    return []


def test_full_run_is_llm_driven():
    llms = _llms(
        supervisor=[_decide("planner"), _decide("executor"), _decide("validator"), _decide("finish", "done!")],
        planner=[_ai("1. do it")],
        executor=[_ai("did it")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "anything"})
    assert out["result"] == "done!"
    assert out["validation_passed"] is True
    assert [h["agent"] for h in out["history"]] == ["planner", "executor", "validator"]
    assert "1. do it" in llms["executor"].calls[0][1].content


def test_bare_finish_on_first_turn_is_redirected_to_a_worker_not_validator():
    """Regression test for a real bug found via live NVIDIA runs: the
    supervisor's very first decision is "finish", with nothing worked on
    and nothing validated. The old finish->validator gate converted this
    straight to "validator" without checking whether any worker had run,
    completely bypassing the validator-needs-a-worker gate -- the exact
    failure observed four times live (history always ["validator"], empty
    otherwise)."""
    llms = _llms(
        supervisor=[
            _decide("finish", "premature"),
            _decide("executor"),
            _decide("validator"),
            _decide("finish", "done"),
        ],
        executor=[_ai("did it")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert [h["agent"] for h in out["history"]] == ["researcher", "executor", "validator"]
    assert out["result"] == "done"


def test_validator_is_blocked_until_a_worker_has_run():
    """The supervisor tries to send straight to the validator with nothing
    done yet -- the graph must not honor that; it forces a real worker
    (planner, researcher, or executor) to run first."""
    llms = _llms(
        supervisor=[
            _decide("validator"),
            _decide("executor"),
            _decide("validator"),
            _decide("finish", "done"),
        ],
        executor=[_ai("did it")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert [h["agent"] for h in out["history"]] == ["researcher", "executor", "validator"]
    assert out["validation_passed"] is True


def test_finish_is_blocked_until_validator_has_run():
    """The supervisor tries to finish right after the executor, skipping the
    validator entirely -- the graph must not honor that attempt; it forces a
    validator run before `finish` is allowed to actually end the graph."""
    llms = _llms(
        supervisor=[_decide("executor"), _decide("finish", "x"), _decide("finish", "y")],
        executor=[_ai("did it")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert [h["agent"] for h in out["history"]] == ["executor", "validator"]
    assert out["validation_passed"] is True
    assert out["result"] == "y"  # the second, actually-honored finish


def test_finish_fallback_skips_the_validators_own_json():
    """When the supervisor finishes without an explicit final_answer, the
    fallback must be the last real work product (e.g. the executor's
    output), never the validator's own {"passed": ...} verdict -- the
    validator almost always runs immediately before finishing."""
    llms = _llms(
        supervisor=[_decide("executor"), _decide("validator"), _decide("finish", "")],
        executor=[_ai("the greeting is: hello!")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert out["result"] == "the greeting is: hello!"
    assert '"passed"' not in out["result"]


def test_routing_ignores_task_keywords():
    llms = _llms(
        supervisor=[_decide("researcher"), _decide("validator"), _decide("finish", "x")],
        researcher=[_ai("facts")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "execute fail plan"})
    assert [h["agent"] for h in out["history"]] == ["researcher", "validator"]


def test_invalid_supervisor_reply_fails_closed():
    llms = _llms(supervisor=[_ai("not json")])
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert out["failed"] is True and "supervisor" in out["error"]


def test_unknown_next_agent_fails():
    llms = _llms(supervisor=[_decide("hacker")])
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert out["failed"] is True


def test_step_cap_bounds_loops():
    llms = _llms(supervisor=[_decide("planner")] * 20, planner=[_ai("p")] * 20)
    out = build_workflow(llms=llms, tools_factory=_no_tools, max_steps=3).invoke({"task": "t"})
    assert out["failed"] is True and out["error"] == "max steps exceeded"
    assert len(out["history"]) == 3


def test_worker_exception_stops_run():
    llms = _llms(supervisor=[_decide("planner")])
    llms["planner"] = BoomLLM(script=[], calls=[])
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert out["failed"] is True and "llm down" in out["error"]


def test_unparseable_validator_is_not_a_pass():
    llms = _llms(supervisor=[_decide("validator"), _decide("finish", "x")], validator=[_ai("looks good")])
    out = build_workflow(llms=llms, tools_factory=_no_tools).invoke({"task": "t"})
    assert out["validation_passed"] is False


def test_tool_call_uses_own_identity_only():
    call = {"name": "save_memory_entry", "id": "c1", "args": {"memory_key": "k", "content": "c"}}
    llms = _llms(
        supervisor=[_decide("researcher"), _decide("finish", "x")],
        researcher=[_ai("", [call]), _ai("saved")],
    )
    with patch("app.tools.memory_tools.get_relevant_context", return_value=[]), patch(
        "app.tools.memory_tools.save_memory", return_value={"id": "m1"}
    ) as save:
        build_workflow(llms=llms).invoke({"task": "t"})
    assert save.call_args.args[0] == "researcher"
    assert save.call_args.kwargs["source_agent_id"] == "researcher"


def test_tool_errors_are_returned_to_the_model_not_raised():
    call = {"name": "memory_ancestry", "id": "c1", "args": {"memory_id": "other"}}
    llms = _llms(
        supervisor=[_decide("executor"), _decide("validator"), _decide("finish", "x")],
        executor=[_ai("", [call]), _ai("gave up")],
        validator=[_ai('{"passed": true, "feedback": "ok"}')],
    )
    with patch("app.tools.memory_tools.get_relevant_context", return_value=[]), patch(
        "app.tools.memory_tools.get_memory", return_value={"agent_id": "planner"}
    ):
        out = build_workflow(llms=llms).invoke({"task": "t"})
    assert not out.get("failed")
    assert "not owned by executor" in str(llms["executor"].calls[-1][-1].content)


def test_context_failure_does_not_crash_agent():
    llms = _llms(supervisor=[_decide("planner"), _decide("finish", "x")], planner=[_ai("p")])
    with patch("app.tools.memory_tools.get_relevant_context", side_effect=RuntimeError("db off")):
        out = build_workflow(llms=llms).invoke({"task": "t"})
    assert out["history"][0]["output"] == "p"
    assert "unavailable" in llms["planner"].calls[0][1].content


# -- identity-scoped tools ---------------------------------------------------


def test_tools_do_not_expose_agent_id_argument():
    for tool in build_agent_tools("planner"):
        assert "agent_id" not in tool.args


def test_unknown_identity_rejected():
    with pytest.raises(ValueError):
        build_agent_tools("mallory")


def test_share_requires_ownership_and_valid_target():
    tools = {t.name: t for t in build_agent_tools("planner")}
    with patch.object(memory_tools, "get_memory", return_value={"agent_id": "executor"}), patch.object(
        memory_tools, "share_memory"
    ) as share:
        with pytest.raises(NotAuthorized):
            tools["share_memory_entry"].invoke({"source_memory_id": "m", "target_agent_id": "validator"})
        share.assert_not_called()
    with patch.object(memory_tools, "get_memory", return_value={"agent_id": "planner"}), patch.object(
        memory_tools, "share_memory", return_value={"id": "n"}
    ) as share:
        for bad in ("planner", "mallory"):
            with pytest.raises(NotAuthorized):
                tools["share_memory_entry"].invoke({"source_memory_id": "m", "target_agent_id": bad})
        tools["share_memory_entry"].invoke({"source_memory_id": "m", "target_agent_id": "validator"})
        share.assert_called_once_with("m", "validator")


def test_get_llm_env_override(monkeypatch):
    monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda m, **k: m)
    monkeypatch.setenv("AGENT_LLM_MODEL", "openai:base")
    monkeypatch.setenv("AGENT_LLM_MODEL_PLANNER", "openai:special")
    assert get_llm("planner") == "openai:special"
    assert get_llm("executor") == "openai:base"


def test_get_llm_defaults_to_nvidia_nemotron_with_speed_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "langchain.chat_models.init_chat_model",
        lambda model, **kwargs: calls.append((model, kwargs)) or object(),
    )
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LLM_MODEL_RESEARCHER", raising=False)

    get_llm("researcher")

    (model, kwargs), = calls
    assert model == "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b"
    assert kwargs["temperature"] == 0
    assert kwargs["max_completion_tokens"] == NVIDIA_MAX_COMPLETION_TOKENS
    assert kwargs["model_kwargs"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_get_llm_only_applies_nvidia_kwargs_to_nvidia_models(monkeypatch):
    """A role overridden to a non-NVIDIA provider must not receive NVIDIA-only kwargs."""
    calls = []
    monkeypatch.setattr(
        "langchain.chat_models.init_chat_model",
        lambda model, **kwargs: calls.append((model, kwargs)) or object(),
    )
    monkeypatch.setenv("AGENT_LLM_MODEL_PLANNER", "openai:gpt-4o-mini")

    get_llm("planner")

    (model, kwargs), = calls
    assert model == "openai:gpt-4o-mini"
    assert "max_completion_tokens" not in kwargs
    assert "model_kwargs" not in kwargs
    assert kwargs == {"temperature": 0}
