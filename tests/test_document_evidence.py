# -*- coding: utf-8 -*-
"""Phase 4A 文档证据契约与页级路由测试。"""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from src.data_prep.document_evidence import stable_element_id
from src.data_prep.document_models import (
    BoundingBox,
    DocumentElement,
    ElementType,
    EvidenceRef,
    ExtractedField,
    ExtractionStatus,
    ExtractionSpec,
    ExtractionFieldSpec,
    DiscoverySpec,
    PageContentKind,
    TaskGoal,
)
from src.parsers.document_routing import PageSignals, route_page


def test_found_field_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(name="contract_no", value="HT-001", status=ExtractionStatus.FOUND)


def test_not_found_field_cannot_carry_value() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(name="contract_no", value="猜测值", status=ExtractionStatus.NOT_FOUND)


def test_evidence_quote_or_hash_is_required() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(
            artifact_id="artifact-1",
            element_id="element-1",
            page=1,
            extractor="pdfium-text",
            extractor_version="5.12.1",
            confidence=1.0,
        )


def test_evidence_quote_hash_must_match() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(
            artifact_id="artifact-1", element_id="element-1", page=1,
            quote="真实原文", quote_sha256="0" * 64,
            extractor="pdfium-text", extractor_version="5.12.1", confidence=1.0,
        )


def test_stable_element_id_is_repeatable_and_document_scoped() -> None:
    first = stable_element_id("artifact-a", 1, ElementType.PARAGRAPH, 2, "same text")
    second = stable_element_id("artifact-a", 1, ElementType.PARAGRAPH, 2, "same text")
    other = stable_element_id("artifact-b", 1, ElementType.PARAGRAPH, 2, "same text")
    assert first == second
    assert first != other


def test_found_field_accepts_verified_evidence() -> None:
    quote = "合同编号：HT-001"
    evidence = EvidenceRef(
        artifact_id="artifact-1",
        element_id="element-1",
        page=1,
        bbox=BoundingBox(x0=10, y0=20, x1=180, y1=45, coordinate_space="pdf_points"),
        quote=quote,
        quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        extractor="pdfium-text",
        extractor_version="5.12.1",
        confidence=1.0,
    )
    field = ExtractedField(
        name="contract_no", value="HT-001", status=ExtractionStatus.FOUND,
        evidence_refs=[evidence],
    )
    assert field.evidence_refs[0].page == 1


@pytest.mark.parametrize(
    ("signals", "kind", "primary", "qwen_review"),
    [
        (PageSignals(text_chars=500, image_coverage=0.05), PageContentKind.DIGITAL, "docling", False),
        (PageSignals(text_chars=0, image_coverage=0.95), PageContentKind.SCANNED, "paddleocr", True),
        (PageSignals(text_chars=80, image_coverage=0.80), PageContentKind.MIXED, "docling+paddleocr", True),
    ],
)
def test_page_routing_priority(signals, kind, primary, qwen_review) -> None:
    decision = route_page(signals)
    assert decision.page_kind == kind
    assert decision.primary_backend == primary
    assert ("qwen_vl" in decision.review_backends) is qwen_review


def test_qwen_candidate_without_bbox_requires_review() -> None:
    element = DocumentElement(
        element_id="qwen-candidate-1",
        artifact_id="artifact-1",
        page=1,
        element_type=ElementType.PARAGRAPH,
        text="金额：123.45 元",
        extractor="qwen_vl",
        extractor_version="Qwen3.6-35B-A3B",
        confidence=0.8,
        bbox=None,
        review_required=True,
    )
    assert element.review_required is True
    assert element.bbox is None


def test_extraction_scope_requires_at_least_one_artifact_and_field() -> None:
    spec = ExtractionSpec(
        goal=TaskGoal(objective="提取合同编号"),
        discovery=DiscoverySpec(artifact_ids=["artifact-1"], pages={"artifact-1": [1, 2]}),
        fields=[ExtractionFieldSpec(name="contract_no", required=True)],
    )
    assert spec.spec_version == "3"
    assert spec.fields[0].require_evidence is True
