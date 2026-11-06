"""Unit tests for the shared specialist-subagent framework (stage 0)."""

from __future__ import annotations

from app.procurement.subagents.base import run_specialists


def test_runs_every_specialist_and_collects_ok_results():
    specialists = {
        "a": lambda ctx, results: {"value": ctx["x"] + 1},
        "b": lambda ctx, results: {"value": ctx["x"] * 2},
    }
    results = run_specialists(specialists, {"x": 10})

    assert results["a"].status == "ok"
    assert results["a"].data == {"value": 11}
    assert results["b"].status == "ok"
    assert results["b"].data == {"value": 20}


def test_a_later_specialist_can_see_an_earlier_ones_result():
    specialists = {
        "first": lambda ctx, results: {"found": 5},
        "second": lambda ctx, results: {"doubled": results["first"]["data"]["found"] * 2},
    }
    results = run_specialists(specialists, {})

    assert results["second"].data == {"doubled": 10}


def test_a_failing_specialist_reports_a_typed_error_and_never_crashes():
    def broken(ctx, results):
        raise RuntimeError("backend unavailable")

    specialists = {
        "ok_one": lambda ctx, results: {"value": 1},
        "broken": broken,
        "after_broken": lambda ctx, results: {"value": "still ran"},
    }
    results = run_specialists(specialists, {})

    assert results["ok_one"].status == "ok"
    assert results["broken"].status == "error"
    assert "backend unavailable" in results["broken"].error
    assert results["broken"].data == {}
    # one specialist failing never stops the ones after it
    assert results["after_broken"].status == "ok"
    assert results["after_broken"].data == {"value": "still ran"}


def test_a_specialist_can_report_skipped_with_a_reason():
    specialists = {"maybe": lambda ctx, results: {"_status": "skipped", "reason": "nothing to estimate"}}
    results = run_specialists(specialists, {})

    assert results["maybe"].status == "skipped"
    assert results["maybe"].data["reason"] == "nothing to estimate"


def test_needed_narrows_which_specialists_actually_run():
    calls = []

    def make(name):
        def fn(ctx, results):
            calls.append(name)
            return {}

        return fn

    specialists = {"a": make("a"), "b": make("b"), "c": make("c")}
    results = run_specialists(specialists, {}, needed=["a", "c"])

    assert set(results.keys()) == {"a", "c"}
    assert calls == ["a", "c"]
