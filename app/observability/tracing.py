"""Langfuse tracing integration (optional).

The installed SDK is Langfuse 4.x, whose client is OTEL-based: ``Langfuse``
no longer has ``.trace()`` / ``trace.span()`` (that was the 2.x client API).
Observations are created and ended explicitly instead, through
``start_observation`` (on the client for a root span, on a span for a child)
and ``.end()`` / ``.update()``. See ``_trace_v3_plus`` below.

A 2.x client is also supported (``_trace_v2``) in case an older SDK is ever
installed, since the two APIs do not overlap and can be told apart with
``hasattr``. Either way this module must stay a no-op when Langfuse is not
installed or not configured, and must never raise into the caller.
"""

from __future__ import annotations

import os
from typing import Any, Dict


__all__ = ["trace_workflow_run", "get_tracer"]


_tracer = None


def get_tracer():
    """Get Langfuse tracer if available."""
    global _tracer
    if _tracer is not None:
        return _tracer

    try:
        from langfuse import Langfuse
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        if public_key and secret_key:
            _tracer = Langfuse(public_key=public_key, secret_key=secret_key)
            return _tracer
    except Exception:
        pass

    return None


def _run_outcome(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "result": result.get("result"),
        "failed": result.get("failed", False),
        "validation_passed": result.get("validation_passed"),
        "error": result.get("error"),
    }


def _trace_v3_plus(tracer: Any, task: str, result: Dict[str, Any]) -> None:
    """Langfuse SDK 3.x/4.x: ``start_observation`` / ``update`` / ``end``."""
    root = tracer.start_observation(name="multi_agent_workflow", as_type="span", input={"task": task})
    try:
        for entry in result.get("history", []) or []:
            child = root.start_observation(
                name=f"agent:{entry.get('agent', 'unknown')}",
                as_type="span",
                output=entry.get("output"),
            )
            child.end()
        root.update(output=_run_outcome(result))
    finally:
        root.end()


def _trace_v2(tracer: Any, task: str, result: Dict[str, Any]) -> None:
    """Langfuse SDK 2.x: ``trace()`` / ``trace.span()``."""
    trace = tracer.trace(name="multi_agent_workflow", input={"task": task})
    for entry in result.get("history", []) or []:
        trace.span(name=f"agent:{entry.get('agent', 'unknown')}", output=entry.get("output"))
    trace.span(name="workflow_execution", output=_run_outcome(result))


def trace_workflow_run(task: str, result: Dict[str, Any]) -> None:
    """Trace a workflow run to Langfuse if configured.

    Records one observation per agent step (in order) plus a final
    update with the run's outcome, so a trace shows which agents ran and
    what the workflow ended with. Dispatches on the client shape so both
    the current SDK (3.x/4.x) and the older 2.x client work. Never raises:
    tracing must not affect the workflow result.
    """
    tracer = get_tracer()
    if tracer is None:
        return

    try:
        if hasattr(tracer, "start_observation"):
            _trace_v3_plus(tracer, task, result)
        elif hasattr(tracer, "trace"):
            _trace_v2(tracer, task, result)
        tracer.flush()
    except Exception:
        pass
