# -*- coding: utf-8 -*-
"""冻结来源的离线导出；原件身份与派生表示分别登记。"""
from __future__ import annotations

import hashlib
import html
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin, urlsplit
import zipfile

_PARSE_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024
_XLSX_ROWS = 1_048_576
_XLSX_COLUMNS = 16_384


def _safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:160] or "source"


def _public_url(value: str) -> str | None:
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        return None
    return value


def _json(value) -> str:
    def typed(item):
        from datetime import date, datetime, time
        from decimal import Decimal
        if isinstance(item, (date, datetime, time, Decimal)):
            return {"$type": type(item).__name__, "value": str(item)}
        raise TypeError("unsupported representation value")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=typed)


def _load_json(text: str, gaps: list[str]):
    from decimal import Decimal

    def pairs(items):
        result = dict(items)
        if len(result) != len(items):
            gaps.append("duplicate_json_keys_use_original")
        return result

    def number(token):
        value = float(token)
        if Decimal(token) != Decimal(str(value)):
            gaps.append("json_number_precision_use_original")
        return value

    return json.loads(text, object_pairs_hook=pairs, parse_float=number)


def _readable_records(rows: list) -> str:
    blocks = []
    for row in rows:
        if isinstance(row, dict) and row.get("elements"):
            # 同一记录的 text 与 elements 是同一正文，只取元素一次。
            blocks.extend(str(element["text"]) for element in row["elements"] if element.get("text"))
        elif isinstance(row, dict) and isinstance(row.get("text"), str):
            blocks.append(row["text"])
        elif isinstance(row, dict):
            blocks.append("\n".join(f"{key}：{_json(value) if not isinstance(value, str) else value}" for key, value in row.items()))
        else:
            blocks.append(_json(row))
    return "\n\n".join(blocks)


def _local_records(raw: bytes, name: str, source: dict):
    from src.data_prep.models import RawArtifact
    from src.parsers.registry import ParserRegistry

    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".tsv", ".xlsx", ".parquet", ".docx", ".pptx", ".txt", ".md", ".markdown", ".xml", ".pdf"}:
        return [], ["unsupported_local_representation"], "none"
    if suffix in {".xlsx", ".docx", ".pptx"}:
        with zipfile.ZipFile(io.BytesIO(raw)) as container:
            members = container.infolist()
            if len(members) > 1000 or sum(item.file_size for item in members) > _PARSE_BYTES:
                return [], ["container_parse_limit"], "none"
    if suffix == ".pdf":
        from src.parsers.pdf import PdfParser
        parser = PdfParser(document_clients=(), use_remote_ocr=False)
    else:
        from src.parsers.tabular import TabularParser
        from src.parsers.office import OfficeParser
        from src.parsers.presentation import PresentationParser
        from src.parsers.markdown import MarkdownParser
        from src.parsers.text_html_xml import TextParser, XmlParser
        # 独立本地注册表不装载图片/远程 OCR 客户端，不触发配置回退。
        registry = ParserRegistry()
        for local_parser in (TabularParser(), OfficeParser(), PresentationParser(), MarkdownParser(), TextParser(), XmlParser()):
            registry.register(local_parser)
        parser = registry.select(extension=suffix)
    if parser is None:
        return [], ["unsupported_local_representation"], "none"
    artifact = RawArtifact(artifact_id=source["artifact_id"], source_id=source["artifact_id"],
                           task_id="source-export", uri=name, media_type=source["media_type"],
                           sha256=source["sha256"], size_bytes=len(raw), storage_path=name)
    records, rejects = parser.parse(artifact, raw)
    rows = []
    for record in records:
        data = dict(record.data)
        if "elements" in data:
            # 只输出正文和可复核结构，不导出解析器宿主路径/原始响应引用。
            data["elements"] = [{key: element[key] for key in (
                "element_id", "page", "element_type", "text", "bbox", "reading_order",
                "extractor", "extractor_version", "confidence", "review_required",
            ) if key in element} for element in data["elements"]]
        rows.append(data)
    gaps = ["local_parser_rejected_records"] if rejects else []
    if suffix in {".csv", ".tsv"}:
        import csv
        from src.parsers.text_html_xml import _decode
        matrix = csv.reader(io.StringIO(_decode(raw)), delimiter="\t" if suffix == ".tsv" else ",")
        header = next(matrix, [])
        if len(set(header)) != len(header) or any(len(row) != len(header) for row in matrix):
            gaps.append("table_column_shape_loss_use_original")
    if suffix == ".xlsx":
        # 既有解析器读取缓存值，原件仍是公式和工作簿格式的还原依据。
        gaps.append("xlsx_cached_values_only")
    return rows, gaps, parser.name


def write_source_entries(archive: zipfile.ZipFile, *, path: Path, source: dict, table_format: str = "none") -> dict:
    """调用方负责 Owner/冻结身份与失败后的临时 ZIP 清理。"""
    if table_format not in {"none", "csv", "xlsx"}:
        raise ValueError("unsupported table format")
    base = f"sources/{_safe_name(source['artifact_id'])}"
    original = f"{base}/{_safe_name(source['original_name'])}"
    if original in archive.namelist():
        raise ValueError("duplicate source archive path")
    digest = hashlib.sha256()
    size = 0
    # 原件不全量加载；写入期间校验，漂移必须使整个临时包作废。
    with path.open("rb") as handle, archive.open(original, "w", force_zip64=True) as target:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            target.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size != source["size_bytes"] or digest.hexdigest() != source["sha256"]:
        raise ValueError("source identity mismatch")
    entry = {key: source[key] for key in ("artifact_id", "original_name", "media_type", "sha256", "size_bytes")}
    entry["original_name"] = _safe_name(source["original_name"])
    entry["artifact_id"] = _safe_name(source["artifact_id"])
    entry["provenance"] = {key: value for key, value in source.get("provenance", {}).items()
                           if key in {"request_url", "source_url", "final_url", "read_at", "snapshot_id", "source_kind"}}
    entry["provenance"] = {key: value for key, value in entry["provenance"].items()
                           if isinstance(value, str) and (not key.endswith("url") or _public_url(value))}
    entry["files"] = [dict(path=original, role="original", format=Path(source["original_name"]).suffix.lstrip("."),
                           sha256=digest.hexdigest(), size_bytes=size, record_count=None)]

    def add(name, raw, fmt, count):
        target = f"{base}/representations/{name}"
        archive.writestr(target, raw)
        entry["files"].append(dict(path=target, role="representation", format=fmt,
                                   sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), record_count=count))

    rows = []
    value = None
    gaps = []
    parser_name = "none"
    if size > _PARSE_BYTES:
        gaps.append("parse_size_limit")
    else:
        # 从已封存 ZIP 读取，避免校验后原文件变化导致派生与原件不一致。
        raw = archive.read(original)
        suffix = Path(source["original_name"]).suffix.lower()
        if suffix in {".json", ".jsonl"}:
            parser_name = "stdlib-json"
            try:
                text = raw.decode("utf-8-sig")
                if suffix == ".json":
                    value = _load_json(text, gaps)
                    _json(value)
                    rows = value if isinstance(value, list) else [value]
                else:
                    for line in text.splitlines():
                        if not line.strip():
                            continue
                        try:
                            row = _load_json(line, gaps)
                            _json(row)
                            rows.append(row)
                        except ValueError:
                            gaps.append("invalid_jsonl_record")
                    value = rows
            except (ValueError, UnicodeError):
                gaps.append("invalid_json")
                value, rows = None, []
        elif suffix in {".html", ".htm"} or source["media_type"].split(";")[0] == "text/html":
            from bs4 import BeautifulSoup
            from src.parsers.text_html_xml import _decode, _package_version
            parser_name = "beautifulsoup4:" + _package_version("beautifulsoup4")
            soup = BeautifulSoup(_decode(raw), "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            links = []
            base_url = entry["provenance"].get("final_url", entry["provenance"].get("request_url", ""))
            base_tag = soup.find("base", href=True)
            if base_tag is not None:
                base_url = _public_url(urljoin(base_url, str(base_tag["href"]))) or base_url
            for tag in soup.find_all(True):
                if tag.name == "base":
                    continue
                for attribute in ("href", "src"):
                    url = _public_url(urljoin(base_url, str(tag.get(attribute) or ""))) if tag.has_attr(attribute) else None
                    if url:
                        links.append(dict(url=url, status="not_saved"))
            value = dict(text=soup.get_text("\n", strip=True), links=links, provenance=entry["provenance"])
            rows = [value]
        else:
            try:
                rows, local_gaps, parser_name = _local_records(raw, source["original_name"], source)
                gaps.extend(local_gaps)
                value = {"records": rows}
                try:
                    _json(value)
                except (TypeError, ValueError):
                    # 未支持的二进制/非有限值不能在最终写包时再次触发失败。
                    gaps.append("local_representation_not_serializable")
                    rows, value = [], {"records": []}
            except Exception:
                # 解析故障不泄漏绝对路径或服务配置，原件和已取得记录仍可导出。
                gaps.append("local_parse_failed")
                value = {"records": rows}
    table_rows = [row if isinstance(row, dict) else {"value": row} for row in rows]
    columns = tuple(dict.fromkeys(key for row in table_rows for key in row))
    if table_format == "csv":
        escaped = ["'" + key if key.lstrip().startswith(("=", "+", "-", "@")) else key for key in columns]
        # 防公式转换可能使不同原列名碰撞；拒绝投影，JSON仍保留两列。
        if len(set(escaped)) != len(escaped):
            gaps.append("csv_header_collision_use_json")
            table_format = "none"
    if table_format == "xlsx" and (len(rows) + 1 > _XLSX_ROWS or len(columns) > _XLSX_COLUMNS):
        gaps.append("xlsx_shape_limit_use_json")
        table_format = "none"
    if table_format == "xlsx" and any(len(_json(row)) > 32767 for row in rows):
        gaps.append("xlsx_cell_limit_use_json")
        table_format = "none"
    entry["parse"] = dict(status="partial" if gaps and rows else "unavailable" if gaps else "complete",
                          parser=parser_name, version="source-export-v1", record_count=len(rows), gaps=list(dict.fromkeys(gaps)))
    add("representation.json", _json(value).encode("utf-8"), "json", len(rows))
    add("records.jsonl", "".join(_json(row) + "\n" for row in rows).encode("utf-8"), "jsonl", len(rows))
    body = value["text"] if isinstance(value, dict) and "text" in value else _json(value)
    if isinstance(value, dict) and "records" in value and parser_name not in {"none", "stdlib-json"}:
        body = _readable_records(rows)
    # Markdown只含转义正文，资源地址作为普通链接列出，不使用图片嵌入语法。
    body = html.escape(body).replace("!", "\\!").replace("[", "\\[")
    if isinstance(value, dict) and "links" in value:
        body += "\n\n" + "\n".join(f"- <{html.escape(link['url'])}> — not_saved" for link in value["links"])
    add("source.md", (f"# {_safe_name(source['original_name'])}\n\n解析状态：{entry['parse']['status']}\n\n缺口：{', '.join(entry['parse']['gaps']) or '无'}\n\n" + body).encode("utf-8"), "markdown", len(rows))
    if table_format != "none" and rows:
        from src.semantic_harness.delivery.service import CanonicalContent, _render_csv, _render_xlsx
        def cell(value):
            result = value if isinstance(value, (str, int, float, bool)) or value is None else _json(value)
            if table_format == "csv" and isinstance(result, str) and result.lstrip().startswith(("=", "+", "-", "@")):
                return "'" + result
            return result
        table_rows = [{key: cell(value) for key, value in row.items()} for row in table_rows]
        safe_columns = tuple(cell(key) for key in columns)
        table_rows = [{cell(key): value for key, value in row.items()} for row in table_rows]
        with tempfile.TemporaryDirectory(prefix="source-export-") as folder:
            target = Path(folder) / f"table.{table_format}"
            content = CanonicalContent(title="来源表格投影", columns=safe_columns, rows=tuple(table_rows), sections=(), raw={})
            (_render_csv if table_format == "csv" else _render_xlsx)(target, content)
            add(target.name, target.read_bytes(), table_format, len(rows))
    add("source.json", json.dumps(entry, ensure_ascii=False, indent=2).encode("utf-8"), "manifest", len(rows))
    return entry
