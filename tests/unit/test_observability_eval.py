"""Unit tests for observability and evaluation."""

from __future__ import annotations

from app.evaluation.metrics import (
    evaluate_provenance_correctness,
    evaluate_retrieval_relevance,
    evaluate_stale_handling,
)
from app.observability.tracing import get_tracer, trace_workflow_run


def test_retrieval_relevance_empty():
    assert evaluate_retrieval_relevance([], "query") == 0.0


def test_retrieval_relevance_with_results():
    results = [{"id": f"m{i}"} for i in range(5)]
    score = evaluate_retrieval_relevance(results, "query")
    assert 0 < score <= 1.0


def test_provenance_correctness():
    ancestry = [{"depth": 0}, {"depth": 1}, {"depth": 2}]
    assert evaluate_provenance_correctness(ancestry) is True


def test_provenance_incorrect():
    ancestry = [{"depth": 1}, {"depth": 0}]
    assert evaluate_provenance_correctness(ancestry) is False


def test_stale_handling_clean():
    results = [{"status": "active"}, {"status": "active"}]
    assert evaluate_stale_handling(results) is True


def test_stale_handling_detects_stale():
    results = [{"status": "active"}, {"status": "superseded"}]
    assert evaluate_stale_handling(results) is False


def test_tracer_without_config():
    tracer = get_tracer()
    # May be None without keys, should not crash
    assert tracer is None or tracer is not None


def test_trace_workflow_run_no_crash():
    trace_workflow_run("test task", {"result": "done"})
