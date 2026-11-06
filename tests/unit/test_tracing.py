"""Unit tests for Langfuse SDK compatibility in app.observability.tracing.

The installed SDK (4.x) replaced the 2.x client's ``trace()``/``span()`` with
an OTEL-based ``start_observation``/``update``/``end`` API. These tests use
fake tracer doubles shaped like each client -- never the real package -- so
they run with no network access and no Langfuse keys configured.
"""

from __future__ import annotations

import pytest

import app.observability.tracing as tracing
from app.observability.tracing import get_tracer, trace_workflow_run


@pytest.fixture(autouse=True)
def _reset_tracer_cache(monkeypatch):
    """The module caches the tracer in a global; keep tests isolated."""
    monkeypatch.setattr(tracing, "_tracer", None, raising=False)
    yield


RESULT = {
    "result": "done!",
    "failed": False,
    "validation_passed": True,
    "error": None,
    "history": [
        {"agent": "planner", "output": "1. do it"},
        {"agent": "executor", "output": "did it"},
    ],
}


# -- fakes shaped like each Langfuse client generation -----------------------


class FakeV4Span:
    """Stand-in for LangfuseSpan (4.x): manual start_observation/update/end."""

    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.ended = False
        self.updated_with = None
        self.children: list["FakeV4Span"] = []

    def start_observation(self, *, name, as_type="span", output=None, **kw):
        child = FakeV4Span(name, as_type=as_type, output=output, **kw)
        self.children.append(child)
        return child

    def update(self, *, output=None, **kw):
        self.updated_with = output

    def end(self, **kw):
        self.ended = True


class FakeV4Tracer:
    """Stand-in for the 4.x ``Langfuse`` client. Deliberately has no ``.trace``."""

    def __init__(self):
        self.flushed = False
        self.root: FakeV4Span | None = None

    def start_observation(self, *, name, as_type="span", input=None, **kw):
        self.root = FakeV4Span(name, as_type=as_type, input=input, **kw)
        return self.root

    def flush(self):
        self.flushed = True


class FakeV2Trace:
    def __init__(self, name, input=None):
        self.name = name
        self.input = input
        self.spans: list[tuple] = []

    def span(self, *, name, output=None):
        self.spans.append((name, output))


class FakeV2Tracer:
    """Stand-in for the 2.x ``Langfuse`` client. Deliberately has no ``start_observation``."""

    def __init__(self):
        self.flushed = False
        self.last_trace: FakeV2Trace | None = None

    def trace(self, *, name, input=None):
        self.last_trace = FakeV2Trace(name, input)
        return self.last_trace

    def flush(self):
        self.flushed = True


class BrokenTracer:
    """A tracer that blows up on first use, in either API shape."""

    def __init__(self):
        self.flushed = False

    def start_observation(self, **kw):
        raise RuntimeError("boom")

    def flush(self):
        self.flushed = True


# -- trace_workflow_run: SDK dispatch -----------------------------------------


def test_noop_without_a_configured_tracer(monkeypatch):
    monkeypatch.setattr(tracing, "get_tracer", lambda: None)
    trace_workflow_run("task", RESULT)  # must not raise


def test_v4_style_tracer_records_steps_and_outcome_then_flushes(monkeypatch):
    fake = FakeV4Tracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda: fake)

    trace_workflow_run("ship it", RESULT)

    root = fake.root
    assert root.name == "multi_agent_workflow"
    assert root.kwargs["input"] == {"task": "ship it"}
    assert [c.name for c in root.children] == ["agent:planner", "agent:executor"]
    assert [c.kwargs["output"] for c in root.children] == ["1. do it", "did it"]
    assert all(c.ended for c in root.children)
    assert root.updated_with == {
        "result": "done!",
        "failed": False,
        "validation_passed": True,
        "error": None,
    }
    assert root.ended is True
    assert fake.flushed is True


def test_v4_style_handles_missing_history(monkeypatch):
    fake = FakeV4Tracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda: fake)

    trace_workflow_run("task", {"result": "ok"})

    assert fake.root.children == []
    assert fake.root.ended is True
    assert fake.flushed is True


def test_v2_style_tracer_still_supported(monkeypatch):
    fake = FakeV2Tracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda: fake)

    trace_workflow_run("ship it", RESULT)

    trace = fake.last_trace
    assert trace.name == "multi_agent_workflow"
    assert trace.input == {"task": "ship it"}
    assert trace.spans == [
        ("agent:planner", "1. do it"),
        ("agent:executor", "did it"),
        (
            "workflow_execution",
            {"result": "done!", "failed": False, "validation_passed": True, "error": None},
        ),
    ]
    assert fake.flushed is True


def test_exception_in_v4_path_is_swallowed_and_never_raises(monkeypatch):
    fake = BrokenTracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda: fake)

    trace_workflow_run("task", RESULT)  # must not raise

    assert fake.flushed is False  # aborted before reaching flush


def test_root_is_ended_even_if_update_raises(monkeypatch):
    class FailingUpdateSpan(FakeV4Span):
        def update(self, *, output=None, **kw):
            raise RuntimeError("update failed")

    class Tracer(FakeV4Tracer):
        def start_observation(self, *, name, as_type="span", input=None, **kw):
            self.root = FailingUpdateSpan(name, as_type=as_type, input=input, **kw)
            return self.root

    fake = Tracer()
    monkeypatch.setattr(tracing, "get_tracer", lambda: fake)

    trace_workflow_run("task", RESULT)  # must not raise

    assert fake.root.ended is True  # the finally block still ran
    assert fake.flushed is False  # the exception pre-empted the outer flush() call


# -- get_tracer: construction and caching ------------------------------------


def test_get_tracer_none_when_keys_missing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert get_tracer() is None


def test_get_tracer_none_when_only_one_key_set(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert get_tracer() is None


def test_get_tracer_constructs_and_caches_client(monkeypatch):
    sentinel = object()
    calls = []

    class StubLangfuse:
        def __new__(cls, **kwargs):
            calls.append(kwargs)
            return sentinel

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    monkeypatch.setitem(
        __import__("sys").modules,
        "langfuse",
        type("m", (), {"Langfuse": StubLangfuse}),
    )

    assert get_tracer() is sentinel
    assert get_tracer() is sentinel  # second call hits the cache
    assert calls == [{"public_key": "pub", "secret_key": "sec"}]  # constructed once


def test_get_tracer_none_when_langfuse_not_importable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("no langfuse installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert get_tracer() is None
