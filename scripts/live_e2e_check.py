"""Live end-to-end check for the Provenance-Aware Shared Memory project.

Phase 1 -- preflight (always runs, read-only):
  * confirms the required environment variables are present (their VALUES
    are never printed or logged)
  * does one read-only GET against Supabase's PostgREST OpenAPI schema to
    confirm the project is reachable and the six atomic memory functions
    exist (migrations 001 + 002 + 004 applied)
  * reports which optional capabilities (embeddings, tracing) are configured

Phase 2 -- live run (writes to Supabase, calls a real LLM, costs money):
  only runs if BOTH of these are set, so starting it is an explicit,
  deliberate act -- this script will never do it on its own:
      MEMORY_ALLOW_WRITE=1       (existing project convention for DB writes)
      RUN_LIVE_LLM_TEST=1        (new: specifically for this paid LLM call)

  IMPORTANT: unlike tests/integration/test_dbwrite_memory_atomic.py, this
  run has no throwaway/sandbox agent identity available. build_agent_tools()
  only accepts the five real agent ids (supervisor/planner/researcher/
  executor/validator) by design, so if an agent decides to save, update or
  share a memory, it writes into your actual agent_memories rows under
  those real identities -- exactly as a production run would. Review the
  printed before/after memory counts below.

  A workflow-level failure (failed=True) is reported and this script exits
  1. It is never swallowed or reported as a pass.

Usage:
  Read-only preflight only (safe, no writes, no LLM call, no cost):
    .venv\\Scripts\\python.exe scripts\\live_e2e_check.py

  Full live run (writes + costs money -- read the warning above first):
    $env:MEMORY_ALLOW_WRITE = "1"
    $env:RUN_LIVE_LLM_TEST = "1"
    .venv\\Scripts\\python.exe scripts\\live_e2e_check.py --task "..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
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

REQUIRED_ALWAYS = ["SUPABASE_URL", "SUPABASE_KEY", "MEMORY_API_TOKEN"]
RECOMMENDED = ["OPENAI_API_KEY"]
OPTIONAL_PAIRS = [("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")]

AGENT_IDS = ("supervisor", "planner", "researcher", "executor", "validator")

EXPECTED_FUNCTIONS = {
    "save_memory_atomic",
    "supersede_memory_atomic",
    "share_memory_atomic",
    "get_ancestry",
    "get_descendants",
    "match_agent_memories",
}


def _present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def check_env() -> bool:
    print("== Environment variables (presence only -- values are never printed) ==")
    ok = True
    for name in REQUIRED_ALWAYS:
        present = _present(name)
        ok = ok and present
        print(f"  {name:28s} {'set' if present else 'MISSING'}")
    for name in RECOMMENDED:
        present = _present(name)
        note = "set" if present else "not set (embeddings and the default LLM provider need it)"
        print(f"  {name:28s} {note}")
    for pub, sec in OPTIONAL_PAIRS:
        both = _present(pub) and _present(sec)
        print(f"  {pub} / {sec}: {'configured' if both else 'not configured -- tracing will be a no-op'}")
    model = os.getenv("AGENT_LLM_MODEL", "openai:gpt-4o-mini")
    print(f"  AGENT_LLM_MODEL (default for every role): {model}")
    return ok


def check_supabase_schema() -> bool:
    print("\n== Supabase connectivity + schema (one read-only GET) ==")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("  SKIPPED: SUPABASE_URL / SUPABASE_KEY not set")
        return False

    request = urllib.request.Request(
        url.rstrip("/") + "/rest/v1/",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/openapi+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            schema = json.load(response)
    except Exception as exc:
        print(f"  FAILED to reach Supabase: {type(exc).__name__}: {exc}")
        return False

    paths = schema.get("paths", {})
    functions = sorted(p.split("/")[-1] for p in paths if p.startswith("/rpc/"))
    missing = EXPECTED_FUNCTIONS - set(functions)

    print("  reachable: yes")
    print(f"  RPC functions found: {functions}")
    if missing:
        print(f"  MISSING (apply migrations 001, 002, 004 in that order): {sorted(missing)}")
        return False
    print("  all six atomic memory functions are present")
    return True


def memory_snapshot(client) -> dict:
    """Active memory count per agent (read-only)."""
    counts: dict[str, object] = {}
    for agent_id in AGENT_IDS:
        try:
            resp = (
                client.table("agent_memories")
                .select("id", count="exact")
                .eq("agent_id", agent_id)
                .eq("status", "active")
                .execute()
            )
            counts[agent_id] = getattr(resp, "count", None) or len(resp.data or [])
        except Exception as exc:  # e.g. the agent has never been registered yet
            counts[agent_id] = f"error: {exc}"
    return counts


def run_live_workflow(task: str) -> int:
    from app.agents.workflow import build_workflow
    from app.memory.client import supabase
    from app.observability.tracing import get_tracer, trace_workflow_run

    print("\n== Before: active memory count per agent (read-only) ==")
    before = memory_snapshot(supabase)
    for agent_id, count in before.items():
        print(f"  {agent_id:12s} {count}")

    tracer_configured = get_tracer() is not None
    print(f"\nLangfuse tracer configured: {tracer_configured}")

    print(f"\n== Running the live five-agent workflow ==\ntask: {task!r}\n")
    workflow = build_workflow()
    result = workflow.invoke({"task": task})

    print("-- history --")
    for step in result.get("history", []) or []:
        print(f"  [{step.get('agent')}] {step.get('output')}")
    print(f"\nresult:             {result.get('result')!r}")
    print(f"validation_passed:  {result.get('validation_passed')}")
    print(f"failed:             {result.get('failed', False)}")
    if result.get("error"):
        print(f"error:              {result['error']}")

    trace_workflow_run(task, result)
    if tracer_configured:
        print("\nTrace sent (best-effort) -- check your Langfuse project for 'multi_agent_workflow'.")

    print("\n== After: active memory count per agent (read-only) ==")
    after = memory_snapshot(supabase)
    for agent_id, count in after.items():
        changed = " <-- changed" if after[agent_id] != before.get(agent_id) else ""
        print(f"  {agent_id:12s} {count}{changed}")

    if result.get("failed"):
        print("\nFAILED: the workflow reported failed=True. See 'error' above.")
        return 1
    print("\nOK: the workflow completed without an internal failure.")
    print("This does not by itself prove every feature ran -- only the history above shows what")
    print("actually happened (e.g. whether any agent chose to save/update/share a memory).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--task",
        default="Research the current price of a widget, save your finding to memory, "
        "then have the executor and validator confirm it.",
        help="the task handed to the supervisor",
    )
    args = parser.parse_args()

    env_ok = check_env()
    schema_ok = check_supabase_schema()

    if not (env_ok and schema_ok):
        print("\nPreflight found problems above. Fix them before running the live workflow.")
        return 1

    if not (_flag("MEMORY_ALLOW_WRITE") and _flag("RUN_LIVE_LLM_TEST")):
        print(
            "\nPreflight OK. Read-only checks only -- nothing was written, no LLM was called, "
            "no money was spent.\n\n"
            "To run the real workflow (writes to your live Supabase project under the real agent "
            "identities, and calls your configured LLM provider -- this costs money), set BOTH:\n"
            '  $env:MEMORY_ALLOW_WRITE = "1"\n'
            '  $env:RUN_LIVE_LLM_TEST = "1"\n'
            "and re-run this script."
        )
        return 0

    print(
        "\n*** LIVE RUN: MEMORY_ALLOW_WRITE=1 and RUN_LIVE_LLM_TEST=1 are both set. ***\n"
        "This calls your real LLM provider and may write to agent_memories under the real\n"
        "supervisor/planner/researcher/executor/validator identities.\n"
    )
    return run_live_workflow(args.task)


if __name__ == "__main__":
    sys.exit(main())
