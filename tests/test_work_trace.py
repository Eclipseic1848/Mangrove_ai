# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

from src.conversation_steering import ProgressStage, StructuredProgressEvent
from src.work_trace import WorkTraceProjection
from src.api.routes.semantic_workspace import _structured_progress_events


def _event(sequence: int, seconds: int, event_type: str, **changes):
    return StructuredProgressEvent(
        event_id=f"event-{sequence}",
        sequence=sequence,
        task_id="task-1",
        revision=2,
        run_id=changes.pop("run_id", "run-2"),
        stage=ProgressStage.EXECUTE,
        event_type=event_type,
        summary=changes.pop("summary", event_type),
        created_at=datetime(2026, 8, 28, tzinfo=timezone.utc)
        + timedelta(seconds=seconds),
        **changes,
    )


def test_work_session_is_one_run_and_separates_waiting_time() -> None:
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="completed",
        events=(
            _event(1, 0, "run.started", runtime_event_type="run.started"),
            _event(2, 2, "source.acquired", run_id=None),
            _event(3, 10, "owner_action.requested"),
            _event(4, 40, "resumed"),
            _event(5, 50, "run.completed", runtime_event_type="run.completed"),
        ),
        provider_usage=[],
    )
    assert [item.event_id for item in view.entries] == [
        "event-1", "event-3", "event-4", "event-5"
    ]
    assert view.waiting_duration_ms == 30_000
    assert view.work_duration_ms == 20_000


def test_usage_keeps_unknown_calls_and_never_counts_tools_as_tokens() -> None:
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="running",
        events=(
            _event(1, 0, "run.started"),
            _event(2, 1, "tool.started", action={"tool": "web.search"}),
            _event(3, 2, "tool.completed", duration_ms=1_000),
        ),
        provider_usage=[
            {"run_id": "run-2", "input_tokens": 2_000, "output_tokens": 420, "total_tokens": 2_420, "request_count": 1},
            {"run_id": "run-2", "input_tokens": 3_000, "output_tokens": 1_000, "total_tokens": 4_000, "request_count": 1},
            {"run_id": "run-2", "input_tokens": 1_500, "output_tokens": 500, "total_tokens": 2_000, "request_count": 1},
            {"run_id": "run-2", "input_tokens": None, "output_tokens": None, "total_tokens": None, "request_count": 1},
            {"run_id": None, "input_tokens": 99_999, "output_tokens": 1, "total_tokens": 100_000, "request_count": 1},
        ],
    )
    assert view.usage.total_tokens == 8_420
    assert view.usage.call_count == 4
    assert view.usage.unknown_call_count == 1
    assert view.tool_call_count == 1


def test_provider_usage_view_keeps_frozen_identity_without_validation_error() -> None:
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="running",
        events=(_event(1, 0, "run.started"),),
        provider_usage=[{
            "owner_user_id": "owner-1",
            "task_id": "task-1",
            "revision": 2,
            "run_id": "run-2",
            "connection_id": "connection-1",
            "model": "provider/model-1",
            "purpose": "agent_inference",
            "status": "recorded",
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_tokens": 3,
            "total_tokens": 12,
            "request_count": 1,
            "created_at": "2026-08-28T00:00:01Z",
        }],
    )

    assert view.provider_usage[0].owner_user_id == "owner-1"
    assert view.provider_usage[0].task_id == "task-1"
    assert view.provider_usage[0].revision == 2
    assert view.provider_usage[0].run_id == "run-2"


def test_running_session_uses_observation_time_and_local_usage_fallback() -> None:
    observed_at = datetime(2026, 8, 28, tzinfo=timezone.utc) + timedelta(seconds=50)
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="needs_input",
        events=(
            _event(1, 0, "run.started"),
            _event(
                2,
                5,
                "provider.usage",
                runtime_event_type="provider.usage",
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
            ),
            _event(3, 10, "owner_action.requested"),
        ),
        provider_usage=[],
        observed_at=observed_at,
    )
    assert view.ended_at is None
    assert view.work_duration_ms == 10_000
    assert view.waiting_duration_ms == 40_000
    assert view.usage.total_tokens == 120
    assert view.usage.call_count == 1


def test_local_model_call_without_usage_is_unknown_not_zero() -> None:
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="completed",
        events=(
            _event(1, 0, "run.started"),
            _event(2, 1, "provider.usage", runtime_event_type="provider.usage"),
            _event(3, 2, "run.completed"),
        ),
        provider_usage=[],
    )
    assert view.usage.total_tokens == 0
    assert view.usage.call_count == 1
    assert view.usage.unknown_call_count == 1


def test_historical_pi_run_without_usage_event_is_at_least_one_unknown_call() -> None:
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="completed",
        events=(
            _event(1, 0, "action_progress", runtime_event_type="runtime.preparing"),
            _event(2, 1, "action_progress", runtime_event_type="agent.started"),
            _event(3, 2, "action_progress", runtime_event_type="agent.settled"),
            _event(4, 3, "action_progress", runtime_event_type="candidate.ready"),
        ),
        provider_usage=[],
    )

    assert view.usage.total_tokens == 0
    assert view.usage.call_count == 1
    assert view.usage.unknown_call_count == 1


def test_legacy_naive_timestamp_can_be_compared_with_aware_observation() -> None:
    event = _event(1, 0, "run.started").model_copy(
        update={"created_at": datetime(2026, 8, 28, 12, 0, 0)}
    )
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=2,
        run_id="run-2",
        status="running",
        events=(event,),
        provider_usage=[],
        observed_at=datetime(2026, 8, 28, 19, 0, 1, tzinfo=timezone.utc),
    )

    assert view.work_duration_ms == 1_000


def test_runtime_event_projection_redacts_sensitive_details_and_paths() -> None:
    events = _structured_progress_events({
        "task_id": "task-1",
        "viewing_revision": 1,
        "run_id": "run-1",
        "harness_events": [],
        "events": [{
            "event_id": "event-1",
            "event_type": "tool.completed",
            "stage": "execute",
            "summary": "读取 C:\\private\\客户.xlsx token=secret-value",
            "created_at": "2026-08-28T00:00:00Z",
            "details": {
                "run_id": "run-1",
                "runtime_event_type": "tool.completed",
                "input_summary": "cookie=abc C:\\private\\客户.xlsx",
                "result_summary": "保存到 /srv/private/output.json",
                "evidence_refs": ["evidence-1", "C:\\private\\raw.log"],
                "action": {
                    "tool": "document.preview",
                    "action_id": "preview-1",
                },
            },
        }],
    })
    encoded = events[0].model_dump_json()
    assert "secret-value" not in encoded
    assert "private" not in encoded
    assert "abc" not in encoded
    assert events[0].evidence_refs == ("evidence-1",)
    work_session = WorkTraceProjection().project(
        task_id="task-1",
        revision=1,
        run_id="run-1",
        status="running",
        events=events,
        provider_usage=[],
        observed_at=datetime(2026, 8, 28, 0, 0, 1, tzinfo=timezone.utc),
    )
    assert work_session.entries[0].input_summary == "cookie=[已隐藏] [路径已隐藏]"
    assert work_session.entries[0].tool_name == "document.preview"
    assert work_session.entries[0].action_id == "preview-1"


def test_unknown_required_runtime_event_fails_closed() -> None:
    try:
        _structured_progress_events({
            "task_id": "task-1",
            "viewing_revision": 1,
            "run_id": "run-1",
            "harness_events": [],
            "events": [{
                "event_id": "event-1",
                "event_type": "runtime.future_required",
                "stage": "execute",
                "summary": "未来必需事件",
                "created_at": "2026-08-28T00:00:00Z",
                "details": {"trace_required": True},
            }],
        })
    except RuntimeError as exc:
        assert "安全归一化" in str(exc)
    else:
        raise AssertionError("未知必需 Runtime 事件必须失败关闭")


def test_known_pi_event_keeps_safe_summary_but_unknown_optional_does_not() -> None:
    events = _structured_progress_events({
        "task_id": "task-1",
        "viewing_revision": 1,
        "run_id": "run-1",
        "harness_events": [],
        "events": [
            {
                "event_id": "known",
                "event_type": "capability.completed",
                "stage": "prepare_capabilities",
                "summary": "已准备 1 项能力：文档解析",
                "created_at": "2026-08-28T00:00:00Z",
                "details": {
                    "source": "pi-runtime",
                    "runtime_event_type": "capability.completed",
                },
            },
            {
                "event_id": "future",
                "event_type": "tool.future_optional",
                "stage": "execute",
                "summary": "原始事件正文不得展示",
                "created_at": "2026-08-28T00:00:01Z",
                "details": {
                    "source": "pi-runtime",
                    "runtime_event_type": "tool.future_optional",
                    "purpose": "system prompt 原文",
                    "input_summary": "cookie=raw-secret",
                    "result_summary": "完整命令和原始日志",
                    "evidence_refs": ["raw-log"],
                    "action": {"tool": "raw-command"},
                    "recovery_status": "handled",
                },
            },
        ],
    })

    assert events[0].summary == "已准备 1 项能力：文档解析"
    assert events[1].summary == "智能体完成一项内部操作"
    assert events[1].purpose is None
    assert events[1].input_summary is None
    assert events[1].result_summary is None
    assert events[1].evidence_refs == ()
    assert events[1].action is None
    assert events[1].recovery_status is None


def test_persisted_runtime_lifecycle_excludes_source_time_and_restores_waiting() -> None:
    events = _structured_progress_events({
        "task_id": "task-1",
        "viewing_revision": 1,
        "run_id": "run-1",
        "harness_events": [],
        "events": [
            {
                "event_id": "source",
                "event_type": "source.observed",
                "stage": "inspect_sources",
                "summary": "来源读取完成",
                "created_at": "2026-08-28T00:00:00Z",
                "details": {},
            },
            {
                "event_id": "start",
                "event_type": "action_progress",
                "stage": "execute",
                "summary": "运行开始",
                "created_at": "2026-08-28T00:00:10Z",
                "details": {
                    "source": "pi-runtime",
                    "runtime_event_type": "runtime.preparing",
                    "run_id": "run-1",
                },
            },
            {
                "event_id": "pause",
                "event_type": "question_required",
                "stage": "execute",
                "summary": "等待确认",
                "created_at": "2026-08-28T00:00:20Z",
                "details": {},
            },
            {
                "event_id": "resume",
                "event_type": "question_answered",
                "stage": "execute",
                "summary": "继续执行",
                "created_at": "2026-08-28T00:00:50Z",
                "details": {},
            },
            {
                "event_id": "end",
                "event_type": "action_progress",
                "stage": "verify",
                "summary": "候选就绪",
                "created_at": "2026-08-28T00:01:10Z",
                "details": {
                    "source": "pi-runtime",
                    "runtime_event_type": "candidate.ready",
                    "run_id": "run-1",
                },
            },
        ],
    })
    view = WorkTraceProjection().project(
        task_id="task-1",
        revision=1,
        run_id="run-1",
        status="candidate_ready",
        events=events,
        provider_usage=[],
    )

    assert [entry.event_id for entry in view.entries] == [
        "start", "pause", "resume", "end"
    ]
    assert view.started_at == datetime(2026, 8, 28, 0, 0, 10, tzinfo=timezone.utc)
    assert view.ended_at == datetime(2026, 8, 28, 0, 1, 10, tzinfo=timezone.utc)
    assert view.waiting_duration_ms == 30_000
    assert view.work_duration_ms == 30_000
