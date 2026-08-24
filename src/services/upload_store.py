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
        """用户隔离目录；Owner 标识必须原样构成单个安全目录段。"""
        if not user_id or any(
            not (c.isalnum() or c in "-_") for c in user_id
        ):
            raise ValueError("无效用户标识")
        root = self.root.expanduser().resolve()
        d = root / user_id / sub
        d.mkdir(parents=True, exist_ok=True)
        resolved = d.resolve()
        # Owner 目录可能被宿主机软链接替换；写入前必须确认仍在配置根内。
        if not resolved.is_relative_to(root):
            raise PermissionError("上传目录越界")
        return resolved

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

    @staticmethod
    def _write_sidecar(objects_dir: Path, item: UploadItem) -> None:
        """sidecar 只记录用户目录内的受管相对路径，避免绑定宿主机。"""
        persisted = item.model_copy(
            update={"storage_path": f"objects/{item.upload_id}"}
        )
        meta_path = objects_dir / f"{item.upload_id}.meta"
        meta_path.write_text(persisted.model_dump_json(), encoding="utf-8")

    @staticmethod
    def _verify_registered_object(
        object_path: Path,
        item: UploadItem,
    ) -> None:
        """按 sidecar 登记值复核对象，防止路径迁移掩盖内容篡改。"""
        if object_path.stat().st_size != item.size_bytes:
            raise PermissionError("上传完整性校验失败")
        digest = hashlib.sha256()
        with object_path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != item.sha256:
            raise PermissionError("上传完整性校验失败")

    @staticmethod
    def _confined_path(base: Path, path: Path) -> Path:
        resolved_base = base.resolve()
        resolved = path.resolve()
        # 对象或 sidecar 软链接展开后仍须位于当前 Owner 的 objects 目录。
        if not resolved.is_relative_to(resolved_base):
            raise PermissionError("上传不存在或无权访问")
        return resolved

    @staticmethod
    def _load_sidecar(
        meta_path: Path,
        *,
        user_id: str,
        upload_id: str,
    ) -> UploadItem:
        try:
            item = UploadItem.model_validate_json(
                meta_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise PermissionError("上传元数据无效") from exc
        if item.upload_id != upload_id or item.user_id != user_id:
            raise PermissionError("上传不存在或无权访问")
        return item

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
        self._write_sidecar(objects_dir, item)
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
        self._write_sidecar(objects_dir, item)
        return item

    def resolve(self, user_id: str, upload_id: str) -> UploadItem:
        """解析上传项。跨用户访问抛 PermissionError。"""
        safe_id = "".join(c for c in upload_id if c.isalnum() or c in "-_")
        if not safe_id or safe_id != upload_id:
            raise PermissionError("无效上传标识")
        objects_dir = self._user_dir(user_id, "objects")
        meta_path = objects_dir / f"{upload_id}.meta"
        object_path = objects_dir / upload_id
        resolved_object = self._confined_path(objects_dir, object_path)
        if not resolved_object.is_file():
            raise PermissionError("上传不存在或无权访问")
        if meta_path.exists():
            resolved_meta = self._confined_path(objects_dir, meta_path)
            item = self._load_sidecar(
                resolved_meta,
                user_id=user_id,
                upload_id=upload_id,
            )
            self._verify_registered_object(resolved_object, item)
            # 历史绝对路径不可信；实际对象始终由当前 root、Owner 和 upload_id 定位。
            return item.model_copy(
                update={"storage_path": str(resolved_object)}
            )
        # 兜底：无 sidecar 时从文件重建（size/sha256 可恢复，original_name 不可恢复）
        data = resolved_object.read_bytes()
        return UploadItem(
            upload_id=upload_id,
            user_id=user_id,
            original_name="",
            storage_path=str(resolved_object),
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
        resolved_object = self._confined_path(objects_dir, object_path)
        if not resolved_object.is_file():
            raise PermissionError("上传不存在或无权访问")
        if meta_path.exists():
            resolved_meta = self._confined_path(objects_dir, meta_path)
            self._load_sidecar(
                resolved_meta,
                user_id=user_id,
                upload_id=upload_id,
            )
        object_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
