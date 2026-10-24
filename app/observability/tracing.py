"""Langfuse tracing integration (optional)."""

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


def trace_workflow_run(task: str, result: Dict[str, Any]) -> None:
    """Trace a workflow run to Langfuse if configured."""
    tracer = get_tracer()
    if tracer is None:
        return
    
    try:
        trace = tracer.trace(name="multi_agent_workflow", input={"task": task})
        trace.span(name="workflow_execution", output=result)
        tracer.flush()
    except Exception:
        pass
