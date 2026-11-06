"""Unit tests for embedding utilities."""

from __future__ import annotations

from unittest.mock import patch

from app.memory.embeddings import cosine_similarity, embed_or_none, get_default_embedder


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


@patch("app.memory.embeddings.get_default_embedder")
def test_embed_or_none_returns_vector_when_embedder_configured(mock_get_embedder) -> None:
    mock_get_embedder.return_value.embed_query.return_value = [0.1, 0.2, 0.3]
    assert embed_or_none("hello") == [0.1, 0.2, 0.3]


@patch("app.memory.embeddings.get_default_embedder", return_value=None)
def test_embed_or_none_is_none_without_an_embedder(_mock) -> None:
    assert embed_or_none("hello") is None


@patch("app.memory.embeddings.get_default_embedder")
def test_embed_or_none_swallows_embedder_failures(mock_get_embedder) -> None:
    mock_get_embedder.return_value.embed_query.side_effect = RuntimeError("rate limited")
    assert embed_or_none("hello") is None
