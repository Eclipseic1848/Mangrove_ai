# -*- coding: utf-8 -*-
"""安全文件上传存储（Phase 2 Task 3）。

职责：
- 用户隔离：<root>/<user_id>/objects/<upload_id>，跨用户不可访问
- 服务端生成文件名：upload_id 用 uuid，绝不使用客户端提供的文件名
- 配额限制：超 max_bytes 拒绝并清理 staging 残留
- 哈希登记：边写边算 sha256 和 size_bytes
- 原始名清理：只保留 basename，去除路径成分

所有文件 UTF-8 显式编码（ADR-0006）。上传根目录可配置（DATA_PREP_UPLOAD_ROOT）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

# 扩展名 -> 期望 MIME（魔数校验用）
_EXT_MIME: dict[str, str] = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".parquet": "application/vnd.apache.parquet",
    ".txt": "text/plain",
    ".html": "text/html",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}

# zip 容器族：filetype 对 xlsx/docx 可能返回 application/zip，与扩展名期望需互认
_ZIP_FAMILY = {
    "application/zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _magic_matches(detected_mime: Optional[str], expected_mime: Optional[str], ext: str) -> bool:
    """判断魔数检测结果与扩展名期望是否一致。文本格式无魔数时放行。"""
    if detected_mime is None:
        return True  # 文本格式（csv/json/txt 等）无魔数，放行
    if expected_mime is None:
        return True  # 未知扩展名，放行
    if detected_mime == expected_mime:
        return True
    # zip 容器族互认（xlsx/docx/zip 共享 zip 容器）
    if detected_mime in _ZIP_FAMILY and ext.lower() in {".xlsx", ".docx", ".zip"}:
        return True
    return False


class UploadItem(BaseModel):
    """已验证上传的元数据。storage_path 对外不可见。"""

    upload_id: str
    user_id: str
    original_name: str
    storage_path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str


class UploadStore:
    """用户隔离的安全上传存储。"""

    def __init__(self, root: str, *, max_bytes: int) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes

    def _user_dir(self, user_id: str, sub: str) -> Path:
        """用户隔离目录。user_id 只用于目录名，不做路径拼接防注入。"""
        safe = "".join(c for c in user_id if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError("无效用户标识")
        d = self.root / safe / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _verify_magic(self, data_or_path: Any, original_name: str) -> None:
        """用 filetype 库检测魔数，与扩展名期望不一致则拒绝。"""
        import filetype

        kind = filetype.guess(data_or_path) if isinstance(data_or_path, (bytes, bytearray)) else filetype.guess(str(data_or_path))
        detected = kind.mime if kind else None
        ext = Path(original_name).suffix.lower()
        expected = _EXT_MIME.get(ext)
        if not _magic_matches(detected, expected, ext):
            raise ValueError(
                f"魔数 {detected} 与扩展名 {ext} 期望 {expected} 不一致，疑似伪造文件类型"
            )

    def save_bytes(
        self,
        user_id: str,
        original_name: str,
        data: bytes,
        *,
        media_type: str = "application/octet-stream",
        verify_magic: bool = False,
    ) -> UploadItem:
        """保存字节流为已验证上传。超限拒绝并清理 staging。"""
        if verify_magic:
            self._verify_magic(data, original_name)
        upload_id = uuid.uuid4().hex
        staging_dir = self._user_dir(user_id, "staging")
        staging_path = staging_dir / upload_id

        digest = hashlib.sha256()
        size = 0
        try:
            with staging_path.open("xb") as fh:
                fh.write(data)
                digest.update(data)
                size = len(data)
            if size > self.max_bytes:
                raise ValueError(f"上传大小 {size} 超过上限 {self.max_bytes} 字节")
        except Exception:
            staging_path.unlink(missing_ok=True)
            raise

        objects_dir = self._user_dir(user_id, "objects")
        object_path = objects_dir / upload_id
        shutil.move(str(staging_path), str(object_path))

        item = UploadItem(
            upload_id=upload_id,
            user_id=user_id,
            original_name=Path(original_name).name,
            storage_path=str(object_path.resolve()),
            media_type=media_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
        # 元数据 sidecar（供 resolve 读回，不依赖重新读文件）
        meta_path = objects_dir / f"{upload_id}.meta"
        meta_path.write_text(item.model_dump_json(), encoding="utf-8")
        return item

    async def save_upload(
        self,
        user_id: str,
        original_name: str,
        stream: Any,
        *,
        media_type: str = "application/octet-stream",
        verify_magic: bool = True,
        chunk_size: int = 1024 * 1024,
    ) -> UploadItem:
        """流式上传：分块读写，边写边算 sha256/size，超限立即停止。

        stream 需提供 async read(size) 方法（FastAPI UploadFile 兼容）。
        不为整个文件分配完整内存缓冲。
        """
        upload_id = uuid.uuid4().hex
        staging_dir = self._user_dir(user_id, "staging")
        staging_path = staging_dir / upload_id

        digest = hashlib.sha256()
        size = 0
        try:
            with staging_path.open("xb") as fh:
                while True:
                    chunk = await stream.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError(f"上传大小 {size} 超过上限 {self.max_bytes} 字节")
        except Exception:
            staging_path.unlink(missing_ok=True)
            raise

        if verify_magic:
            self._verify_magic(staging_path, original_name)

        objects_dir = self._user_dir(user_id, "objects")
        object_path = objects_dir / upload_id
        shutil.move(str(staging_path), str(object_path))

        item = UploadItem(
            upload_id=upload_id,
            user_id=user_id,
            original_name=Path(original_name).name,
            storage_path=str(object_path.resolve()),
            media_type=media_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
        meta_path = objects_dir / f"{upload_id}.meta"
        meta_path.write_text(item.model_dump_json(), encoding="utf-8")
        return item

    def resolve(self, user_id: str, upload_id: str) -> UploadItem:
        """解析上传项。跨用户访问抛 PermissionError。"""
        safe_id = "".join(c for c in upload_id if c.isalnum() or c in "-_")
        if not safe_id or safe_id != upload_id:
            raise PermissionError("无效上传标识")
        objects_dir = self._user_dir(user_id, "objects")
        meta_path = objects_dir / f"{upload_id}.meta"
        object_path = objects_dir / upload_id
        if not object_path.exists():
            raise PermissionError("上传不存在或无权访问")
        if meta_path.exists():
            return UploadItem.model_validate_json(meta_path.read_text(encoding="utf-8"))
        # 兜底：无 sidecar 时从文件重建（size/sha256 可恢复，original_name 不可恢复）
        data = object_path.read_bytes()
        return UploadItem(
            upload_id=upload_id,
            user_id=user_id,
            original_name="",
            storage_path=str(object_path.resolve()),
            media_type="application/octet-stream",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def delete(self, user_id: str, upload_id: str) -> None:
        """删除上传项。跨用户抛 PermissionError。"""
        safe_id = "".join(c for c in upload_id if c.isalnum() or c in "-_")
        if not safe_id or safe_id != upload_id:
            raise PermissionError("无效上传标识")
        objects_dir = self._user_dir(user_id, "objects")
        object_path = objects_dir / upload_id
        meta_path = objects_dir / f"{upload_id}.meta"
        if not object_path.exists():
            raise PermissionError("上传不存在或无权访问")
        object_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
