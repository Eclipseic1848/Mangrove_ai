# -*- coding: utf-8 -*-
"""文档字段与证据评测器测试。"""
from src.evaluation.document_extraction import (
    evaluate_document_predictions,
    evaluate_evidence_ocr,
)


def _field(name, value, *, artifact_id, page=2, quote=None, status="found"):
    return {
        "name": name,
        "value": value,
        "status": status,
        "evidence_refs": [{
            "artifact_id": artifact_id,
            "element_id": f"el-{name}",
            "page": page,
            "quote": quote or f"{name}: {value}",
            "bbox": {
                "x0": 10,
                "y0": 20,
                "x1": 110,
                "y1": 40,
                "coordinate_space": "pdf_points",
            },
        }],
    }


def test_evaluator_reports_field_evidence_and_cross_document_metrics():
    golden = [
        {
            "id": "contract-1",
            "domain": "contract",
            "mode": "digital",
            "expected_fields": {
                "amount": {
                    "value": "CNY 100.00",
                    "page": 2,
                    "quote": "amount: CNY 100.00",
                },
                "delivery_days": {
                    "value": "30 days",
                    "page": 2,
                    "quote": "delivery_days: 30 days",
                },
            },
        },
        {
            "id": "bid-1",
            "domain": "bid",
            "mode": "digital",
            "expected_fields": {
                "bond": {
                    "value": "CNY 5,000.00",
                    "page": 2,
                    "quote": "bond: CNY 5,000.00",
                },
            },
        },
    ]
    predictions = {
        "contract-1": [
            _field("amount", "CNY 100.00", artifact_id="artifact-contract"),
            _field("delivery_days", "60 days", artifact_id="artifact-contract"),
        ],
        "bid-1": [
            _field("bond", "CNY 5,000.00", artifact_id="artifact-contract"),
        ],
    }

    report = evaluate_document_predictions(
        golden,
        predictions,
        {
            "contract-1": "artifact-contract",
            "bid-1": "artifact-bid",
        },
    )

    assert report["field_metrics"]["true_positive"] == 2
    assert report["field_metrics"]["false_positive"] == 1
    assert report["field_metrics"]["false_negative"] == 1
    assert report["field_metrics"]["f1"] == 2 / 3
    assert report["evidence_metrics"]["cross_document_references"] == 1
    assert report["evidence_metrics"]["unsafe_found"] == 1
    assert report["evidence_metrics"]["bbox_accuracy"] == 1.0
    assert report["evidence_metrics"]["binding_correct"] == 2
    assert report["acceptance_gate"]["passed"] is False
    assert report["groups"]["domain:contract"]["exact_accuracy"] == 0.5
    assert report["groups"]["domain:bid"]["exact_accuracy"] == 1.0


def test_evidence_ocr_uses_expected_page_and_reports_jiwer_metrics():
    golden = [{
        "id": "invoice-1",
        "domain": "invoice",
        "mode": "scanned",
        "expected_fields": {
            "invoice_no": {
                "quote": "invoice_no: INV-2026-001",
                "page": 2,
            },
        },
    }]
    parsed = {
        "invoice-1": [
            {
                "element_id": "page-1",
                "page": 1,
                "reading_order": 0,
                "text": "invoice_no: WRONG",
            },
            {
                "element_id": "page-2-a",
                "page": 2,
                "reading_order": 0,
                "text": "invoice_no:",
            },
            {
                "element_id": "page-2-b",
                "page": 2,
                "reading_order": 1,
                "text": "INV-2026-001",
            },
        ],
    }

    report = evaluate_evidence_ocr(golden, parsed)

    assert report["scope"] == "expected_evidence_quotes"
    assert report["samples"] == 1
    assert report["exact"] == 1
    assert report["mean_cer"] == 0.0
    assert report["mean_wer"] == 0.0
