# -*- coding: utf-8 -*-
"""按能力暴露来源观察、候选发现和权威证据读取。"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import io
from importlib.metadata import version as package_version
import json
from pathlib import Path
from statistics import median
import threading
from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from src.agentic_runtime.models import SourceInput
from src.config.settings import settings
from src.parsers.document_routing import PageSignals, route_page
from src.services.document_parser_contracts import (
    DocumentParseResult,
    DocumentParserClient,
)
from src.services.document_parser_factory import (
    configured_document_parser_clients,
)


_CANCEL_EVENT: ContextVar[threading.Event | None] = ContextVar(
    "document_retrieval_cancel_event",
    default=None,
)
_DISCOVERY_CONFIDENCE_THRESHOLD = 0.65
_EVIDENCE_CONFIDENCE_THRESHOLD = 0.90


class DocumentRetrievalError(RuntimeError):
    """文档能力无法形成可信 Observation。"""


class DocumentRetrievalCancelled(DocumentRetrievalError):
    """任务已撤销；后台同步调用即使稍后返回也不得继续写入。"""


def bind_document_retrieval_cancel_event(
    event: threading.Event,
) -> object:
    return _CANCEL_EVENT.set(event)


def reset_document_retrieval_cancel_event(token: object) -> None:
    _CANCEL_EVENT.reset(token)  # type: ignore[arg-type]


def _raise_if_cancelled() -> None:
    event = _CANCEL_EVENT.get()
    if event is not None and event.is_set():
        raise DocumentRetrievalCancelled("文档能力调用已取消")


class PageDiscoveryClient(Protocol):
    """只产出召回索引的低成本视觉 Adapter，不得作为权威证据。"""

    provider: str
    version: str

    def extract_text(self, image_bytes: bytes) -> tuple[str, float]: ...


class RapidOcrPageDiscoveryClient:
    """复用成熟的本地 RapidOCR 小模型建立低分辨率、非权威页面索引。"""

    provider = "rapidocr_discovery"

    def __init__(self) -> None:
        self._engine: object | None = None
        self.version = f"rapidocr-{package_version('rapidocr')}:90dpi-v2"

    def extract_text(self, image_bytes: bytes) -> tuple[str, float]:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        result = self._engine(image_bytes)  # type: ignore[operator]
        texts = tuple(str(value).strip() for value in (result.txts or ()))
        scores = tuple(float(value) for value in (result.scores or ()))
        text = "\n".join(value for value in texts if value)
        # 发现层按页面判断可用性；单个印章或手写噪声块不应让整页变成盲区。
        # 权威读取层仍使用更严格的逐块最低置信度门。
        confidence = float(median(scores)) if scores else 0.0
        return text, confidence


class ContentUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    page: int = Field(ge=1)
    page_kind: str
    text_chars: int = Field(ge=0)
    image_coverage: float = Field(ge=0, le=1)


class SourceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str
    sha256: str
    unit_count: int = Field(ge=1)
    units: tuple[ContentUnit, ...]
    capabilities: tuple[str, ...] = ("discover", "read")


class DiscoveryHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    page: int
    excerpt: str
    quality_status: str


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    observed_unit_ids: tuple[str, ...]
    candidate_unit_ids: tuple[str, ...]
    low_quality_units: tuple[str, ...]
    unknown_units: tuple[str, ...]
    hits: tuple[DiscoveryHit, ...]
    cache_hits: int = Field(ge=0)
    parser_versions: tuple[str, ...]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: str
    source_id: str
    source_name: str
    unit_id: str
    page: int
    text: str
    elements: tuple[dict[str, object], ...]
    quality_status: str
    parser_version: str


class EvidenceReadSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_unit_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    quality_status: str
    authoritative_parser_versions: tuple[str, ...]
    items: tuple[EvidenceItem, ...]
    cache_hits: int = Field(ge=0)


class DocumentRetrievalModule:
    """隐藏页型判断、拆页、解析 Provider 和用户隔离缓存。"""

    def __init__(
        self,
        *,
        document_clients: Sequence[DocumentParserClient] | None = None,
        discovery_client: PageDiscoveryClient | None = None,
        execution_root: Path | None = None,
        legacy_cache_root: Path | None = None,
    ) -> None:
        self._document_clients = (
            tuple(document_clients)
            if document_clients is not None
            else None
        )
        self._execution_root = Path(
            execution_root or settings.semantic_execution_root
        )
        self._discovery_client = discovery_client
        self._legacy_cache_root = Path(
            legacy_cache_root
            or Path(settings.semantic_execution_root) / "source-cache"
        )

    async def inspect(
        self,
        source: SourceInput,
        *,
        owner_key: str = "anonymous",
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._inspect,
            source,
            owner_key,
        )

    def _inspect(
        self,
        source: SourceInput,
        owner_key: str,
    ) -> dict[str, object]:
        del owner_key
        if Path(source.original_name).suffix.lower() != ".pdf":
            raise DocumentRetrievalError("当前文档工具只支持 PDF 来源")
        source_map, _ = self._inspect_pdf(source)
        return source_map.model_dump(mode="json")

    async def discover(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        query: str,
        unit_ids: tuple[str, ...],
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._discover,
            source,
            owner_key,
            query,
            unit_ids,
        )

    def _discover(
        self,
        source: SourceInput,
        owner_key: str,
        query: str,
        unit_ids: tuple[str, ...],
    ) -> dict[str, object]:
        if not query.strip():
            raise DocumentRetrievalError("候选发现必须提供检索目标")
        source_map, native_text = self._inspect_pdf(source)
        selected = self._select_units(source_map, unit_ids)
        hits: list[DiscoveryHit] = []
        observed: list[str] = []
        low_quality: list[str] = []
        unknown: list[str] = []
        versions: set[str] = set()
        cache_hits = 0
        terms = tuple(
            item.casefold()
            for item in query.replace("，", " ").replace(",", " ").split()
            if item.strip()
        ) or (query.casefold(),)
        for unit in selected:
            _raise_if_cancelled()
            try:
                if unit.page_kind not in {"scanned", "mixed"} and native_text[
                    unit.page
                ]:
                    discovery_text = native_text[unit.page]
                    quality_status = "trusted"
                    parser_version = "pdfplumber-native-discovery-v1"
                    cached = False
                else:
                    (
                        discovery_text,
                        quality_status,
                        parser_version,
                        cached,
                    ) = self._discover_scanned_page(
                        source,
                        owner_key=owner_key,
                        page=unit.page,
                    )
            except DocumentRetrievalError:
                unknown.append(unit.unit_id)
                continue
            observed.append(unit.unit_id)
            cache_hits += int(cached)
            versions.add(parser_version)
            if quality_status != "trusted":
                low_quality.append(unit.unit_id)
            normalized = discovery_text.casefold()
            if any(term in normalized for term in terms):
                hits.append(
                    DiscoveryHit(
                        unit_id=unit.unit_id,
                        page=unit.page,
                        excerpt=discovery_text[:1000],
                        quality_status=quality_status,
                    )
                )
        result = DiscoveryResult(
            source_id=source.upload_id,
            observed_unit_ids=tuple(observed),
            candidate_unit_ids=tuple(hit.unit_id for hit in hits),
            low_quality_units=tuple(low_quality),
            unknown_units=tuple(unknown),
            hits=tuple(hits),
            cache_hits=cache_hits,
            parser_versions=tuple(sorted(versions)),
        )
        return result.model_dump(mode="json")

    async def read(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        unit_ids: tuple[str, ...],
        needs: tuple[str, ...] = ("text", "layout"),
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._read,
            source,
            owner_key,
            unit_ids,
            needs,
        )

    def _read(
        self,
        source: SourceInput,
        owner_key: str,
        unit_ids: tuple[str, ...],
        needs: tuple[str, ...],
    ) -> dict[str, object]:
        del needs
        source_map, native_text = self._inspect_pdf(source)
        selected = self._select_units(source_map, unit_ids)
        items: list[EvidenceItem] = []
        cache_hits = 0
        for unit in selected:
            _raise_if_cancelled()
            item, cached = self._read_pdf_page(
                source,
                owner_key=owner_key,
                page=unit.page,
                unit=unit,
                native_text=native_text[unit.page],
                purpose="evidence",
            )
            items.append(item)
            cache_hits += int(cached)
        quality = (
            "trusted"
            if items and all(item.quality_status == "trusted" for item in items)
            else "insufficient"
        )
        result = EvidenceReadSet(
            source_id=source.upload_id,
            source_unit_ids=tuple(item.unit_id for item in items),
            evidence_refs=tuple(item.evidence_ref for item in items),
            quality_status=quality,
            authoritative_parser_versions=tuple(
                sorted({item.parser_version for item in items})
            ),
            items=tuple(items),
            cache_hits=cache_hits,
        )
        return result.model_dump(mode="json")

    def _configured_discovery_client(self) -> PageDiscoveryClient:
        if self._discovery_client is None:
            self._discovery_client = RapidOcrPageDiscoveryClient()
        return self._discovery_client

    def _discover_scanned_page(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        page: int,
    ) -> tuple[str, str, str, bool]:
        """低分辨率发现与权威 OCR 使用不同 Adapter 和缓存命名空间。"""

        import pypdfium2 as pdfium

        _raise_if_cancelled()
        legacy = self._legacy_discovery_text(
            source,
            owner_key=owner_key,
            page=page,
        )
        if legacy is not None:
            return (*legacy, True)
        client = self._configured_discovery_client()
        owner = hashlib.sha256(owner_key.encode("utf-8")).hexdigest()[:16]
        identity = "|".join(
            (owner, source.sha256, str(page), client.provider, client.version)
        )
        cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        cache_path = (
            self._execution_root
            / "source-discovery"
            / owner
            / source.sha256[:16]
            / "lowres"
            / f"page-{page}-{cache_key[:24]}.json"
        )
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return (
                str(payload.get("text") or ""),
                str(payload.get("quality_status") or "insufficient"),
                client.version,
                True,
            )
        try:
            document = pdfium.PdfDocument(str(source.host_path))
            page_handle = document[page - 1]
            # 90 DPI 足以供小模型做关键词召回，候选仍须进入权威解析。
            bitmap = page_handle.render(scale=1.25, grayscale=False)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            text, confidence = client.extract_text(buffer.getvalue())
        except Exception as exc:
            raise DocumentRetrievalError(
                f"第 {page} 页低成本发现失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            for resource_name in ("bitmap", "page_handle", "document"):
                resource = locals().get(resource_name)
                close = getattr(resource, "close", None)
                if callable(close):
                    close()
        _raise_if_cancelled()
        quality_status = (
            "trusted"
            if text.strip() and confidence >= _DISCOVERY_CONFIDENCE_THRESHOLD
            else "insufficient"
        )
        payload = {
            "text": text.strip(),
            "confidence": confidence,
            "quality_status": quality_status,
            "version": client.version,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _raise_if_cancelled()
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return text.strip(), quality_status, client.version, False

    def _legacy_discovery_text(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        page: int,
    ) -> tuple[str, str, str] | None:
        """旧前置路径已经付过解析成本时，直接复用同 Owner 的不可变原始响应。"""

        owner = hashlib.sha256(owner_key.encode("utf-8")).hexdigest()[:16]
        cache_root = (
            self._legacy_cache_root
            / f"pi-source-{owner}-{source.sha256[:16]}"
        )
        for client in self._clients():
            provider = str(getattr(client, "provider", "document_parser"))
            candidates = sorted(
                (cache_root / provider).glob(f"page-{page}-*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for cache_path in candidates:
                try:
                    result = client.parse_response(
                        json.loads(cache_path.read_text(encoding="utf-8"))
                    )
                except Exception:
                    continue
                texts = tuple(
                    block.text.strip()
                    for block in result.blocks
                    if block.text.strip()
                )
                if not texts:
                    continue
                scores = tuple(
                    float(block.confidence)
                    for block in result.blocks
                    if block.text.strip()
                )
                quality = (
                    "trusted"
                    if scores and median(scores) >= _DISCOVERY_CONFIDENCE_THRESHOLD
                    else "insufficient"
                )
                return (
                    "\n".join(dict.fromkeys(texts)),
                    quality,
                    f"{provider}:{result.version}:legacy-cache",
                )
        return None

    def _clients(self) -> tuple[DocumentParserClient, ...]:
        return tuple(
            self._document_clients
            if self._document_clients is not None
            else configured_document_parser_clients()
        )

    @staticmethod
    def _select_units(
        source_map: SourceMap,
        unit_ids: tuple[str, ...],
    ) -> tuple[ContentUnit, ...]:
        by_id = {unit.unit_id: unit for unit in source_map.units}
        selected_ids = unit_ids or tuple(by_id)
        if not set(selected_ids) <= set(by_id):
            raise DocumentRetrievalError("请求包含不存在的内容单元")
        return tuple(by_id[unit_id] for unit_id in selected_ids)

    @staticmethod
    def _inspect_pdf(
        source: SourceInput,
    ) -> tuple[SourceMap, dict[int, str]]:
        import pdfplumber

        raw_bytes = source.host_path.read_bytes()
        units: list[ContentUnit] = []
        native_text: dict[int, str] = {}
        try:
            with pdfplumber.open(io.BytesIO(raw_bytes)) as document:
                for page_number, page in enumerate(document.pages, start=1):
                    text = (page.extract_text() or "").strip()
                    native_text[page_number] = text
                    page_area = max(
                        float(page.width) * float(page.height),
                        1.0,
                    )
                    image_area = sum(
                        max(0.0, float(image.get("x1", 0)) - float(image.get("x0", 0)))
                        * max(0.0, float(image.get("bottom", 0)) - float(image.get("top", 0)))
                        for image in page.images
                    )
                    coverage = min(1.0, image_area / page_area)
                    decision = route_page(
                        PageSignals(
                            text_chars=len(text),
                            image_coverage=coverage,
                        )
                    )
                    units.append(
                        ContentUnit(
                            unit_id=f"{source.upload_id}:page:{page_number}",
                            page=page_number,
                            page_kind=decision.page_kind.value,
                            text_chars=len(text),
                            image_coverage=coverage,
                        )
                    )
        except Exception as exc:
            raise DocumentRetrievalError(
                f"PDF 来源无法可靠检查：{type(exc).__name__}: {exc}"
            ) from exc
        if not units:
            raise DocumentRetrievalError("PDF 来源没有可读取页面")
        return (
            SourceMap(
                source_id=source.upload_id,
                name=source.original_name,
                sha256=source.sha256,
                unit_count=len(units),
                units=tuple(units),
            ),
            native_text,
        )

    def _read_pdf_page(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        page: int,
        unit: ContentUnit,
        native_text: str,
        purpose: str,
    ) -> tuple[EvidenceItem, bool]:
        _raise_if_cancelled()
        if unit.page_kind not in {"scanned", "mixed"} and native_text:
            digest = hashlib.sha256(
                f"{source.sha256}|{page}|pdfium-text".encode("utf-8")
            ).hexdigest()
            return (
                EvidenceItem(
                    evidence_ref=f"evidence:{digest}",
                    source_id=source.upload_id,
                    source_name=source.original_name,
                    unit_id=unit.unit_id,
                    page=page,
                    text=native_text,
                    elements=(),
                    quality_status="trusted",
                    parser_version="pdfplumber-native-v1",
                ),
                False,
            )
        return self._ocr_page(
            source,
            owner_key=owner_key,
            page=page,
            native_text=native_text,
            purpose=purpose,
        )

    def _ocr_page(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        page: int,
        native_text: str,
        purpose: str,
    ) -> tuple[EvidenceItem, bool]:
        from pypdf import PdfReader, PdfWriter

        clients = self._clients()
        healthy: list[tuple[DocumentParserClient, str]] = []
        preloaded: dict[str, DocumentParseResult] = {}
        errors: list[str] = []
        owner = hashlib.sha256(owner_key.encode("utf-8")).hexdigest()[:16]
        for client in clients:
            _raise_if_cancelled()
            provider = str(getattr(client, "provider", "document_parser"))
            legacy_paths = sorted(
                (
                    self._legacy_cache_root
                    / f"pi-source-{owner}-{source.sha256[:16]}"
                    / provider
                ).glob(f"page-{page}-*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if legacy_paths:
                try:
                    cached_result = client.parse_response(
                        json.loads(
                            legacy_paths[0].read_text(encoding="utf-8")
                        )
                    )
                    healthy.append((client, cached_result.version))
                    preloaded[provider] = cached_result
                    continue
                except Exception as exc:
                    errors.append(f"{provider} legacy cache: {exc}")
            try:
                health = client.health()
                if health.status.lower() == "healthy":
                    healthy.append((client, health.version))
                else:
                    errors.append(f"{provider}: {health.status}")
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
        if not healthy:
            raise DocumentRetrievalError(
                "扫描 PDF OCR 服务不可用：" + "；".join(errors)[:500]
            )
        page_bytes: bytes | None = None
        for client, version in healthy:
            _raise_if_cancelled()
            provider = str(getattr(client, "provider", "document_parser"))
            identity = "|".join(
                (
                    owner,
                    source.sha256,
                    str(page),
                    provider,
                    str(getattr(client, "base_url", "")),
                    str(getattr(client, "backend", "")),
                    version,
                    "coverage-read-v1",
                )
            )
            cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            safe_provider = "".join(
                char if char.isalnum() or char in {"_", "-"} else "_"
                for char in provider
            )
            cache_path = (
                self._execution_root
                / "source-discovery"
                / owner
                / source.sha256[:16]
                / safe_provider
                / f"page-{page}-{cache_key[:24]}.json"
            )
            cached = cache_path.is_file()
            try:
                if cached:
                    result = client.parse_response(
                        json.loads(cache_path.read_text(encoding="utf-8"))
                    )
                elif provider in preloaded:
                    result = preloaded[provider]
                    cached = True
                else:
                    if page_bytes is None:
                        raw_bytes = source.host_path.read_bytes()
                        _raise_if_cancelled()
                        reader = PdfReader(io.BytesIO(raw_bytes))
                        writer = PdfWriter()
                        writer.add_page(reader.pages[page - 1])
                        buffer = io.BytesIO()
                        writer.write(buffer)
                        page_bytes = buffer.getvalue()
                    result = client.parse_pdf(
                        page_bytes,
                        filename=f"page-{page}.pdf",
                    )
                    _raise_if_cancelled()
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    _raise_if_cancelled()
                    cache_path.write_text(
                        json.dumps(
                            result.raw_response,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                continue
            parts = [native_text] if native_text else []
            elements: list[dict[str, object]] = []
            for block in result.blocks:
                text = block.text.strip()
                if text and text not in parts:
                    parts.append(text)
                if text:
                    elements.append(
                        {
                            "text": text,
                            "bbox": list(block.bbox),
                            "coordinate_space": block.coordinate_space,
                            "element_type": block.element_type,
                            "confidence": block.confidence,
                        }
                    )
            text = "\n".join(parts).strip()
            if not text:
                errors.append(f"{provider}: 未返回文本")
                continue
            confidence_values = [
                float(block.confidence)
                for block in result.blocks
                if block.text.strip()
            ]
            # 页级证据保留全部块供逐字段复核；孤立印章噪声不应否定整页，
            # 但页面主体低于阈值时仍必须失败关闭。
            quality_status = (
                "trusted"
                if confidence_values
                and median(confidence_values) >= _EVIDENCE_CONFIDENCE_THRESHOLD
                else "insufficient"
            )
            digest = hashlib.sha256(
                f"{source.sha256}|{page}|{provider}|{version}".encode("utf-8")
            ).hexdigest()
            return (
                EvidenceItem(
                    evidence_ref=f"evidence:{digest}",
                    source_id=source.upload_id,
                    source_name=source.original_name,
                    unit_id=f"{source.upload_id}:page:{page}",
                    page=page,
                    text=text,
                    elements=tuple(elements),
                    quality_status=quality_status,
                    parser_version=f"{provider}:{version}:{purpose}",
                ),
                cached,
            )
        raise DocumentRetrievalError(
            f"第 {page} 页无法形成可信文本：" + "；".join(errors)[:500]
        )
