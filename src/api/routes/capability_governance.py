# -*- coding: utf-8 -*-
"""能力治理三轴状态的认证产品 Interface；管理员审核视图与审计查看命令见 admin_router。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user, require_admin
from src.api.catalog_actor import catalog_actor_from_user
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    SqliteCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernance,
    SqliteValidationTaskResolver,
    SqliteCapabilityGovernanceRepository,
)
from src.config.settings import settings


router = APIRouter(
    prefix="/api/capability-governance",
    tags=["capability-governance"],
)

admin_router = APIRouter(
    prefix="/api/capability-governance/admin",
    tags=["capability-governance"],
)


def _governance() -> CapabilityGovernance:
    return CapabilityGovernance(
        CapabilityCatalog(
            SqliteCapabilityCatalogRepository(settings.webui_db_path)
        ),
        SqliteCapabilityGovernanceRepository(settings.webui_db_path),
        task_resolver=SqliteValidationTaskResolver(settings.webui_db_path),
    )


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)


@router.get("/packs")
def list_capability_governance(user=Depends(get_current_user)):
    actor = catalog_actor_from_user(user)
    items = _governance().list_visible_projections(actor)
    return {
        "items": [
            item.model_dump(
                mode="json",
                # 普通用户只能看见自己的 Owner 身份；跨 Owner Pack 已由目录层过滤。
                exclude=set(),
            )
            for item in items
        ]
    }


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error).strip("'"))
    if isinstance(error, RuntimeError):
        return HTTPException(status_code=503, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@router.post("/validations", status_code=202)
async def request_capability_validation(
    body: ValidationRequest,
    user=Depends(get_current_user),
    idempotency_key: str = Header(
        min_length=1,
        max_length=200,
        alias="Idempotency-Key",
    ),
):
    actor = catalog_actor_from_user(user)
    try:
        run = _governance().request_validation_for_task(
            actor,
            pack_ref=CapabilityPackRef(
                pack_id=body.pack_id,
                version=body.version,
                digest=body.digest,
            ),
            task_id=body.task_id,
            revision=body.revision,
            idempotency_key=idempotency_key,
        )
    except (PermissionError, KeyError, RuntimeError, ValueError) as error:
        raise _http_error(error) from error
    from src.api.capability_governance_runtime import (
        get_capability_validation_manager,
    )
    get_capability_validation_manager().notify()
    return run.model_dump(mode="json")


@router.get("/packs/{pack_id}/{version}/validation-tasks")
def list_validation_tasks(
    pack_id: str,
    version: str,
    digest: str,
    user=Depends(get_current_user),
):
    actor = catalog_actor_from_user(user)
    try:
        items = _governance().list_validation_task_options(
            actor,
            pack_ref=CapabilityPackRef(
                pack_id=pack_id,
                version=version,
                digest=digest,
            ),
        )
    except (PermissionError, ValueError, RuntimeError) as error:
        raise _http_error(error) from error
    return {"items": [item.model_dump(mode="json") for item in items]}


@router.get("/packs/{pack_id}/{version}/supply-chain-evidence")
def get_supply_chain_evidence(
    pack_id: str,
    version: str,
    digest: str,
    user=Depends(get_current_user),
):
    actor = catalog_actor_from_user(user)
    try:
        evidence = _governance().get_supply_chain_evidence(
            actor,
            pack_ref=CapabilityPackRef(
                pack_id=pack_id,
                version=version,
                digest=digest,
            ),
        )
    except (PermissionError, ValueError) as error:
        raise _http_error(error) from error
    return {
        "evidence": (
            evidence.model_dump(mode="json") if evidence is not None else None
        )
    }


@router.get("/validations")
def list_capability_validations(user=Depends(get_current_user)):
    actor = catalog_actor_from_user(user)
    return {
        "items": [
            item.model_dump(mode="json")
            for item in _governance().list_validations(actor)
        ]
    }


@router.get("/validations/{run_id}")
def get_capability_validation(run_id: str, user=Depends(get_current_user)):
    actor = catalog_actor_from_user(user)
    try:
        run = _governance().get_validation(actor, run_id)
    except (PermissionError, KeyError) as error:
        raise _http_error(error) from error
    return run.model_dump(mode="json")


@router.post("/validations/{run_id}/cancel")
def cancel_capability_validation(run_id: str, user=Depends(get_current_user)):
    actor = catalog_actor_from_user(user)
    try:
        run = _governance().cancel_validation(actor, run_id)
    except (PermissionError, KeyError, ValueError) as error:
        raise _http_error(error) from error
    return run.model_dump(mode="json")


class AuditViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    subject_type: Literal["task_prompt", "task_sources", "task_output"]
    reason: str = Field(min_length=1, max_length=1000)


@admin_router.get("/review")
def list_admin_review(admin=Depends(require_admin)):
    actor = catalog_actor_from_user(admin)
    items = _governance().list_admin_review(actor)
    return {"items": [item.model_dump(mode="json") for item in items]}


@admin_router.get("/review/{pack_id}/{version}")
def get_admin_review(
    pack_id: str,
    version: str,
    digest: str,
    admin=Depends(require_admin),
):
    actor = catalog_actor_from_user(admin)
    for item in _governance().list_admin_review(actor):
        if (
            item.pack_id == pack_id
            and item.version == version
            and item.digest == digest
        ):
            return item.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="能力审核项不存在")


@admin_router.post("/audit-view")
def audit_view_business_content(
    body: AuditViewRequest,
    admin=Depends(require_admin),
    idempotency_key: str = Header(
        min_length=1,
        max_length=200,
        alias="Idempotency-Key",
    ),
):
    actor = catalog_actor_from_user(admin)
    try:
        outcome = _governance().audit_view_business_content(
            actor,
            pack_ref=CapabilityPackRef(
                pack_id=body.pack_id,
                version=body.version,
                digest=body.digest,
            ),
            task_id=body.task_id,
            revision=body.revision,
            subject_type=body.subject_type,
            reason=body.reason,
            idempotency_key=idempotency_key,
        )
    except (PermissionError, KeyError, RuntimeError, ValueError) as error:
        raise _http_error(error) from error
    return outcome.model_dump(mode="json")


@admin_router.get("/audit-log")
def list_audit_log(admin=Depends(require_admin)):
    actor = catalog_actor_from_user(admin)
    records = _governance().list_audit_records(actor)
    # 界面按时间倒序展示最新记录在前。
    return {
        "items": [
            record.model_dump(mode="json")
            for record in reversed(records)
        ]
    }
