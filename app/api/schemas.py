"""Pydantic schemas for API requests and responses."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ContextRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    limit: int | None = Field(default=None, ge=1)
    memory_limit: int | None = Field(default=None, ge=1)
    exclude_stale: bool = True
    query_embedding: List[float] | str | None = None
    match_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    match_count: int | None = Field(default=None, ge=1)


class ContextResponse(BaseModel):
    agent_id: str
    items: List[Dict[str, Any]]


class SummariesResponse(BaseModel):
    agent_id: str
    summaries: List[Dict[str, Any]]
