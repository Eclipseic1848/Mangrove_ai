# -*- coding: utf-8 -*-
"""Phase 4B 批次 7：正式数据工作台 API。"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile
from typing import Any, Literal

import duckdb
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    status,
)
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from src.agentic_runtime.models import (
    PermissionProfile,
    RuntimeTaskConfig,
    RuntimeVersion,
)
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_current_user, get_store, is_admin_role
from src.api.catalog_actor import catalog_actor_from_user
from src.api.semantic_workspace_runtime import (
    get_semantic_workspace_manager,
)
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountGateRejected,
    CapabilityPackRef,
    DefaultCapabilityMounts,
    SqliteCapabilityCatalogRepository,
)
from src.config.settings import settings
from src.conversation_steering import (
    CapabilityMaturity,
    ConversationSteering,
    ProgressAudience,
    ProgressProjection,
    ProgressStage,
    ProgressValue,
    RevisionDecisionStatus,
    RevisionProposalStatus,
    RevisionSwitchMode,
    SqliteSteeringRepository,
    SteeringAction,
    SteeringRequest,
    StructuredProgressEvent,
    build_context_rewriter,
)
from src.model_connections import GrantError, get_default_broker
from src.delivery_publishing.models import TableOutputContract
from src.services.upload_store import UploadStore


router = APIRouter(
    prefix="/api/semantic-workspace",
    tags=["semantic-workspace"],
)

_FORMATS = {
    "json",
    "jsonl",
    "csv",
    "xlsx",
    "parquet",
    "docx",
    "pdf",
    "html",
    "markdown",
    "txt",
    "pptx",
}
_DOCUMENT_INPUTS = {".docx", ".pdf"}
_TABLE_INPUTS = {".xlsx", ".csv", ".tsv", ".json", ".jsonl", ".parquet"}
_OUTPUT_FORMAT_PATTERN = re.compile(
    r"(?:输出|导出|生成)(?:为|成)?\s*"
    r"(JSONL|JSON|CSV|XLSX|DOCX|PDF|HTML|MARKDOWN|MD|TXT|PPTX)",
    re.IGNORECASE,
)
_TERMINAL = {
    "completed",
    "candidate_ready",
    "failed",
    "cancelled",
    "needs_input",
}


def _capability_catalog() -> CapabilityCatalog:
    """灰度入口显式执行纯新增目录迁移，不在普通任务启动时隐式写库。"""

    return CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )


def _require_capability_gray(user: dict[str, Any]) -> None:
    if not is_admin_role(user.get("role")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="任务级能力灰度只对管理员和超级管理员开放",
        )
    if not settings.pi_capability_host_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="任务级能力 Sidecar 灰度尚未启用",
        )


def _runtime_gate():
    """#13 冻结拦截共用的运行时门；与装载 Seam 是同一 check_mount 实现。"""
    from src.api.capability_governance_runtime import get_runtime_gate

    return get_runtime_gate()


def _runtime_gate_projection_governance():
    """选择列表的只读投影装配；不装配平台发布/签名依赖。"""
    from src.capability_governance import (
        CapabilityGovernance,
        SqliteCapabilityGovernanceRepository,
    )

    return CapabilityGovernance(
        _capability_catalog(),
        SqliteCapabilityGovernanceRepository(settings.webui_db_path),
    )


def _selectable_for_task(projection) -> bool:
    """新任务选择的三轴过滤；deprecated/revoked/quarantined/draft 不可选。

    历史冻结任务的恢复走 resolve_selection 路径，不受此谓词影响。
    """
    from src.capability_governance import (
        CapabilityEligibility,
        CapabilityLifecycle,
        CapabilityMaturity,
    )

    return (
        projection.maturity is CapabilityMaturity.VERIFIED
        and projection.lifecycle is CapabilityLifecycle.ACTIVE
        and projection.eligibility is CapabilityEligibility.ELIGIBLE
    )


def _check_freeze_gate(
    actor,
    pack_refs: tuple[CapabilityPackRef, ...],
    catalog: CapabilityCatalog,
    *,
    validation_target: CapabilityPackRef | None = None,
) -> None:
    """冻结前执行完整装载门 + 新任务可选谓词；拒绝以 409 失败关闭。

    装载门放行 DEPRECATED 是为历史恢复装载；冻结是「新任务」入口，
    必须再按三轴可选谓词拦截（AC3：deprecated 不进入新任务选择）。
    validation_target（#15 D9）：验证任务标记匹配的 ref 走豁免路径——
    仅跳过成熟度与可选谓词，门内其余条件（Owner/生命周期/资格）仍强制。
    """
    gate = _runtime_gate()
    governance = _runtime_gate_projection_governance()
    for ref in pack_refs:
        pack = catalog.resolve_pack(actor, ref.pack_id, ref.version)
        if pack is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="能力包不存在或当前用户不可见",
            )
        if pack.digest != ref.digest:
            # 身份失配是调用方输入错误（引用伪造或版本漂移），
            # 保持 422 语义；目录 freeze_selection 仍二次复核。
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="能力包 digest 与引用不一致",
            )
        exempt = validation_target is not None and validation_target == ref
        try:
            gate.check_mount(actor, pack, validation_exempt=exempt)
        except CapabilityMountGateRejected as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        if not exempt and not _selectable_for_task(
            governance.runtime_projection_for_pack(pack)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="能力当前不可用于新任务选择",
            )


def _inherit_capability_selection(
    owner_id: str,
    *,
    source_task_id: str,
    source_revision: int,
    target_task_id: str,
    target_revision: int,
) -> bool:
    """版本变化只复制冻结身份；没有目录表或旧选择时保持既有路径。"""

    resolver = DefaultCapabilityMounts(
        db_path=settings.webui_db_path,
        oci_layout_path=settings.capability_oci_layout_path,
        mount_root=settings.capability_mount_cache_path,
    )
    return resolver.copy_selection_for_owner(
        owner_id,
        source_task_id=source_task_id,
        source_revision=source_revision,
        target_task_id=target_task_id,
        target_revision=target_revision,
    )


class WorkspaceTaskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_text: str = Field(min_length=1, max_length=20_000)
    upload_ids: tuple[str, ...] = Field(min_length=1)
    output_formats: tuple[str, ...] = ("xlsx",)
    table_output_contracts: tuple[TableOutputContract, ...] = ()
    provider: str = Field(default="local", min_length=1)
    model: str | None = Field(default=None, min_length=1)
    external_api_confirmed: bool = False
    runtime_version: RuntimeVersion = RuntimeVersion.LEGACY
    permission_profile: PermissionProfile = PermissionProfile.STANDARD
    model_connection_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    model_connection_model: str | None = Field(default=None, min_length=1, max_length=200)
    capability_pack_refs: tuple[CapabilityPackRef, ...] = ()
    # #15 D9 验证任务标记：本任务是为验证该个人 draft 能力而创建；
    # 仅在 create_task 校验后随冻结 selection 落库。
    validation_target: CapabilityPackRef | None = None

    @field_validator("objective_text", "provider")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("upload_ids")
    @classmethod
    def unique_uploads(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("upload_ids 不得重复")
        return value

    @field_validator("output_formats")
    @classmethod
    def valid_formats(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if not normalized:
            raise ValueError("至少选择一种正式输出格式")
        invalid = set(normalized) - _FORMATS
        if invalid:
            raise ValueError(f"不支持的正式输出格式：{sorted(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("output_formats 不得重复")
        return normalized

    @field_validator("capability_pack_refs")
    @classmethod
    def unique_capabilities(
        cls,
        value: tuple[CapabilityPackRef, ...],
    ) -> tuple[CapabilityPackRef, ...]:
        identities = {(item.pack_id, item.version) for item in value}
        if len(identities) != len(value):
            raise ValueError("capability_pack_refs 不得重复")
        return value

    @model_validator(mode="after")
    def validate_validation_target(self) -> "WorkspaceTaskCreateIn":
        contract_formats = [
            item.format for item in self.table_output_contracts
        ]
        if len(contract_formats) != len(set(contract_formats)):
            raise ValueError("同一输出格式只能冻结一个表格契约")
        if not set(contract_formats).issubset(self.output_formats):
            raise ValueError("表格输出契约必须绑定正式输出格式")
        if (
            self.table_output_contracts
            and self.runtime_version is not RuntimeVersion.PI
        ):
            raise ValueError("表格输出契约只能由 Pi Runtime 执行")
        # #15 D9：验证目标必须同时出现在能力选择中（否则豁免无载体）。
        if (
            self.validation_target is not None
            and not any(
                item == self.validation_target for item in self.capability_pack_refs
            )
        ):
            raise ValueError("验证目标必须同时出现在能力选择中")
        return self


class WorkspaceAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=10_000)


class WorkspaceTurnIn(BaseModel):
    """运行中追问或修改；先理解差异，不直接改写任务。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)
    external_api_confirmed: bool = False

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class WorkspaceRevisionDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RevisionSwitchMode
    external_api_confirmed: bool = False


class WorkspaceRevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=20_000)
    output_formats: tuple[str, ...] | None = None
    table_output_contracts: tuple[TableOutputContract, ...] | None = None
    external_api_confirmed: bool = False

    @field_validator("instruction")
    @classmethod
    def strip_instruction(cls, value: str) -> str:
        return value.strip()

    @field_validator("output_formats")
    @classmethod
    def valid_revision_formats(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized = tuple(item.strip().lower() for item in value)
        if not normalized:
            raise ValueError("至少选择一种正式输出格式")
        invalid = set(normalized) - _FORMATS
        if invalid:
            raise ValueError(f"不支持的正式输出格式：{sorted(invalid)}")
        return normalized

    @model_validator(mode="after")
    def validate_table_output_contracts(self) -> "WorkspaceRevisionIn":
        if self.table_output_contracts is None:
            return self
        contract_formats = [
            item.format for item in self.table_output_contracts
        ]
        if len(contract_formats) != len(set(contract_formats)):
            raise ValueError("同一输出格式只能冻结一个表格契约")
        if (
            self.output_formats is not None
            and not set(contract_formats).issubset(self.output_formats)
        ):
            raise ValueError("表格输出契约必须绑定正式输出格式")
        return self


def _uploads() -> UploadStore:
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


def _runtime_repository() -> AgenticRuntimeRepository:
    return AgenticRuntimeRepository(settings.webui_db_path)


def _steering_repository() -> SqliteSteeringRepository:
    return SqliteSteeringRepository(settings.webui_db_path)


def _public_runtime(
    user_id: str,
    task_id: str,
    revision: int,
) -> dict[str, Any]:
    row = _runtime_repository().get(user_id, task_id, revision)
    if row is None:
        return {
            "runtime_version": RuntimeVersion.LEGACY.value,
            "permission_profile": PermissionProfile.STANDARD.value,
            "model_connection_id": None,
            "external_api_confirmed": False,
            "status": None,
            "candidates": [],
            "provider_usage": [],
            "events": [],
        }
    coverage = (
        _runtime_repository().get_coverage(
            user_id=user_id,
            task_id=task_id,
            revision=revision,
            run_id=row["run_id"],
        )
        if row["run_id"]
        else None
    )
    verification = (
        row["verification"].model_dump(mode="json")
        if row["verification"]
        else None
    )
    if verification and verification.get("status") == "inconclusive":
        for check in verification.get("checks", []):
            if check.get("code") == "semantic_goal" and not check.get("passed"):
                # 兼容历史任务：持久化的第三方解析栈不得继续暴露给普通工作台。
                check["summary"] = (
                    "语义验证服务暂时不可用，请稍后重新验证候选。"
                )
    return {
        "runtime_version": row["runtime_version"].value,
        "permission_profile": row["permission_profile"].value,
            "model_connection_id": row["model_connection_id"],
            "model_connection_model": row["model_connection_model"],
        "external_api_confirmed": row["external_api_confirmed"],
        "status": row["status"].value,
        "run_id": row["run_id"],
        "session_file": row["session_file"],
        "summary": (
            (row["request"] or {}).get("objective_text")
            if row["request"]
            else None
        ),
        "candidates": [
            {
                **item.public_dict(task_id=task_id, revision=revision),
                "download_allowed": _candidate_download_allowed(
                    row["verification"],
                    candidate_format=item.format,
                ),
            }
            for item in row["candidates"]
        ],
        "verification": verification,
        "failure": row["failure"],
        "provider_usage": (
            get_default_broker().list_usage(
                user_id,
                task_id=task_id,
                revision=revision,
            )
            if row["model_connection_id"]
            else []
        ),
        "events": _runtime_repository().list_events(
            user_id, task_id, revision
        ),
        "coverage": (
            {
                "contract": coverage[0].model_dump(mode="json"),
                "ledger": coverage[1].model_dump(mode="json"),
                "progress": coverage[1].public_progress(),
            }
            if coverage
            else None
        ),
    }


_SAFE_TEXT_CANDIDATE_FORMATS = {"csv", "json", "jsonl", "markdown", "txt"}


def _candidate_download_allowed(
    verification,
    *,
    candidate_format: str,
) -> bool:
    """安全文本候选允许 Owner 人工检查；主动内容和复杂容器继续失败关闭。"""

    if candidate_format in _SAFE_TEXT_CANDIDATE_FORMATS:
        # 候选进入仓库前已经过格式重开、路径归属和哈希检查。来源证据失败只表示
        # 内容不能成为正式交付，不应让任务所有者失去检查自己纯文本候选的能力。
        return True

    if verification is None:
        return True
    blocked_codes = {"manifest", "artifact_set", "source_grounding"}
    return not any(
        not check.passed and check.code in blocked_codes
        for check in verification.checks
    )


@router.get("/guidance")
def workspace_guidance(user=Depends(get_current_user)):
    del user
    path = (
        Path(__file__).resolve().parents[3]
        / "config"
        / "workspace_guidance.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _task_or_404(user_id: str, task_id: str) -> dict[str, Any]:
    task = get_store().get_semantic_workspace_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="工作台任务不存在")
    return task


@router.get("/capabilities")
def list_gray_capabilities(user=Depends(get_current_user)):
    """只列出已验证的平台能力；草稿和执行配置不进入灰度选择界面。"""

    _require_capability_gray(user)
    actor = catalog_actor_from_user(user)
    catalog = _capability_catalog()
    governance = _runtime_gate_projection_governance()
    items = []
    for pack in catalog.list_visible_packs(actor):
        if pack.maturity is not CapabilityMaturity.VERIFIED:
            continue
        projection = governance.runtime_projection_for_pack(pack)
        if not _selectable_for_task(projection):
            # deprecated/revoked/quarantined 不进入新任务选择（AC3）。
            continue
        manifest = dict(pack.manifest)
        kind = manifest.get("kind", "capability_pack")
        if kind not in {
            "tool",
            "mcp_local",
            "skill",
            "dependency_bundle",
            "capability_pack",
        }:
            kind = "capability_pack"
        items.append({
            "pack_id": pack.pack_id,
            "version": pack.version,
            "digest": pack.digest,
            "name": manifest.get("display_name") or pack.pack_id,
            "kind": kind,
            "purpose": (
                manifest.get("purpose")
                or "提供当前任务所需的专业处理能力"
            ),
            "scope": pack.scope.value,
            # 推荐指针（#14 回滚命令折叠）；推荐是默认值不是约束。
            "recommended": (
                projection.recommended_version == pack.version
            ),
        })
    # 推荐版本置顶；无指针的 pack 保持目录既有顺序（旧路径零回归）。
    items.sort(key=lambda item: not item["recommended"])
    return {"enabled": True, "items": items}


@router.get("/tasks/{task_id}/capabilities")
def get_task_capabilities(
    task_id: str,
    revision: int = Query(default=1, ge=1),
    user=Depends(get_current_user),
):
    _task_or_404(user["user_id"], task_id)
    resolver = DefaultCapabilityMounts(
        db_path=settings.webui_db_path,
        oci_layout_path=settings.capability_oci_layout_path,
        mount_root=settings.capability_mount_cache_path,
    )
    items = resolver.describe_for_owner(user["user_id"], task_id, revision)
    return {
        "task_id": task_id,
        "revision": revision,
        "items": [item.model_dump(mode="json") for item in items],
    }


def _revision_events(
    events: list[dict[str, Any]],
    revision: int,
) -> list[dict[str, Any]]:
    """按 revision_created 边界切分工作台事件，避免历史版本串入后续轨迹。"""
    boundaries = [
        event["sequence"]
        for event in events
        if event["event_type"] == "revision_created"
    ]
    if revision >= 2 and len(boundaries) < revision - 1:
        return []
    start = boundaries[revision - 2] if revision >= 2 else 0
    end = (
        boundaries[revision - 1]
        if revision - 1 < len(boundaries)
        else None
    )
    return [
        event
        for event in events
        if event["sequence"] >= start
        and (end is None or event["sequence"] < end)
    ]


_PROGRESS_STAGE_ALIASES = {
    "queued": ProgressStage.UNDERSTAND,
    "interpret": ProgressStage.UNDERSTAND,
    "understand": ProgressStage.UNDERSTAND,
    "goal_interpretation": ProgressStage.UNDERSTAND,
    "inspect": ProgressStage.INSPECT_SOURCES,
    "inspect_sources": ProgressStage.INSPECT_SOURCES,
    "source_probe": ProgressStage.INSPECT_SOURCES,
    "source_discovery": ProgressStage.INSPECT_SOURCES,
    "bind": ProgressStage.INSPECT_SOURCES,
    "prepare_capabilities": ProgressStage.PREPARE_CAPABILITIES,
    "execute": ProgressStage.EXECUTE,
    "repair": ProgressStage.EXECUTE,
    "agent_execute": ProgressStage.EXECUTE,
    "evidence_read": ProgressStage.EXECUTE,
    "verify": ProgressStage.VERIFY,
    "check": ProgressStage.VERIFY,
    "verify_coverage": ProgressStage.VERIFY,
    "deliver": ProgressStage.DELIVER,
    "output": ProgressStage.DELIVER,
}

_PROGRESS_TOOL_SUMMARIES = {
    "freeze_coverage": {
        "tool.started": "正在确认任务范围与完成条件",
        "tool.completed": "已确认任务范围与完成条件",
        "tool.failed": "任务范围确认遇到问题，正在调整",
    },
    "inspect_source": {
        "tool.started": "正在检查来源结构",
        "tool.completed": "已完成来源结构检查",
        "tool.failed": "来源结构检查遇到问题，正在调整",
    },
    "discover_content": {
        "tool.started": "正在定位候选内容",
        "tool.completed": "已完成候选内容定位",
        "tool.failed": "候选内容定位遇到问题，正在调整",
    },
    "read_evidence": {
        "tool.started": "正在读取并核对来源证据",
        "tool.completed": "已完成来源证据读取",
        "tool.failed": "来源证据读取遇到问题，正在调整",
    },
    "propose_completion": {
        "tool.started": "正在检查结果完整性",
        "tool.completed": "已完成结果完整性检查",
        "tool.failed": "结果完整性检查未通过，正在补充处理",
    },
}


def _progress_stage(value: str) -> ProgressStage:
    normalized = value.strip().lower()
    return _PROGRESS_STAGE_ALIASES.get(normalized, ProgressStage.EXECUTE)


def _progress_summary(event: dict[str, Any], details: dict[str, Any]) -> str:
    tool = str(details.get("tool") or "")
    runtime_event_type = str(
        details.get("runtime_event_type")
        or event.get("event_type")
        or event.get("type")
        or ""
    )
    return _PROGRESS_TOOL_SUMMARIES.get(tool, {}).get(
        runtime_event_type,
        str(event.get("summary") or "正在处理"),
    )


def _structured_progress_events(task: dict[str, Any]) -> tuple[StructuredProgressEvent, ...]:
    projected: list[StructuredProgressEvent] = []
    raw_events = list(task["events"])
    raw_events.extend(
        {
            **event,
            "stage": event.get("node", "execute"),
        }
        for event in task["harness_events"]
    )
    for index, event in enumerate(raw_events, start=1):
        details = event.get("details") or {}
        raw_progress = details.get("progress")
        progress = None
        if isinstance(raw_progress, dict):
            try:
                progress = ProgressValue.model_validate(raw_progress)
            except ValueError:
                progress = None
        try:
            audience = ProgressAudience(details.get("audience", "all"))
        except ValueError:
            audience = ProgressAudience.ALL
        projected.append(
            StructuredProgressEvent(
                event_id=event.get("event_id") or f"legacy_{index}",
                # 工作台与 Harness 各自维护 sequence；兼容投影按合并后的
                # 事实顺序重新编号，避免两个 sequence=1 造成活动阶段倒退。
                sequence=index,
                task_id=task["task_id"],
                revision=int(task["viewing_revision"]),
                run_id=event.get("run_id") or task.get("run_id"),
                stage=_progress_stage(str(event.get("stage") or "execute")),
                event_type=str(event.get("event_type") or event.get("type") or "progress"),
                # 主时间线对新旧任务统一使用业务语言；原始事件仍供管理员诊断。
                summary=_progress_summary(event, details),
                progress=progress,
                refs=(details.get("refs") if isinstance(details.get("refs"), dict) else {}),
                action=(details.get("action") if isinstance(details.get("action"), dict) else None),
                audience=audience,
                created_at=event.get("created_at") or datetime.now().astimezone(),
            )
        )
    return tuple(projected)


def _task_detail(
    user_id: str,
    task_id: str,
    *,
    revision: int | None = None,
    audience: ProgressAudience = ProgressAudience.USER,
) -> dict[str, Any]:
    store = get_store()
    task = _task_or_404(user_id, task_id)
    task["current_status"] = task["status"]
    task["current_revision"] = task["active_revision"]
    task["revisions"] = store.list_semantic_workspace_revisions(
        user_id, task_id
    )
    selected_revision = (
        store.get_semantic_workspace_revision(
            user_id, task_id, revision
        )
        if revision is not None
        else store.get_semantic_workspace_revision(
            user_id, task_id, task["active_revision"]
        )
    )
    if selected_revision is None:
        raise HTTPException(status_code=404, detail="结果版本不存在")
    task["viewing_revision"] = selected_revision["revision"]
    if selected_revision["revision"] != task["active_revision"]:
        task.update(
            {
                key: selected_revision[key]
                for key in (
                    "objective_text",
                    "output_formats",
                    "plan_id",
                    "logical_revision",
                    "binding_revision",
                    "run_id",
                    "status",
                    "summary",
                )
            }
        )
    task["events"] = _revision_events(
        store.list_semantic_workspace_events(user_id, task_id),
        selected_revision["revision"],
    )
    task["uploads"] = []
    for upload_id in task["upload_ids"]:
        try:
            item = _uploads().resolve(user_id, upload_id)
        except PermissionError:
            continue
        task["uploads"].append(
            item.model_dump(exclude={"user_id", "storage_path"})
        )
    task["plan"] = None
    if task["plan_id"] and task["logical_revision"]:
        plan = store.get_semantic_plan_revision(
            user_id,
            task["plan_id"],
            task["logical_revision"],
        )
        if plan:
            task["plan"] = {
                "revision": plan["revision"],
                "summary": plan["summary"],
                "plan": plan["plan"],
                "diagnostics": plan["diagnostics"],
            }
    task["run"] = None
    task["attempts"] = []
    task["harness_events"] = []
    task["delivery"] = None
    if task["run_id"]:
        task["run"] = store.get_semantic_harness_run(
            user_id, task["run_id"]
        )
        task["attempts"] = store.list_semantic_harness_attempts(
            user_id, task["run_id"]
        )
        task["harness_events"] = store.list_semantic_harness_events(
            user_id, task["run_id"]
        )
        task["delivery"] = store.latest_semantic_delivery(
            user_id, task["run_id"]
        )
    task["agentic_runtime"] = _public_runtime(
        user_id,
        task_id,
        selected_revision["revision"],
    )
    task["runtime_version"] = task["agentic_runtime"]["runtime_version"]
    task["permission_profile"] = task["agentic_runtime"][
        "permission_profile"
    ]
    task["model_connection_id"] = task["agentic_runtime"][
        "model_connection_id"
    ]
    task["progress"] = ProgressProjection().project(
        _structured_progress_events(task),
        audience=audience,
        task_status=task["status"],
    ).model_dump(mode="json")
    return task


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    payload: WorkspaceTaskCreateIn,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    capability_catalog = None
    if payload.capability_pack_refs:
        _require_capability_gray(user)
        if payload.runtime_version is not RuntimeVersion.PI:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="任务能力只能冻结到 vNext TaskRevision",
            )
        capability_catalog = _capability_catalog()
        actor = catalog_actor_from_user(user)
        if payload.validation_target is not None:
            # #15 D9 验证目标资格：本人所有的个人包（平台包/他人包不能
            # 作为验证目标；gate 内仍强制其余三轴）。「必须同时被选择」
            # 已由模型 validator 保证。
            target_pack = capability_catalog.resolve_pack(
                actor,
                payload.validation_target.pack_id,
                payload.validation_target.version,
            )
            from src.conversation_steering import ProcedureScope

            if (
                target_pack is None
                or target_pack.digest != payload.validation_target.digest
                or target_pack.scope is not ProcedureScope.PERSONAL
                or target_pack.owner_id != user_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="验证目标必须是自己的个人能力",
                )
        # 创建与冻结共用装载门（AC4 同一公开 Interface）；digest 精确
        # 匹配仍由目录 freeze_selection 复核。
        _check_freeze_gate(
            actor,
            payload.capability_pack_refs,
            capability_catalog,
            validation_target=payload.validation_target,
        )
    connection_binding = None
    if (
        payload.runtime_version is RuntimeVersion.PI
        and payload.model_connection_id is None
    ):
        preference = get_default_broker().get_usage_preference(user_id)
        if preference is not None:
            if not preference["available"]:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="默认模型连接已失效，请重新选择",
                )
            payload.model_connection_id = str(preference["connection_id"])
            payload.model_connection_model = str(preference["model_id"])
    if (
        payload.model_connection_id is not None
        and payload.runtime_version is not RuntimeVersion.PI
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="模型连接当前只能用于 vNext 任务",
        )
    if payload.runtime_version is RuntimeVersion.PI:
        if not settings.pi_runtime_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Pi 灰度入口当前未启用",
            )
        if payload.permission_profile is not PermissionProfile.STANDARD:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "当前只开放任务级容器标准增强模式；"
                    "扩展目录和宿主机权限需要单任务授权范围"
                ),
            )
        if payload.model_connection_id is not None:
            broker = get_default_broker()
            selected_connection = broker.get_connection(
                user_id,
                payload.model_connection_id,
            )
            if selected_connection is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="模型连接不存在或无权访问",
                )
            if not payload.external_api_confirmed:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="使用外部模型连接前必须确认本任务的数据外发",
                )
            if (
                payload.model_connection_model is not None
                and not any(
                    item["model_id"] == payload.model_connection_model
                    and item["status"] == "available"
                    and item["enabled"]
                    for item in selected_connection.get("models", [])
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="任务模型必须与已验证连接冻结的模型一致",
                )
            try:
                connection_binding = broker.freeze_connection(
                    user_id,
                    payload.model_connection_id,
                )
            except GrantError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="模型连接不存在或无权访问",
                ) from exc
        elif not is_admin_role(user.get("role")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="普通用户需要选择自己的连接或管理员发布的连接",
            )
        elif payload.provider.lower() != "local":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Pi 首期生产灰度只允许使用本地模型",
            )
    upload_store = _uploads()
    source_refs = []
    input_suffixes = set()
    for upload_id in payload.upload_ids:
        try:
            upload = upload_store.resolve(user_id, upload_id)
        except PermissionError as exc:
            raise HTTPException(
                status_code=404,
                detail="上传文件不存在或无权访问",
            ) from exc
        source_refs.append({
            "upload_id": upload.upload_id,
            "sha256": upload.sha256,
        })
        input_suffixes.add(Path(upload.original_name).suffix.lower())
    if (
        payload.runtime_version is RuntimeVersion.LEGACY
        and input_suffixes & _DOCUMENT_INPUTS
        and input_suffixes & _TABLE_INPUTS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "当前版本暂不支持在同一任务中混合处理文档和表格；"
                "文档和表格请分别创建任务。"
            ),
        )
    requested_in_text = {
        "markdown" if item.lower() == "md" else item.lower()
        for item in _OUTPUT_FORMAT_PATTERN.findall(payload.objective_text)
    }
    selected_formats = set(payload.output_formats)
    missing_formats = requested_in_text - selected_formats
    if missing_formats:
        requested_label = "、".join(
            item.upper() for item in sorted(requested_in_text)
        )
        selected_label = "、".join(
            item.upper() for item in sorted(selected_formats)
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"文字要求输出 {requested_label}，"
                f"但界面选择了 {selected_label}；请统一后再执行。"
            ),
        )
    task_id = f"workspace_{uuid.uuid4().hex[:16]}"
    repository = _runtime_repository()
    claimed_key = None
    if idempotency_key is not None:
        claimed_key = idempotency_key.strip()
        if not claimed_key or len(claimed_key) > 200:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Idempotency-Key 长度必须为 1 至 200 个字符",
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "payload": payload.model_dump(mode="json"),
                    "sources": source_refs,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            task_id, is_new_claim = repository.claim_idempotency(
                user_id,
                claimed_key,
                request_hash=fingerprint,
                proposed_task_id=task_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        if not is_new_claim:
            existing = get_store().get_semantic_workspace_task(
                user_id,
                task_id,
            )
            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="同一幂等请求正在创建，请稍后重试",
                )
            existing_runtime = repository.get(
                user_id,
                task_id,
                int(existing["active_revision"]),
            )
            return {
                **existing,
                "runtime_version": (
                    existing_runtime["runtime_version"].value
                    if existing_runtime
                    else RuntimeVersion.LEGACY.value
                ),
                "permission_profile": (
                    existing_runtime["permission_profile"].value
                    if existing_runtime
                    else PermissionProfile.STANDARD.value
                ),
                "model_connection_id": (
                    existing_runtime["model_connection_id"]
                    if existing_runtime
                    else None
                ),
            }
    first_line = payload.objective_text.splitlines()[0].strip()
    title = first_line[:40] + ("…" if len(first_line) > 40 else "")
    store = get_store()
    try:
        task = store.create_semantic_workspace_task(
            user_id,
            task_id=task_id,
            title=title or "未命名任务",
            objective_text=payload.objective_text,
            upload_ids=list(payload.upload_ids),
            output_formats=list(payload.output_formats),
            provider=payload.provider.lower(),
            model=payload.model,
            external_api_confirmed=payload.external_api_confirmed,
            source_refs=source_refs,
            table_output_contracts=[
                item.model_dump(mode="json")
                for item in payload.table_output_contracts
            ],
        )
        repository.register(
            RuntimeTaskConfig(
                user_id=user_id,
                task_id=task_id,
                revision=1,
                runtime_version=payload.runtime_version,
                permission_profile=payload.permission_profile,
                model_connection_id=payload.model_connection_id,
                model_connection_version=(
                    connection_binding.connection_version
                    if connection_binding
                    else None
                ),
                model_connection_model=(
                    payload.model_connection_model or connection_binding.model
                    if connection_binding
                    else None
                ),
                external_api_confirmed=bool(connection_binding),
            )
        )
        if capability_catalog is not None:
            capability_catalog.freeze_selection(
                catalog_actor_from_user(user),
                task_id=task_id,
                revision=1,
                pack_refs=payload.capability_pack_refs,
                validation_target=payload.validation_target,
            )
    except Exception:
        if claimed_key:
            repository.release_idempotency(
                user_id,
                claimed_key,
                task_id=task_id,
            )
        raise
    store.append_semantic_workspace_event(
        user_id,
        task_id,
        stage="queued",
        event_type="task_created",
        summary=f"任务已创建，共 {len(payload.upload_ids)} 个文件",
    )
    get_semantic_workspace_manager().enqueue(user_id, task_id)
    return {
        **task,
        "runtime_version": payload.runtime_version.value,
        "permission_profile": payload.permission_profile.value,
        "model_connection_id": payload.model_connection_id,
        "model_connection_model": (
            payload.model_connection_model or connection_binding.model
            if connection_binding
            else None
        ),
    }


@router.get("/tasks")
def list_tasks(
    task_status: str | None = Query(default=None, alias="status"),
    deleted: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(get_current_user),
):
    tasks = get_store().list_semantic_workspace_tasks(
        user["user_id"],
        status=task_status,
        deleted=deleted,
        limit=limit,
    )
    for task in tasks:
        runtime = _runtime_repository().get(
            user["user_id"],
            task["task_id"],
            task["active_revision"],
        )
        task["runtime_version"] = (
            runtime["runtime_version"].value
            if runtime
            else RuntimeVersion.LEGACY.value
        )
        task["permission_profile"] = (
            runtime["permission_profile"].value
            if runtime
            else PermissionProfile.STANDARD.value
        )
        task["model_connection_id"] = (
            runtime["model_connection_id"] if runtime else None
        )
        task["agentic_runtime_status"] = (
            runtime["status"].value if runtime else None
        )
    return tasks


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    revision: int | None = Query(default=None, ge=1),
    user=Depends(get_current_user),
):
    return _task_detail(
        user["user_id"],
        task_id,
        revision=revision,
        audience=(
            ProgressAudience.ADMIN
            if is_admin_role(user.get("role"))
            else ProgressAudience.USER
        ),
    )


@router.get("/tasks/{task_id}/events")
def get_task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    user=Depends(get_current_user),
):
    _task_or_404(user["user_id"], task_id)
    return get_store().list_semantic_workspace_events(
        user["user_id"], task_id, after=after
    )


@router.get("/tasks/{task_id}/stream")
def stream_task(
    task_id: str,
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    _task_or_404(user_id, task_id)
    audience = (
        ProgressAudience.ADMIN
        if is_admin_role(user.get("role"))
        else ProgressAudience.USER
    )

    def safe_progress_event(
        task: dict[str, Any],
        event: dict[str, Any],
        *,
        harness: bool = False,
    ) -> dict[str, Any] | None:
        projection_task = {
            **task,
            "viewing_revision": int(task["active_revision"]),
            "events": [] if harness else [event],
            "harness_events": [event] if harness else [],
        }
        projected = ProgressProjection().project(
            _structured_progress_events(projection_task),
            audience=audience,
            task_status=task["status"],
        )
        if not projected.events:
            return None
        return projected.events[0].model_dump(mode="json")

    async def event_gen():
        store = get_store()
        existing_events = store.list_semantic_workspace_events(
            user_id, task_id
        )
        revision_boundaries = [
            event["sequence"]
            for event in existing_events
            if event["event_type"] == "revision_created"
        ]
        workspace_after = (
            revision_boundaries[-1] - 1 if revision_boundaries else 0
        )
        harness_after = 0
        last_status = ""
        while True:
            task = store.get_semantic_workspace_task(user_id, task_id)
            if task is None:
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": "任务不存在"}, ensure_ascii=False
                    ),
                }
                break
            for event in store.list_semantic_workspace_events(
                user_id, task_id, after=workspace_after
            ):
                workspace_after = max(workspace_after, event["sequence"])
                public_event = safe_progress_event(task, event)
                if public_event is None:
                    continue
                yield {
                    "id": event["event_id"],
                    "event": "progress",
                    "data": json.dumps(
                        public_event,
                        ensure_ascii=False,
                    ),
                }
            if task["run_id"]:
                for event in store.list_semantic_harness_events(
                    user_id, task["run_id"]
                ):
                    if event["sequence"] <= harness_after:
                        continue
                    harness_after = event["sequence"]
                    public_event = safe_progress_event(
                        task,
                        event,
                        harness=True,
                    )
                    if public_event is None:
                        continue
                    yield {
                        "id": event["event_id"],
                        "event": "progress",
                        "data": json.dumps(
                            public_event,
                            ensure_ascii=False,
                        ),
                    }
            if task["status"] != last_status:
                last_status = task["status"]
                yield {
                    "event": "status",
                    "data": json.dumps(
                        {
                            "task_id": task_id,
                            "status": task["status"],
                            "question": task["question"],
                            "error": task["error"],
                            "failure": task["failure"],
                        },
                        ensure_ascii=False,
                    ),
                }
            if task["status"] in _TERMINAL:
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {"status": task["status"]},
                        ensure_ascii=False,
                    ),
                }
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(
        event_gen(),
        ping=15,
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/tasks/{task_id}/turns")
async def steer_task(
    task_id: str,
    payload: WorkspaceTurnIn,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    user=Depends(get_current_user),
):
    """理解运行中追问；在用户确认前绝不修改活动 revision。"""

    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    events = get_store().list_semantic_workspace_events(user_id, task_id)
    request = SteeringRequest(
        owner_id=user_id,
        task_id=task_id,
        revision=int(task["active_revision"]),
        run_id=task.get("run_id"),
        text=payload.text,
        idempotency_key=idempotency_key,
        current_status=task["status"],
        status_summary=task.get("summary") or "",
        current_goal=task.get("objective_text") or "",
        event_summaries=tuple(
            str(event.get("summary") or "") for event in events[-8:]
        ),
        provider=task.get("provider") or "local",
        model=task.get("model"),
        external_api_confirmed=bool(
            task.get("external_api_confirmed")
            or payload.external_api_confirmed
        ),
    )
    try:
        service = ConversationSteering(
            _steering_repository(),
            build_context_rewriter(request),
        )
        result = await service.handle_turn(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    # HTTP 幂等重放不能制造重复进度事件；结果 ID 是同一语义回合的稳定边界。
    if not any(
        event.get("details", {}).get("steering_result_id")
        == result.result_id
        for event in events
    ):
        event_type = {
            SteeringAction.ANSWER_ONLY: "followup.answered_without_change",
            SteeringAction.NORMALIZED_NO_MATERIAL_CHANGE: "context.rewrite_completed",
            SteeringAction.REVISION_PROPOSAL: "context.revision_proposed",
            SteeringAction.NEW_TASK_PROPOSAL: "context.new_task_proposed",
            SteeringAction.PERMISSION_REQUEST: "context.permission_required",
        }[result.action]
        current_stage = events[-1]["stage"] if events else "understand"
        get_store().append_semantic_workspace_event(
            user_id,
            task_id,
            stage=current_stage,
            event_type=event_type,
            summary=result.answer or result.acknowledgement,
            details={
                "steering_result_id": result.result_id,
                "proposal_id": result.proposal_id,
                "revision": result.revision,
                "run_id": result.run_id,
            },
        )
    return result.model_dump(mode="json")


@router.get("/tasks/{task_id}/turns")
def list_steering_turns(
    task_id: str,
    user=Depends(get_current_user),
):
    """恢复原话、语义差异和待确认草案，不把转写冒充用户原话。"""

    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    repository = _steering_repository()
    proposals = []
    for proposal in repository.list_proposals(user_id, task_id):
        if (
            proposal.status is RevisionProposalStatus.PENDING
            and proposal.base_revision != int(task["active_revision"])
        ):
            proposal = repository.update_proposal(
                proposal.model_copy(update={"status": RevisionProposalStatus.EXPIRED})
            )
        proposals.append(proposal.model_dump(mode="json"))
    turns = repository.list_turns(user_id, task_id)
    return {
        "turns": [turn.model_dump(mode="json") for turn in turns],
        "deltas": [
            delta.model_dump(mode="json")
            for turn in turns
            if (delta := repository.get_delta_for_turn(user_id, turn.turn_id))
            is not None
        ],
        "results": [
            result.model_dump(mode="json")
            for turn in turns
            if (result := repository.get_result_for_turn(user_id, turn.turn_id))
            is not None
        ],
        "proposals": proposals,
    }


@router.post("/tasks/{task_id}/revision-proposals/{proposal_id}/reject")
def reject_steering_revision(
    task_id: str,
    proposal_id: str,
    user=Depends(get_current_user),
):
    """拒绝草案只改变草案状态，绝不取消或改写当前 Run。"""

    user_id = user["user_id"]
    _task_or_404(user_id, task_id)
    repository = _steering_repository()
    proposal = repository.get_proposal(user_id, proposal_id)
    if proposal is None or proposal.task_id != task_id:
        raise HTTPException(status_code=404, detail="Revision 草案不存在或无权访问")
    if proposal.status is not RevisionProposalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Revision 草案已处理")
    rejected = repository.update_proposal(
        proposal.model_copy(update={"status": RevisionProposalStatus.REJECTED})
    )
    return rejected.model_dump(mode="json")


def _apply_confirmed_steering_revision(
    user_id: str,
    task_id: str,
    decision_id: str,
    *,
    external_api_confirmed: bool,
) -> dict[str, Any]:
    """把已确认的结构化差异应用为新版本；旧版本和旧 Run 保持可追溯。"""

    repository = _steering_repository()
    decision = repository.get_decision(user_id, decision_id)
    if decision is None or decision.task_id != task_id:
        raise HTTPException(status_code=404, detail="Revision 决策不存在或无权访问")
    if decision.status is not RevisionDecisionStatus.READY_TO_APPLY:
        raise HTTPException(status_code=409, detail="Revision 决策尚未到可应用状态")
    proposal = repository.get_proposal(user_id, decision.proposal_id)
    delta = repository.get_delta(user_id, proposal.delta_id) if proposal else None
    if proposal is None or delta is None:
        raise HTTPException(status_code=409, detail="Revision 草案数据不完整")
    task = _task_or_404(user_id, task_id)
    if int(task["active_revision"]) != decision.base_revision:
        raise HTTPException(status_code=409, detail="活动版本已变化，请重新确认修改")

    previous_runtime = _runtime_repository().get(
        user_id,
        task_id,
        decision.base_revision,
    )
    connection_binding = None
    if previous_runtime and previous_runtime["model_connection_id"]:
        if not external_api_confirmed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="新版本的数据外发范围必须再次确认",
            )
        try:
            connection_binding = get_default_broker().freeze_connection(
                user_id,
                previous_runtime["model_connection_id"],
            )
        except GrantError as exc:
            raise HTTPException(
                status_code=404,
                detail="模型连接不存在或无权访问",
            ) from exc

    formats = list(task["output_formats"])
    if delta.output_delta:
        normalized = [item.strip().lower() for item in delta.output_delta]
        invalid = set(normalized) - _FORMATS
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"语义草案包含不支持的输出格式：{sorted(invalid)}",
            )
        formats = normalized
    objective = (
        f"{task['objective_text']}\n\n"
        "已确认的上下文变更（仅应用下列差异，其余语义继承）：\n"
        f"{delta.normalized_text}"
    )
    revision = get_store().create_semantic_workspace_revision(
        user_id,
        task_id,
        objective_text=objective,
        output_formats=formats,
        change_summary=delta.normalized_text,
    )
    _runtime_repository().register(
        RuntimeTaskConfig(
            user_id=user_id,
            task_id=task_id,
            revision=revision["revision"],
            runtime_version=(
                previous_runtime["runtime_version"]
                if previous_runtime
                else RuntimeVersion.LEGACY
            ),
            permission_profile=(
                previous_runtime["permission_profile"]
                if previous_runtime
                else PermissionProfile.STANDARD
            ),
            model_connection_id=(
                previous_runtime["model_connection_id"]
                if previous_runtime
                else None
            ),
            model_connection_version=(
                connection_binding.connection_version
                if connection_binding
                else None
            ),
            model_connection_model=(
                previous_runtime["model_connection_model"]
                if previous_runtime
                else None
            ),
            external_api_confirmed=bool(connection_binding),
        )
    )
    _inherit_capability_selection(
        user_id,
        source_task_id=task_id,
        source_revision=proposal.base_revision,
        target_task_id=task_id,
        target_revision=int(revision["revision"]),
    )
    applied = repository.update_decision(
        decision.model_copy(
            update={
                "status": RevisionDecisionStatus.APPLIED,
                "updated_at": datetime.now().astimezone(),
            }
        )
    )
    get_store().append_semantic_workspace_event(
        user_id,
        task_id,
        stage="queued",
        event_type="revision_created",
        summary=f"已确认修改并创建结果版本 V{revision['revision']}",
        details={
            "proposal_id": proposal.proposal_id,
            "decision_id": decision.decision_id,
            "base_revision": decision.base_revision,
            "material_changes": list(proposal.material_changes),
        },
    )
    return {
        "decision": applied.model_dump(mode="json"),
        "revision": revision,
    }


@router.post(
    "/tasks/{task_id}/revision-proposals/{proposal_id}/decision",
    status_code=status.HTTP_202_ACCEPTED,
)
async def decide_steering_revision(
    task_id: str,
    proposal_id: str,
    payload: WorkspaceRevisionDecisionIn,
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    repository = _steering_repository()
    proposal = repository.get_proposal(user_id, proposal_id)
    if proposal is None or proposal.task_id != task_id:
        raise HTTPException(status_code=404, detail="Revision 草案不存在或无权访问")
    if int(task["active_revision"]) != proposal.base_revision:
        raise HTTPException(status_code=409, detail="活动版本已变化，请重新确认修改")
    previous_runtime = _runtime_repository().get(
        user_id,
        task_id,
        proposal.base_revision,
    )
    # 外发确认必须发生在保存决策和取消 Run 之前；否则 422 响应仍可能破坏旧任务。
    if (
        previous_runtime
        and previous_runtime["model_connection_id"]
        and not payload.external_api_confirmed
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="新版本或独立任务的数据外发范围必须再次确认",
        )
    service = ConversationSteering(_steering_repository(), None)
    try:
        decision = service.decide_proposal(
            user_id,
            proposal_id,
            payload.mode,
            external_api_confirmed=payload.external_api_confirmed,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.mode is RevisionSwitchMode.NEW_TASK:
        delta = repository.get_delta(user_id, proposal.delta_id) if proposal else None
        if proposal is None or delta is None:
            raise HTTPException(status_code=409, detail="Revision 草案数据不完整")
        formats = list(task["output_formats"])
        if delta.output_delta:
            formats = [item.strip().lower() for item in delta.output_delta]
            invalid = set(formats) - _FORMATS
            if invalid:
                raise HTTPException(
                    status_code=422,
                    detail=f"语义草案包含不支持的输出格式：{sorted(invalid)}",
                )
        connection_binding = None
        if previous_runtime and previous_runtime["model_connection_id"]:
            if not payload.external_api_confirmed:
                raise HTTPException(
                    status_code=422,
                    detail="独立任务的数据外发范围必须单独确认",
                )
            try:
                connection_binding = get_default_broker().freeze_connection(
                    user_id,
                    previous_runtime["model_connection_id"],
                )
            except GrantError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="模型连接不存在或无权访问",
                ) from exc
        new_task_id = f"workspace_{uuid.uuid4().hex[:16]}"
        new_task = get_store().create_semantic_workspace_task(
            user_id,
            task_id=new_task_id,
            title=f"{task['title']}（独立任务）",
            objective_text=(
                f"{task['objective_text']}\n\n"
                "已确认的独立任务差异：\n"
                f"{delta.normalized_text}"
            ),
            upload_ids=task["upload_ids"],
            output_formats=formats,
            provider=task["provider"],
            model=task["model"],
            external_api_confirmed=bool(connection_binding),
            table_output_contracts=[
                item
                for item in task.get("table_output_contracts", [])
                if item.get("format") in formats
            ],
        )
        _runtime_repository().register(
            RuntimeTaskConfig(
                user_id=user_id,
                task_id=new_task_id,
                revision=1,
                runtime_version=(
                    previous_runtime["runtime_version"]
                    if previous_runtime
                    else RuntimeVersion.LEGACY
                ),
                permission_profile=(
                    previous_runtime["permission_profile"]
                    if previous_runtime
                    else PermissionProfile.STANDARD
                ),
                model_connection_id=(
                    previous_runtime["model_connection_id"]
                    if previous_runtime
                    else None
                ),
                model_connection_version=(
                    connection_binding.connection_version
                    if connection_binding
                    else None
                ),
                model_connection_model=(
                    previous_runtime["model_connection_model"]
                    if previous_runtime
                    else None
                ),
                external_api_confirmed=bool(connection_binding),
            )
        )
        _inherit_capability_selection(
            user_id,
            source_task_id=task_id,
            source_revision=proposal.base_revision,
            target_task_id=new_task_id,
            target_revision=1,
        )
        applied = repository.update_decision(
            decision.model_copy(
                update={
                    "status": RevisionDecisionStatus.APPLIED,
                    "updated_at": datetime.now().astimezone(),
                }
            )
        )
        get_store().append_semantic_workspace_event(
            user_id,
            new_task_id,
            stage="queued",
            event_type="task.created_from_revision_proposal",
            summary="已从确认的修改草案创建独立任务",
            details={
                "source_task_id": task_id,
                "proposal_id": proposal_id,
            },
        )
        get_semantic_workspace_manager().enqueue(user_id, new_task_id)
        return {
            "decision": applied.model_dump(mode="json"),
            "revision": None,
            "new_task": new_task,
        }
    if payload.mode is RevisionSwitchMode.AFTER_SAFE_POINT:
        get_store().append_semantic_workspace_event(
            user_id,
            task_id,
            stage="execute",
            event_type="revision.waiting_safe_point",
            summary="已确认修改，将在当前原子步骤结束后切换版本",
            details={"decision_id": decision.decision_id},
        )
        return {"decision": decision.model_dump(mode="json"), "revision": None}

    if task["status"] not in _TERMINAL:
        await get_semantic_workspace_manager().cancel(user_id, task_id)
    response = _apply_confirmed_steering_revision(
        user_id,
        task_id,
        decision.decision_id,
        external_api_confirmed=payload.external_api_confirmed,
    )
    get_semantic_workspace_manager().enqueue(user_id, task_id)
    return response


@router.post("/tasks/{task_id}/answer")
async def answer_task(
    task_id: str,
    payload: WorkspaceAnswerIn,
    user=Depends(get_current_user),
):
    try:
        return await get_semantic_workspace_manager().answer(
            user["user_id"], task_id, payload.answer.strip()
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    user=Depends(get_current_user),
):
    try:
        return await get_semantic_workspace_manager().cancel(
            user["user_id"], task_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/candidate-verification/retry")
async def retry_candidate_verification(
    task_id: str,
    user=Depends(get_current_user),
):
    """只重试现有候选的语义验证；不创建 revision，也不重新执行任务。"""

    try:
        await get_semantic_workspace_manager().retry_candidate_verification(
            user["user_id"],
            task_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _task_detail(
        user["user_id"],
        task_id,
        audience=(
            ProgressAudience.ADMIN
            if is_admin_role(user.get("role"))
            else ProgressAudience.USER
        ),
    )


@router.post(
    "/tasks/{task_id}/revisions",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_revision(
    task_id: str,
    payload: WorkspaceRevisionIn,
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    previous_runtime = _runtime_repository().get(
        user_id,
        task_id,
        int(task["active_revision"]),
    )
    if (
        payload.table_output_contracts
        and (
            previous_runtime is None
            or previous_runtime["runtime_version"] is not RuntimeVersion.PI
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="表格输出契约只能由 Pi Runtime 执行",
        )
    connection_binding = None
    if previous_runtime and previous_runtime["model_connection_id"]:
        if not payload.external_api_confirmed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="外部连接任务创建新版本前必须再次确认本版本的数据外发",
            )
        try:
            connection_binding = get_default_broker().freeze_connection(
                user_id,
                previous_runtime["model_connection_id"],
            )
        except GrantError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="模型连接不存在或无权访问",
            ) from exc
    if task["status"] not in {
        "completed",
        "candidate_ready",
        "failed",
        "cancelled",
    }:
        await get_semantic_workspace_manager().cancel(user_id, task_id)
        task = _task_or_404(user_id, task_id)
    formats = (
        list(payload.output_formats)
        if payload.output_formats is not None
        else task["output_formats"]
    )
    if (
        payload.table_output_contracts is not None
        and not {
            item.format for item in payload.table_output_contracts
        }.issubset(formats)
    ):
        # 继承旧格式时也必须现场核对，不能把错配契约写入不可变 Revision。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="表格输出契约必须绑定正式输出格式",
        )
    objective = (
        f"{task['objective_text']}\n用户修改要求：{payload.instruction}"
    )
    revision = get_store().create_semantic_workspace_revision(
        user_id,
        task_id,
        objective_text=objective,
        output_formats=formats,
        change_summary=payload.instruction,
        table_output_contracts=(
            [
                item.model_dump(mode="json")
                for item in payload.table_output_contracts
            ]
            if payload.table_output_contracts is not None
            else None
        ),
    )
    _runtime_repository().register(
        RuntimeTaskConfig(
            user_id=user_id,
            task_id=task_id,
            revision=revision["revision"],
            runtime_version=(
                previous_runtime["runtime_version"]
                if previous_runtime
                else RuntimeVersion.LEGACY
            ),
            permission_profile=(
                previous_runtime["permission_profile"]
                if previous_runtime
                else PermissionProfile.STANDARD
            ),
            model_connection_id=(
                previous_runtime["model_connection_id"]
                if previous_runtime
                else None
            ),
            model_connection_version=(
                connection_binding.connection_version
                if connection_binding
                else None
            ),
            model_connection_model=(
                previous_runtime["model_connection_model"]
                if previous_runtime
                else None
            ),
            external_api_confirmed=bool(connection_binding),
        )
    )
    _inherit_capability_selection(
        user_id,
        source_task_id=task_id,
        source_revision=int(task["active_revision"]),
        target_task_id=task_id,
        target_revision=int(revision["revision"]),
    )
    get_store().append_semantic_workspace_event(
        user_id,
        task_id,
        stage="queued",
        event_type="revision_created",
        summary=f"已创建结果版本 V{revision['revision']}",
    )
    get_semantic_workspace_manager().enqueue(user_id, task_id)
    return revision


@router.get("/tasks/{task_id}/candidates/{artifact_id}")
def download_candidate(
    task_id: str,
    artifact_id: str,
    revision: int | None = Query(default=None, ge=1),
    user=Depends(get_current_user),
):
    """下载明确标记为“未验证候选”的 Pi 结果。"""

    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    selected_revision = revision or int(task["active_revision"])
    runtime = _runtime_repository().get(
        user_id, task_id, selected_revision
    )
    if runtime is None:
        raise HTTPException(status_code=404, detail="候选文件不存在")
    candidate = next(
        (
            item
            for item in runtime["candidates"]
            if item.artifact_id == artifact_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选文件不存在")
    if not _candidate_download_allowed(
        runtime["verification"],
        candidate_format=candidate.format,
    ):
        raise HTTPException(
            status_code=409,
            detail="候选缺少可信来源或身份校验，已禁止下载",
        )
    path = candidate.host_path.resolve()
    workspace_root = Path(runtime["workspace_root"] or "").resolve()
    # 下载时再次检查路径归属，避免数据库或容器产物被篡改后越过任务目录。
    if (
        not path.is_file()
        or path.is_symlink()
        or workspace_root not in path.parents
    ):
        raise HTTPException(
            status_code=409,
            detail="候选文件已失效或未通过路径校验",
        )
    return FileResponse(
        path,
        filename=candidate.filename,
        media_type="application/octet-stream",
        headers={
            "X-Mangrove-Artifact-Status": "unverified-candidate",
        },
    )


@router.delete("/tasks/{task_id}")
async def move_to_recycle_bin(
    task_id: str,
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    if task["status"] not in _TERMINAL:
        await get_semantic_workspace_manager().cancel(user_id, task_id)
    return get_store().soft_delete_semantic_workspace_task(
        user_id, task_id
    )


@router.post("/tasks/{task_id}/restore")
def restore_task(
    task_id: str,
    user=Depends(get_current_user),
):
    task = _task_or_404(user["user_id"], task_id)
    if task["deleted_at"] is None:
        raise HTTPException(status_code=409, detail="任务不在回收站")
    return get_store().restore_semantic_workspace_task(
        user["user_id"], task_id
    )


@router.delete("/tasks/{task_id}/permanent")
def permanently_delete_task(
    task_id: str,
    user=Depends(get_current_user),
):
    task = _task_or_404(user["user_id"], task_id)
    if task["deleted_at"] is None:
        raise HTTPException(
            status_code=409,
            detail="必须先把任务移入回收站",
        )
    if not get_store().purge_semantic_workspace_task(
        user["user_id"], task_id
    ):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_preview(
    path: Path,
    *,
    lineage_path: Path | None,
    offset: int,
    limit: int,
    search: str,
    sort_by: str | None,
    sort_direction: Literal["asc", "desc"],
) -> dict[str, Any]:
    with duckdb.connect(":memory:") as conn:
        suffix = path.suffix.lower()
        relation_sql = {
            ".parquet": "read_parquet(?)",
            ".csv": "read_csv_auto(?, header=true)",
            ".jsonl": "read_json_auto(?, format='newline_delimited')",
        }.get(suffix)
        if relation_sql is None:
            raise ValueError("当前表格类型不支持在线预览")
        all_columns = [
            row[0]
            for row in conn.execute(
                f"DESCRIBE SELECT * FROM {relation_sql}", [str(path)]
            ).fetchall()
        ]
        columns = [
            column
            for column in all_columns
            if column != "__mg_output_record_id"
        ]
        where = ""
        params: list[Any] = [str(path)]
        if search:
            clauses = [
                f"CAST({_quote_identifier(column)} AS VARCHAR) ILIKE ?"
                for column in columns
            ]
            where = " WHERE " + " OR ".join(clauses)
            params.extend([f"%{search}%"] * len(columns))
        total = conn.execute(
            f"SELECT COUNT(*) FROM {relation_sql}{where}", params
        ).fetchone()[0]
        order = ""
        if sort_by:
            if sort_by not in columns:
                raise ValueError("排序字段不存在")
            order = (
                f" ORDER BY {_quote_identifier(sort_by)} "
                f"{sort_direction.upper()} NULLS LAST"
            )
        rows = conn.execute(
            "WITH indexed AS ("
            f"SELECT *, row_number() OVER () - 1 AS __mg_result_index "
            f"FROM {relation_sql}) "
            f"SELECT * FROM indexed{where}{order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        result_columns = [*all_columns, "__mg_result_index"]
        records = [
            {
                column: value
                for column, value in zip(result_columns, row)
            }
            for row in rows
        ]
        if lineage_path and lineage_path.is_file() and records:
            output_ids = [
                str(record["__mg_output_record_id"])
                for record in records
                if record.get("__mg_output_record_id")
            ]
            placeholders = ", ".join("?" for _ in output_ids)
            lineage_rows = conn.execute(
                "SELECT output_record_id, artifact_id, table_ref, "
                "row_number, evidence_json FROM read_parquet(?) "
                f"WHERE output_record_id IN ({placeholders}) "
                "ORDER BY output_record_id, artifact_id, row_number",
                [str(lineage_path), *output_ids],
            ).fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {}
            for output_id, artifact_id, table_ref, row_number, evidence in (
                lineage_rows
            ):
                grouped.setdefault(str(output_id), []).append(
                    {
                        "artifact_id": artifact_id,
                        "table_ref": table_ref,
                        "row_number": row_number,
                        "values": json.loads(evidence),
                    }
                )
            for record in records:
                record.pop("__mg_result_index", None)
                output_id = str(
                    record.pop("__mg_output_record_id", "")
                )
                record["__lineage"] = grouped.get(output_id, [])
        else:
            for record in records:
                record.pop("__mg_result_index", None)
                record.pop("__mg_output_record_id", None)
        return {
            "kind": "table",
            "columns": columns,
            "rows": records,
            "total": total,
            "offset": offset,
            "limit": limit,
        }


def _python_table_preview(
    rows: list[dict[str, Any]],
    *,
    columns: list[str],
    offset: int,
    limit: int,
    search: str,
    sort_by: str | None,
    sort_direction: Literal["asc", "desc"],
) -> dict[str, Any]:
    if search:
        keyword = search.casefold()
        rows = [
            row
            for row in rows
            if any(keyword in str(row.get(column, "")).casefold() for column in columns)
        ]
    if sort_by:
        if sort_by not in columns:
            raise ValueError("排序字段不存在")

        def sort_key(row: dict[str, Any]) -> tuple[int, int, Any]:
            value = row.get(sort_by)
            if value is None:
                return (1, 0, "")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (0, 0, value)
            return (0, 1, str(value).casefold())

        rows.sort(key=sort_key, reverse=sort_direction == "desc")
    return {
        "kind": "table",
        "columns": columns,
        "rows": rows[offset : offset + limit],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


def _xlsx_preview(
    path: Path,
    *,
    offset: int,
    limit: int,
    search: str,
    sort_by: str | None,
    sort_direction: Literal["asc", "desc"],
) -> dict[str, Any]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        columns = ["工作表"]
        rows: list[dict[str, Any]] = []
        for sheet in workbook.worksheets:
            iterator = sheet.iter_rows(values_only=True)
            raw_headers = next(iterator, ())
            headers: list[str] = []
            seen: dict[str, int] = {}
            for index, value in enumerate(raw_headers):
                base = str(value).strip() if value is not None else f"列{index + 1}"
                seen[base] = seen.get(base, 0) + 1
                headers.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
            for header in headers:
                if header not in columns:
                    columns.append(header)
            for values in iterator:
                if not any(value is not None for value in values):
                    continue
                row = {"工作表": sheet.title}
                row.update(
                    {
                        header: value
                        for header, value in zip(headers, values)
                    }
                )
                rows.append(row)
    finally:
        workbook.close()
    return _python_table_preview(
        rows,
        columns=columns,
        offset=offset,
        limit=limit,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )


def _paginate_document_items(
    items: list[dict[str, Any]],
    *,
    action: str,
    offset: int,
    limit: int,
    search: str,
) -> dict[str, Any]:
    if search:
        keyword = search.casefold()
        items = [
            item
            for item in items
            if keyword
            in f"{item.get('label', '')} {item.get('content', '')}".casefold()
        ]
    return {
        "kind": "document",
        "action": action,
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "warnings": [],
    }


def _file_document_preview(
    path: Path,
    *,
    offset: int,
    limit: int,
    search: str,
) -> dict[str, Any]:
    suffix = path.suffix.lower()
    sections: list[tuple[str, str]] = []
    if suffix in {".txt", ".md", ".markdown"}:
        text = path.read_text(encoding="utf-8-sig")
        sections = [
            (f"段落 {index}", value.strip())
            for index, value in enumerate(re.split(r"\n\s*\n", text), 1)
            if value.strip()
        ]
    elif suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(path.read_text(encoding="utf-8-sig"), "html.parser")
        blocks = [
            value.strip()
            for value in soup.get_text("\n").splitlines()
            if value.strip()
        ]
        sections = [(f"内容 {index}", value) for index, value in enumerate(blocks, 1)]
    elif suffix == ".docx":
        from docx import Document

        document = Document(path)
        paragraphs = [item.text.strip() for item in document.paragraphs if item.text.strip()]
        sections.extend(
            (f"段落 {index}", value) for index, value in enumerate(paragraphs, 1)
        )
        for table_index, table in enumerate(document.tables, 1):
            content = "\n".join(
                " | ".join(cell.text.strip() for cell in row.cells)
                for row in table.rows
            ).strip()
            if content:
                sections.append((f"表格 {table_index}", content))
    elif suffix == ".pdf":
        from pypdf import PdfReader

        sections = [
            (f"第 {index} 页", (page.extract_text() or "").strip())
            for index, page in enumerate(PdfReader(path).pages, 1)
        ]
    elif suffix == ".pptx":
        from pptx import Presentation

        presentation = Presentation(path)
        for index, slide in enumerate(presentation.slides, 1):
            texts = [
                shape.text.strip()
                for shape in slide.shapes
                if hasattr(shape, "text") and shape.text.strip()
            ]
            sections.append((f"第 {index} 页", "\n".join(texts)))
    else:
        raise ValueError("当前文档类型不支持在线预览")
    items = [
        {
            "type": "derived",
            "id": f"file-{index}",
            "label": label,
            "content": content or "（该页没有可提取文本）",
            "evidence_refs": [],
        }
        for index, (label, content) in enumerate(sections, 1)
    ]
    return _paginate_document_items(
        items,
        action=f"{suffix.lstrip('.')}_preview",
        offset=offset,
        limit=limit,
        search=search,
    )


def _document_preview(
    path: Path,
    *,
    offset: int,
    limit: int,
    search: str,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    document_payload = payload if isinstance(payload, dict) else {}
    for item in document_payload.get("passages", []):
        items.append(
            {
                "type": "passage",
                "id": item["passage_id"],
                "label": item["label"],
                "content": item["text"],
                "evidence_refs": item["evidence_refs"],
            }
        )
    for item in document_payload.get("differences", []):
        content = "\n".join(
            value
            for value in (item.get("before"), item.get("after"))
            if value
        )
        items.append(
            {
                "type": "difference",
                "id": item["diff_id"],
                "label": item["label"],
                "content": content,
                "change_type": item["change_type"],
                "evidence_refs": [
                    *item.get("before_evidence", []),
                    *item.get("after_evidence", []),
                ],
            }
        )
    for item in document_payload.get("findings", []):
        items.append(
            {
                "type": "finding",
                "id": item["finding_id"],
                "label": item["label"],
                "content": item["message"],
                "status": item["status"],
                "evidence_refs": item["evidence_refs"],
            }
        )
    for item in document_payload.get("derived_content", []):
        items.append(
            {
                "type": "derived",
                "id": item["content_id"],
                "label": item["action"],
                "content": item["content"],
                "evidence_refs": item["evidence_refs"],
            }
        )
    if not items:
        # Pi 可以按用户要求直接生成通用 JSON；它不是 Legacy DocumentExecutionResult，
        # 但仍应按稳定根键形成可读项，不能让合法正式交付显示为空白。
        values = (
            list(payload.items())
            if isinstance(payload, dict)
            else list(enumerate(payload, start=1))
            if isinstance(payload, list)
            else [("结果", payload)]
        )
        for index, (label, value) in enumerate(values, start=1):
            content = (
                value
                if isinstance(value, str)
                else json.dumps(
                    value,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            items.append(
                {
                    "type": "derived",
                    "id": f"json-{index}",
                    "label": str(label),
                    "content": content,
                    "evidence_refs": [],
                }
            )
    if search:
        keyword = search.casefold()
        items = [
            item
            for item in items
            if keyword
            in f"{item.get('label', '')} {item.get('content', '')}".casefold()
        ]
    return {
        "kind": "document",
        "action": document_payload.get("action") or "json_preview",
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "warnings": document_payload.get("warnings", []),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preview_result_file(
    result_path: Path,
    *,
    lineage_path: Path | None,
    offset: int,
    limit: int,
    search: str,
    sort_by: str | None,
    sort_direction: Literal["asc", "desc"],
) -> dict[str, Any]:
    suffix = result_path.suffix.lower()
    if suffix in {".parquet", ".csv", ".jsonl"}:
        return _table_preview(
            result_path,
            lineage_path=lineage_path,
            offset=offset,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
    if suffix == ".xlsx":
        return _xlsx_preview(
            result_path,
            offset=offset,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
    if suffix == ".json":
        return _document_preview(
            result_path,
            offset=offset,
            limit=limit,
            search=search,
        )
    if suffix in {
        ".docx",
        ".pdf",
        ".html",
        ".htm",
        ".md",
        ".markdown",
        ".txt",
        ".pptx",
    }:
        return _file_document_preview(
            result_path,
            offset=offset,
            limit=limit,
            search=search,
        )
    raise ValueError("当前结果类型不支持在线预览")


@router.get("/tasks/{task_id}/preview")
def preview_task_result(
    task_id: str,
    revision: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    search: str = Query(default="", max_length=200),
    sort_by: str | None = Query(default=None),
    sort_direction: Literal["asc", "desc"] = "asc",
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    selected_revision = (
        get_store().get_semantic_workspace_revision(
            user_id, task_id, revision
        )
        if revision is not None
        else get_store().get_semantic_workspace_revision(
            user_id, task_id, task["active_revision"]
        )
    )
    if selected_revision is None:
        raise HTTPException(status_code=404, detail="结果版本不存在")
    run_id = selected_revision["run_id"]
    if not run_id:
        raise HTTPException(status_code=409, detail="任务尚无可预览结果")
    store = get_store()
    delivery = store.latest_semantic_delivery(user_id, run_id)
    formal_output = next(
        (
            output
            for output in (delivery or {}).get("outputs", [])
            if output.get("format") in _FORMATS
        ),
        None,
    )
    root = Path(settings.semantic_execution_root).resolve()
    paths = store.latest_semantic_harness_artifact_paths(user_id, run_id)
    legacy_result = Path(paths.get("result", "")).resolve()
    legacy_available = (
        legacy_result.is_file()
        and (legacy_result == root or root in legacy_result.parents)
    )
    lineage_value = paths.get("lineage") if legacy_available else None
    if legacy_available:
        # Legacy 的 Parquet 携带逐行来源；正式 Excel 只负责下载，不能反过来削弱预览证据。
        result_path = legacy_result
    elif formal_output is not None:
        record = store.get_semantic_delivery_output(
            user_id,
            formal_output["output_id"],
        )
        if record is None:
            raise HTTPException(
                status_code=409,
                detail="正式交付预览文件登记缺失",
            )
        result_path = Path(record["file_path"]).resolve()
        if (
            (result_path != root and root not in result_path.parents)
            or not result_path.is_file()
            or result_path.stat().st_size != record["size_bytes"]
            or _sha256_file(result_path) != record["sha256"]
        ):
            raise HTTPException(
                status_code=409,
                detail="正式交付预览文件完整性校验失败",
            )
    else:
        result_path = legacy_result
    lineage_path = (
        Path(lineage_value).resolve() if lineage_value else None
    )
    if lineage_path is not None and (
        not lineage_path.is_file()
        or lineage_path != root
        and root not in lineage_path.parents
    ):
        lineage_path = None
    if (
        not result_path.is_file()
        or result_path != root
        and root not in result_path.parents
    ):
        raise HTTPException(status_code=404, detail="预览制品不存在")
    try:
        return _preview_result_file(
            result_path,
            lineage_path=lineage_path,
            offset=offset,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
    except (ValueError, OSError, duckdb.Error, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _safe_archive_name(name: str) -> str:
    value = Path(name).name.strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    return value or "file"


@router.get("/tasks/{task_id}/bundle")
def download_bundle(
    task_id: str,
    include_sources: bool = False,
    revision: int | None = Query(default=None, ge=1),
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    task = _task_or_404(user_id, task_id)
    selected_revision = (
        get_store().get_semantic_workspace_revision(
            user_id, task_id, revision
        )
        if revision is not None
        else get_store().get_semantic_workspace_revision(
            user_id, task_id, task["active_revision"]
        )
    )
    if selected_revision is None:
        raise HTTPException(status_code=404, detail="结果版本不存在")
    run_id = selected_revision["run_id"]
    if not run_id:
        raise HTTPException(status_code=409, detail="任务尚无正式交付")
    store = get_store()
    manifest = store.latest_semantic_delivery(user_id, run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="正式交付不存在")
    bundle_root = (
        Path(settings.semantic_execution_root) / "_bundles"
    ).resolve()
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_root / f"{task_id}-{uuid.uuid4().hex[:8]}.zip"
    trace = {
        "task": {
            key: value
            for key, value in task.items()
            if key not in {"question"}
        },
        "workspace_events": store.list_semantic_workspace_events(
            user_id, task_id
        ),
        "harness_events": store.list_semantic_harness_events(
            user_id, run_id
        ),
        "attempts": store.list_semantic_harness_attempts(
            user_id, run_id
        ),
    }
    qa = {
        item["filename"]: item["qa"] for item in manifest["outputs"]
    }
    with zipfile.ZipFile(
        bundle_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for output in manifest["outputs"]:
            record = store.get_semantic_delivery_output(
                user_id, output["output_id"]
            )
            if record is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"交付文件登记缺失：{output['filename']}",
                )
            path = Path(record["file_path"]).resolve()
            if not path.is_file():
                raise HTTPException(
                    status_code=409,
                    detail=f"交付文件不存在：{output['filename']}",
                )
            archive.write(
                path,
                arcname=f"outputs/{_safe_archive_name(output['filename'])}",
            )
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "qa.json",
            json.dumps(qa, ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "trace.json",
            json.dumps(trace, ensure_ascii=False, indent=2),
        )
        if include_sources:
            for upload_id in task["upload_ids"]:
                item = _uploads().resolve(user_id, upload_id)
                archive.write(
                    Path(item.storage_path),
                    arcname=(
                        "sources/"
                        + _safe_archive_name(item.original_name)
                    ),
                )
    return FileResponse(
        bundle_path,
        media_type="application/zip",
        filename=f"{_safe_archive_name(task['title'])}.zip",
        background=BackgroundTask(bundle_path.unlink, missing_ok=True),
    )


@router.get("/storage")
def storage_usage(user=Depends(get_current_user)):
    user_id = user["user_id"]
    store = get_store()
    tasks = [
        *store.list_semantic_workspace_tasks(
            user_id, deleted=False, limit=500
        ),
        *store.list_semantic_workspace_tasks(
            user_id, deleted=True, limit=500
        ),
    ]
    upload_ids = {
        upload_id
        for task in tasks
        for upload_id in task["upload_ids"]
    }
    upload_bytes = 0
    for upload_id in upload_ids:
        try:
            upload_bytes += _uploads().resolve(
                user_id, upload_id
            ).size_bytes
        except PermissionError:
            pass
    output_ids: set[str] = set()
    output_bytes = 0
    for task in tasks:
        for revision in store.list_semantic_workspace_revisions(
            user_id, task["task_id"]
        ):
            if not revision["run_id"]:
                continue
            delivery = store.latest_semantic_delivery(
                user_id, revision["run_id"]
            )
            if not delivery:
                continue
            for output in delivery["outputs"]:
                if output["output_id"] in output_ids:
                    continue
                output_ids.add(output["output_id"])
                output_bytes += output["size_bytes"]
    return {
        "task_count": len(tasks),
        "recycle_bin_count": sum(
            1 for task in tasks if task["deleted_at"]
        ),
        "upload_bytes": upload_bytes,
        "delivery_bytes": output_bytes,
        "total_bytes": upload_bytes + output_bytes,
        "retention": "用户删除前永久保留；回收站保留 30 天",
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
    }
