"""Evaluation metrics for memory system."""

from __future__ import annotations

from typing import Any, Dict, List


__all__ = ["evaluate_retrieval_relevance", "evaluate_provenance_correctness", "evaluate_stale_handling"]


def evaluate_retrieval_relevance(results: List[Dict[str, Any]], query: str) -> float:
    """Evaluate retrieval relevance (simple heuristic)."""
    if not results:
        return 0.0
    # Simple score based on results returned
    return min(1.0, len(results) / 10.0)


def evaluate_provenance_correctness(ancestry: List[Dict[str, Any]]) -> bool:
    """Check if provenance chain is correct."""
    if not ancestry:
        return False
    # Check depths are sequential
    depths = [item.get("depth", -1) for item in ancestry]
    return depths == sorted(depths)


def evaluate_stale_handling(results: List[Dict[str, Any]]) -> bool:
    """Check that no stale memories are returned."""
    for item in results:
        if item.get("status") in ("superseded", "archived", "stale"):
            return False
    return True
