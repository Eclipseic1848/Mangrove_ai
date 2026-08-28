"""数据工作台的匿名网页来源获取 API。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user
from src.config.settings import settings
from src.source_acquisition import (
    AcquisitionConflictError,
    AnonymousWebFetcher,
    SourceAcquisitionRepository,
    SourceAcquisitionRequest,
    SourceAcquisitionService,
)


router = APIRouter(
    prefix="/api/semantic-workspace",
    tags=["semantic-workspace"],
)


class SourceAcquisitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=4096)
    purpose: str = Field(min_length=1, max_length=500)
    allowed_scope: Literal["current_page", "same_site"] = "current_page"
    page_limit: int = Field(default=1, ge=1, le=50)
    completeness_mode: Literal[
        "exploratory",
        "hard_min_pages",
        "hard_scope_complete",
    ] = "exploratory"
    required_valid_pages: int | None = Field(default=None, ge=1, le=50)


def get_source_acquisition_service() -> SourceAcquisitionService:
    return SourceAcquisitionService(
        SourceAcquisitionRepository(settings.webui_db_path),
        AnonymousWebFetcher(),
    )


def _not_found() -> HTTPException:
    # 对其他 Owner 也返回不存在，避免泄露资源身份。
    return HTTPException(status_code=404, detail="来源获取记录不存在")


@router.post(
    "/source-acquisitions",
    status_code=status.HTTP_202_ACCEPTED,
)
async def acquire_source(
    payload: SourceAcquisitionIn,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user=Depends(get_current_user),
):
    service = get_source_acquisition_service()
    try:
        return await service.acquire(
            owner_id=user["user_id"],
            idempotency_key=idempotency_key,
            request=SourceAcquisitionRequest(
                url=payload.url,
                purpose=payload.purpose,
                scope_kind=payload.allowed_scope,
                page_limit=payload.page_limit,
                completeness_mode=payload.completeness_mode,
                required_valid_pages=payload.required_valid_pages,
            ),
        )
    except AcquisitionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/source-acquisitions/{attempt_id}")
def get_source_acquisition(
    attempt_id: str,
    user=Depends(get_current_user),
):
    result = get_source_acquisition_service().repository.get_attempt(
        user["user_id"], attempt_id
    )
    if result is None:
        raise _not_found()
    return result


@router.post("/source-acquisitions/{attempt_id}/cancel")
def cancel_source_acquisition(
    attempt_id: str,
    user=Depends(get_current_user),
):
    result = get_source_acquisition_service().repository.cancel_attempt(
        user["user_id"], attempt_id
    )
    if result is None:
        raise _not_found()
    return result


@router.get("/source-snapshots/{snapshot_id}")
def get_source_snapshot(
    snapshot_id: str,
    user=Depends(get_current_user),
):
    result = get_source_acquisition_service().repository.get_snapshot(
        user["user_id"], snapshot_id
    )
    if result is None:
        raise _not_found()
    return result


@router.get("/source-artifacts/{artifact_id}")
def get_source_artifact(
    artifact_id: str,
    user=Depends(get_current_user),
):
    result = get_source_acquisition_service().repository.get_artifact(
        user["user_id"], artifact_id
    )
    if result is None:
        raise _not_found()
    return result
