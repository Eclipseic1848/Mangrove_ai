# -*- coding: utf-8 -*-
"""Phase 4B 批次 4 后端测试 API：冻结、执行并验证文档计划。"""
from __future__ import annotations

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from src.api.auth import get_current_user, get_store
from src.config.settings import settings
from src.semantic_harness.document_executor import (
    LocalDocumentSemanticProvider,
)
from src.semantic_harness.document_graph import run_document_execution_graph
from src.semantic_harness.document_models import (
    AuditOperator,
    DocumentAction,
    DocumentPhysicalPlan,
    DocumentPlanStatus,
)
from src.semantic_harness.document_planner import compile_document_plan
from src.semantic_harness.inspection_models import SourceInspectionReport
from src.semantic_harness.models import BoundPlan, SemanticTaskPlan
from src.semantic_harness.physical_models import RuntimeProfileName
from src.services.upload_store import UploadStore


router = APIRouter(prefix="/api/semantic-plans", tags=["semantic-documents"])


class PrepareDocumentPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_profile: RuntimeProfileName = RuntimeProfileName.WINDOWS_LOCAL


def _upload_store() -> UploadStore:
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


def _context(user_id: str, plan_id: str, binding_revision: int | None = None):
    plan_row = get_store().latest_semantic_plan_revision(user_id, plan_id)
    if plan_row is None or plan_row["plan"] is None:
        raise HTTPException(status_code=404, detail="语义计划不存在")
    binding_row = (
        get_store().latest_semantic_binding_revision(user_id, plan_id)
        if binding_revision is None
        else get_store().get_semantic_binding_revision(
            user_id, plan_id, binding_revision
        )
    )
    if binding_row is None or binding_row["bound_plan"] is None:
        raise HTTPException(status_code=409, detail="可执行来源绑定不存在")
    return (
        SemanticTaskPlan.model_validate(plan_row["plan"]),
        BoundPlan.model_validate(binding_row["bound_plan"]),
        tuple(
            SourceInspectionReport.model_validate(item)
            for item in binding_row["reports"]
        ),
    )


def _semantic_provider(physical: DocumentPhysicalPlan):
    needs_semantics = physical.action in {
        DocumentAction.COMPARE,
        DocumentAction.SUMMARIZE,
        DocumentAction.REWRITE,
        DocumentAction.TRANSLATE,
        DocumentAction.COMPOSE,
    } or any(
        rule.operator == AuditOperator.SEMANTIC
        for rule in physical.audit_rules
    )
    return LocalDocumentSemanticProvider() if needs_semantics else None


@router.post("/{plan_id}/document-plans")
def prepare_document_plan(
    plan_id: str,
    payload: PrepareDocumentPlanIn,
    user=Depends(get_current_user),
):
    plan, bound_plan, reports = _context(user["user_id"], plan_id)
    physical = compile_document_plan(
        plan,
        bound_plan,
        reports,
        profile=payload.runtime_profile,
    )
    return get_store().save_physical_plan(user["user_id"], physical)


@router.get("/{plan_id}/document-plans")
def list_document_plans(
    plan_id: str,
    user=Depends(get_current_user),
):
    rows = [
        row
        for row in get_store().list_physical_plans(user["user_id"], plan_id)
        if row["physical_plan"].get("capability_id") == "document.evidence"
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="DocumentPhysicalPlan 不存在")
    return rows


@router.post("/{plan_id}/document-plans/{physical_plan_id}/execute")
async def execute_document(
    plan_id: str,
    physical_plan_id: str,
    user=Depends(get_current_user),
):
    stored = get_store().get_physical_plan(user["user_id"], physical_plan_id)
    if stored is None or stored["plan_id"] != plan_id:
        raise HTTPException(status_code=404, detail="DocumentPhysicalPlan 不存在")
    physical = DocumentPhysicalPlan.model_validate(stored["physical_plan"])
    if physical.status != DocumentPlanStatus.READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="DocumentPhysicalPlan 仍需用户确认",
        )
    plan, bound_plan, reports = _context(
        user["user_id"], plan_id, physical.binding_revision
    )
    if plan.canonical_hash() != physical.logical_plan_hash:
        raise HTTPException(
            status_code=409,
            detail="逻辑计划已变化，请重新冻结 DocumentPhysicalPlan",
        )
    if bound_plan.canonical_hash() != physical.bound_plan_hash:
        raise HTTPException(
            status_code=409,
            detail="来源绑定已变化，请重新冻结 DocumentPhysicalPlan",
        )

    paths = {}
    try:
        for source in physical.sources:
            item = _upload_store().resolve(user["user_id"], source.artifact_id)
            paths[source.artifact_id] = Path(item.storage_path)
    except PermissionError as exc:
        raise HTTPException(
            status_code=404, detail="来源不存在或无权访问"
        ) from exc

    safe_user = "".join(
        char for char in user["user_id"] if char.isalnum() or char in "-_"
    )
    run_id = f"docrun_{uuid.uuid4().hex[:16]}"
    output_dir = (
        Path(settings.semantic_execution_root)
        / safe_user
        / plan_id
        / run_id
    )
    try:
        _, bundle, verification = await run_document_execution_graph(
            plan,
            bound_plan,
            reports,
            profile=physical.runtime_policy.profile,
            artifact_paths=paths,
            output_dir=output_dir,
            semantic_provider=_semantic_provider(physical),
            physical_plan=physical,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc)[:400],
        ) from exc
    return get_store().save_document_execution_run(
        user["user_id"],
        run_id=run_id,
        plan_id=plan_id,
        physical_plan_id=physical_plan_id,
        result=bundle.result,
        tool_result=bundle.tool_result,
        verification=verification,
        artifact_paths={"result": str(bundle.result_path.resolve())},
    )


@router.get("/{plan_id}/document-runs/{run_id}")
def get_document_run(
    plan_id: str,
    run_id: str,
    user=Depends(get_current_user),
):
    row = get_store().get_document_execution_run(user["user_id"], run_id)
    if row is None or row["plan_id"] != plan_id:
        raise HTTPException(status_code=404, detail="文档执行记录不存在")
    return row
