"""Typed inter-system messages and task/result schemas for the coordinator.

Every message carries enough to trace a fact back to its source and judge
whether it's still trustworthy: who sent it, what task/correlation it
belongs to, what it's backed by (source type/reference/version/time,
verification and freshness status), and why it was shared (purpose/
permission). This is the structured alternative the spec's research
question compares against an ordinary-message baseline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .identities import SYSTEM_IDS

__all__ = [
    "MessageType",
    "VerificationStatus",
    "FreshnessStatus",
    "SourceReference",
    "SystemMessage",
    "TaskRequest",
    "TaskResult",
    "new_id",
]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class MessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    SHARE = "share"
    ERROR = "error"


VerificationStatus = Literal["unverified", "verified", "disputed"]
FreshnessStatus = Literal["fresh", "stale", "unknown"]

SENDERS = SYSTEM_IDS + ("coordinator",)


class SourceReference(BaseModel):
    """What backs one piece of information in a message's payload."""

    source_type: str = Field(..., description="e.g. 'erpnext_doctype', 'document', 'computed', 'seeded'")
    reference: str = Field(..., description="e.g. 'Supplier/SUP-0001', a document path, or a calc description")
    version: Optional[str] = None
    observed_at: Optional[datetime] = None
    verification_status: VerificationStatus = "unverified"
    freshness: FreshnessStatus = "unknown"


class SystemMessage(BaseModel):
    """One inter-system message exchanged through the coordinator."""

    message_id: str = Field(default_factory=lambda: new_id("msg"))
    task_id: str
    correlation_id: str
    sender: str
    receiver: str
    message_type: MessageType
    payload: Dict[str, Any] = Field(default_factory=dict)
    sources: List[SourceReference] = Field(default_factory=list)
    purpose: Optional[str] = None
    permission: Optional[str] = None
    status: Literal["ok", "error", "pending"] = "pending"
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context: Any) -> None:  # pydantic v2 hook
        if self.sender not in SENDERS:
            raise ValueError(f"unknown sender: {self.sender!r}")
        if self.receiver not in SENDERS:
            raise ValueError(f"unknown receiver: {self.receiver!r}")


class TaskRequest(BaseModel):
    """The user-facing task the coordinator receives and routes."""

    task_id: str = Field(default_factory=lambda: new_id("task"))
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))
    description: str
    requested_systems: Optional[List[str]] = None  # None -> coordinator decides


class TaskResult(BaseModel):
    """What the coordinator returns once routing finishes (or fails/halts)."""

    task_id: str
    correlation_id: str
    systems_invoked: List[str] = Field(default_factory=list)
    messages: List[SystemMessage] = Field(default_factory=list)
    requires_approval: bool = False
    approval_summary: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    failed: bool = False
    blocked: bool = False
    error: Optional[str] = None
