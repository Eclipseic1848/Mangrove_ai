# -*- coding: utf-8 -*-
"""DOCX 解析器（Phase 2 Task 6）。

使用 python-docx（成熟库）提取段落和表格：
- 非空段落 -> {"text": paragraph_text}
- 表格行 -> 行记录（首行作表头）
- 保留段落序号、表格号、行号位置
- 不执行宏、嵌入对象或外部链接（python-docx 只读文本）
"""
from __future__ import annotations

import hashlib
import io
from importlib.metadata import PackageNotFoundError, version
import json
import logging
from typing import Any, Dict, List, Tuple

from src.data_prep.document_models import DocumentElement, ElementType
from src.data_prep.models import RawArtifact, RecordEnvelope

from .registry import Parser

logger = logging.getLogger(__name__)

try:
    _PYTHON_DOCX_VERSION = version("python-docx")
except PackageNotFoundError:  # pragma: no cover - 依赖存在时不会进入
    _PYTHON_DOCX_VERSION = "unknown"


def _element_id(
    artifact_id: str,
    element_type: ElementType,
    position: Dict[str, Any],
    text: str,
) -> str:
    """以不可变制品、结构位置和文本生成稳定元素标识。"""
    seed = json.dumps(
        {
            "artifact_id": artifact_id,
            "element_type": element_type.value,
            "position": position,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"el-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _document_element(
    artifact: RawArtifact,
    *,
    element_type: ElementType,
    text: str,
    reading_order: int,
    location: Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """把 python-docx 的结构位置映射为统一 DocumentElement。"""
    element = DocumentElement(
        element_id=_element_id(
            artifact.artifact_id,
            element_type,
            location,
            text,
        ),
        artifact_id=artifact.artifact_id,
        page=1,
        element_type=element_type,
        text=text,
        reading_order=reading_order,
        extractor="python-docx",
        extractor_version=_PYTHON_DOCX_VERSION,
        metadata={"location": location, **(metadata or {})},
    )
    return element.model_dump(mode="json")


def _make_env(
    artifact: RawArtifact,
    data: Dict[str, Any],
    *,
    parser_name: str,
    position: Dict[str, Any],
) -> RecordEnvelope:
    content_hash = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    rid = hashlib.sha256(
        f"{artifact.artifact_id}:{parser_name}:{position}".encode("utf-8")
    ).hexdigest()[:16]
    return RecordEnvelope(
        record_id=rid,
        data=dict(data),
        meta={
            "source_id": artifact.source_id,
            "artifact_id": artifact.artifact_id,
            "parser": parser_name,
            "position": position,
            "content_hash": content_hash,
        },
    )


class OfficeParser(Parser):
    """DOCX 解析器：python-docx 提取段落和表格。"""

    name = "docx"
    media_types = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",)
    extensions = ("docx",)

    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        from docx import Document

        try:
            doc = Document(io.BytesIO(raw_bytes))
        except Exception as e:  # noqa: BLE001 损坏/非 DOCX
            logger.warning("DOCX 解析失败 %s: %s", artifact.artifact_id, e)
            return [], [{
                "artifact_id": artifact.artifact_id,
                "reason": f"docx_parse_failed: {e}",
                "position": {},
            }]

        records: List[RecordEnvelope] = []
        para_index = 0
        table_no = 0
        reading_order = 0
        for block in doc.iter_inner_content():
            if hasattr(block, "rows"):
                table_no += 1
                header: List[str] | None = None
                for row_no, row in enumerate(block.rows):
                    cells = [cell.text.strip() for cell in row.cells]
                    if header is None:
                        header = cells
                        continue
                    if not any(cells):
                        continue
                    data = {
                        (header[i] if i < len(header) else f"col_{i}"): (
                            cells[i] if i < len(cells) else ""
                        )
                        for i in range(max(len(header), len(cells)))
                    }
                    text = "；".join(
                        f"{key}：{value}" for key, value in data.items()
                    )
                    location = {
                        "kind": "docx_table_row",
                        "table": table_no,
                        "row": row_no,
                    }
                    data["elements"] = [_document_element(
                        artifact,
                        element_type=ElementType.TABLE,
                        text=text,
                        reading_order=reading_order,
                        location=location,
                        metadata={
                            "table_columns": list(data),
                            "table_row": dict(data),
                        },
                    )]
                    records.append(_make_env(
                        artifact,
                        data,
                        parser_name="docx_table",
                        position={"table": table_no, "row": row_no},
                    ))
                    reading_order += 1
                continue

            text = (block.text or "").strip()
            if text:
                para_index += 1
                location = {
                    "kind": "docx_paragraph",
                    "paragraph": para_index,
                }
                style_name = getattr(getattr(block, "style", None), "name", "") or ""
                element_type = (
                    ElementType.HEADING
                    if style_name.lower().startswith("heading")
                    else ElementType.PARAGRAPH
                )
                records.append(_make_env(
                    artifact, {
                        "text": text,
                        "elements": [_document_element(
                            artifact,
                            element_type=element_type,
                            text=text,
                            reading_order=reading_order,
                            location=location,
                            metadata={"style": style_name},
                        )],
                    }, parser_name="docx",
                    position={"index": para_index - 1, "type": "paragraph"},
                ))
                reading_order += 1

        return records, []
