# -*- coding: utf-8 -*-
"""复用 Phase 4A DocumentElement 的文档结构检查器。"""
from __future__ import annotations

from pathlib import Path
import uuid
from typing import Sequence

from src.data_prep.document_models import DocumentElement, ElementType
from src.semantic_harness.inspection_models import (
    DocumentTarget,
    InspectionDiagnostic,
    InspectionStatus,
    SourceInspectionReport,
    SourceKind,
    TargetKind,
)


INSPECTOR_VERSION = "batch2-document-v1"
_MAX_TARGETS = 1000


def _target_kind(element: DocumentElement) -> TargetKind:
    if element.element_type == ElementType.SECTION:
        return TargetKind.DOCUMENT_SECTION
    if element.element_type == ElementType.CELL:
        return TargetKind.DOCUMENT_TABLE_CELL
    return TargetKind.DOCUMENT_ELEMENT


def _evidence_ready(element: DocumentElement) -> bool:
    if element.bbox is not None:
        return True
    metadata = element.metadata or {}
    return any(
        key in metadata
        for key in (
            "paragraph_index",
            "table_index",
            "row_index",
            "cell_index",
            "location",
        )
    ) or element.extractor in {"python-docx", "pdfplumber", "pymupdf"}


def inspect_document_elements(
    *,
    artifact_id: str,
    artifact_sha256: str,
    original_name: str,
    declared_media_type: str,
    size_bytes: int,
    elements: Sequence[DocumentElement],
) -> SourceInspectionReport:
    diagnostics = []
    targets = []
    ordered = sorted(
        (element for element in elements if element.artifact_id == artifact_id),
        key=lambda item: (
            item.reading_order is None,
            item.reading_order or 0,
            item.page,
            item.element_id,
        ),
    )
    for element in ordered:
        text = (element.text or "").strip()
        if not text or element.element_type in {
            ElementType.DOCUMENT,
            ElementType.PAGE,
            ElementType.IMAGE,
        }:
            continue
        ready = _evidence_ready(element)
        if not ready:
            diagnostics.append(
                InspectionDiagnostic(
                    code="missing_source_position",
                    message="元素缺少可复核的 bbox 或结构位置",
                    path=element.element_id,
                )
            )
        targets.append(
            DocumentTarget(
                physical_ref=(
                    f"artifact://{artifact_id}/page/{element.page}"
                    f"/element/{element.element_id}"
                ),
                artifact_id=artifact_id,
                target_kind=_target_kind(element),
                label=text[:120],
                text_excerpt=text[:500],
                page=element.page,
                element_ids=(element.element_id,),
                confidence=element.confidence,
                evidence_ready=ready and not element.review_required,
            )
        )
        if len(targets) >= _MAX_TARGETS:
            diagnostics.append(
                InspectionDiagnostic(
                    code="target_limit_reached",
                    message=f"文档候选超过 {_MAX_TARGETS}，已按阅读顺序截断",
                )
            )
            break
    status = InspectionStatus.READY if targets else InspectionStatus.CORRUPT
    if not targets:
        diagnostics.append(
            InspectionDiagnostic(
                code="no_document_targets",
                message="没有发现带文本的可绑定文档元素",
            )
        )
    return SourceInspectionReport(
        inspection_id=f"insp_{uuid.uuid4().hex[:16]}",
        inspector_version=INSPECTOR_VERSION,
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        size_bytes=size_bytes,
        original_name=original_name,
        declared_media_type=declared_media_type,
        detected_format=Path(original_name).suffix.lower().lstrip(".") or "document",
        source_kind=SourceKind.DOCUMENT,
        status=status,
        document_targets=tuple(targets),
        diagnostics=tuple(diagnostics),
    )
