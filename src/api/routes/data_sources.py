# -*- coding: utf-8 -*-
"""数据源 API（Phase 2 Task 3 + Phase 3 database connections）。

- POST   /api/data-sources/uploads：流式上传
- GET    /api/data-sources/uploads/{upload_id}
- DELETE /api/data-sources/uploads/{upload_id}
- POST   /api/data-sources/connections：创建数据库命名连接（Phase 3）
- GET    /api/data-sources/connections：列出当前用户的数据库连接
- DELETE /api/data-sources/connections/{connection_id}
- POST   /api/data-sources/connections/test：测试连接（支持已存 connection_id 或内联草案）
- GET    /api/data-sources/connections/{connection_id}/schema：数据库 Schema 发现

响应不含 password / password_enc / 完整 DSN。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from src.config.settings import settings
from src.data_prep.document_models import DocumentElement
from src.data_prep.models import RawArtifact
from src.parsers.registry import get_parser_registry
from src.services.upload_store import UploadStore

from ..auth import get_current_user, get_store

router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])


def get_upload_store() -> UploadStore:
    """构造上传存储实例。根目录与配额由 settings 控制。"""
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


@router.post("/uploads")
async def upload_source(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """流式上传文件。user_id 从认证取，不接受请求体中的 user_id。"""
    store = get_upload_store()
    try:
        item = await store.save_upload(
            user["user_id"], file.filename or "unnamed", file, verify_magic=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(e))
    return item.model_dump(mode="json", exclude={"storage_path", "user_id"})


@router.get("/uploads/{upload_id}")
def get_upload(
    upload_id: str,
    user=Depends(get_current_user),
):
    """查询上传项。跨用户返回 404（不泄露存在性）。"""
    store = get_upload_store()
    try:
        item = store.resolve(user["user_id"], upload_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上传不存在")
    return item.model_dump(mode="json", exclude={"storage_path", "user_id"})


@router.get("/uploads/{upload_id}/content")
def get_upload_content(
    upload_id: str,
    user=Depends(get_current_user),
):
    """流式返回上传原件，供刷新后恢复预览；仍执行用户归属校验。"""
    store = get_upload_store()
    try:
        item = store.resolve(user["user_id"], upload_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上传不存在")
    return FileResponse(
        path=item.storage_path,
        media_type=item.media_type,
        filename=item.original_name or upload_id,
        content_disposition_type="inline",
    )


@router.get("/uploads/{upload_id}/document-preview")
async def get_document_preview(
    upload_id: str,
    user=Depends(get_current_user),
):
    """解析用户自己的 DOCX，并返回可定位的结构化预览元素。"""
    store = get_upload_store()
    try:
        item = store.resolve(user["user_id"], upload_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上传不存在")

    ext = Path(item.original_name).suffix.lstrip(".").lower()
    if ext != "docx":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="当前结构化文档预览仅支持 DOCX",
        )
    parser = get_parser_registry().select(
        media_type=item.media_type,
        extension=ext,
    )
    if parser is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="未找到 DOCX 解析器",
        )

    raw_bytes = await asyncio.to_thread(Path(item.storage_path).read_bytes)
    artifact = RawArtifact(
        artifact_id=f"raw-{item.sha256[:16]}",
        source_id=f"upload:{upload_id}",
        task_id=f"preview-{upload_id}",
        uri=item.original_name,
        media_type=item.media_type,
        size_bytes=item.size_bytes,
        sha256=item.sha256,
        storage_path=item.storage_path,
    )
    records, rejects = await asyncio.to_thread(parser.parse, artifact, raw_bytes)
    elements = [
        DocumentElement.model_validate(raw_element)
        for record in records
        for raw_element in (record.data.get("elements") or [])
    ]
    elements.sort(
        key=lambda element: (
            element.reading_order is None,
            element.reading_order or 0,
        )
    )
    return {
        "upload_id": upload_id,
        "original_name": item.original_name,
        "status": "ready" if elements else "empty",
        "elements": [element.model_dump(mode="json") for element in elements],
        "rejects": rejects,
    }


@router.delete("/uploads/{upload_id}")
def delete_upload(
    upload_id: str,
    user=Depends(get_current_user),
):
    """删除上传项。跨用户返回 404。"""
    store = get_upload_store()
    try:
        store.delete(user["user_id"], upload_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上传不存在")
    return {"ok": True}


# ===== Phase 3：数据库命名连接管理 =====


@router.post("/connections")
def create_db_connection(payload: dict, user=Depends(get_current_user)):
    """创建数据库命名连接（密码 Fernet 加密落库）。"""
    store = get_store()
    from src.services.db_connections import to_public_dict
    try:
        row = store.create_db_connection(
            user["user_id"],
            name=payload.get("name", ""),
            dialect=payload["dialect"],
            host=payload.get("host", ""),
            port=payload.get("port", 0),
            database_name=payload.get("database_name", ""),
            username=payload.get("username", ""),
            password=payload.get("password", ""),
            sqlite_relpath=payload.get("sqlite_relpath", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return to_public_dict(row)


@router.get("/connections")
def list_db_connections(user=Depends(get_current_user)):
    """列出当前用户的全部数据库连接（无密码）。"""
    store = get_store()
    from src.services.db_connections import to_public_dict
    items = store.list_db_connections(user["user_id"])
    return [to_public_dict(r) for r in items]


@router.delete("/connections/{connection_id}")
def delete_db_connection(connection_id: str, user=Depends(get_current_user)):
    """删除数据库连接（仅 owner）。"""
    store = get_store()
    deleted = store.delete_db_connection(connection_id, user["user_id"])
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="连接不存在")
    return {"ok": True}


@router.post("/connections/test")
def test_db_connection(payload: dict, user=Depends(get_current_user)):
    """统一测试上传或数据库连接；数据库支持已存连接与内联草案。"""
    if payload.get("source_type") == "upload_file":
        upload_id = payload.get("upload_id")
        if not upload_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 upload_id")
        try:
            item = get_upload_store().resolve(user["user_id"], upload_id)
        except PermissionError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上传不存在")
        return {
            "reachable": True, "message": "上传文件可访问",
            "sample": {"size_bytes": item.size_bytes, "original_name": item.original_name,
                       "sha256": item.sha256},
        }
    from src.services.db_connections import resolve_credential
    from src.connectors.database_connector import DatabaseConnector
    from src.data_prep.models import SourceSpec, SourceType

    user_id = user["user_id"]
    conn_id = payload.get("connection_id")

    if conn_id:
        # 校验归属
        store = get_store()
        row = store.get_db_connection(conn_id)
        if not row or row["user_id"] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="连接不存在")
        spec = SourceSpec(
            source_id="test", source_type=SourceType.DATABASE,
            locator=f"dbconn://{conn_id}", options={"user_id": user_id},
            credential_ref=f"dbconn:{conn_id}",
        )
    else:
        # 内联草案——直接构造临时 SourceSpec
        spec = SourceSpec(
            source_id="test", source_type=SourceType.DATABASE,
            locator="dbconn://inline", options={"user_id": user_id},
        )
        # 为内联草案直接注入 credentials
        from src.connectors.db_dialects import DbCredentials as DBC
        creds = DBC(
            dialect=payload["dialect"],
            host=payload.get("host", ""),
            port=payload.get("port", 0),
            database=payload.get("database_name", ""),
            username=payload.get("username", ""),
            password=payload.get("password", ""),
            sqlite_relpath=payload.get("sqlite_relpath", ""),
        )
        connector = DatabaseConnector(credentials=creds)
        import asyncio
        result = asyncio.run(connector.probe(spec))
        return {"reachable": result.reachable, "message": result.message,
                "sample": result.sample}

    connector = DatabaseConnector(credential_resolver=resolve_credential)
    import asyncio
    result = asyncio.run(connector.probe(spec))
    return {"reachable": result.reachable, "message": result.message,
            "sample": result.sample}


@router.get("/connections/{connection_id}/schema")
def get_db_schema(connection_id: str, schema: str = None, user=Depends(get_current_user)):
    """获取数据库 Schema 摘要（表/列/PK/估行数）。"""
    from src.services.db_connections import resolve_credential
    from src.connectors.database_connector import DatabaseConnector
    from src.data_prep.models import SourceSpec, SourceType

    store = get_store()
    row = store.get_db_connection(connection_id)
    if not row or row["user_id"] != user["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="连接不存在")

    spec = SourceSpec(
        source_id="schema", source_type=SourceType.DATABASE,
        locator=f"dbconn://{connection_id}", options={"user_id": user["user_id"]},
        credential_ref=f"dbconn:{connection_id}",
    )
    connector = DatabaseConnector(credential_resolver=resolve_credential)
    import asyncio
    result = asyncio.run(connector.discover(spec))
    return result
