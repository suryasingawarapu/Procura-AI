"""Langfuse tracing following the platform standard (langfuse.md).

Identity (guide section 1): ONE slug (``AGENT_ID``), used in the trace name, the
tags and the metadata, because the trace is the only record of which project
spent the money.

* name      ``"<Display Name> - <step>"``  (cost is attributed by the part before " - ")
* tags      ``agent:<slug>`` and ``env:<environment>``
* metadata  ``{"slug": ..., "kind": "agent"}``
* user_id   the signed-in user's email (``X-User-Id``) -- never a constant
* session   the platform / chat conversation id

Never put a retry counter, version, run id, timestamp or user id in a name.

Everything here is a no-op when Langfuse is not configured or
``LANGFUSE_ENABLED=0`` (unit tests), and nothing here may raise into the app:
tracing must never be the reason a request fails.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, Iterator, Optional

__all__ = ["DISPLAY", "SLUG", "enabled", "trace", "span", "tool_span", "llm_config", "mark", "flush", "status"]

DISPLAY = "Procurement Coordinator"  # stable and human-readable; must not contain " - "
_MAX_INPUT = 1500

_state: Dict[str, Any] = {"ready": None}


def _slug() -> str:
    return os.getenv("AGENT_ID", "procurement-coordinator").strip() or "procurement-coordinator"


SLUG = _slug()


def _env() -> str:
    return os.getenv("ENVIRONMENT", "development").strip() or "development"


def configured() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def enabled() -> bool:
    """Tracing is on when keys exist and it was not switched off (``LANGFUSE_ENABLED=0``)."""
    if os.getenv("LANGFUSE_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return configured()


def _capture_content() -> bool:
    return os.getenv("LANGFUSE_CAPTURE_CONTENT", "1").strip().lower() not in {"0", "false", "no", "off"}


def _ready() -> bool:
    """Create the shared Langfuse client once. False (and silent) if anything is wrong."""
    if not enabled():
        return False
    if _state["ready"] is not None:
        return _state["ready"]
    try:
        from langfuse import Langfuse

        kwargs: Dict[str, Any] = {}
        if not _capture_content():
            # Whatever is sent is stored on a third party's servers: this withholds the content, keeps the shape.
            kwargs["mask"] = lambda data=None, **_k: "[withheld]"
        Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            base_url=os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com",
            environment=_env(),
            **kwargs,
        )
        _state["ready"] = True
    except Exception:
        _state["ready"] = False
    return _state["ready"]


def _client():
    from langfuse import get_client

    return get_client()


def _clip(value: Any) -> Any:
    if not _capture_content():
        return None
    if isinstance(value, str) and len(value) > _MAX_INPUT:
        return value[:_MAX_INPUT] + "..."
    return value


class _Handle:
    """What a traced block can do to its own observation. Every method is safe to call."""

    def __init__(self, observation: Any = None):
        self._obs = observation

    def update(self, **fields: Any) -> None:
        if self._obs is None:
            return
        try:
            if "output" in fields:
                fields["output"] = _clip(fields["output"])
            self._obs.update(**fields)
        except Exception:
            pass

    def error(self, message: str) -> None:
        self.update(level="ERROR", status_message=str(message)[:500])

    def warning(self, message: str) -> None:
        self.update(level="WARNING", status_message=str(message)[:500])


_NULL = _Handle()


def _level_for(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return "WARNING" if isinstance(status, int) and status < 500 else "ERROR"


@contextmanager
def _observation(name: str, as_type: str, input: Any, stack: ExitStack) -> Iterator[_Handle]:
    handle = _NULL
    try:
        obs = stack.enter_context(_client().start_as_current_observation(name=name, as_type=as_type, input=_clip(input)))
        handle = _Handle(obs)
    except Exception:
        pass
    try:
        yield handle
    except BaseException as exc:  # an exception in the traced block is recorded, never swallowed
        getattr(handle, "warning" if _level_for(exc) == "WARNING" else "error")(f"{type(exc).__name__}: {exc}")
        raise


@contextmanager
def trace(
    step: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    input: Any = None,
    metadata: Optional[Dict[str, str]] = None,
) -> Iterator[_Handle]:
    """The root of one request: ``"Procurement Coordinator - <step>"`` with the identity fields."""
    if not _ready():
        yield _NULL
        return
    name = f"{DISPLAY} - {step}"
    with ExitStack() as stack:
        try:
            from langfuse import propagate_attributes

            stack.enter_context(
                propagate_attributes(
                    trace_name=name,
                    user_id=user_id or None,
                    session_id=session_id or None,
                    tags=[f"agent:{_slug()}", f"env:{_env()}"],
                    metadata={"slug": _slug(), "kind": "agent", **{k: str(v)[:200] for k, v in (metadata or {}).items()}},
                    environment=_env(),
                )
            )
        except Exception:
            pass
        with _observation(name, "span", input, stack) as handle:
            yield handle


@contextmanager
def span(step: str, *, input: Any = None, as_type: str = "span") -> Iterator[_Handle]:
    """A step inside a trace, named ``"Procurement Coordinator - <step>"``."""
    if not _ready():
        yield _NULL
        return
    with ExitStack() as stack:
        with _observation(f"{DISPLAY} - {step}", as_type, input, stack) as handle:
            yield handle


def tool_span(name: str, *, input: Any = None):
    """A call to an external tool (ERPNext, ...)."""
    return span(name, input=input, as_type="tool")


def llm_config(step: str) -> Dict[str, Any]:
    """LangChain ``config`` that traces one model call as a generation named
    ``"Procurement Coordinator - <step>"`` (the console attributes cost by that name).
    Empty when tracing is off, so call sites pass nothing extra."""
    if not _ready():
        return {}
    try:
        from langfuse.langchain import CallbackHandler

        return {"callbacks": [CallbackHandler()], "run_name": f"{DISPLAY} - {step}"}
    except Exception:
        return {}


def mark(level: str, message: str) -> None:
    """Mark the current observation (``ERROR`` / ``WARNING``) so it reaches the Errors page."""
    if not _ready():
        return
    try:
        _client().update_current_span(level=level, status_message=message[:500])
    except Exception:
        pass


def flush() -> None:
    """Send whatever is buffered (call on shutdown: batches are sent in the background)."""
    if _state["ready"]:
        try:
            _client().flush()
        except Exception:
            pass


def status() -> Dict[str, Any]:
    return {
        "configured": configured(),
        "enabled": enabled(),
        "slug": _slug(),
        "environment": _env(),
        "base_url": os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com",
        "capture_content": _capture_content(),
    }


def llm_kwargs(step: str) -> Dict[str, Any]:
    """``llm.invoke(prompt, **llm_kwargs(step))``: ``{"config": ...}`` when tracing, else nothing."""
    cfg = llm_config(step)
    return {"config": cfg} if cfg else {}
