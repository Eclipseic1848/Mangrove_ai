# -*- coding: utf-8 -*-
"""把单文档或安全 ZIP 中的文档统一解析为证据元素。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.document_models import DocumentElement
from src.data_prep.models import RawArtifact
from src.parsers.archive import ArchiveParser
from src.parsers.registry import ParserRegistry


_DOCUMENT_EXTENSIONS = {"pdf", "docx", "png", "jpg", "jpeg", "webp"}
_MAX_ARCHIVE_DEPTH = 3
_MAX_ARCHIVE_MEMBERS = 1000


@dataclass
class DocumentIngestResult:
    raw_artifacts: list[RawArtifact] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    elements: list[DocumentElement] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)


def ingest_document_artifact(
    artifact: RawArtifact,
    raw_bytes: bytes,
    *,
    registry: ParserRegistry,
    store: ArtifactStore,
    depth: int = 0,
    result: DocumentIngestResult | None = None,
) -> DocumentIngestResult:
    """递归展开 ZIP；只把受支持文档成员送入文档解析链。"""
    output = result or DocumentIngestResult()
    output.raw_artifacts.append(artifact)
    ext = Path(artifact.uri.replace("\\", "/")).suffix.lstrip(".").lower()
    parser = registry.select(media_type=artifact.media_type, extension=ext)
    if isinstance(parser, ArchiveParser):
        if depth >= _MAX_ARCHIVE_DEPTH:
            output.rejects.append({
                "artifact_id": artifact.artifact_id,
                "reason": "zip_max_depth_exceeded",
                "member": artifact.uri,
            })
            return output
        archive = ArchiveParser(
            max_files=_MAX_ARCHIVE_MEMBERS,
            max_total_bytes=settings.data_prep_max_task_bytes,
        )
        expanded = archive.extract(
            artifact,
            raw_bytes,
            task_id=artifact.task_id,
            store=store,
        )
        output.rejects.extend(expanded.rejects)
        for child in expanded.children:
            child_ext = Path(child.uri.replace("\\", "/")).suffix.lstrip(".").lower()
            if child_ext not in _DOCUMENT_EXTENSIONS | {"zip"}:
                output.raw_artifacts.append(child)
                output.rejects.append({
                    "artifact_id": child.artifact_id,
                    "reason": "zip_member_unsupported",
                    "member": child.uri,
                })
                continue
            ingest_document_artifact(
                child,
                store.read_raw_bytes(child.task_id, child.storage_path),
                registry=registry,
                store=store,
                depth=depth + 1,
                result=output,
            )
        return output

    if parser is None or ext not in _DOCUMENT_EXTENSIONS:
        output.rejects.append({
            "artifact_id": artifact.artifact_id,
            "reason": f"无匹配文档解析器: {artifact.media_type}/{ext}",
        })
        return output

    records, parse_rejects = parser.parse(artifact, raw_bytes)
    output.rejects.extend(parse_rejects)
    output.artifact_ids.append(artifact.artifact_id)
    for record in records:
        for raw_element in record.data.get("elements") or []:
            output.elements.append(DocumentElement.model_validate(raw_element))
    return output
