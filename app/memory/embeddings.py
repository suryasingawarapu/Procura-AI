"""Embedding generation and similarity utilities.

Provides a small, testable interface for generating embeddings.
Uses LangChain OpenAI embeddings if available; otherwise allows injection for tests.
"""

from __future__ import annotations

from typing import Any, List, Protocol


__all__ = ["Embedder", "get_default_embedder", "cosine_similarity"]


class Embedder(Protocol):
    """Protocol for embedding providers."""
    
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query text."""
        ...
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple documents."""
        ...


_default_embedder: Embedder | None = None


def get_default_embedder() -> Embedder | None:
    """Get or create default embedder using OpenAI if available."""
    global _default_embedder
    if _default_embedder is not None:
        return _default_embedder
    
    try:
        from langchain_openai import OpenAIEmbeddings
        
        _default_embedder = OpenAIEmbeddings()
        return _default_embedder
    except Exception:
        return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    
    return dot / (norm_a * norm_b)
