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


class MemoriesResponse(BaseModel):
    agent_id: str
    memories: List[Dict[str, Any]]


class WorkflowRunRequest(BaseModel):
    task: str = Field(..., min_length=1)


class WorkflowRunResponse(BaseModel):
    task: str
    result: str | None = None
    validation_passed: bool | None = None
    failed: bool = False
    error: str | None = None
    step: int | None = None
    history: List[Dict[str, Any]] = Field(default_factory=list)


class SaveMemoryRequest(BaseModel):
    memory_key: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    source_reference: str | None = None
    observed_at: str | None = None
    subject_key: str | None = None
    valid_until: str | None = None


class UpdateMemoryRequest(BaseModel):
    new_content: str = Field(..., min_length=1)
    source_reference: str | None = None
    observed_at: str | None = None
    subject_key: str | None = None
    valid_until: str | None = None


class ShareMemoryRequest(BaseModel):
    target_agent_id: str = Field(..., min_length=1)
    memory_key: str | None = None
    max_hops: int | None = Field(default=None, ge=0)


class MemoryResponse(BaseModel):
    memory: Dict[str, Any]


class ProvenanceResponse(BaseModel):
    memory_id: str
    items: List[Dict[str, Any]]


class VersionHistoryResponse(BaseModel):
    agent_id: str
    memory_key: str
    versions: List[Dict[str, Any]]
