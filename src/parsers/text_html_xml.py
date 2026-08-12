# -*- coding: utf-8 -*-
"""TXT/HTML/XML 只读解析器，统一输出带结构位置的 DocumentElement。"""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from typing import Any, Dict, List, Tuple

from src.data_prep.document_models import DocumentElement, ElementType
from src.data_prep.models import RawArtifact, RecordEnvelope

from .registry import Parser


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"


def _decode(raw_bytes: bytes) -> str:
    """编码探测：utf-8-sig -> gbk -> utf-8 replace。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _element(
    artifact: RawArtifact,
    *,
    element_type: ElementType,
    text: str,
    reading_order: int,
    extractor: str,
    extractor_version: str,
    location: Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    seed = json.dumps(
        {
            "artifact_id": artifact.artifact_id,
            "element_type": element_type.value,
            "location": location,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return DocumentElement(
        element_id=f"el-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}",
        artifact_id=artifact.artifact_id,
        page=1,
        element_type=element_type,
        text=text,
        reading_order=reading_order,
        extractor=extractor,
        extractor_version=extractor_version,
        metadata={"location": location, **(metadata or {})},
    ).model_dump(mode="json")


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


class TextParser(Parser):
    """TXT 解析器：非空行按原始行号输出。"""

    name = "txt"
    media_types = ("text/plain",)
    extensions = ("txt",)

    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        records: List[RecordEnvelope] = []
        reading_order = 0
        for line_no, line in enumerate(_decode(raw_bytes).splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            location = {"kind": "text_line", "line": line_no}
            records.append(_make_env(
                artifact,
                {
                    "text": text,
                    "elements": [_element(
                        artifact,
                        element_type=ElementType.PARAGRAPH,
                        text=text,
                        reading_order=reading_order,
                        extractor="stdlib-text",
                        extractor_version="1",
                        location=location,
                    )],
                },
                parser_name="txt",
                position={"line": line_no},
            ))
            reading_order += 1
        return records, []


class HtmlParser(Parser):
    """HTML 解析器：正文与表格按 DOM 顺序共同输出，不执行脚本。"""

    name = "html"
    media_types = ("text/html",)
    extensions = ("html", "htm")

    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(_decode(raw_bytes), "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        records: List[RecordEnvelope] = []
        reading_order = 0
        text_index = 0
        tables = list(soup.find_all("table"))
        table_numbers = {id(table): index for index, table in enumerate(tables, start=1)}
        table_headers: Dict[int, List[str]] = {}
        for node in soup.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]
        ):
            if node.name == "tr":
                table = node.find_parent("table")
                if table is None:
                    continue
                table_no = table_numbers[id(table)]
                rows = table.find_all("tr")
                row_no = rows.index(node) + 1
                cells = [
                    cell.get_text(" ", strip=True)
                    for cell in node.find_all(["td", "th"], recursive=False)
                ]
                if not cells:
                    continue
                header = table_headers.get(table_no)
                if header is None:
                    table_headers[table_no] = cells
                    continue
                if not any(cells):
                    continue
                data = {
                    (header[i] if i < len(header) and header[i] else f"col_{i}"): (
                        cells[i] if i < len(cells) else ""
                    )
                    for i in range(max(len(header), len(cells)))
                }
                row_text = "；".join(f"{key}：{value}" for key, value in data.items())
                location = {
                    "kind": "html_table_row",
                    "table": table_no,
                    "row": row_no,
                }
                data["elements"] = [_element(
                    artifact,
                    element_type=ElementType.TABLE,
                    text=row_text,
                    reading_order=reading_order,
                    extractor="beautifulsoup4",
                    extractor_version=_package_version("beautifulsoup4"),
                    location=location,
                    metadata={
                        "table_columns": list(data),
                        "table_row": dict(data),
                    },
                )]
                records.append(_make_env(
                    artifact,
                    data,
                    parser_name="html_table",
                    position={"table": table_no, "row": row_no},
                ))
                reading_order += 1
                continue

            if node.find_parent("table") is not None:
                continue
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            text_index += 1
            element_type = (
                ElementType.HEADING
                if node.name.startswith("h")
                else ElementType.LIST_ITEM
                if node.name == "li"
                else ElementType.PARAGRAPH
            )
            location = {
                "kind": "html_element",
                "tag": node.name,
                "index": text_index,
            }
            records.append(_make_env(
                artifact,
                {
                    "text": text,
                    "elements": [_element(
                        artifact,
                        element_type=element_type,
                        text=text,
                        reading_order=reading_order,
                        extractor="beautifulsoup4",
                        extractor_version=_package_version("beautifulsoup4"),
                        location=location,
                    )],
                },
                parser_name="html_text",
                position=location,
            ))
            reading_order += 1
        return records, []


class XmlParser(Parser):
    """XML 解析器：禁用 DTD、外部实体和网络访问。"""

    name = "xml"
    media_types = ("application/xml", "text/xml")
    extensions = ("xml",)

    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        from lxml import etree

        try:
            root = etree.fromstring(
                raw_bytes,
                parser=etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    recover=False,
                    load_dtd=False,
                ),
            )
        except etree.XMLSyntaxError as exc:
            return [], [{
                "artifact_id": artifact.artifact_id,
                "reason": f"XML 解析失败: {exc}",
                "position": {},
            }]

        records: List[RecordEnvelope] = []
        reading_order = 0
        for index, child in enumerate(root, start=1):
            if not isinstance(child.tag, str):
                continue
            data = {
                sub.tag.split("}")[-1]: (sub.text or "").strip()
                for sub in child
                if isinstance(sub.tag, str)
            }
            if child.text and child.text.strip() and not data:
                data = {
                    **{
                        key.split("}")[-1]: value
                        for key, value in child.attrib.items()
                    },
                    "text": child.text.strip(),
                }
            if not any(str(value).strip() for value in data.values()):
                continue
            tag = child.tag.split("}")[-1]
            text = "；".join(f"{key}：{value}" for key, value in data.items())
            location = {"kind": "xml_child", "index": index, "tag": tag}
            data["elements"] = [_element(
                artifact,
                element_type=ElementType.PARAGRAPH,
                text=text,
                reading_order=reading_order,
                extractor="lxml",
                extractor_version=_package_version("lxml"),
                location=location,
            )]
            records.append(_make_env(
                artifact,
                data,
                parser_name="xml",
                position={"index": index, "tag": tag},
            ))
            reading_order += 1
        return records, []
