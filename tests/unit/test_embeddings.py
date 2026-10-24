"""Unit tests for embedding utilities."""

from __future__ import annotations

from app.memory.embeddings import cosine_similarity, get_default_embedder


def test_cosine_similarity_identical() -> None:
    a = [1.0, 2.0, 3.0]
    assert cosine_similarity(a, a) == 1.0


def test_cosine_similarity_orthogonal() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(cosine_similarity(a, b)) < 1e-10


def test_cosine_similarity_opposite() -> None:
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == -1.0


def test_cosine_similarity_empty() -> None:
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], []) == 0.0


def test_cosine_similarity_different_lengths() -> None:
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_get_default_embedder_returns_none_without_key() -> None:
    # May return None if no API key configured
    embedder = get_default_embedder()
    # Just check it doesn't crash
    assert embedder is None or hasattr(embedder, "embed_query")
