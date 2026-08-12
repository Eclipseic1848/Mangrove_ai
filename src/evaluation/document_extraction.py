# -*- coding: utf-8 -*-
"""文档字段与证据的确定性黄金集评测。"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from jiwer import cer, wer
from rapidfuzz.fuzz import partial_ratio, ratio

from src.data_prep.document_models import DocumentElement, ExtractedField


def _normalize(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def _as_dict(field: ExtractedField | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(field, ExtractedField):
        return field.model_dump(mode="json")
    return dict(field)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _bbox_is_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        x0 = float(value["x0"])
        y0 = float(value["y0"])
        x1 = float(value["x1"])
        y1 = float(value["y1"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        x0 >= 0
        and y0 >= 0
        and x1 > x0
        and y1 > y0
        and value.get("coordinate_space")
        in {"pdf_points", "image_pixels", "normalized_1000"}
    )


def _element_dict(
    element: DocumentElement | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(element, DocumentElement):
        return element.model_dump(mode="json")
    return dict(element)


def _candidate_windows(
    elements: Sequence[DocumentElement | Mapping[str, Any]],
    page: int,
) -> list[str]:
    page_elements = sorted(
        (
            _element_dict(element)
            for element in elements
            if int(_element_dict(element).get("page") or 0) == page
        ),
        key=lambda item: (
            item.get("reading_order") is None,
            item.get("reading_order") or 0,
            str(item.get("element_id") or ""),
        ),
    )
    segments: list[str] = []
    for element in page_elements:
        segments.extend(
            line.strip()
            for line in str(element.get("text") or "").splitlines()
            if line.strip()
        )
    windows: list[str] = []
    for start in range(len(segments)):
        for size in range(1, min(8, len(segments) - start) + 1):
            windows.append(" ".join(segments[start:start + size]))
    return windows


def evaluate_evidence_ocr(
    golden_documents: Sequence[Mapping[str, Any]],
    parsed_elements: Mapping[
        str,
        Sequence[DocumentElement | Mapping[str, Any]],
    ],
) -> dict[str, Any]:
    """用黄金字段原文评估对应页 OCR；这是证据片段指标，不冒充整页 OCR 指标。"""
    details: list[dict[str, Any]] = []
    char_errors: list[float] = []
    word_errors: list[float] = []
    exact = missing = 0
    for document in golden_documents:
        document_id = str(document["id"])
        elements = parsed_elements.get(document_id, [])
        for field_name, expected in dict(document.get("expected_fields") or {}).items():
            reference = str(expected.get("quote") or "")
            candidates = _candidate_windows(elements, int(expected.get("page") or 0))
            hypothesis = max(
                candidates,
                key=lambda item: (
                    partial_ratio(_normalize(reference), _normalize(item)),
                    ratio(_normalize(reference), _normalize(item)),
                ),
                default="",
            )
            if not hypothesis:
                missing += 1
            is_exact = _normalize(reference) == _normalize(hypothesis)
            exact += int(is_exact)
            char_error = float(cer(reference, hypothesis))
            word_error = float(wer(reference, hypothesis))
            char_errors.append(char_error)
            word_errors.append(word_error)
            details.append({
                "document_id": document_id,
                "domain": document.get("domain"),
                "mode": document.get("mode"),
                "field_name": field_name,
                "page": expected.get("page"),
                "reference": reference,
                "hypothesis": hypothesis,
                "exact": is_exact,
                "cer": char_error,
                "wer": word_error,
            })
    total = len(details)
    return {
        "scope": "expected_evidence_quotes",
        "samples": total,
        "exact": exact,
        "exact_rate": _ratio(exact, total),
        "missing": missing,
        "mean_cer": sum(char_errors) / total if total else 0.0,
        "mean_wer": sum(word_errors) / total if total else 0.0,
        "details": details,
    }


def evaluate_document_predictions(
    golden_documents: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Sequence[ExtractedField | Mapping[str, Any]]],
    artifact_ids: Mapping[str, str],
    *,
    quote_threshold: float = 0.95,
) -> dict[str, Any]:
    """评估字段终态与证据，不调用模型，也不依赖具体文档领域。"""
    true_positive = false_positive = false_negative = 0
    expected_total = found_total = 0
    evidence_complete = evidence_page_correct = evidence_quote_correct = 0
    evidence_bbox_correct = evidence_binding_correct = 0
    cross_document_references = unsafe_found = 0
    unexpected_found = 0
    status_counts: Counter[str] = Counter()
    grouped: dict[str, dict[str, int]] = defaultdict(
        lambda: {"expected": 0, "correct": 0, "found": 0}
    )
    details: list[dict[str, Any]] = []

    for document in golden_documents:
        document_id = str(document["id"])
        domain = str(document.get("domain") or "unknown")
        mode = str(document.get("mode") or "unknown")
        expected_artifact = artifact_ids[document_id]
        expected_fields = dict(document.get("expected_fields") or {})
        predicted_fields = {
            str(item.get("name")): item
            for item in (_as_dict(field) for field in predictions.get(document_id, []))
        }

        for field_name, expected in expected_fields.items():
            expected_total += 1
            grouped[f"domain:{domain}"]["expected"] += 1
            grouped[f"mode:{mode}"]["expected"] += 1
            predicted = predicted_fields.get(field_name)
            status = str((predicted or {}).get("status") or "missing")
            status_counts[status] += 1
            is_found = status == "found"
            if is_found:
                found_total += 1
                grouped[f"domain:{domain}"]["found"] += 1
                grouped[f"mode:{mode}"]["found"] += 1

            value_correct = is_found and (
                _normalize(predicted.get("value")) == _normalize(expected.get("value"))
            )
            if value_correct:
                true_positive += 1
                grouped[f"domain:{domain}"]["correct"] += 1
                grouped[f"mode:{mode}"]["correct"] += 1
            else:
                false_negative += 1
                if is_found:
                    false_positive += 1

            refs = list((predicted or {}).get("evidence_refs") or [])
            artifact_ok = bool(refs) and all(
                ref.get("artifact_id") == expected_artifact for ref in refs
            )
            page_ok = bool(refs) and any(
                ref.get("page") == expected.get("page") for ref in refs
            )
            quote_scores = [
                partial_ratio(
                    _normalize(expected.get("quote")),
                    _normalize(ref.get("quote")),
                ) / 100.0
                for ref in refs
                if ref.get("quote")
            ]
            quote_ok = bool(quote_scores) and max(quote_scores) >= quote_threshold
            bbox_ok = bool(refs) and all(_bbox_is_valid(ref.get("bbox")) for ref in refs)
            binding_ok = (
                is_found
                and artifact_ok
                and page_ok
                and quote_ok
                and bbox_ok
            )
            if is_found and refs:
                evidence_complete += 1
            if is_found and page_ok:
                evidence_page_correct += 1
            if is_found and quote_ok:
                evidence_quote_correct += 1
            if is_found and bbox_ok:
                evidence_bbox_correct += 1
            if binding_ok:
                evidence_binding_correct += 1
            cross_document_references += sum(
                ref.get("artifact_id") != expected_artifact for ref in refs
            )
            if is_found and not binding_ok:
                unsafe_found += 1

            failure_reasons = []
            if not value_correct:
                failure_reasons.append("field_value")
            if is_found and not artifact_ok:
                failure_reasons.append("artifact")
            if is_found and not page_ok:
                failure_reasons.append("page")
            if is_found and not quote_ok:
                failure_reasons.append("quote")
            if is_found and not bbox_ok:
                failure_reasons.append("bbox")
            details.append({
                "document_id": document_id,
                "domain": domain,
                "mode": mode,
                "field_name": field_name,
                "expected_value": expected.get("value"),
                "predicted_value": (predicted or {}).get("value"),
                "status": status,
                "value_correct": value_correct,
                "evidence_complete": bool(refs),
                "artifact_correct": artifact_ok,
                "page_correct": page_ok,
                "quote_correct": quote_ok,
                "bbox_correct": bbox_ok,
                "binding_correct": binding_ok,
                "failure_reasons": failure_reasons,
            })

        for field_name, predicted in predicted_fields.items():
            if field_name not in expected_fields and predicted.get("status") == "found":
                false_positive += 1
                unexpected_found += 1

    grouped_metrics = {}
    for key, counts in sorted(grouped.items()):
        grouped_metrics[key] = {
            **counts,
            "exact_accuracy": _ratio(counts["correct"], counts["expected"]),
            "found_rate": _ratio(counts["found"], counts["expected"]),
        }
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    gate_passed = (
        expected_total > 0
        and true_positive == expected_total
        and found_total == expected_total
        and false_positive == 0
        and false_negative == 0
        and evidence_binding_correct == expected_total
        and cross_document_references == 0
        and unsafe_found == 0
        and unexpected_found == 0
    )
    return {
        "documents": len(golden_documents),
        "expected_fields": expected_total,
        "field_metrics": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": _ratio(2 * true_positive, 2 * true_positive + false_positive + false_negative),
            "exact_value_accuracy": _ratio(true_positive, expected_total),
        },
        "evidence_metrics": {
            "found_fields": found_total,
            "complete": evidence_complete,
            "complete_rate": _ratio(evidence_complete, found_total),
            "page_correct": evidence_page_correct,
            "page_accuracy": _ratio(evidence_page_correct, found_total),
            "quote_correct": evidence_quote_correct,
            "quote_accuracy": _ratio(evidence_quote_correct, found_total),
            "bbox_correct": evidence_bbox_correct,
            "bbox_accuracy": _ratio(evidence_bbox_correct, found_total),
            "binding_correct": evidence_binding_correct,
            "binding_accuracy": _ratio(evidence_binding_correct, expected_total),
            "cross_document_references": cross_document_references,
            "unsafe_found": unsafe_found,
            "unexpected_found": unexpected_found,
        },
        "acceptance_gate": {
            "passed": gate_passed,
            "required_exact_values": expected_total,
            "required_evidence_bindings": expected_total,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "groups": grouped_metrics,
        "details": details,
    }
