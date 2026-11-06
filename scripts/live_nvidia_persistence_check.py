"""Live, real-NVIDIA verification that a content-producing agent saves a
reusable finding and a separate, later workflow run retrieves it
automatically.

Unlike tests/integration/test_live_workflow_persistence_e2e.py (which
deliberately scripts the LLM to isolate the *mechanism* from model choice),
this script makes real calls to NVIDIA Nemotron 3.5 Lightning end to end, so
whether the model actually chooses to call save_memory_entry is a genuine,
non-deterministic observation -- not something this script can force. That is
exactly why this is a script with a printed verdict (PASS / INCONCLUSIVE /
FAIL), not a pytest test with a hard assertion: a model choosing not to save
this one time is not a code bug, and a hard-failing test would conflate the
two.

WHAT IT DOES
  Task 1 (real build_workflow(), real NVIDIA, real Supabase via the default
  identity-scoped tools): asks the Researcher, by an explicit task, to save
  one clearly-marked, uniquely-keyed test fact.
    -> read-only check against Supabase: did a matching row actually land?

  Task 2 (a SECOND, independent build_workflow() call -- same pattern
  app.api.main.run_workflow() uses per request): an unrelated-sounding task
  that never mentions the fact or the key. The Researcher's real chat model
  is wrapped in a thin recorder (RecordingLLM) that still makes the real
  call but also records the exact messages sent, so this script can check
  -- deterministically, not by trusting the model's reply -- whether the
  saved fact was actually present in the Researcher's memory context this
  time, on a different task.

SAFEGUARDS
  * A fresh uuid (RUN_ID) names both the memory key and the fact text, so
    this can never collide with or be confused with real data.
  * Cleanup matches *any* memory_key containing RUN_ID for the real
    "researcher" identity (not just the exact key asked for), in case the
    model saved under a slightly different key -- still 100% safe, since
    RUN_ID cannot appear in any pre-existing row. No other agent, key, or
    provenance row is touched.
  * No API key or secret value is ever printed -- only presence booleans.
  * Double opt-in, same convention as scripts/live_e2e_check.py: nothing
    beyond the free, read-only preflight runs unless BOTH
    MEMORY_ALLOW_WRITE=1 and RUN_LIVE_LLM_TEST=1 are set.

COST (reported again at runtime, from key presence only -- see main()):
  * ~6 real NVIDIA calls for task 1, ~5 for task 2 (supervisor routing +
    researcher + validator turns; mandatory validation applies).
  * One additional real OpenAI embeddings call IF AND ONLY IF
    OPENAI_API_KEY is configured (save_memory_entry calls embed_or_none()).
  * One real Supabase write (the test memory), cleaned up as described.

Usage:
  Preflight only (safe, no writes, no LLM calls, no cost):
    .venv\\Scripts\\python.exe scripts\\live_nvidia_persistence_check.py

  Full live run:
    $env:MEMORY_ALLOW_WRITE = "1"
    $env:RUN_LIVE_LLM_TEST = "1"
    .venv\\Scripts\\python.exe scripts\\live_nvidia_persistence_check.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Running this file directly puts its own directory (scripts/) on sys.path,
# not the project root -- so `import app...` fails unless the root is added
# explicitly. (`python -c "..."` doesn't have this problem: it uses the
# current working directory instead.)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

RUN_ID = uuid.uuid4().hex[:12]
AGENT_ID = "researcher"  # the real, permanently-registered agent -- no throwaway identity exists
MEMORY_KEY = f"e2e_nvidia_persist_{RUN_ID}"
FINDING = f"AUTOMATED LIVE NVIDIA PERSISTENCE CHECK -- safe to delete -- run {RUN_ID}"

TASK_1 = (
    f"The test constant is {RUN_ID}. Find out what it is, then remember it for later by saving "
    f"it to your memory under the key '{MEMORY_KEY}' with the content '{FINDING}'."
)
TASK_2 = "What is your top priority today?"  # deliberately unrelated -- never mentions the fact or key


def _present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class RecordingLLM:
    """Wraps a real chat model: makes real calls, also records every
    message list it was invoked with, so a later check can inspect exactly
    what the model was shown -- without faking the model itself."""

    def __init__(self, inner, calls=None):
        self._inner = inner
        self.calls = calls if calls is not None else []

    def bind_tools(self, tools, **kwargs):
        return RecordingLLM(self._inner.bind_tools(tools, **kwargs), calls=self.calls)

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return self._inner.invoke(messages, **kwargs)


def print_preflight() -> None:
    print("== Preflight (presence only -- values are never printed) ==")
    nvidia_ok = _present("NVIDIA_API_KEY")
    openai_ok = _present("OPENAI_API_KEY")
    print(f"  NVIDIA_API_KEY   {'set' if nvidia_ok else 'MISSING'}")
    print(f"  OPENAI_API_KEY   {'set' if openai_ok else 'not set'}")
    print(f"  SUPABASE_URL     {'set' if _present('SUPABASE_URL') else 'MISSING'}")
    print(f"  SUPABASE_KEY     {'set' if _present('SUPABASE_KEY') else 'MISSING'}")
    print()
    print("Estimated cost of a full run:")
    print("  ~11 real NVIDIA Nemotron 3.5 Lightning calls total (task 1 + task 2)")
    if openai_ok:
        print("  1 real OpenAI embeddings call WILL occur (OPENAI_API_KEY is set, used by save_memory_entry)")
    else:
        print("  0 OpenAI embeddings calls (OPENAI_API_KEY is not set; the save proceeds without a vector)")
    print("  1 real Supabase write (one memory row), cleaned up by this script before it exits")
    print()


def _cleanup(client) -> int:
    """Delete only rows for the real researcher agent whose memory_key
    contains this run's RUN_ID. Broad on purpose (covers the model saving
    under a slightly different key than asked), still 100% safe: RUN_ID is a
    fresh uuid that cannot appear in any pre-existing row."""
    matches = (
        client.table("agent_memories")
        .select("id")
        .eq("agent_id", AGENT_ID)
        .ilike("memory_key", f"%{RUN_ID}%")
        .execute()
        .data
        or []
    )
    ids = [row["id"] for row in matches]
    if ids:
        client.table("memory_provenance").delete().in_("memory_id", ids).execute()
        client.table("memory_provenance").delete().in_("parent_memory_id", ids).execute()
        client.table("agent_memories").delete().in_("id", ids).execute()
    return len(ids)


def run() -> int:
    from app.agents.workflow import build_workflow, get_llm
    from app.memory.client import supabase

    print(f"RUN_ID: {RUN_ID}")
    print(f"memory key requested: {MEMORY_KEY}\n")

    deleted_before = _cleanup(supabase)
    if deleted_before:
        print(f"(cleaned up {deleted_before} row(s) from a previous crashed run first)\n")

    verdict = "FAIL"
    try:
        # -- Task 1: real workflow, real NVIDIA, real Supabase tools --------
        print("== Task 1: asking the Researcher to save a finding ==")
        print(f"task: {TASK_1!r}\n")
        result1 = build_workflow().invoke({"task": TASK_1})

        print("-- task 1 history --")
        for step in result1.get("history", []) or []:
            print(f"  [{step.get('agent')}] {step.get('output')}")
        print(f"failed: {result1.get('failed', False)}")
        if result1.get("error"):
            print(f"error: {result1['error']}")

        if result1.get("failed"):
            verdict = "FAIL"
            print("\nVerdict: FAIL -- the workflow itself failed on task 1 (see error above).")
            return 1

        saved_rows = (
            supabase.table("agent_memories")
            .select("*")
            .eq("agent_id", AGENT_ID)
            .ilike("memory_key", f"%{RUN_ID}%")
            .eq("status", "active")
            .execute()
            .data
            or []
        )
        if not saved_rows:
            verdict = "INCONCLUSIVE"
            print(
                "\nVerdict: INCONCLUSIVE -- the Researcher did not save anything matching this "
                "run. This is model behavior, not necessarily a code bug: real models don't always "
                "comply with a tool-use instruction the same way twice. Try re-running."
            )
            return 0

        print(f"\nFound {len(saved_rows)} saved row(s) matching this run:")
        for row in saved_rows:
            print(f"  memory_key={row['memory_key']!r} content={row['content']!r}")

        # -- Task 2: a SEPARATE real workflow run, unrelated task -----------
        print("\n== Task 2: a separate, unrelated task (new build_workflow() call) ==")
        print(f"task: {TASK_2!r}\n")

        recorder = RecordingLLM(get_llm("researcher"))
        result2 = build_workflow(llms={"researcher": recorder}).invoke({"task": TASK_2})

        print("-- task 2 history --")
        for step in result2.get("history", []) or []:
            print(f"  [{step.get('agent')}] {step.get('output')}")
        print(f"failed: {result2.get('failed', False)}")
        if result2.get("error"):
            print(f"error: {result2['error']}")

        if result2.get("failed"):
            verdict = "FAIL"
            print("\nVerdict: FAIL -- the workflow itself failed on task 2 (see error above).")
            return 1

        if not recorder.calls:
            verdict = "FAIL"
            print("\nVerdict: FAIL -- the Researcher was never actually invoked on task 2.")
            return 1

        researcher_prompt = str(recorder.calls[0][1].content)
        found_in_prompt = any(row["content"] in researcher_prompt for row in saved_rows)

        if found_in_prompt:
            verdict = "PASS"
            print(
                "\nVerdict: PASS -- the finding saved in task 1 was present in the Researcher's "
                "memory context on an unrelated task 2. Persistence -> automatic retrieval is "
                "verified live, end to end."
            )
            return 0

        verdict = "FAIL"
        print(
            "\nVerdict: FAIL -- the finding was saved in task 1 but was NOT present in the "
            "Researcher's actual task-2 prompt. The save worked; automatic retrieval did not. "
            "This points at get_relevant_context() / freshness filtering, not the LLM."
        )
        print("\n-- actual task 2 researcher prompt, for diagnosis --")
        print(researcher_prompt)
        return 1

    finally:
        deleted_after = _cleanup(supabase)
        print(f"\nCleanup: removed {deleted_after} row(s) for this run. Final verdict: {verdict}")


def main() -> int:
    print_preflight()

    if not _present("NVIDIA_API_KEY") or not _present("SUPABASE_URL") or not _present("SUPABASE_KEY"):
        print("Missing required configuration above -- fix before running the live check.")
        return 1

    if not (_flag("MEMORY_ALLOW_WRITE") and _flag("RUN_LIVE_LLM_TEST")):
        print(
            "Preflight only -- nothing was written, no LLM was called, no money was spent.\n\n"
            "To run the real check, set BOTH:\n"
            '  $env:MEMORY_ALLOW_WRITE = "1"\n'
            '  $env:RUN_LIVE_LLM_TEST = "1"\n'
            "and re-run this script."
        )
        return 0

    print("*** LIVE RUN: MEMORY_ALLOW_WRITE=1 and RUN_LIVE_LLM_TEST=1 are both set. ***\n")
    return run()


if __name__ == "__main__":
    sys.exit(main())
