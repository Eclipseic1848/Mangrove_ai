# -*- coding: utf-8 -*-
"""Phase 4B 批次 2 后端测试 API；只检查和绑定，不执行数据操作。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user, get_store
from src.config.settings import settings
from src.semantic_harness.binder_graph import run_inspect_bind_graph
from src.semantic_harness.models import SemanticTaskPlan
from src.semantic_harness.inspectors import UploadSourceInspector
from src.services.upload_store import UploadStore


router = APIRouter(prefix="/api/semantic-plans", tags=["semantic-bindings"])


class InspectBindIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_local_semantics: bool = True


class BindingResolutionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ambiguity_id: str = Field(min_length=1)
    physical_ref: str = Field(min_length=1)
    use_local_semantics: bool = True


def _upload_store() -> UploadStore:
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


def _logical_plan(user_id: str, plan_id: str) -> SemanticTaskPlan:
    row = get_store().latest_semantic_plan_revision(user_id, plan_id)
    if row is None or row["plan"] is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="可绑定的语义计划不存在",
        )
    plan = SemanticTaskPlan.model_validate(row["plan"])
    if not plan.is_executable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="语义计划仍有未解决歧义，不能开始来源绑定",
        )
    return plan


async def _run_and_save(
    *,
    user_id: str,
    plan: SemanticTaskPlan,
    binding_revision: int,
    resolutions: dict[str, str],
    use_local_semantics: bool,
):
    inspector = UploadSourceInspector(
        user_id=user_id,
        upload_store=_upload_store(),
        cache_lookup=lambda artifact_id, artifact_sha256, inspector_version: (
            get_store().cached_source_inspection_report(
                user_id,
                artifact_id=artifact_id,
                artifact_sha256=artifact_sha256,
                inspector_version=inspector_version,
            )
        ),
    )
    try:
        reports, result = await run_inspect_bind_graph(
            plan,
            inspector=inspector,
            binding_revision=binding_revision,
            resolutions=resolutions,
            use_local_semantics=use_local_semantics,
        )
        return get_store().save_semantic_binding_revision(
            user_id,
            reports=reports,
            result=result,
            resolutions=resolutions,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="来源不存在或无权访问",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/{plan_id}/inspect-bind")
async def inspect_and_bind(
    plan_id: str,
    payload: InspectBindIn,
    user=Depends(get_current_user),
):
    """检查真实上传来源并生成 binding revision 1。"""

    plan = _logical_plan(user["user_id"], plan_id)
    if get_store().latest_semantic_binding_revision(
        user["user_id"], plan_id
    ) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该计划已经开始绑定，请创建下一 revision",
        )
    return await _run_and_save(
        user_id=user["user_id"],
        plan=plan,
        binding_revision=1,
        resolutions={},
        use_local_semantics=payload.use_local_semantics,
    )


@router.get("/{plan_id}/bound-revisions")
def list_bound_revisions(
    plan_id: str,
    user=Depends(get_current_user),
):
    rows = get_store().list_semantic_binding_revisions(
        user["user_id"], plan_id
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="语义绑定不存在",
        )
    return rows


@router.get("/{plan_id}/bound-revisions/{binding_revision}")
def get_bound_revision(
    plan_id: str,
    binding_revision: int,
    user=Depends(get_current_user),
):
    row = get_store().get_semantic_binding_revision(
        user["user_id"],
        plan_id,
        binding_revision,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="语义绑定 revision 不存在",
        )
    return row


@router.post("/{plan_id}/bound-revisions")
async def revise_binding(
    plan_id: str,
    payload: BindingResolutionIn,
    user=Depends(get_current_user),
):
    """用户从候选集合中确认一个物理目标后创建下一 revision。"""

    previous = get_store().latest_semantic_binding_revision(
        user["user_id"], plan_id
    )
    if previous is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="语义绑定不存在",
        )
    clarification = previous["result"].get("clarification")
    if (
        clarification is None
        or clarification.get("ambiguity_id") != payload.ambiguity_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只能回答当前待确认问题",
        )
    semantic_ref = payload.ambiguity_id.split("|", 1)[0]
    allowed_refs = {
        item["physical_ref"]
        for item in previous["result"].get("candidates", [])
        if item["semantic_ref"] == semantic_ref
    }
    if payload.physical_ref not in allowed_refs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="选择必须来自当前候选集合",
        )
    resolutions = dict(previous["resolutions"])
    resolutions[payload.ambiguity_id] = payload.physical_ref
    return await _run_and_save(
        user_id=user["user_id"],
        plan=_logical_plan(user["user_id"], plan_id),
        binding_revision=previous["binding_revision"] + 1,
        resolutions=resolutions,
        use_local_semantics=payload.use_local_semantics,
    )
