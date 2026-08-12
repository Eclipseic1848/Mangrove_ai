# -*- coding: utf-8 -*-
"""PDF 解析器（Phase 2 Task 6）。

使用 pdfplumber（成熟库）提取数字文本和坐标，并按页决定解析优先级：
- 数字页：Docling 优先，当前 pdfplumber 坐标文本作为确定性降级
- 扫描页：PaddleOCR 优先，Qwen VL 仅作低置信度复核/语义候选
- 混合页：结构解析和 OCR 分别处理后合并
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.document_evidence import stable_element_id
from src.data_prep.document_models import BoundingBox, DocumentElement, ElementType
from src.data_prep.models import RawArtifact, RecordEnvelope
from src.services.document_parser_contracts import (
    DocumentPageBlock,
    DocumentParseResult,
    DocumentParserClient,
)
from src.services.document_parser_factory import configured_document_parser_clients
from src.services.mineru_document import MinerUDocumentClient

from .document_routing import PageSignals, route_page
from .registry import Parser

logger = logging.getLogger(__name__)
_PDFPLUMBER_VERSION = importlib.metadata.version("pdfplumber")


def _clean_table_cell(value: Any) -> str:
    """保留单元格原文，只清理首尾空白。"""
    return "" if value is None else str(value).strip()


def _pdfplumber_tables(page: Any) -> list[dict[str, Any]]:
    """用 pdfplumber 的成熟表格识别保留原始行列和行级坐标。"""
    extracted: list[dict[str, Any]] = []
    try:
        tables = page.find_tables()
    except Exception as exc:  # noqa: BLE001 单页表格识别失败不阻断正文解析
        logger.warning("pdfplumber 表格识别失败: %s", exc)
        return extracted

    for table in tables:
        raw_rows = table.extract() or []
        width = max((len(row or []) for row in raw_rows), default=0)
        if width <= 0:
            continue
        rows: list[list[str]] = []
        row_boxes: list[tuple[float, float, float, float]] = []
        source_rows = list(getattr(table, "rows", ()) or ())
        for row_no, raw_row in enumerate(raw_rows):
            values = [
                _clean_table_cell(raw_row[index] if index < len(raw_row) else None)
                for index in range(width)
            ]
            if not any(values):
                continue
            rows.append(values)
            source_row = source_rows[row_no] if row_no < len(source_rows) else None
            row_bbox = getattr(source_row, "bbox", None) or table.bbox
            row_boxes.append(tuple(float(value) for value in row_bbox))
        if rows:
            extracted.append({
                "bbox": tuple(float(value) for value in table.bbox),
                "rows": rows,
                "row_bboxes": row_boxes,
                "columns": [f"列{index}" for index in range(1, width + 1)],
            })
    return extracted


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


class PdfParser(Parser):
    """PDF 解析器：逐页分类并保留可验证的文本坐标。"""

    name = "pdf"
    media_types = ("application/pdf",)
    extensions = ("pdf",)

    def __init__(
        self,
        *,
        mineru_client: Optional[MinerUDocumentClient] = None,
        document_clients: Optional[Sequence[DocumentParserClient]] = None,
        artifact_store: Optional[ArtifactStore] = None,
        use_remote_ocr: Optional[bool] = None,
    ) -> None:
        if document_clients is not None:
            clients = list(document_clients)
        elif mineru_client is not None:
            clients = [mineru_client]
        else:
            clients = configured_document_parser_clients()
        self.use_remote_ocr = bool(clients) if use_remote_ocr is None else use_remote_ocr
        self.document_clients = clients if self.use_remote_ocr else []
        # 保留旧属性，避免既有调用方在迁移期失效。
        self.mineru_client = mineru_client
        self.artifact_store = artifact_store or ArtifactStore()

    def _load_document_result(
        self,
        client: DocumentParserClient,
        artifact: RawArtifact,
        raw_bytes: bytes,
    ) -> tuple[DocumentParseResult, str, bool]:
        """按服务版本和输入哈希缓存第三方原始响应，避免重复 OCR。"""
        provider = getattr(client, "provider", "document_parser")
        health = client.health()
        if health.status.lower() != "healthy":
            raise RuntimeError(f"{provider} 健康状态异常: {health.status}")
        cache_identity = "|".join((
            artifact.sha256,
            provider,
            client.base_url,
            client.backend,
            health.version,
            "ocr-v1",
        ))
        cache_key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()[:24]
        safe_provider = "".join(
            char if char.isalnum() or char in {"_", "-"} else "_"
            for char in provider
        )
        rel_within_task = (
            f"third_party/{safe_provider}/{artifact.artifact_id}-{cache_key}.json"
        )
        raw_response = self.artifact_store.read_json_if_exists(
            artifact.task_id, rel_within_task
        )
        cache_hit = raw_response is not None
        if raw_response is None:
            filename = Path(artifact.uri.replace("\\", "/")).name or "document.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            result = client.parse_pdf(raw_bytes, filename=filename)
            raw_response = result.raw_response
            raw_result_ref = self.artifact_store.write_json_if_absent(
                artifact.task_id, rel_within_task, raw_response
            )
        else:
            result = client.parse_response(raw_response)
            raw_result_ref = (
                f"{artifact.task_id}/{rel_within_task}".replace("\\", "/")
            )
        return result, raw_result_ref, cache_hit

    def _load_document_page_result(
        self,
        client: DocumentParserClient,
        artifact: RawArtifact,
        raw_bytes: bytes,
        page_no: int,
    ) -> tuple[DocumentParseResult, str, bool]:
        """整份解析失败或缺页时，只对目标页做有限重试。"""
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(raw_bytes))
        writer = PdfWriter()
        writer.add_page(reader.pages[page_no - 1])
        buffer = io.BytesIO()
        writer.write(buffer)
        page_bytes = buffer.getvalue()

        provider = getattr(client, "provider", "document_parser")
        health = client.health()
        if health.status.lower() != "healthy":
            raise RuntimeError(f"{provider} 健康状态异常: {health.status}")
        cache_identity = "|".join((
            artifact.sha256,
            str(page_no),
            provider,
            client.base_url,
            client.backend,
            health.version,
            "ocr-page-v1",
        ))
        cache_key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()[:24]
        safe_provider = "".join(
            char if char.isalnum() or char in {"_", "-"} else "_"
            for char in provider
        )
        rel_within_task = (
            f"third_party/{safe_provider}/"
            f"{artifact.artifact_id}-page-{page_no}-{cache_key}.json"
        )
        raw_response = self.artifact_store.read_json_if_exists(
            artifact.task_id, rel_within_task
        )
        cache_hit = raw_response is not None
        if raw_response is None:
            result = None
            for attempt in range(settings.document_parser_page_retries + 1):
                try:
                    result = client.parse_pdf(
                        page_bytes,
                        filename=f"page-{page_no}.pdf",
                    )
                    break
                except Exception:  # noqa: BLE001 最后一次保留原异常
                    if attempt >= settings.document_parser_page_retries:
                        raise
                    time.sleep(
                        settings.document_parser_retry_backoff_seconds
                        * (2 ** attempt)
                    )
            assert result is not None
            raw_response = result.raw_response
            raw_result_ref = self.artifact_store.write_json_if_absent(
                artifact.task_id, rel_within_task, raw_response
            )
        else:
            result = client.parse_response(raw_response)
            raw_result_ref = (
                f"{artifact.task_id}/{rel_within_task}".replace("\\", "/")
            )
        remapped = DocumentParseResult(
            task_id=result.task_id,
            backend=result.backend,
            version=result.version,
            blocks=tuple(
                DocumentPageBlock(
                    page=page_no,
                    text=block.text,
                    bbox=block.bbox,
                    coordinate_space=block.coordinate_space,
                    confidence=block.confidence,
                    element_type=block.element_type,
                )
                for block in result.blocks
            ),
            raw_response=result.raw_response,
            provider=result.provider,
        )
        return remapped, raw_result_ref, cache_hit

    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        import pdfplumber

        records: List[RecordEnvelope] = []
        rejects: List[Dict] = []
        try:
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                page_results: List[Dict[str, Any]] = []
                table_no = 0
                for page_no, page in enumerate(pdf.pages, start=1):
                    text = (page.extract_text() or "").strip()
                    page_tables = _pdfplumber_tables(page)
                    for table in page_tables:
                        table_no += 1
                        table["table_no"] = table_no
                    page_area = max(float(page.width) * float(page.height), 1.0)
                    image_area = sum(
                        max(0.0, float(img.get("x1", 0)) - float(img.get("x0", 0)))
                        * max(0.0, float(img.get("bottom", 0)) - float(img.get("top", 0)))
                        for img in page.images
                    )
                    decision = route_page(PageSignals(
                        text_chars=len(text), image_coverage=min(1.0, image_area / page_area),
                    ))
                    page_results.append({
                        "page_no": page_no,
                        "page": page,
                        "text": text,
                        "decision": decision,
                        "tables": page_tables,
                    })

                kinds = {item["decision"].page_kind.value for item in page_results}
                document_kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"

                needs_ocr = any(
                    item["decision"].page_kind.value in {"scanned", "mixed"}
                    for item in page_results
                )
                ocr_pages = {
                    item["page_no"]
                    for item in page_results
                    if item["decision"].page_kind.value in {"scanned", "mixed"}
                }
                remote_by_page: Dict[
                    int,
                    List[
                        tuple[
                            DocumentPageBlock,
                            DocumentParseResult,
                            str,
                            bool,
                            str,
                        ]
                    ],
                ] = {}
                document_errors: List[str] = []
                if needs_ocr and self.use_remote_ocr:
                    for client in self.document_clients:
                        missing_pages = ocr_pages - set(remote_by_page)
                        provider = getattr(client, "provider", "document_parser")
                        table_enrichment_pages = set()
                        if provider == "paddleocr_vl":
                            table_enrichment_pages = {
                                page_no
                                for page_no, values in remote_by_page.items()
                                if any(
                                    block.element_type == "table"
                                    for block, *_ in values
                                )
                            }
                        target_pages = missing_pages | table_enrichment_pages
                        if not target_pages:
                            break
                        try:
                            result, raw_ref, cache_hit = self._load_document_result(
                                client, artifact, raw_bytes
                            )
                        except Exception as exc:  # noqa: BLE001 单服务失败后尝试备用
                            document_errors.append(f"{provider}: {exc}")
                            logger.warning(
                                "%s 解析失败 %s: %s",
                                provider,
                                artifact.artifact_id,
                                exc,
                            )
                            result = None
                            raw_ref = ""
                            cache_hit = False
                        if result is not None:
                            result_provider = result.provider or provider
                            for block in result.blocks:
                                if block.page not in target_pages:
                                    continue
                                if (
                                    block.page not in missing_pages
                                    and block.element_type != "table"
                                ):
                                    continue
                                remote_by_page.setdefault(block.page, []).append((
                                    block,
                                    result,
                                    raw_ref,
                                    cache_hit,
                                    result_provider,
                                ))
                    retry_pages = ocr_pages - set(remote_by_page)
                    for client in self.document_clients:
                        if not retry_pages:
                            break
                        provider = getattr(client, "provider", "document_parser")
                        for retry_page in sorted(tuple(retry_pages)):
                            try:
                                (
                                    page_result,
                                    page_raw_ref,
                                    page_cache_hit,
                                ) = self._load_document_page_result(
                                    client,
                                    artifact,
                                    raw_bytes,
                                    retry_page,
                                )
                            except Exception as page_exc:  # noqa: BLE001
                                document_errors.append(
                                    f"{provider} 第 {retry_page} 页: {page_exc}"
                                )
                                logger.warning(
                                    "%s 单页重试失败 %s 第 %s 页: %s",
                                    provider,
                                    artifact.artifact_id,
                                    retry_page,
                                    page_exc,
                                )
                                continue
                            result_provider = page_result.provider or provider
                            for block in page_result.blocks:
                                remote_by_page.setdefault(retry_page, []).append((
                                    block,
                                    page_result,
                                    page_raw_ref,
                                    page_cache_hit,
                                    result_provider,
                                ))
                            if page_result.blocks:
                                retry_pages.discard(retry_page)

                next_table_no = table_no
                for item in page_results:
                    page_no = item["page_no"]
                    page = item["page"]
                    text = item["text"]
                    decision = item["decision"]
                    route_meta = {
                        "primary_backend": decision.primary_backend,
                        "fallback_backends": list(decision.fallback_backends),
                        "review_backends": list(decision.review_backends),
                        "reason": decision.reason,
                    }
                    remote_blocks = remote_by_page.get(page_no, [])
                    if remote_blocks:
                        _, actual_result, _, cache_hit, provider = remote_blocks[0]
                        route_meta.update({
                            "actual_backend": f"{provider}:{actual_result.backend}",
                            "actual_version": actual_result.version,
                            "cache_hit": cache_hit,
                        })

                    if text or remote_blocks:
                        elements: List[Dict[str, Any]] = []
                        for order, word in enumerate(page.extract_words() or []):
                            word_text = str(word.get("text") or "").strip()
                            if not word_text:
                                continue
                            element = DocumentElement(
                                element_id=stable_element_id(
                                    artifact.artifact_id, page_no, ElementType.PARAGRAPH, order, word_text,
                                ),
                                artifact_id=artifact.artifact_id,
                                page=page_no,
                                element_type=ElementType.PARAGRAPH,
                                text=word_text,
                                bbox=BoundingBox(
                                    x0=float(word["x0"]), y0=float(word["top"]),
                                    x1=float(word["x1"]), y1=float(word["bottom"]),
                                    coordinate_space="pdf_points",
                                ),
                                reading_order=order,
                                extractor="pdfplumber",
                                extractor_version=_PDFPLUMBER_VERSION,
                            )
                            elements.append(element.model_dump(mode="json"))

                        source_name = (
                            Path(artifact.uri.replace("\\", "/")).stem
                            or artifact.artifact_id
                        )
                        for table in item["tables"]:
                            columns = list(table["columns"])
                            table_name = (
                                f"{source_name}-第{page_no}页-表{table['table_no']}"
                            )
                            for row_no, (row, row_bbox) in enumerate(
                                zip(table["rows"], table["row_bboxes"]),
                                start=1,
                            ):
                                row_data = dict(zip(columns, row))
                                row_text = "；".join(
                                    f"{name}：{value}"
                                    for name, value in row_data.items()
                                    if value
                                )
                                order = len(elements)
                                element = DocumentElement(
                                    element_id=stable_element_id(
                                        artifact.artifact_id,
                                        page_no,
                                        ElementType.TABLE,
                                        order,
                                        row_text,
                                    ),
                                    artifact_id=artifact.artifact_id,
                                    page=page_no,
                                    element_type=ElementType.TABLE,
                                    text=row_text,
                                    bbox=BoundingBox(
                                        x0=row_bbox[0],
                                        y0=row_bbox[1],
                                        x1=row_bbox[2],
                                        y1=row_bbox[3],
                                        coordinate_space="pdf_points",
                                    ),
                                    reading_order=order,
                                    extractor="pdfplumber",
                                    extractor_version=_PDFPLUMBER_VERSION,
                                    metadata={
                                        "location": {
                                            "kind": "pdf_table_row",
                                            "table": table["table_no"],
                                            "row": row_no,
                                        },
                                        "table_name": table_name,
                                        "table_columns": columns,
                                        "table_row": row_data,
                                    },
                                )
                                elements.append(element.model_dump(mode="json"))

                        combined_text = [text] if text else []
                        seen_remote_text = {text} if text else set()
                        for (
                            block,
                            block_result,
                            raw_result_ref,
                            _,
                            provider,
                        ) in remote_blocks:
                            if block.text in seen_remote_text:
                                continue
                            if (
                                block.element_type == "table"
                                and item["tables"]
                            ):
                                continue
                            seen_remote_text.add(block.text)
                            combined_text.append(block.text)
                            block_type = {
                                "table": ElementType.TABLE,
                                "image": ElementType.IMAGE,
                            }.get(block.element_type, ElementType.PARAGRAPH)
                            order = len(elements)
                            metadata = {
                                "document_parser_provider": provider,
                                "source_element_type": block.element_type,
                            }
                            if block_type == ElementType.TABLE:
                                next_table_no += 1
                                metadata.update({
                                    "location": {
                                        "kind": "remote_pdf_table",
                                        "table": next_table_no,
                                    },
                                    "table_name": (
                                        f"{source_name}-第{page_no}页-表{next_table_no}"
                                    ),
                                })
                            element = DocumentElement(
                                element_id=stable_element_id(
                                    artifact.artifact_id,
                                    page_no,
                                    block_type,
                                    order,
                                    block.text,
                                ),
                                artifact_id=artifact.artifact_id,
                                page=page_no,
                                element_type=block_type,
                                text=block.text,
                                bbox=BoundingBox(
                                    x0=block.bbox[0],
                                    y0=block.bbox[1],
                                    x1=block.bbox[2],
                                    y1=block.bbox[3],
                                    coordinate_space=block.coordinate_space,
                                ),
                                reading_order=order,
                                extractor=provider,
                                extractor_version=block_result.version,
                                confidence=block.confidence,
                                raw_result_ref=raw_result_ref,
                                metadata=metadata,
                            )
                            elements.append(element.model_dump(mode="json"))
                        env = _make_env(
                            artifact,
                            {
                                "text": "\n".join(part for part in combined_text if part),
                                "elements": elements,
                            },
                            parser_name="pdf", position={"page": page_no},
                        )
                        env.meta.update({
                            "page_kind": decision.page_kind.value,
                            "document_kind": document_kind,
                            "route": route_meta,
                        })
                        records.append(env)

                    ocr_incomplete = (
                        decision.page_kind.value in {"scanned", "mixed"}
                        and not remote_blocks
                    )
                    if ocr_incomplete:
                        reject = {
                            "artifact_id": artifact.artifact_id,
                            "reason": "ocr_required",
                            "position": {"page": page_no},
                            "page_kind": decision.page_kind.value,
                            "document_kind": document_kind,
                            "recommended_backend": decision.primary_backend,
                            "fallback_backends": list(decision.fallback_backends),
                            "route_reason": decision.reason,
                        }
                        if self.use_remote_ocr:
                            reject["ocr_attempted"] = True
                            reject["ocr_error"] = (
                                "; ".join(document_errors)
                                or "文档解析服务未返回该页的坐标型文本块"
                            )
                        rejects.append(reject)
                    elif not text and not remote_blocks:
                        rejects.append({
                            "artifact_id": artifact.artifact_id,
                            "reason": "empty_page",
                            "position": {"page": page_no},
                            "page_kind": decision.page_kind.value,
                            "document_kind": document_kind,
                            "recommended_backend": decision.primary_backend,
                            "fallback_backends": list(decision.fallback_backends),
                            "route_reason": decision.reason,
                        })
        except Exception as e:  # noqa: BLE001 损坏/加密 PDF
            logger.warning("PDF 解析失败 %s: %s", artifact.artifact_id, e)
            return [], [{
                "artifact_id": artifact.artifact_id,
                "reason": f"pdf_parse_failed: {e}",
                "position": {},
            }]
        return records, rejects
