"""End-to-end verification: does a real workflow run persist a finding to
Supabase and does a later task actually see it in its memory context?

Deliberately split into two independent concerns, tested together but not
conflated:

  * The LLM layer is SCRIPTED (ScriptedLLM, like the unit workflow tests) --
    free, deterministic, no network, no NVIDIA call. Whether a real model
    *chooses* to call save_memory_entry is a separate, already-verified
    question (the live NVIDIA run); this test does not re-litigate that.
  * The tool/memory layer is 100% real: build_workflow() is called with its
    default tools_factory (app.tools.memory_tools.build_agent_tools), which
    talks to the real, configured Supabase project. This is the actual code
    path a live run uses -- nothing about it is faked.

This proves the mechanism -- identity-scoped save, real persistence, and
automatic retrieval on a later task -- works, independent of model choice.

Two separate build_workflow() calls are used for "task 1" and "task 2",
matching exactly what app.api.main.run_workflow() does per request (it also
calls build_workflow() fresh every time) -- so "a subsequent task" here means
exactly what it would in production: a new graph, new LLM/tool instances,
same persistent Supabase-backed memory underneath.

SCOPE / SAFETY:
  * Uses the real "researcher" agent identity (there is no throwaway agent
    identity available -- build_agent_tools() only accepts the five real
    roles, by design). It does NOT touch or create a memory_agents row:
    "researcher" is already permanently registered by migration 001.
  * Writes exactly ONE memory row, under a memory_key carrying a fresh uuid
    that cannot collide with any real memory key researcher already has.
    Cleanup deletes only that one row, filtered by that exact key -- every
    other memory researcher holds is untouched.
  * COST NOTE: save_memory_entry computes an embedding via embed_or_none()
    before saving. If OPENAI_API_KEY is configured, this test will make one
    real (tiny, cheap) OpenAI embeddings call as a side effect of exercising
    the real tool. If it is not configured, embed_or_none() returns None and
    the save proceeds without a vector, exactly as it would in production.

Opt-in, like every other dbwrite test:
    $env:MEMORY_ALLOW_WRITE = "1"
    .venv\\Scripts\\python.exe -m pytest tests/integration/test_live_workflow_persistence_e2e.py -v
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.workflow import ROLES, build_workflow

pytestmark = [
    pytest.mark.dbwrite,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MEMORY_ALLOW_WRITE", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="set MEMORY_ALLOW_WRITE=1 to run the database write tests",
    ),
]

RUN_ID = uuid.uuid4().hex[:12]
AGENT_ID = "researcher"  # a real, permanently-registered agent -- no throwaway identity exists
MEMORY_KEY = f"e2e_persistence_test_{RUN_ID}"
FINDING = f"AUTOMATED E2E PERSISTENCE TEST -- safe to delete -- run {RUN_ID} -- the sky is blue"


class ScriptedLLM(BaseChatModel):
    """Replays scripted AIMessages and records the exact prompts it received."""

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


def _ai(text="", tool_calls=None):
    return AIMessage(content=text, tool_calls=tool_calls or [])


def _decide(nxt, answer=""):
    return _ai(json.dumps({"next": nxt, "reason": "r", "final_answer": answer}))


def _llms(**scripts):
    return {r: ScriptedLLM(script=list(scripts.get(r, [])), calls=[]) for r in ROLES}


def _cleanup(client) -> None:
    """Remove only the one row this run creates, scoped by its unique key."""
    client.table("agent_memories").delete().eq("agent_id", AGENT_ID).eq("memory_key", MEMORY_KEY).execute()


def test_a_finding_saved_in_one_task_is_retrieved_automatically_in_the_next(supabase_client) -> None:
    _cleanup(supabase_client)  # defensive: debris from any crashed prior run

    try:
        # -- Task 1: the researcher saves a finding via its real, identity- --
        # -- scoped tool, hitting the real Supabase project. ----------------
        save_call = {
            "name": "save_memory_entry",
            "id": "c1",
            "args": {"memory_key": MEMORY_KEY, "content": FINDING},
        }
        llms_task1 = _llms(
            supervisor=[_decide("researcher"), _decide("validator"), _decide("finish", "noted")],
            researcher=[_ai("", [save_call]), _ai("saved the finding")],
            validator=[_ai('{"passed": true, "feedback": "ok"}')],
        )
        result1 = build_workflow(llms=llms_task1).invoke({"task": "Research a fact and save it."})

        assert not result1.get("failed"), f"task 1 failed: {result1.get('error')}"
        assert "researcher" in [h["agent"] for h in result1.get("history", [])]

        # Confirm the save actually landed in Supabase -- not trusting the
        # tool call alone, an independent read-after-write.
        row = (
            supabase_client.table("agent_memories")
            .select("*")
            .eq("agent_id", AGENT_ID)
            .eq("memory_key", MEMORY_KEY)
            .eq("status", "active")
            .execute()
            .data
        )
        assert len(row) == 1, "the researcher's save_memory_entry call did not persist to Supabase"
        assert row[0]["content"] == FINDING

        # -- Task 2: a brand-new workflow invocation (build_workflow() is --
        # -- called fresh, exactly like app.api.main.run_workflow() does  --
        # -- per request) -- does the researcher's *automatic* get_context --
        # -- call (made by _run_agent for every turn, not an LLM choice)  --
        # -- actually surface the finding saved under a *different* task? --
        llms_task2 = _llms(
            supervisor=[_decide("researcher"), _decide("validator"), _decide("finish", "recalled")],
            researcher=[_ai("I see a prior finding in memory.")],
            validator=[_ai('{"passed": true, "feedback": "ok"}')],
        )
        result2 = build_workflow(llms=llms_task2).invoke({"task": "What have you learned recently?"})

        assert not result2.get("failed"), f"task 2 failed: {result2.get('error')}"

        researcher_prompt = str(llms_task2["researcher"].calls[0][1].content)
        assert FINDING in researcher_prompt, (
            "the finding saved in task 1 was not present in the researcher's memory context "
            "on an unrelated, later task -- retrieval is not actually wired to persistence"
        )

    finally:
        _cleanup(supabase_client)
