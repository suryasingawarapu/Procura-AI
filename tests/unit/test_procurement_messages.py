"""Unit tests for the inter-system message/task schemas."""

from __future__ import annotations

import pytest

from app.procurement.messages import MessageType, SystemMessage, TaskRequest, TaskResult


def test_system_message_rejects_unknown_sender():
    with pytest.raises(ValueError):
        SystemMessage(
            task_id="t1",
            correlation_id="c1",
            sender="mallory",
            receiver="procura",
            message_type=MessageType.REQUEST,
        )


def test_system_message_rejects_unknown_receiver():
    with pytest.raises(ValueError):
        SystemMessage(
            task_id="t1",
            correlation_id="c1",
            sender="vendra",
            receiver="mallory",
            message_type=MessageType.REQUEST,
        )


def test_system_message_accepts_coordinator_as_sender_or_receiver():
    msg = SystemMessage(
        task_id="t1", correlation_id="c1", sender="coordinator", receiver="vendra", message_type=MessageType.REQUEST
    )
    assert msg.sender == "coordinator"
    assert msg.status == "pending"
    assert msg.message_id.startswith("msg-")


def test_task_request_generates_unique_ids():
    a = TaskRequest(description="find suppliers")
    b = TaskRequest(description="find suppliers")
    assert a.task_id != b.task_id
    assert a.correlation_id != b.correlation_id


def test_task_result_defaults():
    result = TaskResult(task_id="t1", correlation_id="c1")
    assert result.systems_invoked == []
    assert result.messages == []
    assert result.requires_approval is False
    assert result.failed is False
