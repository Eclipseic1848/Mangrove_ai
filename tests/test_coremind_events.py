# -*- coding: utf-8 -*-
"""CoreMind 公开协议到 Mangrove 工作记录的脱敏边界。"""
from __future__ import annotations

import re
import pytest

from src.agentic_runtime.coremind_events import project_coremind_event
from src.agentic_runtime.kernel import AgentKernelCapabilityError
from src.conversation_steering import StructuredProgressEvent
from src.work_trace import WorkTraceProjection


@pytest.mark.parametrize("runtime_total", [None, 0, 15])
def test_runtime_usage_without_native_provenance_stays_unknown(runtime_total):
    event = {
        "protocolVersion": "2.0",
        "eventSchemaVersion": 1,
        "runId": "run-owner-a",
        "sequence": 7,
        "eventId": "event-7",
        "turnId": "turn-1",
        "timestamp": "2026-09-04T05:00:00.000Z",
        "ignorable": False,
        "sensitivity": "local",
        "eventType": "turn_end",
        "payload": {
            "type": "turn_end",
            "agent": "main",
            "inputTokens": 12,
            "outputTokens": 3,
            "tokens": runtime_total,
            "costUsd": 99.0,
        },
    }

    result = project_coremind_event(event, run_id="run-owner-a", model="chosen-model")

    assert result.event_type == "provider.usage"
    assert result.details == {
        "runtime_event_id": "cm_event_d7243aa4c962419a9ae7603aea1846b6dc7ecb4d9c40dafdd81b6561a8a7c79b",
        "runtime_sequence": 7,
        "runtime_timestamp": "2026-09-04T05:00:00.000Z",
        "turn_id": "cm_turn_c8dae96c3bfdf7540ccb929173498fa1c8af5015085158751c3798215cd22cdb",
        "trace_normalized": True,
        "purpose": "执行任务",
        "model_name": "chosen-model",
        "input_tokens": None,
        "output_tokens": None,
        "cache_tokens": None,
        "total_tokens": None,
    }
    assert "模型请求账本" in result.summary

    progress = StructuredProgressEvent(
        event_id="event-7", sequence=7, task_id="task-a", revision=1,
        run_id="run-owner-a", stage="execute", event_type=result.event_type,
        summary=result.summary, created_at=event["timestamp"],
        **{key: result.details[key] for key in ("input_tokens", "output_tokens", "cache_tokens", "total_tokens")},
    )
    for native_usage, expected_total, unknown_calls in (
        ([], 0, 1),
        ([{"run_id": "run-owner-a", "input_tokens": 12, "output_tokens": 3, "total_tokens": 15, "request_count": 1}], 15, 0),
        ([{"run_id": "run-owner-a", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 1}], 0, 0),
    ):
        view = WorkTraceProjection().project(
            task_id="task-a", revision=1, run_id="run-owner-a", status="running",
            events=(progress,), provider_usage=native_usage,
        )
        assert view.usage.total_tokens == expected_total
        assert view.usage.unknown_call_count == unknown_calls
        assert view.usage.call_count == 1


def test_tool_progress_uses_catalog_name_without_copying_arguments_or_raw_output():
    event = {
        "protocolVersion": "2.0",
        "eventSchemaVersion": 1,
        "runId": "run-owner-a",
        "sequence": 8,
        "eventId": "event-8",
        "timestamp": "2026-09-04T05:00:01.000Z",
        "ignorable": False,
        "sensitivity": "local",
        "eventType": "tool_call",
        "callId": "call-a",
        "payload": {
            "type": "tool_call",
            "agent": "main",
            "tool": "read",
            "callId": "call-a",
            "args": {"path": "private/source.txt", "cookie": "owner-secret"},
        },
    }

    result = project_coremind_event(
        event,
        run_id="run-owner-a",
        model="chosen-model",
        tool_names={"read": "读取来源"},
    )

    assert result.event_type == "tool.started"
    assert result.summary == "正在使用读取来源"
    assert result.details == {
        "runtime_event_id": "cm_event_2f094176d362cd2c9936667f8caca9eaebc4380b40529926bd667211ec809b6c",
        "runtime_sequence": 8,
        "runtime_timestamp": "2026-09-04T05:00:01.000Z",
        "trace_normalized": True,
        "purpose": "执行任务",
        "tool": "read",
        "tool_call_id": "cm_call_b05e49894d0046f87c7f41103b6c16a7fc439f88945a534aceffc8db9e72bc08",
        "action": {"tool": "读取来源", "action_id": "cm_call_b05e49894d0046f87c7f41103b6c16a7fc439f88945a534aceffc8db9e72bc08"},
    }
    assert "owner-secret" not in result.model_dump_json()
    assert "private/source.txt" not in result.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("runId", "run-owner-b"),
        ("protocolVersion", "1.0"),
        ("eventSchemaVersion", True),
        ("sequence", True),
        ("sequence", 0),
        ("eventId", ""),
        ("eventId", " "),
        ("eventId", "a" * 257),
        ("turnId", {}),
        ("stepId", "step\x7f"),
        ("timestamp", "not-a-time"),
        ("timestamp", "2026-09-04T05:00:01"),
        ("payload", {"type": "tool_call", "cookie": "owner-secret"}),
    ],
)
def test_invalid_or_cross_run_event_is_rejected_without_exposing_its_payload(field, value):
    event = {
        "protocolVersion": "2.0",
        "eventSchemaVersion": 1,
        "runId": "run-owner-a",
        "sequence": 9,
        "eventId": "event-9",
        "timestamp": "2026-09-04T05:00:02.000Z",
        "ignorable": False,
        "sensitivity": "local",
        "eventType": "turn_end",
        "payload": {"type": "turn_end", "agent": "main", "tokens": 15},
    }
    event[field] = value

    with pytest.raises(AgentKernelCapabilityError) as caught:
        project_coremind_event(event, run_id="run-owner-a", model="chosen-model")

    assert "owner-secret" not in str(caught.value)


@pytest.mark.parametrize("failed", [False, True])
def test_tool_result_keeps_the_action_identity_and_actual_failure(failed):
    event = {
        "protocolVersion": "2.0",
        "eventSchemaVersion": 1,
        "runId": "run-owner-a",
        "sequence": 10,
        "eventId": "event-10",
        "timestamp": "2026-09-04T05:00:03.000Z",
        "ignorable": False,
        "sensitivity": "local",
        "eventType": "tool_result",
        "callId": "call-a",
        "payload": {
            "type": "tool_result", "agent": "main", "tool": "read",
            "callId": "call-a", "isError": failed,
        },
    }

    result = project_coremind_event(
        event, run_id="run-owner-a", model="chosen-model", tool_names={"read": "读取来源"},
    )

    assert result.event_type == ("tool.failed" if failed else "tool.completed")
    assert result.details["tool_call_id"] == "cm_call_b05e49894d0046f87c7f41103b6c16a7fc439f88945a534aceffc8db9e72bc08"
    assert result.details["failed"] is failed
    progress = StructuredProgressEvent(
        event_id="event-10", sequence=10, task_id="task-a", revision=1,
        run_id="run-owner-a", stage="execute", event_type=result.event_type,
        summary=result.summary, action=result.details.get("action"),
        created_at="2026-09-04T05:00:03.000Z",
    )
    view = WorkTraceProjection().project(
        task_id="task-a", revision=1, run_id="run-owner-a", status="running",
        events=(progress,), provider_usage=[],
    )
    assert view.entries[0].action_id == "cm_call_b05e49894d0046f87c7f41103b6c16a7fc439f88945a534aceffc8db9e72bc08"


@pytest.mark.parametrize("ignorable", [False, True])
@pytest.mark.parametrize("event_type", ["fact.future_metadata", "arbitrary_unknown"])
def test_unknown_event_is_ignored_only_when_the_protocol_explicitly_allows_it(ignorable, event_type):
    event = {
        "protocolVersion": "2.0",
        "eventSchemaVersion": 1,
        "runId": "run-owner-a",
        "sequence": 11,
        "eventId": "event-11",
        "timestamp": "2026-09-04T05:00:04.000Z",
        "ignorable": ignorable,
        "sensitivity": "local",
        "eventType": event_type,
        "payload": "owner-secret",
    }

    if ignorable and event_type == "fact.future_metadata":
        assert project_coremind_event(event, run_id="run-owner-a", model="chosen-model") is None
    else:
        with pytest.raises(AgentKernelCapabilityError) as caught:
            project_coremind_event(event, run_id="run-owner-a", model="chosen-model")
        assert "owner-secret" not in str(caught.value)


def test_protocol_identifiers_keep_public_correlation_without_exposing_paths():
    event = {
        "protocolVersion": "2.0", "eventSchemaVersion": 1,
        "runId": "run-owner-a", "sequence": 12,
        "eventId": "/private/owner-a/event.json",
        "turnId": "cookie=owner-secret",
        "timestamp": "2026-09-04T05:00:04.000Z",
        "ignorable": False, "sensitivity": "local",
        "eventType": "tool_call", "callId": "/private/owner-a/source.csv",
        "payload": {"type": "tool_call", "tool": "read", "agent": "main"},
    }
    started = project_coremind_event(
        event, run_id="run-owner-a", model="chosen-model", tool_names={"read": "读取来源"},
    )
    event.update(eventType="tool_result", eventId="event-13", sequence=13)
    event["payload"] = {"type": "tool_result", "tool": "read", "agent": "main", "isError": False}
    finished = project_coremind_event(
        event, run_id="run-owner-a", model="chosen-model", tool_names={"read": "读取来源"},
    )
    assert started.details["action"] == finished.details["action"]
    for result in (started, finished):
        assert "/private" not in result.model_dump_json()
        assert "owner-secret" not in result.model_dump_json()
        assert re.fullmatch(r"cm_call_[a-f0-9]{64}", result.details["tool_call_id"])
