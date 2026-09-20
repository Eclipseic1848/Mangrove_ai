"""数据工作台的匿名网页来源获取 API。"""
from __future__ import annotations

from src.source_acquisition.reuse import guarded_response

from src.api.auth import get_execution_user

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    url: str | None = Field(default=None, min_length=1, max_length=4096)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    time_range: Literal["any", "day", "week", "month", "year"] = "any"
    domains: list[str] = Field(default_factory=list, max_length=10)
    purpose: str = Field(min_length=1, max_length=500)
    allowed_scope: Literal["current_page", "same_site", "public_search"] = "current_page"
    page_limit: int | None = Field(default=None, ge=1, le=50)
    completeness_mode: Literal[
        "exploratory",
        "hard_min_pages",
        "hard_scope_complete",
    ] = "exploratory"
    required_valid_pages: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def validate_source_scope(self):
        if bool(self.url) == bool(self.query):
            raise ValueError("请提供网址或搜索需求，不能同时提供")
        if self.query:
            if self.allowed_scope == "same_site":
                raise ValueError("搜索需求不能使用同站网址范围")
            self.allowed_scope = "public_search"
            self.page_limit = self.page_limit or 10
            if self.page_limit > 20:
                raise ValueError("一次公开搜索最多读取 20 个候选页面")
        else:
            # 单网址请求不能借可选搜索字段扩大既有授权。
            if self.allowed_scope == "public_search" or self.domains or self.time_range != "any":
                raise ValueError("搜索范围和时间筛选仅适用于公开搜索需求")
            self.page_limit = self.page_limit or 1
        return self


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
    openapi_extra={"x-mangrove-task-control": True},
)
async def acquire_source(
    payload: SourceAcquisitionIn,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user=Depends(get_execution_user),
):
    service = get_source_acquisition_service()
    try:
        return await service.acquire(
            owner_id=user["user_id"],
            idempotency_key=idempotency_key,
            request=SourceAcquisitionRequest(
                url=payload.url or "",
                query=payload.query or "",
                time_range=payload.time_range,
                domains=tuple(payload.domains),
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


def _saved_source_refs(values):
    import sqlite3
    from contextlib import closing
    from src.config.settings import settings
    owner=values["user"]["user_id"]
    # 身份工厂不读取摘要或正文；使用事实登记后才进入原读取入口。
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        connection.row_factory=sqlite3.Row
        if values.get("artifact_id"):
            artifacts=connection.execute("SELECT artifact_id,snapshot_id,content_sha256 FROM source_artifacts WHERE owner_id=? AND artifact_id=?",(owner,values["artifact_id"])).fetchall()
            if not artifacts: raise PermissionError("来源不存在")
        else:
            if values.get("attempt_id"):
                row=connection.execute("SELECT snapshot_id FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?",(owner,values["attempt_id"])).fetchone()
                if row is None: raise PermissionError("来源不存在")
                snapshot_id=row[0]
            else:
                snapshot_id=values["snapshot_id"]
                if connection.execute("SELECT 1 FROM source_snapshots WHERE owner_id=? AND snapshot_id=?",(owner,snapshot_id)).fetchone() is None:
                    raise PermissionError("来源不存在")
            artifacts=connection.execute("SELECT artifact_id,snapshot_id,content_sha256 FROM source_artifacts WHERE owner_id=? AND snapshot_id=?",(owner,snapshot_id)).fetchall() if snapshot_id else []
    return [{"kind":"web_artifact","snapshot_id":item["snapshot_id"],"artifact_id":item["artifact_id"],"sha256":item["content_sha256"]} for item in artifacts]


@guarded_response("preview",_saved_source_refs)
def _saved_attempt_response(snapshot_id,attempt,user):
    snapshot=get_source_acquisition_service().repository.get_snapshot(user["user_id"],snapshot_id)
    if snapshot is None: raise _not_found()
    return {**attempt,"snapshot":snapshot}


@router.get("/source-acquisitions/{attempt_id}")
def get_source_acquisition(
    attempt_id: str,
    user=Depends(get_current_user),
):
    result = get_source_acquisition_service().repository.get_attempt(
        user["user_id"], attempt_id, include_snapshot=False
    )
    if result is None:
        raise _not_found()
    if result.get("snapshot_id"):
        return _saved_attempt_response(snapshot_id=result["snapshot_id"],attempt=result,user=user)
    return result


@router.post("/source-acquisitions/{attempt_id}/cancel")
def cancel_source_acquisition(
    attempt_id: str,
    user=Depends(get_execution_user),
):
    result = get_source_acquisition_service().repository.cancel_attempt(
        user["user_id"], attempt_id
    )
    if result is None:
        raise _not_found()
    return result


@router.get("/source-snapshots/{snapshot_id}")
@guarded_response("preview",_saved_source_refs)
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
@guarded_response("preview",_saved_source_refs)
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
