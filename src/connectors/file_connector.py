# -*- coding: utf-8 -*-
"""FileConnector：把已验证上传文件转为不可变 RawArtifact（Phase 2 Task 3）。

职责：
- probe：验证上传存在、属主匹配、返回大小/原始名样本
- read：读取上传字节，通过 ArtifactStore 写为 RawArtifact，产出单批

不直接解析业务内容（CSV/JSON 等），那是 ParserRegistry 的职责。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.models import ConnectorCapability

from .base import ProbeResult, RecordBatch, SourceConnector
from src.data_prep.models import SourceSpec
from src.services.upload_store import UploadStore

logger = logging.getLogger(__name__)

# 后缀 -> media_type 简单映射（ParserRegistry 按后缀路由）
_SUFFIX_MEDIA = {
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


class FileConnector(SourceConnector):
    """已验证上传文件连接器。只读，不写源系统。"""

    name = "upload_file"
    source_type = "upload_file"

    def __init__(self, upload_store: UploadStore, artifact_store: Optional[ArtifactStore] = None) -> None:
        self._upload_store = upload_store
        self._artifact_store = artifact_store

    def capabilities(self):
        return {
            ConnectorCapability.READ_ONLY,
            ConnectorCapability.SUPPORTS_CHECKPOINT,
        }

    def _resolve(self, spec: SourceSpec):
        opts = spec.options or {}
        upload_id = opts.get("upload_id")
        user_id = opts.get("user_id")
        if not upload_id or not user_id:
            raise ValueError("上传文件源缺少 upload_id 或 user_id")
        return self._upload_store.resolve(user_id, upload_id)

    async def probe(self, spec: SourceSpec) -> ProbeResult:
        try:
            item = self._resolve(spec)
        except (PermissionError, ValueError) as e:
            return ProbeResult(reachable=False, message=str(e))
        return ProbeResult(
            reachable=True,
            message="上传文件可访问",
            sample={
                "size_bytes": item.size_bytes,
                "original_name": item.original_name,
                "sha256": item.sha256,
            },
        )

    async def read(
        self, spec: SourceSpec, checkpoint: Optional[Checkpoint] = None
    ) -> AsyncIterator[RecordBatch]:
        opts = spec.options or {}
        task_id = opts.get("task_id")
        if not task_id:
            raise ValueError("上传文件源缺少 task_id")
        item = self._resolve(spec)
        data = Path(item.storage_path).read_bytes()

        store = self._artifact_store or ArtifactStore()
        suffix = Path(item.original_name).suffix.lower()
        media_type = _SUFFIX_MEDIA.get(suffix, "application/octet-stream")
        art = store.write_raw(
            task_id=task_id,
            source_id=spec.source_id,
            data=data,
            uri=f"upload://{item.original_name}",
            media_type=media_type,
            ext=suffix.lstrip(".") or "bin",
        )
        yield RecordBatch(
            artifacts=[art],
            checkpoint=Checkpoint(is_final=True),
            byte_count=len(data),
        )
