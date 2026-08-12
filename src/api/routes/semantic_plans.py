# -*- coding: utf-8 -*-
"""Phase 4B 批次 1 后端测试接口；不接正式前端和执行器。"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user, get_store
from src.semantic_harness.compiler import (
    DeferredPlanDraftGenerator,
    InstructorPlanDraftGenerator,
)
from src.semantic_harness.compiler_graph import compile_semantic_plan
from src.semantic_harness.compiler_models import (
    ClarificationResolution,
    CompileRequest,
)
from src.semantic_harness.models import DeliveryFormat


router = APIRouter(prefix="/api/semantic-plans", tags=["semantic-plans"])


class SemanticCompileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    objective_text: str = Field(min_length=1)
    artifact_ids: Tuple[str, ...] = ()
    source_ids: Tuple[str, ...] = ()
    pages: Dict[str, Tuple[int, ...]] = Field(default_factory=dict)
    table_scope: Optional[str] = Field(default=None, min_length=1)
    section_patterns: Tuple[str, ...] = ()
    time_ranges: Tuple[str, ...] = ()
    accepted_formats: Tuple[str, ...] = ()
    accepted_media_types: Tuple[str, ...] = ()
    requested_output_formats: Tuple[DeliveryFormat, ...] = ()
    provider: str = Field(default="local", min_length=1)
    model: Optional[str] = Field(default=None, min_length=1)
    external_api_confirmed: bool = False
    max_repair_attempts: int = Field(default=2, ge=0, le=2)


class SemanticRevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    external_api_confirmed: Optional[bool] = None


def _build_generator(*, provider: str, model: str | None):
    return InstructorPlanDraftGenerator(provider=provider, model=model)


def _compile_request(payload: SemanticCompileIn) -> CompileRequest:
    return CompileRequest.model_validate(payload.model_dump())


async def _run_and_save(
    request: CompileRequest,
    *,
    user_id: str,
    plan_id: str | None = None,
    revision: int = 1,
):
    try:
        if request.provider != "local" and not request.external_api_confirmed:
            generator = DeferredPlanDraftGenerator(
                provider=request.provider,
                model=request.model,
            )
        else:
            generator = _build_generator(
                provider=request.provider,
                model=request.model,
            )
        result = await compile_semantic_plan(
            request,
            generator=generator,
            plan_id=plan_id,
            revision=revision,
        )
        return get_store().save_semantic_plan_revision(
            user_id,
            request=request,
            result=result,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/compile")
async def compile_plan(
    payload: SemanticCompileIn,
    user=Depends(get_current_user),
):
    """编译并保存 revision 1；只返回计划，不执行任何数据操作。"""

    return await _run_and_save(
        _compile_request(payload),
        user_id=user["user_id"],
    )


@router.get("/{plan_id}/revisions")
def list_revisions(
    plan_id: str,
    user=Depends(get_current_user),
):
    rows = get_store().list_semantic_plan_revisions(
        user["user_id"],
        plan_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="语义计划不存在")
    return rows


@router.get("/{plan_id}/revisions/{revision}")
def get_revision(
    plan_id: str,
    revision: int,
    user=Depends(get_current_user),
):
    row = get_store().get_semantic_plan_revision(
        user["user_id"],
        plan_id,
        revision,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="语义计划 revision 不存在")
    return row


@router.post("/{plan_id}/revisions")
async def revise_plan(
    plan_id: str,
    payload: SemanticRevisionIn,
    user=Depends(get_current_user),
):
    """用一次用户补充生成下一不可变 revision，不原地覆盖旧计划。"""

    previous = get_store().latest_semantic_plan_revision(
        user["user_id"],
        plan_id,
    )
    if previous is None:
        raise HTTPException(status_code=404, detail="语义计划不存在")
    values = dict(previous["request"])
    values.pop("prior_plan", None)
    values.pop("clarification", None)
    values["objective_text"] = (
        f"{values['objective_text']}\n用户补充：{payload.answer}"
    )
    if payload.external_api_confirmed is not None:
        values["external_api_confirmed"] = payload.external_api_confirmed
    clarification = previous.get("clarification")
    if previous.get("plan") and clarification:
        values["prior_plan"] = previous["plan"]
        values["clarification"] = ClarificationResolution(
            ambiguity_id=clarification["ambiguity_id"],
            question=clarification["question"],
            answer=payload.answer,
        )
    request = CompileRequest.model_validate(values)
    return await _run_and_save(
        request,
        user_id=user["user_id"],
        plan_id=plan_id,
        revision=previous["revision"] + 1,
    )
