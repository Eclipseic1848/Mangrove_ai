# -*- coding: utf-8 -*-
"""AC-00～AC-03：只通过冻结的对话与进度 Interface 验证行为。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from tests.database_migration_helpers import migrated_webui_database

from src.conversation_steering import (
    CapabilityMaturity,
    CapabilityPack,
    ContextDelta,
    ContextCompileRequest,
    ContextCompiler,
    ConversationSteering,
    DeltaConfidence,
    InMemorySteeringRepository,
    ProcedureScope,
    ProgressAudience,
    ProgressProjection,
    ProgressStage,
    ProgressValue,
    RawUserTurn,
    RevisionDecisionStatus,
    RevisionSwitchMode,
    SemanticDiffGate,
    SqliteSteeringRepository,
    SteeringAction,
    SteeringRequest,
    StructuredProgressEvent,
    TurnIntent,
    build_context_rewriter,
)


@pytest.fixture(autouse=True)
def _migrated_database(tmp_path: Path) -> None:
    migrated_webui_database(tmp_path / "steering.db")


def test_capability_scope_and_raw_turn_are_immutable_contracts() -> None:
    personal = CapabilityPack(
        pack_id="pack_pdf_table",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="user-a",
    )
    assert personal.owner_id == "user-a"

    with pytest.raises(ValidationError):
        CapabilityPack(
            pack_id="pack_shared",
            version="1.0.0",
            digest="sha256:" + "b" * 64,
            scope=ProcedureScope.PLATFORM,
            maturity=CapabilityMaturity.VERIFIED,
            owner_id="user-a",
            task_refs=("workspace-private",),
        )

    turn = RawUserTurn(
        turn_id="turn-1",
        owner_id="user-a",
        task_id="workspace-1",
        revision=1,
        text="上次那些也算上，这次只要王总的，做成表格",
    )
    with pytest.raises(ValidationError):
        turn.text = "只保留改写后的文本"


def test_raw_turn_is_idempotent_and_owner_isolated(tmp_path) -> None:
    repository = SqliteSteeringRepository(str(tmp_path / "steering.db"))
    turn = RawUserTurn(
        turn_id="turn-1",
        owner_id="user-a",
        task_id="workspace-1",
        revision=1,
        text="现在做到哪了？",
        idempotency_key="message-1",
    )

    first = repository.save_turn(turn)
    repeated = repository.save_turn(
        turn.model_copy(update={"turn_id": "turn-retry"})
    )

    assert repeated.turn_id == first.turn_id == "turn-1"
    assert repository.get_turn("user-a", "turn-1") == first
    assert repository.get_turn("user-b", "turn-1") is None

    with pytest.raises(ValueError, match="幂等键"):
        repository.save_turn(
            turn.model_copy(
                update={"turn_id": "turn-conflict", "text": "增加一个字段"}
            )
        )


class _StatusRewriter:
    def __init__(self) -> None:
        self.calls = 0

    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        self.calls += 1
        return ContextDelta(
            delta_id="delta-1",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=(turn.turn_id,),
            intent=TurnIntent.STATUS_QUESTION,
            confidence=DeltaConfidence.HIGH,
            normalized_text="询问当前任务进度",
            direct_answer="正在检查来源，当前任务会继续执行。",
        )


def test_status_followup_is_persisted_without_changing_run(tmp_path) -> None:
    repository = SqliteSteeringRepository(str(tmp_path / "steering.db"))
    rewriter = _StatusRewriter()
    steering = ConversationSteering(repository, rewriter)
    request = SteeringRequest(
        owner_id="user-a",
        task_id="workspace-1",
        revision=2,
        run_id="run-existing",
        text="现在做到哪了？",
        idempotency_key="message-2",
        current_status="running",
        status_summary="正在检查来源",
    )

    first = asyncio.run(steering.handle_turn(request))
    repeated = asyncio.run(steering.handle_turn(request))

    assert first.action is SteeringAction.ANSWER_ONLY
    assert first.answer == "正在检查来源，当前任务会继续执行。"
    assert first.run_id == "run-existing"
    assert first.revision == 2
    assert repeated.result_id == first.result_id
    assert rewriter.calls == 1
    assert repository.get_result("user-b", first.result_id) is None


class _FieldChangeRewriter:
    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        return ContextDelta(
            delta_id="delta-field-change",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=(turn.turn_id,),
            intent=TurnIntent.TASK_REFINEMENT,
            confidence=DeltaConfidence.HIGH,
            normalized_text="保留现有要求，新增部门和人民币大写金额字段",
            field_semantics_delta={
                "add": ["部门", "人民币大写金额"],
            },
        )


def test_material_change_creates_proposal_without_mutating_run(tmp_path) -> None:
    repository = SqliteSteeringRepository(str(tmp_path / "steering.db"))
    steering = ConversationSteering(repository, _FieldChangeRewriter())

    result = asyncio.run(
        steering.handle_turn(
            SteeringRequest(
                owner_id="user-a",
                task_id="workspace-1",
                revision=3,
                run_id="run-still-active",
                text="再加上部门和人民币大写金额",
                current_status="running",
                current_goal="提取报销审批记录",
            )
        )
    )

    assert result.action is SteeringAction.REVISION_PROPOSAL
    assert result.run_id == "run-still-active"
    assert result.revision == 3
    proposal = repository.get_proposal("user-a", result.proposal_id or "")
    assert proposal is not None
    assert proposal.base_revision == 3
    assert proposal.material_changes == ("field_semantics",)


def test_after_safe_point_decision_survives_restart(tmp_path) -> None:
    db_path = str(tmp_path / "steering.db")
    repository = SqliteSteeringRepository(db_path)
    steering = ConversationSteering(repository, _FieldChangeRewriter())
    result = asyncio.run(
        steering.handle_turn(
            SteeringRequest(
                owner_id="user-a",
                task_id="workspace-1",
                revision=1,
                run_id="run-1",
                text="增加部门字段",
                current_status="running",
            )
        )
    )

    waiting = steering.decide_proposal(
        "user-a",
        result.proposal_id or "",
        RevisionSwitchMode.AFTER_SAFE_POINT,
    )
    reopened = SqliteSteeringRepository(db_path)

    assert waiting.status is RevisionDecisionStatus.WAITING_SAFE_POINT
    assert reopened.get_decision("user-a", waiting.decision_id) == waiting

    ready = ConversationSteering(reopened, _FieldChangeRewriter()).mark_safe_point(
        "user-a",
        "workspace-1",
        revision=1,
        safe_point="inspect.completed",
    )
    assert ready is not None
    assert ready.status is RevisionDecisionStatus.READY_TO_APPLY
    assert ready.safe_point == "inspect.completed"


def test_context_compiler_preserves_confirmed_meaning_within_budget() -> None:
    compiler = ContextCompiler()
    compiled = compiler.compile(
        ContextCompileRequest(
            owner_id="user-a",
            task_id="workspace-1",
            revision=2,
            system_boundaries=("不得扩大来源或外发数据",),
            goal_contract="只处理附件一，提取王总的报销记录并输出 JSON",
            confirmed_semantics=("全部指附件一中的全部匹配记录",),
            run_summary="正在检查来源",
            procedure_summaries=("平台方案：扫描表格提取",),
            relevant_turns=(
                RawUserTurn(
                    turn_id="turn-related",
                    owner_id="user-a",
                    task_id="workspace-1",
                    revision=2,
                    text="金额保留两位小数",
                ),
            ),
            evidence_snippets=tuple(
                f"候选证据 {index}：一段不应全部进入窗口的长文本"
                for index in range(20)
            ),
            max_chars=420,
        )
    )

    assert "不得扩大来源或外发数据" in compiled.content
    assert "只处理附件一" in compiled.content
    assert "全部指附件一" in compiled.content
    assert len(compiled.content) <= 420
    assert compiled.omitted_categories == ("evidence",)
    assert compiled.summary_sha256.startswith("sha256:")


def test_progress_projection_is_audience_safe_and_has_one_active_stage() -> None:
    events = (
        StructuredProgressEvent(
            event_id="evt-2",
            sequence=2,
            task_id="workspace-1",
            revision=1,
            run_id="run-1",
            stage=ProgressStage.PREPARE_CAPABILITIES,
            event_type="capability.search_started",
            summary="正在查找可复用能力",
            progress=ProgressValue(current=3, unit="candidate"),
            refs={
                "pack_id": "pack-1",
                "host_path": "C:/secret",
                "nested": {"api_key": "secret", "version": "1"},
            },
            action={
                "label": "重试",
                "token": "secret",
                "details": {"command": "npm ci", "safe": True},
            },
            audience=ProgressAudience.ALL,
        ),
        StructuredProgressEvent(
            event_id="evt-admin",
            sequence=3,
            task_id="workspace-1",
            revision=1,
            run_id="run-1",
            stage=ProgressStage.PREPARE_CAPABILITIES,
            event_type="capability.debug",
            summary="构建器诊断",
            refs={"command": "npm ci"},
            audience=ProgressAudience.ADMIN,
        ),
        StructuredProgressEvent(
            event_id="evt-1",
            sequence=1,
            task_id="workspace-1",
            revision=1,
            run_id="run-1",
            stage=ProgressStage.INSPECT_SOURCES,
            event_type="source.inspection_completed",
            summary="已检查 1 个来源",
            audience=ProgressAudience.ALL,
        ),
    )

    user_view = ProgressProjection().project(
        events,
        audience=ProgressAudience.USER,
        task_status="running",
    )
    admin_view = ProgressProjection().project(
        events,
        audience=ProgressAudience.ADMIN,
        task_status="running",
    )

    assert user_view.active_stage is ProgressStage.PREPARE_CAPABILITIES
    assert sum(item.status == "active" for item in user_view.stages) == 1
    assert [event.event_id for event in user_view.events] == ["evt-1", "evt-2"]
    assert user_view.events[-1].progress is not None
    assert user_view.events[-1].progress.total is None
    assert "host_path" not in user_view.events[-1].refs
    public_json = user_view.events[-1].model_dump_json()
    assert "secret" not in public_json
    assert "npm ci" not in public_json
    assert "重试" in public_json
    assert [event.event_id for event in admin_view.events] == [
        "evt-1",
        "evt-2",
        "evt-admin",
    ]


def test_progress_projection_recovers_after_a_successful_tool_retry() -> None:
    """可恢复动作先失败后成功时，不得把整个业务阶段永久标记为失败。"""

    events = (
        StructuredProgressEvent(
            event_id="inspect-started",
            sequence=1,
            task_id="workspace-1",
            revision=1,
            stage=ProgressStage.INSPECT_SOURCES,
            event_type="tool.started",
            summary="正在发现候选内容",
        ),
        StructuredProgressEvent(
            event_id="inspect-attempt-failed",
            sequence=2,
            task_id="workspace-1",
            revision=1,
            stage=ProgressStage.INSPECT_SOURCES,
            event_type="tool.failed",
            summary="首次发现失败，正在调整",
        ),
        StructuredProgressEvent(
            event_id="inspect-retry-completed",
            sequence=3,
            task_id="workspace-1",
            revision=1,
            stage=ProgressStage.INSPECT_SOURCES,
            event_type="tool.completed",
            summary="候选发现已完成",
        ),
    )

    view = ProgressProjection().project(
        events,
        audience=ProgressAudience.USER,
        task_status="running",
    )
    stage = next(
        item
        for item in view.stages
        if item.stage is ProgressStage.INSPECT_SOURCES
    )

    assert stage.status == "completed"
    assert stage.summary == "候选发现已完成"


def test_progress_projection_hides_unobserved_optional_capability_stage() -> None:
    """未选择或获取额外能力时，不得让完成任务残留“准备能力尚未开始”。"""

    events = (
        StructuredProgressEvent(
            event_id="understand-completed",
            sequence=1,
            task_id="workspace-1",
            revision=1,
            stage=ProgressStage.UNDERSTAND,
            event_type="stage_completed",
            summary="已确认任务范围与完成条件",
        ),
        StructuredProgressEvent(
            event_id="deliver-completed",
            sequence=2,
            task_id="workspace-1",
            revision=1,
            stage=ProgressStage.DELIVER,
            event_type="task_completed",
            summary="正式交付已发布",
        ),
    )

    view = ProgressProjection().project(
        events,
        audience=ProgressAudience.USER,
        task_status="completed",
    )

    assert ProgressStage.PREPARE_CAPABILITIES not in {
        item.stage for item in view.stages
    }
    assert len(view.stages) == 5


def test_progress_projection_exposes_safe_capability_identity_to_user() -> None:
    """普通用户可见专业能力身份，但路径、摘要和调用参数必须继续失败关闭。"""

    event = StructuredProgressEvent(
        event_id="capability-completed",
        sequence=1,
        task_id="workspace-1",
        revision=1,
        stage=ProgressStage.PREPARE_CAPABILITIES,
        event_type="stage_completed",
        summary="已准备 1 项能力：MinerU 文档解析（Tool）",
        refs={
            "capabilities": [
                {
                    "name": "MinerU 文档解析",
                    "kind": "tool",
                    "version": "2.1.0",
                    "purpose": "解析 PDF 文档结构",
                    "digest": "sha256:secret",
                    "host_path": "C:/secret",
                    "arguments": {"token": "secret"},
                }
            ],
            "workspace_root": "C:/secret",
        },
    )

    user_view = ProgressProjection().project(
        (event,),
        audience=ProgressAudience.USER,
        task_status="completed",
    )
    admin_view = ProgressProjection().project(
        (event,),
        audience=ProgressAudience.ADMIN,
        task_status="completed",
    )

    assert user_view.events[0].refs == {
        "capabilities": [
            {
                "name": "MinerU 文档解析",
                "kind": "tool",
                "version": "2.1.0",
                "purpose": "解析 PDF 文档结构",
            }
        ]
    }
    assert admin_view.events[0].refs["workspace_root"] == "C:/secret"
    assert admin_view.events[0].refs["capabilities"][0]["digest"] == (
        "sha256:secret"
    )


def test_status_intent_with_material_delta_still_requires_revision() -> None:
    """模型把混合输入标成状态询问时，结构化业务差异仍必须失败关闭。"""

    delta = ContextDelta(
        delta_id="delta-mixed",
        owner_id="user-a",
        task_id="workspace-1",
        source_turn_ids=("turn-mixed",),
        inherited_revision=1,
        intent=TurnIntent.STATUS_QUESTION,
        normalized_text="现在到哪了，并增加部门字段",
        field_semantics_delta={"department": "报销人所属部门"},
        confidence=DeltaConfidence.HIGH,
    )

    assert SemanticDiffGate.classify(delta) is SteeringAction.REVISION_PROPOSAL


def test_unconfirmed_external_rewrite_fails_closed_without_calling_provider(
    tmp_path,
) -> None:
    request = SteeringRequest(
        owner_id="user-a",
        task_id="workspace-1",
        revision=1,
        run_id="run-1",
        text="把音频交给新的外部 ASR",
        current_status="running",
        provider="deepseek",
        model="deepseek-v4-pro",
        external_api_confirmed=False,
    )
    steering = ConversationSteering(
        SqliteSteeringRepository(str(tmp_path / "steering.db")),
        build_context_rewriter(request),
    )

    result = asyncio.run(steering.handle_turn(request))

    assert result.action is SteeringAction.PERMISSION_REQUEST
    assert result.answer is None


def test_memory_adapter_uses_same_steering_interface() -> None:
    repository = InMemorySteeringRepository()
    steering = ConversationSteering(repository, _StatusRewriter())
    request = SteeringRequest(
        owner_id="user-a",
        task_id="workspace-memory",
        revision=1,
        run_id="run-memory",
        text="现在做到哪了？",
        idempotency_key="memory-1",
        current_status="running",
    )

    first = asyncio.run(steering.handle_turn(request))
    repeated = asyncio.run(steering.handle_turn(request))

    assert first == repeated
    assert repository.get_turn("user-b", first.turn_id) is None


def test_frozen_corpora_and_forward_migration_dry_run() -> None:
    fixture_root = Path("tests/fixtures/conversation_steering")
    rewrite_cases = json.loads(
        (fixture_root / "rewrite_cases.json").read_text(encoding="utf-8")
    )
    permission_cases = json.loads(
        (fixture_root / "capability_permission_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(rewrite_cases) >= 24
    assert len({item["id"] for item in rewrite_cases}) == len(rewrite_cases)
    assert len(permission_cases) >= 12
    assert len({item["id"] for item in permission_cases}) == len(permission_cases)

    migration = Path(
        "src/conversation_steering/migrations/0001_conversation_steering.sql"
    ).read_text(encoding="utf-8")
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE legacy_data (id TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO legacy_data VALUES ('old', 'preserve')")
    connection.executescript(migration)
    connection.executescript(migration)
    assert connection.execute(
        "SELECT value FROM legacy_data WHERE id='old'"
    ).fetchone() == ("preserve",)
    created = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "conversation_raw_turns",
        "conversation_context_deltas",
        "conversation_revision_proposals",
        "conversation_revision_decisions",
        "conversation_steering_results",
    } <= created
