# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
import threading

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from src.agentic_runtime.document_retrieval import DocumentRetrievalModule
from src.agentic_runtime.document_tools import DocumentToolBroker
from src.agentic_runtime.models import SourceInput
from src.services.document_parser_contracts import (
    DocumentPageBlock,
    DocumentParseResult,
    DocumentParserHealth,
)


def _digital_pdf(path: Path, pages: tuple[str, ...]) -> SourceInput:
    document = canvas.Canvas(str(path))
    for text in pages:
        document.drawString(72, 720, text)
        document.showPage()
    document.save()
    return SourceInput(
        upload_id="upload-digital",
        original_name=path.name,
        host_path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )


class CountingOcrClient:
    provider = "test_ocr"
    base_url = "http://ocr.test"
    backend = "pipeline"

    def __init__(self, *, confidence: float = 0.99) -> None:
        self.calls = 0
        self.confidence = confidence

    def health(self) -> DocumentParserHealth:
        return DocumentParserHealth(status="healthy", version="test-1")

    def parse_pdf(
        self,
        raw_bytes: bytes,
        *,
        filename: str,
    ) -> DocumentParseResult:
        del raw_bytes
        self.calls += 1
        return self.parse_response({"filename": filename})

    def parse_response(
        self,
        raw_response: dict,
    ) -> DocumentParseResult:
        filename = str(raw_response["filename"])
        page = int(filename.removeprefix("page-").removesuffix(".pdf"))
        return DocumentParseResult(
            task_id="ocr-test",
            backend=self.backend,
            version="test-1",
            provider=self.provider,
            blocks=(
                DocumentPageBlock(
                    page=1,
                    text=f"第{page}页 报销人：张三 结算金额：100.00",
                    bbox=(10.0, 20.0, 300.0, 80.0),
                    coordinate_space="pdf_points",
                    confidence=self.confidence,
                    element_type="table",
                ),
            ),
            raw_response={"filename": filename},
        )


class SequenceDiscoveryClient:
    provider = "test_lowres_discovery"
    version = "test-lowres-v1"

    def __init__(
        self,
        texts: tuple[str, ...],
        *,
        fail_calls: set[int] | None = None,
    ) -> None:
        self._texts = iter(texts)
        self.calls = 0
        self._fail_calls = fail_calls or set()

    def extract_text(self, image_bytes: bytes) -> tuple[str, float]:
        assert image_bytes.startswith(b"\x89PNG")
        self.calls += 1
        text = next(self._texts)
        if self.calls in self._fail_calls:
            raise TimeoutError("模拟低成本发现超时")
        return text, 0.95


@pytest.mark.asyncio
async def test_targeted_digital_page_reads_only_requested_unit(
    tmp_path: Path,
) -> None:
    source = _digital_pdf(
        tmp_path / "digital.pdf",
        ("first page", "target amount 100", "last page"),
    )
    module = DocumentRetrievalModule(execution_root=tmp_path / "cache")

    source_map = await module.inspect(source, owner_key="user-a")
    result = await module.read(
        source,
        owner_key="user-a",
        unit_ids=("upload-digital:page:2",),
        needs=("text",),
    )

    assert source_map["unit_count"] == 3
    assert result["source_unit_ids"] == ["upload-digital:page:2"]
    assert "target amount 100" in result["items"][0]["text"]
    assert result["quality_status"] == "trusted"


@pytest.mark.asyncio
async def test_scanned_page_cache_is_reused_only_within_same_owner(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (300, 200), "white")
    pdf = tmp_path / "scan.pdf"
    image.save(pdf, format="PDF")
    source = SourceInput(
        upload_id="upload-scan",
        original_name=pdf.name,
        host_path=pdf,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )
    client = CountingOcrClient()
    module = DocumentRetrievalModule(
        document_clients=(client,),
        execution_root=tmp_path / "cache",
    )

    first = await module.read(
        source,
        owner_key="user-a",
        unit_ids=("upload-scan:page:1",),
    )
    repeated = await module.read(
        source,
        owner_key="user-a",
        unit_ids=("upload-scan:page:1",),
    )
    other_owner = await module.read(
        source,
        owner_key="user-b",
        unit_ids=("upload-scan:page:1",),
    )

    assert first["cache_hits"] == 0
    assert repeated["cache_hits"] == 1
    assert other_owner["cache_hits"] == 0
    assert client.calls == 2
    assert first["items"][0]["elements"][0]["bbox"] == [
        10.0,
        20.0,
        300.0,
        80.0,
    ]


@pytest.mark.asyncio
async def test_exhaustive_109_page_discovery_finds_all_scattered_matches(
    tmp_path: Path,
) -> None:
    pages = tuple(
        (
            f"page {page} MATCHDR03 amount {page}"
            if page in {2, 57, 108}
            else f"page {page} ordinary content"
        )
        for page in range(1, 110)
    )
    source = _digital_pdf(tmp_path / "109-pages.pdf", pages)
    module = DocumentRetrievalModule(
        document_clients=(),
        execution_root=tmp_path / "cache",
    )

    discovery = await module.discover(
        source,
        owner_key="user-a",
        query="MATCHDR03",
        unit_ids=(),
    )
    evidence = await module.read(
        source,
        owner_key="user-a",
        unit_ids=tuple(discovery["candidate_unit_ids"]),
        needs=("text",),
    )

    assert len(discovery["observed_unit_ids"]) == 109
    assert discovery["unknown_units"] == []
    assert discovery["candidate_unit_ids"] == [
        "upload-digital:page:2",
        "upload-digital:page:57",
        "upload-digital:page:108",
    ]
    assert evidence["source_unit_ids"] == discovery["candidate_unit_ids"]
    assert len(evidence["evidence_refs"]) == 3


@pytest.mark.asyncio
async def test_scanned_discovery_uses_lowres_adapter_before_authoritative_ocr(
    tmp_path: Path,
) -> None:
    images = [Image.new("RGB", (300, 200), "white") for _ in range(3)]
    pdf = tmp_path / "scan-three-pages.pdf"
    images[0].save(
        pdf,
        format="PDF",
        save_all=True,
        append_images=images[1:],
    )
    source = SourceInput(
        upload_id="upload-scan",
        original_name=pdf.name,
        host_path=pdf,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )
    discovery_client = SequenceDiscoveryClient(
        ("ordinary", "MATCH-DR03", "ordinary")
    )
    authoritative_client = CountingOcrClient()
    module = DocumentRetrievalModule(
        discovery_client=discovery_client,
        document_clients=(authoritative_client,),
        execution_root=tmp_path / "cache",
    )

    discovery = await module.discover(
        source,
        owner_key="user-a",
        query="MATCH-DR03",
        unit_ids=(),
    )

    assert discovery_client.calls == 3
    assert authoritative_client.calls == 0
    assert discovery["candidate_unit_ids"] == ["upload-scan:page:2"]

    evidence = await module.read(
        source,
        owner_key="user-a",
        unit_ids=tuple(discovery["candidate_unit_ids"]),
    )
    assert authoritative_client.calls == 1
    assert evidence["quality_status"] == "trusted"


@pytest.mark.asyncio
async def test_scanned_109_page_discovery_only_ocr_candidates_and_keeps_timeout_unknown(
    tmp_path: Path,
) -> None:
    images = [Image.new("L", (80, 80), "white") for _ in range(109)]
    pdf = tmp_path / "scan-109-pages.pdf"
    images[0].save(
        pdf,
        format="PDF",
        save_all=True,
        append_images=images[1:],
    )
    source = SourceInput(
        upload_id="upload-scan-109",
        original_name=pdf.name,
        host_path=pdf,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )
    texts = tuple(
        "MATCH-109" if page in {2, 57, 108} else "ordinary"
        for page in range(1, 110)
    )
    discovery_client = SequenceDiscoveryClient(texts, fail_calls={40})
    authoritative_client = CountingOcrClient()
    module = DocumentRetrievalModule(
        discovery_client=discovery_client,
        document_clients=(authoritative_client,),
        execution_root=tmp_path / "cache",
    )

    discovery = await module.discover(
        source,
        owner_key="user-a",
        query="MATCH-109",
        unit_ids=(),
    )

    assert len(discovery["observed_unit_ids"]) == 108
    assert discovery["unknown_units"] == ["upload-scan-109:page:40"]
    assert discovery["candidate_unit_ids"] == [
        "upload-scan-109:page:2",
        "upload-scan-109:page:57",
        "upload-scan-109:page:108",
    ]
    assert authoritative_client.calls == 0

    evidence = await module.read(
        source,
        owner_key="user-a",
        unit_ids=tuple(discovery["candidate_unit_ids"]),
    )
    assert authoritative_client.calls == 3
    assert len(evidence["evidence_refs"]) == 3


@pytest.mark.asyncio
async def test_low_confidence_ocr_is_not_marked_as_trusted(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (300, 200), "white")
    pdf = tmp_path / "low-confidence.pdf"
    image.save(pdf, format="PDF")
    source = SourceInput(
        upload_id="upload-low",
        original_name=pdf.name,
        host_path=pdf,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )
    module = DocumentRetrievalModule(
        document_clients=(CountingOcrClient(confidence=0.42),),
        execution_root=tmp_path / "cache",
    )

    evidence = await module.read(
        source,
        owner_key="user-a",
        unit_ids=("upload-low:page:1",),
    )

    assert evidence["quality_status"] == "insufficient"
    assert evidence["items"][0]["quality_status"] == "insufficient"


@pytest.mark.asyncio
async def test_cancelled_threaded_ocr_cannot_write_cache_or_ledger(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingOcrClient(CountingOcrClient):
        def parse_pdf(
            self,
            raw_bytes: bytes,
            *,
            filename: str,
        ) -> DocumentParseResult:
            del raw_bytes
            self.calls += 1
            started.set()
            release.wait(timeout=5)
            try:
                return self.parse_response({"filename": filename})
            finally:
                finished.set()

    image = Image.new("RGB", (300, 200), "white")
    pdf = tmp_path / "cancelled.pdf"
    image.save(pdf, format="PDF")
    source = SourceInput(
        upload_id="upload-cancelled",
        original_name=pdf.name,
        host_path=pdf,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )
    cache_root = tmp_path / "cache"
    broker = DocumentToolBroker(
        retriever=DocumentRetrievalModule(
            document_clients=(BlockingOcrClient(),),
            execution_root=cache_root,
        )
    )
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-cancelled",
        revision=1,
        run_id="run-cancelled",
        sources=(source,),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": source.upload_id},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": [source.upload_id]},
            "result_cardinality": "first",
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": [],
            "object_boundary": "单页记录",
            "stop_semantics": "首个记录已读",
            "interpretation": "读取首个记录",
            "confidence": "high",
        },
    )
    pending = asyncio.create_task(
        broker.call(
            grant_token=grant.token,
            operation="read_evidence",
            payload={
                "source_id": source.upload_id,
                "unit_ids": ["upload-cancelled:page:1"],
            },
        )
    )
    assert await asyncio.to_thread(started.wait, 2)

    broker.revoke_grant(grant.grant_id, "用户取消")
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert await asyncio.to_thread(finished.wait, 2)

    assert list(cache_root.rglob("*.json")) == []
    state = broker.completion_state(grant.grant_id)
    assert state is not None
    assert state[1].authoritatively_read_unit_ids == ()
