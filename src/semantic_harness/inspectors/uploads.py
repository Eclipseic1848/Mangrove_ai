# -*- coding: utf-8 -*-
"""把用户隔离 UploadStore 来源解析为批次 2 InspectionReport。"""
from __future__ import annotations

from pathlib import Path
import uuid
from typing import Callable, Mapping

from src.data_prep.document_models import DocumentElement
from src.data_prep.models import RawArtifact
from src.parsers.registry import ParserRegistry, get_parser_registry
from src.services.upload_store import UploadStore

from ..inspection_models import (
    InspectionDiagnostic,
    InspectionStatus,
    SourceInspectionReport,
    SourceKind,
)
from ..models import SemanticTaskPlan
from .document import INSPECTOR_VERSION as DOCUMENT_INSPECTOR_VERSION
from .document import inspect_document_elements
from .tabular import inspect_tabular_path
from .tabular import INSPECTOR_VERSION as TABULAR_INSPECTOR_VERSION


_TABULAR_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".parquet", ".json", ".jsonl"}
_DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".markdown",
    ".txt", ".xml", ".png", ".jpg", ".jpeg", ".webp",
}
_DOCUMENT_PARSER_HINTS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "txt",
    ".xml": "xml",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}


class UploadSourceInspector:
    """只接受当前用户拥有的 upload ID，不接受客户端路径。"""

    def __init__(
        self,
        *,
        user_id: str,
        upload_store: UploadStore,
        registry: ParserRegistry | None = None,
        cache_lookup: Callable[
            [str, str, str], Mapping | SourceInspectionReport | None
        ]
        | None = None,
    ) -> None:
        self._user_id = user_id
        self._upload_store = upload_store
        self._registry = registry or get_parser_registry()
        self._cache_lookup = cache_lookup

    def _cached(
        self,
        artifact_id: str,
        artifact_sha256: str,
        inspector_version: str,
    ) -> SourceInspectionReport | None:
        if self._cache_lookup is None:
            return None
        value = self._cache_lookup(
            artifact_id,
            artifact_sha256,
            inspector_version,
        )
        if value is None:
            return None
        return (
            value
            if isinstance(value, SourceInspectionReport)
            else SourceInspectionReport.model_validate(value)
        )

    @staticmethod
    def _failure(
        *,
        artifact_id: str,
        sha256: str,
        size_bytes: int,
        original_name: str,
        media_type: str,
        status: InspectionStatus,
        code: str,
        message: str,
    ) -> SourceInspectionReport:
        return SourceInspectionReport(
            inspection_id=f"insp_{uuid.uuid4().hex[:16]}",
            inspector_version=DOCUMENT_INSPECTOR_VERSION,
            artifact_id=artifact_id,
            artifact_sha256=sha256,
            size_bytes=size_bytes,
            original_name=original_name,
            declared_media_type=media_type,
            detected_format=Path(original_name).suffix.lower().lstrip(".") or "unknown",
            source_kind=SourceKind.DOCUMENT,
            status=status,
            diagnostics=(
                InspectionDiagnostic(code=code, message=message[:400]),
            ),
        )

    def _inspect_document(self, upload_id: str, item) -> SourceInspectionReport:
        path = Path(item.storage_path)
        extension = Path(item.original_name).suffix.lower().lstrip(".")
        suffix = Path(item.original_name).suffix.lower()
        parser = self._registry.select(
            media_type=item.media_type,
            extension=extension,
            hint=_DOCUMENT_PARSER_HINTS.get(suffix),
        )
        if parser is None:
            return self._failure(
                artifact_id=upload_id,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                original_name=item.original_name,
                media_type=item.media_type,
                status=InspectionStatus.UNSUPPORTED,
                code="parser_unavailable",
                message="没有找到可用的只读文档解析器",
            )
        try:
            raw_bytes = path.read_bytes()
            artifact = RawArtifact(
                artifact_id=upload_id,
                source_id=f"upload:{upload_id}",
                task_id=f"semantic-inspect-{upload_id}",
                uri=item.original_name,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                storage_path=str(path),
            )
            records, rejects = parser.parse(artifact, raw_bytes)
            elements = [
                DocumentElement.model_validate(raw_element)
                for record in records
                for raw_element in (record.data.get("elements") or [])
            ]
            report = inspect_document_elements(
                artifact_id=upload_id,
                artifact_sha256=item.sha256,
                original_name=item.original_name,
                declared_media_type=item.media_type,
                size_bytes=item.size_bytes,
                elements=elements,
            )
            if rejects:
                report = report.model_copy(
                    update={
                        "diagnostics": (
                            *report.diagnostics,
                            InspectionDiagnostic(
                                code="parse_rejects",
                                message=f"文档解析产生 {len(rejects)} 个 reject",
                            ),
                        )
                    }
                )
            return report
        except Exception as exc:
            text = str(exc).lower()
            encrypted = "password" in text or "encrypt" in text
            return self._failure(
                artifact_id=upload_id,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                original_name=item.original_name,
                media_type=item.media_type,
                status=(
                    InspectionStatus.ENCRYPTED
                    if encrypted
                    else InspectionStatus.CORRUPT
                ),
                code="encrypted" if encrypted else "parse_failed",
                message=f"{type(exc).__name__}: {str(exc)}",
            )

    def inspect(self, plan: SemanticTaskPlan) -> tuple[SourceInspectionReport, ...]:
        reports = []
        for upload_id in plan.source_scope.artifact_ids:
            item = self._upload_store.resolve(self._user_id, upload_id)
            suffix = Path(item.original_name).suffix.lower()
            inspector_version = (
                TABULAR_INSPECTOR_VERSION
                if suffix in _TABULAR_EXTENSIONS
                else DOCUMENT_INSPECTOR_VERSION
            )
            cached = self._cached(
                upload_id,
                item.sha256,
                inspector_version,
            )
            if cached is not None:
                reports.append(cached)
                continue
            if suffix in _TABULAR_EXTENSIONS:
                reports.append(
                    inspect_tabular_path(
                        artifact_id=upload_id,
                        artifact_sha256=item.sha256,
                        path=Path(item.storage_path),
                        original_name=item.original_name,
                        declared_media_type=item.media_type,
                    )
                )
            elif suffix in _DOCUMENT_EXTENSIONS:
                reports.append(self._inspect_document(upload_id, item))
            else:
                reports.append(
                    self._failure(
                        artifact_id=upload_id,
                        sha256=item.sha256,
                        size_bytes=item.size_bytes,
                        original_name=item.original_name,
                        media_type=item.media_type,
                        status=InspectionStatus.UNSUPPORTED,
                        code="unsupported_format",
                        message=f"批次 2 不支持格式 {suffix or 'unknown'}",
                    )
                )
        return tuple(reports)
