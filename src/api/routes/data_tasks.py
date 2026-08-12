# -*- coding: utf-8 -*-
"""数据准备任务 API（Phase 2 Task 10）。

预览（小样本解析 + schema 推断 + recipe 影响）、正式任务（调 run_data_prep）、
状态查询、任务列表、Manifest 获取、复跑。所有端点复用 get_current_user 鉴权 + 用户归属校验。

公开任务入口支持 upload_file 与匿名 GET HTTP page 数据源；高级 HTTP 策略仍由连接器层提供。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.cleaning.profiler import ProfileAccumulator
from src.config.runtime_config import USER_KEYS
from src.config.settings import settings
from src.config.user_ctx import effective, user_overrides_context
from src.connectors.file_connector import FileConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.graph import run_data_prep
from src.data_prep.models import (
    DataPrepTaskSpec,
    OutputFormat,
    RawArtifact,
    Recipe,
    SourceLimits,
    SourceSpec,
    SourceType,
)
from src.data_prep.document_models import (
    DiscoverySpec,
    DocumentElement,
    ExtractedDocument,
    ExtractedField,
    ExtractedRecord,
    ExtractedTable,
    ExtractionFieldSpec,
    ExtractionSpec,
    ReviewTask,
    TaskGoal,
)
from src.parsers.registry import get_parser_registry
from src.services.document_extraction import (
    EvidenceBoundExtractor,
    InstructorQwenCandidateProvider,
    InstructorQwenIntentProvider,
)
from src.services.document_delivery import write_document_delivery
from src.services.document_ingest import ingest_document_artifact
from src.services.document_table_recipe import normalize_merged_tables
from src.services.upload_store import UploadStore
from src.llm.provider import get_provider

from ..auth import get_current_user, get_store

router = APIRouter(prefix="/api/data-tasks", tags=["data-tasks"])


# ---------- 请求模型 ----------
class PagePaginationOptionsIn(BaseModel):
    page_param: str = Field(default="page", min_length=1, max_length=100)
    per_page_param: str = Field(default="per_page", min_length=1, max_length=100)
    per_page: int = Field(default=100, ge=1, le=10000)
    start_page: int = Field(default=1, ge=1)
    max_pages: int = Field(default=100, ge=1, le=10000)


class PagePaginationIn(BaseModel):
    strategy: Literal["page"] = "page"
    options: PagePaginationOptionsIn = Field(default_factory=PagePaginationOptionsIn)


class PreviewSourceIn(BaseModel):
    source_type: Literal["upload_file", "http_api", "database"] = "upload_file"
    upload_id: Optional[str] = None
    url: Optional[str] = None
    pagination: Optional[PagePaginationIn] = None
    connection_id: Optional[str] = None
    table: Optional[str] = None
    fields: Optional[List[str]] = None
    filters: Optional[List[Dict[str, Any]]] = None
    time_range: Optional[Dict[str, Any]] = None
    incremental: Optional[Dict[str, Any]] = None


class PreviewIn(BaseModel):
    source: PreviewSourceIn
    sample_records: int = Field(default=20, ge=1, le=1000)


class TaskCreateSourceIn(BaseModel):
    source_type: Literal["upload_file", "http_api", "database"] = "upload_file"
    upload_id: Optional[str] = None
    url: Optional[str] = None
    pagination: Optional[PagePaginationIn] = None
    connection_id: Optional[str] = None
    table: Optional[str] = None
    fields: Optional[List[str]] = None
    filters: Optional[List[Dict[str, Any]]] = None
    time_range: Optional[Dict[str, Any]] = None
    incremental: Optional[Dict[str, Any]] = None


class TaskCreateIn(BaseModel):
    source: TaskCreateSourceIn
    intent: Optional[str] = None
    outputs: Optional[List[str]] = None
    max_records: Optional[int] = Field(default=None, ge=1)


class DocumentDraftIn(BaseModel):
    upload_ids: List[str] = Field(default_factory=list, max_length=50)
    unit_id: Optional[str] = None
    intent: str = Field(min_length=2, max_length=2000)
    provider: Optional[str] = Field(default=None, min_length=1, max_length=50)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)


class DocumentIntentMessageIn(BaseModel):
    intent: str = Field(min_length=2, max_length=2000)
    provider: Optional[str] = Field(default=None, min_length=1, max_length=50)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)


class DocumentModelSelectionIn(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=200)


class DocumentWorkspaceIn(BaseModel):
    upload_ids: List[str] = Field(default_factory=list, max_length=50)
    checked_upload_ids: Optional[List[str]] = Field(default=None, max_length=50)
    active_unit_id: Optional[str] = None
    active_task_id: Optional[str] = None
    selected_upload_id: Optional[str] = None


class DocumentScopeRevisionIn(BaseModel):
    upload_ids: List[str] = Field(min_length=1, max_length=50)


class DocumentUnitCreateIn(BaseModel):
    unit_type: Literal["single_file", "file_set"]
    name: str = Field(min_length=1, max_length=120)
    business_type: str = Field(default="", max_length=80)
    upload_ids: List[str] = Field(min_length=1, max_length=50)


class ReviewDecisionIn(BaseModel):
    decision: Literal["accept_candidate", "replace", "mark_not_found"]
    candidate_index: Optional[int] = Field(default=None, ge=0)
    value: Any = None
    note: Optional[str] = Field(default=None, max_length=1000)


# ---------- 工具 ----------
def _result_contract_from_intent(intent: str, draft: Any):
    """模型先判形态；明显的“全部/原表”意图再由确定性规则兜底。"""
    from src.data_prep.document_models import (
        ResultCardinality,
        ResultContract,
        ResultShape,
    )

    contract = draft.result_contract()
    normalized = "".join((intent or "").split()).casefold()
    asks_all = any(token in normalized for token in (
        "所有", "全部", "每个", "逐项", "全量", "all",
    ))
    asks_table = any(token in normalized for token in (
        "所有表格", "全部表格", "完整表格", "表格内容", "原表",
    ))
    asks_excel = any(token in normalized for token in ("excel", "xlsx"))
    asks_merge_table = (
        "合并" in normalized
        and "表" in normalized
        and any(token in normalized for token in ("一张", "一个", "单张"))
    )
    if asks_table:
        contract = contract.model_copy(update={
            "shape": ResultShape.TABLES,
            "cardinality": ResultCardinality.ALL,
            "record_grain": "每行对应原表中的一行",
            "renderer": "table_tabs",
            "exhaustive": True,
            "merge_tables": asks_merge_table,
        })
    elif asks_all and contract.shape == ResultShape.FIELDS:
        contract = contract.model_copy(update={
            "shape": ResultShape.RECORDS,
            "cardinality": ResultCardinality.ALL,
            "record_grain": contract.record_grain or "每行对应一条匹配记录",
            "renderer": "data_grid",
            "exhaustive": True,
        })
    if asks_excel and "xlsx" not in contract.output_formats:
        contract = contract.model_copy(update={
            "output_formats": [*contract.output_formats, "xlsx"],
        })
    return ResultContract.model_validate(contract)


def _upload_store() -> UploadStore:
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


def _public_document_artifacts(task_spec: Mapping[str, Any]) -> List[Dict[str, str]]:
    """只返回证据定位所需映射，不暴露服务端存储路径。"""
    raw_artifacts = list(task_spec.get("raw_artifacts") or [])
    return [
        {
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "upload_id": str(artifact.get("source_id") or "").removeprefix("upload:"),
            "original_name": str(artifact.get("uri") or ""),
        }
        for artifact in raw_artifacts
        if (
            isinstance(artifact, Mapping)
            and artifact.get("artifact_id")
            and artifact.get("media_type") != "application/zip"
        )
    ]


def _enforce_document_task_size(items: Sequence[Any]) -> None:
    """统一执行文档任务总字节上限，防止多文件绕过单文件配额。"""
    total_bytes = sum(int(item.size_bytes) for item in items)
    if total_bytes > settings.data_prep_max_task_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "单任务文件总大小超过限制："
                f"{total_bytes} > {settings.data_prep_max_task_bytes} 字节"
            ),
        )


def _user_model_context(user_id: str):
    """装载当前用户可覆盖配置，并在请求结束后恢复原上下文。"""
    values = get_store().config_all(user_id) or {}
    return user_overrides_context({
        key: value for key, value in values.items() if key in USER_KEYS
    })


def _resolve_document_model_selection(
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, str]:
    """解析并校验文档任务模型；显式任务值优先于用户/全局默认值。"""
    explicit = provider is not None or model is not None
    if explicit and not (provider and model):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provider 与 model 必须同时提供",
        )
    if not explicit:
        configured = effective("document_extraction_model")
        provider, separator, model = configured.partition("::")
        if not separator:
            provider = None
            model = None

    catalog = get_provider().list_models()
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip()
    if (
        normalized_provider
        and normalized_model
        and normalized_model in catalog.get(normalized_provider, [])
    ):
        return {"provider": normalized_provider, "model": normalized_model}
    if explicit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"模型不可用: {normalized_provider}::{normalized_model}",
        )

    preferred_provider = get_provider().default_provider
    fallback_provider = (
        preferred_provider
        if catalog.get(preferred_provider)
        else next(iter(catalog), "")
    )
    if not fallback_provider:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="当前没有可用模型，请先在设置页配置模型端点或 API Key",
        )
    return {
        "provider": fallback_provider,
        "model": catalog[fallback_provider][0],
    }


def _document_delivery_status(delivery, *, pending_reviews: int) -> str:
    """质量失败不能伪装为完成；可人工处理的结果才进入复核态。"""
    if delivery.quality.overall.value == "fail":
        return "FAILED"
    return "NEEDS_REVIEW" if pending_reviews else "COMPLETED"


def _resolve_upload(user_id: str, upload_id: Optional[str]) -> Any:
    """校验归属并返回 UploadItem，跨用户或不存在抛 404。"""
    if not upload_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 upload_id")
    try:
        return _upload_store().resolve(user_id, upload_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上传不存在")


def _resolve_document_unit(user_id: str, unit_id: str) -> Dict[str, Any]:
    """校验任务单位归属。"""
    unit = get_store().get_document_unit(user_id, unit_id)
    if not unit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务单位不存在")
    return unit


def _public_document_unit(user_id: str, unit: Mapping[str, Any]) -> Dict[str, Any]:
    """补齐成员公开元数据，不暴露服务端存储路径。"""
    members = []
    for upload_id in unit.get("upload_ids") or []:
        item = _resolve_upload(user_id, str(upload_id))
        members.append(item.model_dump(
            mode="json",
            exclude={"storage_path", "user_id"},
        ))
    name = str(unit.get("name") or "")
    if (
        unit.get("unit_type") == "single_file"
        and members
        and (not name or name == members[0]["upload_id"])
    ):
        name = members[0]["original_name"]
    return {
        **dict(unit),
        "name": name,
        "members": members,
    }


def _source_spec(source: PreviewSourceIn | TaskCreateSourceIn, user_id: str) -> SourceSpec:
    """把公开请求转换为内部 SourceSpec；客户端不能注入 headers 或凭证。"""
    if source.source_type == "upload_file":
        item = _resolve_upload(user_id, source.upload_id)
        return SourceSpec(
            source_id="src-1",
            source_type=SourceType.UPLOAD_FILE,
            locator=item.upload_id,
            options={"upload_id": item.upload_id, "user_id": user_id},
        )
    if source.source_type == "http_api":
        if not source.url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 HTTP API URL")
        options: Dict[str, Any] = {"url": source.url}
        if source.pagination:
            options["pagination"] = source.pagination.model_dump(mode="json")
        return SourceSpec(
            source_id="src-1",
            source_type=SourceType.HTTP_API,
            locator=source.url,
            options=options,
        )
    if source.source_type == "database":
        if not source.connection_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 connection_id")
        row = get_store().get_db_connection(source.connection_id)
        if not row or row["user_id"] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据库连接不存在")
        options: Dict[str, Any] = {"user_id": user_id, "mode": "table"}
        if source.table:
            options["table"] = source.table
        if source.fields is not None:
            options["fields"] = source.fields
        if source.filters is not None:
            options["filters"] = source.filters
        if source.time_range is not None:
            options["time_range"] = source.time_range
        if source.incremental is not None:
            options["incremental"] = source.incremental
        return SourceSpec(
            source_id="src-1",
            source_type=SourceType.DATABASE,
            locator=f"dbconn://{source.connection_id}",
            credential_ref=f"dbconn:{source.connection_id}",
            options=options,
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"不支持的源类型: {source.source_type}",
    )


def _http_preview_spec(src_spec: SourceSpec) -> SourceSpec:
    """把分页 HTTP 规格收敛为单页预览请求，避免预览拉取全量。"""
    preview_options = dict(src_spec.options)
    pagination = preview_options.pop("pagination", None)
    if pagination and pagination.get("strategy") == "page":
        page_options = pagination.get("options") or {}
        preview_options["params"] = {
            page_options.get("page_param", "page"): page_options.get("start_page", 1),
            page_options.get("per_page_param", "per_page"): page_options.get("per_page", 100),
        }
    return src_spec.model_copy(update={"options": preview_options})


async def _preview_source(
    src_spec: SourceSpec,
    task_id: str,
    artifact_store: ArtifactStore,
) -> tuple[List[Any], Any]:
    """读取预览制品，返回制品及可选上传元数据。"""
    src_spec.options["task_id"] = task_id
    item = None
    if src_spec.source_type == SourceType.UPLOAD_FILE:
        item = _resolve_upload(src_spec.options["user_id"], src_spec.options["upload_id"])
        connector = FileConnector(_upload_store(), artifact_store)
    elif src_spec.source_type == SourceType.HTTP_API:
        from src.connectors.http_api_connector import HttpApiConnector
        src_spec = _http_preview_spec(src_spec)
        connector = HttpApiConnector(artifact_store=artifact_store)
    elif src_spec.source_type == SourceType.DATABASE:
        from src.connectors.database_connector import DatabaseConnector
        from src.services.db_connections import resolve_credential
        connector = DatabaseConnector(
            artifact_store=artifact_store,
            credential_resolver=resolve_credential,
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的数据源")

    artifacts = []
    async for batch in connector.read(src_spec):
        if batch.fatal_error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=batch.fatal_error)
        artifacts.extend(batch.artifacts)
    await connector.close()
    return artifacts, item


def _finalize_status(final_state: Dict[str, Any]) -> str:
    """从 run_data_prep 最终状态推断任务状态。"""
    s = final_state.get("status")
    if s:
        return s
    if final_state.get("error") or not final_state.get("manifest_path"):
        return "FAILED"
    return "SUCCEEDED"


async def _execute_task(
    spec: DataPrepTaskSpec,
    task_id: str,
    checkpoint: Optional[Checkpoint] = None,
) -> Dict[str, Any]:
    """执行 run_data_prep 并更新 store。返回最终任务记录。"""
    store = get_store()
    try:
        final_state = await run_data_prep(spec, task_id, checkpoint=checkpoint)
        st = _finalize_status(final_state)
        quality = final_state.get("quality")
        store.update_data_prep_task(
            task_id,
            status=st,
            record_counts=final_state.get("record_counts") or {},
            quality=quality.model_dump(mode="json") if quality else None,
            manifest_path=final_state.get("manifest_path"),
            error=final_state.get("error"),
        )
        final_checkpoint = final_state.get("checkpoint")
        is_database = bool(spec.sources and spec.sources[0].source_type == SourceType.DATABASE)
        if is_database and isinstance(final_checkpoint, Checkpoint):
            store.set_task_checkpoint(task_id, final_checkpoint.to_dict())
    except Exception as e:  # noqa: BLE001
        store.update_data_prep_task(task_id, status="FAILED", error=str(e))
    return store.get_data_prep_task(task_id) or {"task_id": task_id, "status": "FAILED"}


# ---------- 端点 ----------
@router.post("/document-drafts")
async def create_document_draft(
    req: DocumentDraftIn,
    user=Depends(get_current_user),
):
    """根据用户意图生成可编辑 ExtractionSpec；此步骤不执行字段抽取。"""
    store = get_store()
    if req.unit_id:
        unit = _resolve_document_unit(user["user_id"], req.unit_id)
        upload_ids = list(unit["upload_ids"])
        upload_items = [
            _resolve_upload(user["user_id"], upload_id)
            for upload_id in upload_ids
        ]
    else:
        upload_ids = list(dict.fromkeys(req.upload_ids))
        if not upload_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少任务单位或上传文件",
            )
        items = [_resolve_upload(user["user_id"], item) for item in upload_ids]
        upload_items = items
        _enforce_document_task_size(upload_items)
        unit = store.create_document_unit(
            user["user_id"],
            unit_type="single_file" if len(upload_ids) == 1 else "file_set",
            name=(
                items[0].original_name
                if len(upload_ids) == 1
                else f"临时文件集 · {items[0].original_name}"
            ),
            upload_ids=upload_ids,
        )
    _enforce_document_task_size(upload_items)

    with _user_model_context(user["user_id"]):
        model_selection = _resolve_document_model_selection(req.provider, req.model)
        try:
            draft = await asyncio.to_thread(
                InstructorQwenIntentProvider(**model_selection).draft,
                req.intent,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"抽取方案生成失败: {exc}",
            ) from exc

    task_id = f"doc_{uuid.uuid4().hex[:12]}"
    extraction_spec = ExtractionSpec(
        goal=TaskGoal(
            objective=draft.objective,
            document_types=["document"],
            success_criteria=["所有非空字段必须绑定原文证据"],
        ),
        discovery=DiscoverySpec(artifact_ids=upload_ids),
        fields=[
            ExtractionFieldSpec(
                name=field.name,
                dtype=field.dtype,
                required=field.required,
                description=field.description,
            )
            for field in draft.fields
        ],
        result_contract=_result_contract_from_intent(req.intent, draft),
    )
    existing_runs = store.list_document_runs_for_unit(
        user["user_id"],
        unit["unit_id"],
    )
    revision = max(
        (
            int(item.get("spec", {}).get("revision") or 1)
            for item in existing_runs
        ),
        default=0,
    ) + 1
    task_spec = {
        "task_type": "document_extraction",
        "unit_id": unit["unit_id"],
        "thread_id": f"document-unit:{unit['unit_id']}",
        "upload_ids": upload_ids,
        "intent_messages": [req.intent],
        "model_selection": model_selection,
        "extraction_spec": extraction_spec.model_dump(mode="json"),
        "revision": revision,
    }
    task = store.create_data_prep_task(
        user["user_id"],
        task_id,
        task_spec,
        status="SPEC_DRAFT",
    )
    store.document_workspace_set(
        user["user_id"],
        upload_ids=upload_ids,
        checked_upload_ids=[],
        active_unit_id=unit["unit_id"],
        active_task_id=task_id,
        selected_upload_id=upload_ids[0],
    )
    return {
        "task_id": task_id,
        "unit_id": unit["unit_id"],
        "revision": revision,
        "status": task["status"],
        "model_selection": model_selection,
        "extraction_spec": extraction_spec.model_dump(mode="json"),
    }


@router.get("/document-workspace")
def get_document_workspace(user=Depends(get_current_user)):
    """读取持久化工作区；刷新不得从旧任务猜测当前文件范围。"""
    return get_store().document_workspace_get(user["user_id"])


@router.post("/document-units")
def create_document_unit(
    req: DocumentUnitCreateIn,
    user=Depends(get_current_user),
):
    """用户显式创建独立文件任务或批处理文件集。"""
    if req.unit_type == "single_file" and len(req.upload_ids) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="独立文件任务必须且只能包含一个文件",
        )
    if req.unit_type == "file_set" and len(set(req.upload_ids)) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件集至少需要两个不同文件",
        )
    for upload_id in req.upload_ids:
        _resolve_upload(user["user_id"], upload_id)
    try:
        unit = get_store().create_document_unit(
            user["user_id"],
            unit_type=req.unit_type,
            name=req.name,
            business_type=req.business_type,
            upload_ids=req.upload_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _public_document_unit(user["user_id"], unit)


@router.get("/document-units")
def list_document_units(user=Depends(get_current_user)):
    """列出用户的独立文件任务和文件集。"""
    store = get_store()
    result = []
    for unit in store.list_document_units(user["user_id"]):
        public = _public_document_unit(user["user_id"], unit)
        runs = [
            task
            for task in store.list_document_runs_for_unit(
                user["user_id"],
                unit["unit_id"],
            )
            if task.get("spec", {}).get("task_type") == "document_extraction"
        ]
        public["latest_task"] = runs[0] if runs else None
        public["run_count"] = len(runs)
        result.append(public)
    return result


@router.get("/document-units/{unit_id}/runs")
def list_document_unit_runs(
    unit_id: str,
    user=Depends(get_current_user),
):
    """读取任务单位自己的执行版本；不会混入其他任务单位。"""
    _resolve_document_unit(user["user_id"], unit_id)
    return [
        task
        for task in get_store().list_document_runs_for_unit(
            user["user_id"],
            unit_id,
        )
        if task.get("spec", {}).get("task_type") == "document_extraction"
    ]


@router.delete("/document-units/{unit_id}")
def archive_document_unit(
    unit_id: str,
    user=Depends(get_current_user),
):
    """从工作区移除任务单位；原文件、历史任务和结果均保留。"""
    unit = _resolve_document_unit(user["user_id"], unit_id)
    if not get_store().archive_document_unit(user["user_id"], unit_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务单位不存在",
        )
    return {
        "ok": True,
        "unit_id": unit_id,
        "unit_type": unit["unit_type"],
        "retained_uploads": True,
        "retained_history": True,
    }


@router.put("/document-workspace")
def update_document_workspace(
    req: DocumentWorkspaceIn,
    user=Depends(get_current_user),
):
    """保存当前文件范围和选中项，不删除上传原件或历史结果。"""
    for upload_id in req.upload_ids:
        _resolve_upload(user["user_id"], upload_id)
    if req.checked_upload_ids is not None and not set(
        req.checked_upload_ids
    ).issubset(set(req.upload_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务范围包含工作区之外的文件",
        )
    if req.active_task_id:
        task = get_store().get_data_prep_task(req.active_task_id)
        if not task or task["user_id"] != user["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="活动任务不存在",
            )
    if req.active_unit_id:
        _resolve_document_unit(user["user_id"], req.active_unit_id)
    return get_store().document_workspace_set(
        user["user_id"],
        upload_ids=req.upload_ids,
        checked_upload_ids=req.checked_upload_ids,
        active_unit_id=req.active_unit_id,
        active_task_id=req.active_task_id,
        selected_upload_id=req.selected_upload_id,
    )


@router.get("/document-runs/by-upload/{upload_id}")
def list_document_runs_by_upload(
    upload_id: str,
    user=Depends(get_current_user),
):
    """点击历史文件时读取其执行版本，最新结果排在第一位。"""
    _resolve_upload(user["user_id"], upload_id)
    return [
        task
        for task in get_store().list_document_runs_for_upload(
            user["user_id"],
            upload_id,
        )
        if task.get("spec", {}).get("task_type") == "document_extraction"
    ]


@router.put("/{task_id}/model-selection")
def update_document_model_selection(
    task_id: str,
    req: DocumentModelSelectionIn,
    user=Depends(get_current_user),
):
    """更新同一任务使用的模型；失败任务切换模型后可重新确认并重试。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task["spec"].get("task_type") != "document_extraction":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不是文档抽取任务")
    if task["status"] in {"EXTRACTING", "COMPLETED", "NEEDS_REVIEW"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前任务状态不允许切换模型",
        )
    with _user_model_context(user["user_id"]):
        model_selection = _resolve_document_model_selection(req.provider, req.model)
    task_spec = dict(task["spec"])
    task_spec["model_selection"] = model_selection
    next_status = "READY" if task["status"] == "FAILED" else task["status"]
    store.update_data_prep_task(task_id, status=next_status, spec=task_spec, error=None)
    return {
        "task_id": task_id,
        "status": next_status,
        "model_selection": model_selection,
    }


@router.post("/{task_id}/scope-revisions")
def create_document_scope_revision(
    task_id: str,
    req: DocumentScopeRevisionIn,
    user=Depends(get_current_user),
):
    """文件范围变化时创建不可变新版本；旧结果继续可查。"""
    store = get_store()
    parent = store.get_data_prep_task(task_id)
    if not parent or parent["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    parent_spec = dict(parent["spec"])
    if parent_spec.get("task_type") != "document_extraction":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不是文档抽取任务")
    upload_items = [
        _resolve_upload(user["user_id"], upload_id)
        for upload_id in req.upload_ids
    ]
    _enforce_document_task_size(upload_items)

    new_task_id = f"doc_{uuid.uuid4().hex[:12]}"
    extraction_spec = ExtractionSpec.model_validate(parent_spec["extraction_spec"])
    revised_extraction_spec = extraction_spec.model_copy(update={
        "discovery": extraction_spec.discovery.model_copy(update={
            "artifact_ids": req.upload_ids,
        }),
    })
    revision = int(parent_spec.get("revision") or 1) + 1
    new_spec = {
        key: value
        for key, value in parent_spec.items()
        if key not in {"raw_artifacts", "effective_extraction_spec"}
    }
    new_spec.update({
        "upload_ids": req.upload_ids,
        "parent_task_id": task_id,
        "revision": revision,
        "extraction_spec": revised_extraction_spec.model_dump(mode="json"),
    })
    unit_id = new_spec.get("unit_id")
    try:
        store.create_document_scope_revision_task(
            user["user_id"],
            new_task_id,
            new_spec,
            upload_ids=req.upload_ids,
            status="READY",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    workspace = store.document_workspace_set(
        user["user_id"],
        upload_ids=req.upload_ids,
        checked_upload_ids=req.upload_ids,
        active_unit_id=new_spec.get("unit_id"),
        active_task_id=new_task_id,
        selected_upload_id=req.upload_ids[0],
    )
    return {
        "task_id": new_task_id,
        "status": "READY",
        "model_selection": new_spec.get("model_selection"),
        "extraction_spec": new_spec["extraction_spec"],
        "parent_task_id": task_id,
        "revision": revision,
        "unit_id": unit_id,
        "workspace": workspace,
    }


@router.put("/{task_id}/extraction-spec")
def update_extraction_spec(
    task_id: str,
    extraction_spec: ExtractionSpec,
    user=Depends(get_current_user),
):
    """保存用户修改后的方案；确认前不执行抽取。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task["spec"].get("task_type") != "document_extraction":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不是文档抽取任务")
    if task["status"] not in {"SPEC_DRAFT", "READY"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="抽取已开始，不能修改当前版本；请新建修订版本",
        )
    allowed = set(task["spec"].get("upload_ids") or [])
    if not set(extraction_spec.discovery.artifact_ids).issubset(allowed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="抽取范围包含未授权文件")
    updated = dict(task["spec"])
    updated["extraction_spec"] = extraction_spec.model_dump(mode="json")
    store.update_data_prep_task(task_id, status="READY", spec=updated)
    return {
        "task_id": task_id,
        "status": "READY",
        "extraction_spec": updated["extraction_spec"],
    }


@router.post("/{task_id}/intent-messages")
async def revise_document_draft(
    task_id: str,
    req: DocumentIntentMessageIn,
    user=Depends(get_current_user),
):
    """在同一任务中根据后续聊天修订 ExtractionSpec，不新建任务。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task["spec"].get("task_type") != "document_extraction":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不是文档抽取任务")
    if task["status"] not in {"SPEC_DRAFT", "READY"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="抽取已开始，不能继续修改当前方案",
        )

    task_spec = dict(task["spec"])
    current_spec = ExtractionSpec.model_validate(task_spec["extraction_spec"])
    messages = list(task_spec.get("intent_messages") or [])
    messages.append(req.intent)
    with _user_model_context(user["user_id"]):
        stored_selection = task_spec.get("model_selection") or {}
        if req.provider is not None or req.model is not None:
            model_selection = _resolve_document_model_selection(
                req.provider,
                req.model,
            )
        else:
            model_selection = _resolve_document_model_selection(
                stored_selection.get("provider"),
                stored_selection.get("model"),
            )
        try:
            draft = await asyncio.to_thread(
                InstructorQwenIntentProvider(**model_selection).revise,
                current_spec,
                messages,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"抽取方案修订失败: {exc}",
            ) from exc

    revised_spec = current_spec.model_copy(update={
        "goal": current_spec.goal.model_copy(update={"objective": draft.objective}),
        "fields": [
            ExtractionFieldSpec(
                name=field.name,
                dtype=field.dtype,
                required=field.required,
                description=field.description,
            )
            for field in draft.fields
        ],
        "result_contract": _result_contract_from_intent(
            "\n".join(messages),
            draft,
        ),
    })
    task_spec["intent_messages"] = messages
    task_spec["model_selection"] = model_selection
    task_spec["extraction_spec"] = revised_spec.model_dump(mode="json")
    store.update_data_prep_task(task_id, status="SPEC_DRAFT", spec=task_spec)
    return {
        "task_id": task_id,
        "status": "SPEC_DRAFT",
        "model_selection": model_selection,
        "extraction_spec": task_spec["extraction_spec"],
    }


@router.post("/{task_id}/extract")
async def execute_document_extraction(
    task_id: str,
    user=Depends(get_current_user),
):
    """确认后解析文档并执行证据约束字段抽取。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task["status"] != "READY":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="抽取方案尚未确认")
    task_spec = dict(task["spec"])
    extraction_spec = ExtractionSpec.model_validate(task_spec["extraction_spec"])
    upload_items = [
        _resolve_upload(user["user_id"], upload_id)
        for upload_id in task_spec.get("upload_ids") or []
    ]
    _enforce_document_task_size(upload_items)
    if not store.transition_data_prep_task(
        task_id,
        from_statuses={"READY"},
        to_status="EXTRACTING",
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="任务状态已变化，请刷新后重试",
        )
    artifact_store = ArtifactStore()
    elements: List[DocumentElement] = []
    artifact_ids: List[str] = []
    raw_artifacts: List[RawArtifact] = []
    rejects: List[Dict[str, Any]] = []
    try:
        registry = get_parser_registry()
        for upload_id, item in zip(
            task_spec.get("upload_ids") or [],
            upload_items,
        ):
            raw_bytes = Path(item.storage_path).read_bytes()
            ext = Path(item.original_name).suffix.lstrip(".").lower()
            artifact = artifact_store.write_raw(
                task_id,
                f"upload:{upload_id}",
                raw_bytes,
                uri=item.original_name,
                media_type=item.media_type,
                ext=ext or None,
            )
            ingested = await asyncio.to_thread(
                ingest_document_artifact,
                artifact,
                raw_bytes,
                registry=registry,
                store=artifact_store,
            )
            artifact_ids.extend(ingested.artifact_ids)
            raw_artifacts.extend(ingested.raw_artifacts)
            elements.extend(ingested.elements)
            rejects.extend(ingested.rejects)

        if not artifact_ids:
            raise ValueError("任务范围内未发现可解析的 PDF、DOCX 或图片文档")
        effective_spec = extraction_spec.model_copy(update={
            "discovery": extraction_spec.discovery.model_copy(update={
                "artifact_ids": artifact_ids,
            }),
        })
        with _user_model_context(user["user_id"]):
            stored_selection = task_spec.get("model_selection") or {}
            model_selection = _resolve_document_model_selection(
                stored_selection.get("provider"),
                stored_selection.get("model"),
            )
            run = await asyncio.to_thread(
                EvidenceBoundExtractor(
                    InstructorQwenCandidateProvider(**model_selection)
                ).extract,
                effective_spec,
                elements,
            )
        task_spec["model_selection"] = model_selection
        task_spec["effective_extraction_spec"] = effective_spec.model_dump(mode="json")
        task_spec["raw_artifacts"] = [
            item.model_dump(mode="json") for item in raw_artifacts
        ]
        raw_tables = list(run.tables)
        table_recipe = (
            normalize_merged_tables(raw_tables)
            if extraction_spec.result_contract.merge_tables and raw_tables
            else None
        )
        effective_tables = table_recipe.tables if table_recipe else raw_tables
        delivery = write_document_delivery(
            artifact_store,
            task_id,
            spec=effective_spec,
            raw_artifacts=raw_artifacts,
            fields=run.fields,
            review_tasks=run.review_tasks,
            parse_rejects=rejects,
            records=run.records,
            tables=effective_tables,
            documents=run.documents,
            coverage=run.coverage,
            raw_tables=raw_tables if table_recipe else None,
            table_recipe_audit=table_recipe.audit if table_recipe else None,
        )
        final_status = _document_delivery_status(
            delivery,
            pending_reviews=len(run.review_tasks),
        )
        store.update_data_prep_task(
            task_id,
            status=final_status,
            spec=task_spec,
            record_counts=delivery.counts,
            quality=delivery.quality.model_dump(mode="json"),
            manifest_path=delivery.manifest_path,
            error="",
        )
        return {
            "task_id": task_id,
            "status": final_status,
            "artifacts": _public_document_artifacts(task_spec),
            "fields": [field.model_dump(mode="json") for field in run.fields],
            "records": [
                {
                    **record.model_dump(mode="json"),
                    "values": record.values,
                }
                for record in run.records
            ],
            "tables": [
                table.model_dump(mode="json")
                for table in effective_tables
            ],
            "documents": [
                document.model_dump(mode="json")
                for document in run.documents
            ],
            "aggregates": [
                {
                    **aggregate.model_dump(mode="json"),
                    "values": aggregate.values,
                }
                for aggregate in run.aggregates
            ],
            "table_recipe": table_recipe.audit if table_recipe else None,
            "coverage": run.coverage,
            "review_tasks": [
                review.model_dump(mode="json") for review in run.review_tasks
            ],
        }
    except Exception as exc:  # noqa: BLE001
        store.update_data_prep_task(task_id, status="FAILED", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文档抽取失败: {exc}",
        ) from exc


@router.post("/{task_id}/review-decisions/{review_task_id}")
def decide_document_review(
    task_id: str,
    review_task_id: str,
    req: ReviewDecisionIn,
    user=Depends(get_current_user),
):
    """保存人工裁决并更新结果；人工替换值不会被伪装为自动证据命中。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task["spec"].get("task_type") != "document_extraction":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不是文档抽取任务")

    artifact_store = ArtifactStore()
    fields_path = artifact_store.task_dir(task_id) / "extraction/extracted_fields.json"
    records_path = artifact_store.task_dir(task_id) / "extraction/extracted_records.jsonl"
    tables_path = artifact_store.task_dir(task_id) / "extraction/extracted_tables.json"
    documents_path = (
        artifact_store.task_dir(task_id) / "extraction/extracted_documents.json"
    )
    aggregates_path = (
        artifact_store.task_dir(task_id) / "extraction/extracted_aggregates.json"
    )
    reviews_path = artifact_store.task_dir(task_id) / "extraction/review_tasks.json"
    if not fields_path.exists() or not reviews_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="复核任务尚未生成")

    fields = json.loads(fields_path.read_text(encoding="utf-8"))
    record_rows = (
        artifact_store.read_jsonl(
            task_id,
            "extraction/extracted_records.jsonl",
        )
        if records_path.exists()
        else []
    )
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    review = next(
        (item for item in reviews if item.get("task_id") == review_task_id),
        None,
    )
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="复核项不存在")
    if review.get("status", "pending") != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="复核项已处理")
    row = None
    if review.get("record_id"):
        row = next(
            (
                item for item in record_rows
                if item.get("record_id") == review.get("record_id")
            ),
            None,
        )
        field = next(
            (
                item for item in (row or {}).get("fields") or []
                if item.get("name") == review.get("field_name")
            ),
            None,
        )
    else:
        field = next(
            (item for item in fields if item.get("name") == review.get("field_name")),
            None,
        )
    if field is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="复核字段不存在")

    decision_value = req.value
    if req.decision == "accept_candidate":
        candidate_index = req.candidate_index if req.candidate_index is not None else 0
        candidates = review.get("candidates") or []
        if candidate_index >= len(candidates):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="候选序号无效")
        candidate = candidates[candidate_index]
        element_ids = set(candidate.get("element_ids") or [])
        evidence_refs = [
            item for item in field.get("evidence_refs") or []
            if item.get("element_id") in element_ids
        ]
        if not evidence_refs:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="候选缺少已验证证据，不能标记为已找到",
            )
        decision_value = candidate.get("value")
        field.update({
            "value": decision_value,
            "status": "found",
            "evidence_refs": evidence_refs,
            "review_reason": None,
        })
    elif req.decision == "replace":
        if req.value is None or (isinstance(req.value, str) and not req.value.strip()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="人工修订值不能为空")
        field.update({
            "value": req.value,
            "status": "low_confidence",
            "review_reason": "用户人工修订；保留原始证据供审计，不视为模型自动验证值",
        })
    else:
        decision_value = None
        field.update({
            "value": None,
            "status": "not_found",
            "evidence_refs": [],
            "review_reason": "用户人工确认未找到",
        })

    resolution = {
        "decision": req.decision,
        "candidate_index": req.candidate_index,
        "value": decision_value,
        "note": req.note,
        "user_id": user["user_id"],
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    review["status"] = "resolved"
    review["resolution"] = resolution
    if row is not None:
        row["values"] = {
            item.get("name"): item.get("value")
            for item in row.get("fields") or []
        }
        row["review_required"] = any(
            item.get("record_id") == row.get("record_id")
            and item.get("status", "pending") == "pending"
            for item in reviews
        )
        if not row["review_required"]:
            row["status"] = "found"
    if not store.transition_data_prep_task(
        task_id,
        from_statuses={"NEEDS_REVIEW"},
        to_status="REVIEWING",
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="复核任务状态已变化，请刷新后重试",
        )
    artifact_store.append_jsonl(
        task_id,
        "extraction/review_decisions.jsonl",
        [{"review_task_id": review_task_id, "field_name": review["field_name"], **resolution}],
    )
    pending = sum(item.get("status", "pending") == "pending" for item in reviews)
    if record_rows:
        artifact_store.write_jsonl(
            task_id,
            "extraction",
            "extracted_records.jsonl",
            record_rows,
        )
    task_spec = task["spec"]
    effective_spec = ExtractionSpec.model_validate(
        task_spec["effective_extraction_spec"]
    )
    raw_artifacts = [
        RawArtifact.model_validate(item)
        for item in task_spec.get("raw_artifacts") or []
    ]
    rejects_path = artifact_store.task_dir(task_id) / "extraction/parse_rejects.json"
    raw_tables_path = (
        artifact_store.task_dir(task_id)
        / "extraction/extracted_tables_raw.json"
    )
    table_recipe_path = (
        artifact_store.task_dir(task_id)
        / "extraction/table_recipe_audit.json"
    )
    parse_rejects = (
        json.loads(rejects_path.read_text(encoding="utf-8"))
        if rejects_path.exists()
        else []
    )
    delivery = write_document_delivery(
        artifact_store,
        task_id,
        spec=effective_spec,
        raw_artifacts=raw_artifacts,
        fields=[ExtractedField.model_validate(item) for item in fields],
        review_tasks=[ReviewTask.model_validate(item) for item in reviews],
        parse_rejects=parse_rejects,
        records=[ExtractedRecord.model_validate(item) for item in record_rows],
        tables=(
            [
                ExtractedTable.model_validate(item)
                for item in json.loads(tables_path.read_text(encoding="utf-8"))
            ]
            if tables_path.exists()
            else []
        ),
        documents=(
            [
                ExtractedDocument.model_validate(item)
                for item in json.loads(
                    documents_path.read_text(encoding="utf-8")
                )
            ]
            if documents_path.exists()
            else []
        ),
        raw_tables=(
            [
                ExtractedTable.model_validate(item)
                for item in json.loads(
                    raw_tables_path.read_text(encoding="utf-8")
                )
            ]
            if raw_tables_path.exists()
            else None
        ),
        table_recipe_audit=(
            json.loads(table_recipe_path.read_text(encoding="utf-8"))
            if table_recipe_path.exists()
            else None
        ),
        coverage={
            key: value
            for key, value in (task.get("record_counts") or {}).items()
            if key.startswith((
                "elements_",
                "records_",
                "table_",
                "tables_",
                "document_",
                "documents_",
                "aggregate_",
                "aggregates_",
            ))
        },
    )
    final_status = _document_delivery_status(
        delivery,
        pending_reviews=pending,
    )
    store.update_data_prep_task(
        task_id,
        status=final_status,
        record_counts=delivery.counts,
        quality=delivery.quality.model_dump(mode="json"),
        manifest_path=delivery.manifest_path,
    )
    return {
        "task_id": task_id,
        "status": final_status,
        "artifacts": _public_document_artifacts(task_spec),
        "fields": fields,
        "records": record_rows,
        "tables": (
            json.loads(tables_path.read_text(encoding="utf-8"))
            if tables_path.exists()
            else []
        ),
        "documents": (
            json.loads(documents_path.read_text(encoding="utf-8"))
            if documents_path.exists()
            else []
        ),
        "aggregates": (
            json.loads(aggregates_path.read_text(encoding="utf-8"))
            if aggregates_path.exists()
            else []
        ),
        "review_tasks": reviews,
        "review_decisions": artifact_store.read_jsonl(
            task_id,
            "extraction/review_decisions.jsonl",
        ),
        "table_recipe": (
            json.loads(table_recipe_path.read_text(encoding="utf-8"))
            if table_recipe_path.exists()
            else None
        ),
    }


@router.get("/{task_id}/extraction-results")
def get_document_extraction_results(
    task_id: str,
    user=Depends(get_current_user),
):
    """读取字段与复核产物；跨用户不可见。"""
    task = get_store().get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    artifact_store = ArtifactStore()
    fields_path = artifact_store.task_dir(task_id) / "extraction/extracted_fields.json"
    records_path = artifact_store.task_dir(task_id) / "extraction/extracted_records.jsonl"
    tables_path = artifact_store.task_dir(task_id) / "extraction/extracted_tables.json"
    documents_path = (
        artifact_store.task_dir(task_id) / "extraction/extracted_documents.json"
    )
    aggregates_path = (
        artifact_store.task_dir(task_id) / "extraction/extracted_aggregates.json"
    )
    reviews_path = artifact_store.task_dir(task_id) / "extraction/review_tasks.json"
    decisions_path = (
        artifact_store.task_dir(task_id) / "extraction/review_decisions.jsonl"
    )
    table_recipe_path = (
        artifact_store.task_dir(task_id)
        / "extraction/table_recipe_audit.json"
    )
    if not fields_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="抽取结果尚未生成")
    return {
        "task_id": task_id,
        "status": task["status"],
        "artifacts": _public_document_artifacts(task["spec"]),
        "fields": json.loads(fields_path.read_text(encoding="utf-8")),
        "records": (
            artifact_store.read_jsonl(
                task_id,
                "extraction/extracted_records.jsonl",
            )
            if records_path.exists()
            else []
        ),
        "tables": (
            json.loads(tables_path.read_text(encoding="utf-8"))
            if tables_path.exists()
            else []
        ),
        "documents": (
            json.loads(documents_path.read_text(encoding="utf-8"))
            if documents_path.exists()
            else []
        ),
        "aggregates": (
            json.loads(aggregates_path.read_text(encoding="utf-8"))
            if aggregates_path.exists()
            else []
        ),
        "coverage": {
            key: value
            for key, value in (task.get("record_counts") or {}).items()
            if key.startswith((
                "elements_",
                "records_",
                "table_",
                "tables_",
                "document_",
                "documents_",
                "aggregate_",
                "aggregates_",
            ))
        },
        "review_tasks": (
            json.loads(reviews_path.read_text(encoding="utf-8"))
            if reviews_path.exists()
            else []
        ),
        "review_decisions": (
            artifact_store.read_jsonl(task_id, "extraction/review_decisions.jsonl")
            if decisions_path.exists()
            else []
        ),
        "table_recipe": (
            json.loads(table_recipe_path.read_text(encoding="utf-8"))
            if table_recipe_path.exists()
            else None
        ),
    }


@router.post("/preview")
async def preview_task(req: PreviewIn, user=Depends(get_current_user)):
    """小样本预览：文件限字节；分页 HTTP 只请求起始页。"""
    src_spec = _source_spec(req.source, user["user_id"])
    if src_spec.source_type == SourceType.DATABASE:
        src_spec.limits = SourceLimits(max_records=req.sample_records)
    if src_spec.source_type == SourceType.UPLOAD_FILE:
        item = _resolve_upload(user["user_id"], req.source.upload_id)
        if item.size_bytes > settings.data_prep_preview_max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"预览文件超过 {settings.data_prep_preview_max_bytes} 字节限制",
            )

    task_id = f"preview_{uuid.uuid4().hex[:8]}"
    artifact_store = ArtifactStore()
    try:
        artifacts, item = await _preview_source(src_spec, task_id, artifact_store)
        if not artifacts:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法获取上传文件")

        art = artifacts[0]
        raw_bytes = artifact_store.read_raw_bytes(task_id, art.storage_path)
        registry = get_parser_registry()
        ext = Path(art.uri or art.storage_path).suffix.lstrip(".").lower()
        parser = registry.select(media_type=art.media_type, extension=ext)
        if parser is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无匹配解析器: {art.media_type}/{ext}",
            )
        records, rejects = parser.parse(art, raw_bytes)

        sample_n = max(1, min(req.sample_records, settings.data_prep_preview_max_records))
        sample = [r.data for r in records[:sample_n]]

        acc = ProfileAccumulator()
        acc.add_records(records)
        prof = acc.finalize()
        schema = {
            "fields": [{"name": f, "dtype": "string"} for f in prof.fields],
            "inferred": True,
            "record_count": prof.record_count,
        }

        recipe = Recipe()
        high_impact = [r.rule_id for r in recipe.rules if r.high_impact]

        return {
            "probe": {
                "reachable": True,
                "size_bytes": art.size_bytes,
                "media_type": art.media_type,
                "original_name": item.original_name if item else (art.uri or "HTTP API"),
            },
            "sample": sample,
            "schema": schema,
            "parser_warnings": [r.get("reason", str(r)) if isinstance(r, dict) else str(r) for r in rejects],
            "recipe": recipe.model_dump(mode="json"),
            "estimated_records": len(records),
            "estimated_bytes": art.size_bytes,
            "high_impact_rules": high_impact,
        }
    finally:
        shutil.rmtree(artifact_store.task_dir(task_id), ignore_errors=True)


@router.post("")
async def create_task(req: TaskCreateIn, user=Depends(get_current_user)):
    """创建并同步执行数据准备任务。返回最终任务记录。"""
    source = _source_spec(req.source, user["user_id"])

    task_id = f"task_{uuid.uuid4().hex[:12]}"
    outputs = (
        [OutputFormat(f) for f in req.outputs]
        if req.outputs
        else [OutputFormat.JSONL, OutputFormat.PARQUET]
    )
    source.limits = SourceLimits(max_records=req.max_records) if req.max_records else None
    spec = DataPrepTaskSpec(
        intent=req.intent or "数据准备任务",
        sources=[source],
        outputs=outputs,
    )
    spec.task_id = task_id

    store = get_store()
    store.create_data_prep_task(user["user_id"], task_id, spec.model_dump(mode="json"))
    return await _execute_task(spec, task_id)


@router.get("")
def list_tasks(user=Depends(get_current_user)):
    """列出当前用户的数据准备任务，供页面刷新后恢复。"""
    store = get_store()
    return [
        store.get_data_prep_task(item["task_id"])
        for item in store.list_data_prep_tasks(user["user_id"])
    ]


@router.get("/{task_id}")
def get_task(task_id: str, user=Depends(get_current_user)):
    """查询任务状态、计数与质量。跨用户返回 404。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task


@router.get("/{task_id}/manifest")
def get_manifest(task_id: str, user=Depends(get_current_user)):
    """获取任务 Manifest。跨用户返回 404。"""
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if not task.get("manifest_path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest 尚未生成")
    artifact_store = ArtifactStore()
    path = artifact_store.resolve_path(task["manifest_path"])
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/{task_id}/rerun")
async def rerun_task(task_id: str, user=Depends(get_current_user)):
    """复跑任务（重新执行完整图）。跨用户返回 404。

    reuse_raw 复用 RawArtifact 的增量路径保留给后续阶段；本版重新获取。
    """
    store = get_store()
    task = store.get_data_prep_task(task_id)
    if not task or task["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    spec = DataPrepTaskSpec.model_validate(task["spec"])
    spec.task_id = task_id

    # 清理旧产物后重跑（parsed/clean 的 part 文件用独占写，必须先清）
    shutil.rmtree(ArtifactStore().task_dir(task_id), ignore_errors=True)
    store.update_data_prep_task(task_id, status="RUNNING", error=None)
    is_database = bool(spec.sources and spec.sources[0].source_type == SourceType.DATABASE)
    checkpoint_data = store.get_task_checkpoint(task_id) if is_database else None
    checkpoint = None
    if checkpoint_data:
        checkpoint = Checkpoint(
            cursor=checkpoint_data.get("cursor"),
            watermark=checkpoint_data.get("watermark"),
            processed_artifact_ids=set(checkpoint_data.get("processed_artifact_ids") or []),
            processed_record_keys=set(checkpoint_data.get("processed_record_keys") or []),
            page=int(checkpoint_data.get("page") or 0),
            completed_batch_ids=list(checkpoint_data.get("completed_batch_ids") or []),
            next_part_no=int(checkpoint_data.get("next_part_no") or 0),
            is_final=False,
        )
        if checkpoint.cursor:
            try:
                cursor_data = json.loads(checkpoint.cursor)
                cursor_data["done"] = False
                checkpoint.cursor = json.dumps(cursor_data, ensure_ascii=False)
            except (TypeError, ValueError, json.JSONDecodeError):
                checkpoint = None
    return await _execute_task(spec, task_id, checkpoint=checkpoint)
