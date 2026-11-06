"""Live progress events for a running chat request.

Pipeline code calls ``emit(stage, text)`` at real milestones (task saved,
agent started/finished, PO submitted). It is a no-op unless a caller has
installed a sink with ``capture`` -- the streaming chat route does, and
forwards each event to the browser. The sink lives in a ContextVar, so it
follows the request into LangGraph's worker threads (which copy context)
and never leaks between concurrent requests.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Optional

__all__ = ["emit", "capture"]

Sink = Callable[[str, str], None]

_sink: ContextVar[Optional[Sink]] = ContextVar("procurement_progress_sink", default=None)


def emit(stage: str, text: str) -> None:
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(stage, text)
    except Exception:
        pass  # a broken listener must never break the pipeline it observes


@contextmanager
def capture(sink: Sink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
