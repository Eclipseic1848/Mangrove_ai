# -*- coding: utf-8 -*-
"""Phase 4B 批次 5 后端灰度 API；不替换 Phase 4A 正式入口。"""
from __future__ import annotations

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict

from src.api.auth import get_current_user, get_store
from src.config.settings import settings
from src.semantic_harness.capabilities import get_capability_registry
from src.semantic_harness.harness_graph import HarnessRuntime, invoke_harness
from src.semantic_harness.harness_models import (
    HarnessLoopPolicy,
    HarnessNode,
    HarnessResume,
    HarnessRun,
    HarnessStatus,
)
from src.semantic_harness.models import BoundPlan, SemanticTaskPlan, TaskFamily
from src.semantic_harness.physical_models import RuntimeProfileName
from src.services.upload_store import UploadStore


router = APIRouter(
    prefix="/api/semantic-harness",
    tags=["semantic-harness"],
)


class HarnessRunCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    logical_revision: int | None = None
    binding_revision: int | None = None
    runtime_profile: RuntimeProfileName = RuntimeProfileName.WINDOWS_LOCAL


class HarnessResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    resume_token: str
    answer: str


def _upload_store() -> UploadStore:
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


def _runtime() -> HarnessRuntime:
    return HarnessRuntime(
        store=get_store(),
        upload_store=_upload_store(),
        output_root=Path(settings.semantic_execution_root),
    )


def _checkpoint_path() -> Path:
    path = (
        Path(settings.semantic_execution_root)
        / "_checkpoints"
        / "semantic-harness.sqlite"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _capability_id(plan: SemanticTaskPlan) -> str:
    if plan.task_family == TaskFamily.TABULAR_TRANSFORM:
        return "table.duckdb"
    if plan.task_family in {
        TaskFamily.EXTRACT,
        TaskFamily.COMPARE,
        TaskFamily.AUDIT,
        TaskFamily.COMPOSE,
        TaskFamily.SUMMARIZE,
        TaskFamily.TRANSLATE,
    }:
        return "document.evidence"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="批次 5 灰度入口只接入表格和文档能力",
    )


def _frozen_context(
    user_id: str,
    payload: HarnessRunCreateIn,
) -> tuple[dict, dict, SemanticTaskPlan, BoundPlan]:
    store = get_store()
    if payload.logical_revision is None:
        plan_row = store.latest_semantic_plan_revision(
            user_id, payload.plan_id
        )
    else:
        plan_row = store.get_semantic_plan_revision(
            user_id,
            payload.plan_id,
            payload.logical_revision,
        )
    if plan_row is None or plan_row["plan"] is None:
        raise HTTPException(status_code=404, detail="语义计划不存在")
    if payload.binding_revision is None:
        binding_row = store.latest_semantic_binding_revision(
            user_id, payload.plan_id
        )
    else:
        binding_row = store.get_semantic_binding_revision(
            user_id,
            payload.plan_id,
            payload.binding_revision,
        )
    if binding_row is None or binding_row["bound_plan"] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="可执行来源绑定不存在",
        )
    plan = SemanticTaskPlan.model_validate(plan_row["plan"])
    bound = BoundPlan.model_validate(binding_row["bound_plan"])
    if not plan.is_executable or not bound.is_executable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="计划或来源绑定仍需用户确认",
        )
    if (
        bound.logical_plan_revision != plan.revision
        or bound.logical_plan_hash != plan.canonical_hash()
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="来源绑定与所选逻辑计划不一致",
        )
    return plan_row, binding_row, plan, bound


def create_run_record(
    user_id: str,
    payload: HarnessRunCreateIn,
) -> dict:
    """冻结计划和绑定并只创建 run 记录，不在当前请求中执行。"""

    plan_row, binding_row, plan, _ = _frozen_context(user_id, payload)
    capability_id = _capability_id(plan)
    manifest = get_capability_registry().manifest(capability_id)
    run_id = f"harness_{uuid.uuid4().hex[:16]}"
    run = HarnessRun(
        run_id=run_id,
        user_id=user_id,
        thread_id=run_id,
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan_row["plan_hash"],
        binding_revision=binding_row["binding_revision"],
        binding_hash=binding_row["bound_plan_hash"],
        capability_id=capability_id,
        capability_version=manifest.version,
        runtime_profile=payload.runtime_profile.value,
        policy=HarnessLoopPolicy(),
        status=HarnessStatus.RUNNING,
        current_node=HarnessNode.INTERPRET,
    )
    return get_store().create_semantic_harness_run(run)


async def invoke_run_record(
    user_id: str,
    run_id: str,
    *,
    resume: HarnessResume | None = None,
):
    """从持久化 run 和 checkpoint 执行或恢复。"""

    async with AsyncSqliteSaver.from_conn_string(
        str(_checkpoint_path())
    ) as saver:
        return await invoke_harness(
            _runtime(),
            saver,
            user_id=user_id,
            run_id=run_id,
            resume=resume,
        )


@router.post("/runs")
async def create_run(
    payload: HarnessRunCreateIn,
    user=Depends(get_current_user),
):
    run = create_run_record(user["user_id"], payload)
    return await invoke_run_record(user["user_id"], run["run_id"])


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    user=Depends(get_current_user),
):
    row = get_store().get_semantic_harness_run(
        user["user_id"], run_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Harness run 不存在")
    return row


@router.get("/runs/{run_id}/events")
def get_events(
    run_id: str,
    user=Depends(get_current_user),
):
    if get_store().get_semantic_harness_run(
        user["user_id"], run_id
    ) is None:
        raise HTTPException(status_code=404, detail="Harness run 不存在")
    return get_store().list_semantic_harness_events(
        user["user_id"], run_id
    )


@router.get("/runs/{run_id}/attempts")
def get_attempts(
    run_id: str,
    user=Depends(get_current_user),
):
    if get_store().get_semantic_harness_run(
        user["user_id"], run_id
    ) is None:
        raise HTTPException(status_code=404, detail="Harness run 不存在")
    return get_store().list_semantic_harness_attempts(
        user["user_id"], run_id
    )


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    payload: HarnessResumeIn,
    user=Depends(get_current_user),
):
    row = get_store().get_semantic_harness_run(
        user["user_id"], run_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Harness run 不存在")
    if row["status"] != HarnessStatus.NEEDS_USER.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前 run 不在等待用户状态",
        )
    question = row["question"]
    if (
        question is None
        or question["question_id"] != payload.question_id
        or question["resume_token"] != payload.resume_token
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="回答与当前待确认问题不匹配",
        )
    allowed_answers = {
        item["value"] for item in question.get("options", [])
    }
    if (
        payload.answer not in allowed_answers
        and not question.get("allow_free_text", False)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="回答不符合当前问题 Schema",
        )
    resume = HarnessResume.model_validate(payload.model_dump())
    return await invoke_run_record(
        user["user_id"],
        run_id,
        resume=resume,
    )
