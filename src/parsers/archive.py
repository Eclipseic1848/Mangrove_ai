# -*- coding: utf-8 -*-
"""安全 ZIP 展开解析器（Phase 2 Task 7）。

使用标准库 zipfile（成熟库），叠加多层安全校验：
- 成员数上限（防海量小文件）
- 路径逃逸拒绝（ZIP Slip：../、绝对路径、盘符）
- 压缩比上限（防 ZIP 炸弹）
- 总展开字节上限
- 子成员写为 RawArtifact，parent_artifact_id 指向 ZIP 制品

安全校验是确定性逻辑，需自行实现，不外包给第三方。
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.data_prep.models import RawArtifact

from .registry import Parser

logger = logging.getLogger(__name__)

# 扩展名 -> media_type（子成员路由用）
_EXT_MEDIA: Dict[str, str] = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
    "txt": "text/plain",
    "html": "text/html",
    "xml": "application/xml",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "zip": "application/zip",
}


@dataclass
class ArchiveResult:
    """ZIP 展开结果：子制品 + 被拒绝成员。"""

    children: List[RawArtifact] = field(default_factory=list)
    rejects: List[Dict[str, Any]] = field(default_factory=list)


class ArchiveParser(Parser):
    """安全 ZIP 解析器。只读展开，多层安全校验。"""

    name = "zip"
    media_types = ("application/zip",)
    extensions = ("zip",)

    def __init__(
        self,
        *,
        max_files: int = 1000,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_ratio: int = 100,
    ) -> None:
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.max_ratio = max_ratio

    # ------------------------------------------------------------------
    # 安全校验
    # ------------------------------------------------------------------
    @staticmethod
    def _is_path_escape(name: str) -> bool:
        """检测 ZIP Slip：绝对路径、..、Windows 盘符。"""
        if name.startswith(("/", "\\")):
            return True
        if len(name) >= 2 and name[1] == ":":  # Windows 盘符如 C:
            return True
        parts = Path(name).parts
        return ".." in parts

    # ------------------------------------------------------------------
    # 展开接口
    # ------------------------------------------------------------------
    def extract(
        self,
        artifact: RawArtifact,
        raw_bytes: bytes,
        *,
        task_id: str,
        store: Any,
    ) -> ArchiveResult:
        """安全展开 ZIP，子成员写为 RawArtifact。"""
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        except zipfile.BadZipFile as e:
            return ArchiveResult([], [{
                "artifact_id": artifact.artifact_id,
                "reason": f"zip_corrupt: {e}",
                "position": {},
            }])

        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > self.max_files:
            return ArchiveResult([], [{
                "artifact_id": artifact.artifact_id,
                "reason": "zip_too_many_files",
                "position": {},
                "count": len(infos),
            }])

        children: List[RawArtifact] = []
        rejects: List[Dict[str, Any]] = []
        total_uncompressed = 0

        for info in infos:
            member_name = info.filename
            if self._is_path_escape(member_name):
                rejects.append({
                    "artifact_id": artifact.artifact_id,
                    "reason": "zip_path_escape",
                    "member": member_name,
                    "position": {},
                })
                continue

            if info.compress_size > 0 and info.file_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > self.max_ratio:
                    rejects.append({
                        "artifact_id": artifact.artifact_id,
                        "reason": "zip_ratio_exceeded",
                        "member": member_name,
                        "ratio": round(ratio, 1),
                    })
                    continue

            try:
                content = zf.read(info)
            except Exception as e:  # noqa: BLE001
                rejects.append({
                    "artifact_id": artifact.artifact_id,
                    "reason": f"zip_read_failed: {e}",
                    "member": member_name,
                })
                continue

            total_uncompressed += len(content)
            if total_uncompressed > self.max_total_bytes:
                rejects.append({
                    "artifact_id": artifact.artifact_id,
                    "reason": "zip_total_exceeded",
                    "member": member_name,
                })
                break

            ext = Path(member_name).suffix.lstrip(".").lower() or "bin"
            child = store.write_raw(
                task_id=task_id,
                source_id=artifact.source_id,
                data=content,
                uri=f"zip://{artifact.artifact_id}/{member_name}",
                media_type=_EXT_MEDIA.get(ext, "application/octet-stream"),
                parent_artifact_id=artifact.artifact_id,
                ext=ext,
            )
            children.append(child)

        return ArchiveResult(children, rejects)

    # ------------------------------------------------------------------
    # Parser 接口（ZIP 需先 extract 再由 ParserRegistry 递归路由子制品）
    # ------------------------------------------------------------------
    def parse(self, artifact: RawArtifact, raw_bytes: bytes):
        """ZIP 本身不直接产记录，需通过 extract 展开后递归解析子制品。"""
        return [], [{
            "artifact_id": artifact.artifact_id,
            "reason": "zip_requires_extract",
            "position": {},
        }]
