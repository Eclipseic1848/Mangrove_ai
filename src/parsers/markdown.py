# -*- coding: utf-8 -*-
"""Markdown 结构解析器；使用 markdown-it-py Token 流。"""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from typing import Any, Dict, List, Tuple

from src.data_prep.document_models import DocumentElement, ElementType
from src.data_prep.models import RawArtifact, RecordEnvelope

from .registry import Parser
from .text_html_xml import _decode


try:
    _MARKDOWN_IT_VERSION = version("markdown-it-py")
except PackageNotFoundError:  # pragma: no cover
    _MARKDOWN_IT_VERSION = "unknown"


def _record(
    artifact: RawArtifact,
    *,
    text: str,
    element_type: ElementType,
    reading_order: int,
    position: Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
    values: Dict[str, Any] | None = None,
) -> RecordEnvelope:
    seed = json.dumps(
        {
            "artifact_id": artifact.artifact_id,
            "type": element_type.value,
            "position": position,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    element = DocumentElement(
        element_id=f"el-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}",
        artifact_id=artifact.artifact_id,
        page=1,
        element_type=element_type,
        text=text,
        reading_order=reading_order,
        extractor="markdown-it-py",
        extractor_version=_MARKDOWN_IT_VERSION,
        metadata={"location": position, **(metadata or {})},
    )
    data = {
        **(values or {"text": text}),
        "elements": [element.model_dump(mode="json")],
    }
    content_hash = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return RecordEnvelope(
        record_id=hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],
        data=data,
        meta={
            "source_id": artifact.source_id,
            "artifact_id": artifact.artifact_id,
            "parser": "markdown",
            "position": position,
            "content_hash": content_hash,
        },
    )


class MarkdownParser(Parser):
    name = "markdown"
    media_types = ("text/markdown",)
    extensions = ("md", "markdown")

    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        from markdown_it import MarkdownIt

        tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(
            _decode(raw_bytes)
        )
        records: List[RecordEnvelope] = []
        reading_order = 0
        pending_type = ElementType.PARAGRAPH
        pending_meta: Dict[str, Any] = {}
        table_no = 0
        table_header: List[str] | None = None
        table_row: List[str] | None = None
        table_cell: List[str] | None = None
        table_row_no = 0
        for token in tokens:
            if token.type == "table_open":
                table_no += 1
                table_header = None
                table_row_no = 0
                continue
            if token.type == "tr_open":
                table_row = []
                table_row_no += 1
                continue
            if token.type in {"th_open", "td_open"}:
                table_cell = []
                continue
            if token.type in {"th_close", "td_close"}:
                if table_row is not None:
                    table_row.append(" ".join(table_cell or ()).strip())
                table_cell = None
                continue
            if token.type == "tr_close":
                if table_row is None:
                    continue
                if table_header is None:
                    table_header = table_row
                elif any(table_row):
                    values = {
                        (
                            table_header[index]
                            if index < len(table_header) and table_header[index]
                            else f"col_{index}"
                        ): (
                            table_row[index] if index < len(table_row) else ""
                        )
                        for index in range(max(len(table_header), len(table_row)))
                    }
                    text = "；".join(
                        f"{key}：{value}" for key, value in values.items()
                    )
                    records.append(_record(
                        artifact,
                        text=text,
                        element_type=ElementType.TABLE,
                        reading_order=reading_order,
                        position={
                            "kind": "markdown_table_row",
                            "table": table_no,
                            "row": table_row_no,
                        },
                        metadata={
                            "table_columns": list(values),
                            "table_row": dict(values),
                        },
                        values=values,
                    ))
                    reading_order += 1
                table_row = None
                continue
            if token.type == "heading_open":
                pending_type = ElementType.HEADING
                pending_meta = {"level": int(token.tag[1:])}
                continue
            if token.type == "blockquote_open":
                pending_type = ElementType.QUOTE
                pending_meta = {}
                continue
            if token.type == "list_item_open":
                pending_type = ElementType.LIST_ITEM
                pending_meta = {}
                continue
            if token.type != "inline":
                continue
            text = token.content.strip()
            if not text:
                continue
            if table_cell is not None:
                table_cell.append(text)
                continue
            start_line = (token.map or [reading_order, reading_order + 1])[0] + 1
            position = {"kind": "markdown_block", "line": start_line}
            records.append(_record(
                artifact,
                text=text,
                element_type=pending_type,
                reading_order=reading_order,
                position=position,
                metadata=pending_meta,
            ))
            reading_order += 1
            pending_type = ElementType.PARAGRAPH
            pending_meta = {}
        return records, []
