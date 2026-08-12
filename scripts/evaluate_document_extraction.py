#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在固定黄金集上运行真实解析与证据约束字段抽取。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_prep.artifact_store import ArtifactStore  # noqa: E402
from src.data_prep.document_models import (  # noqa: E402
    DiscoverySpec,
    DocumentElement,
    ExtractionFieldSpec,
    ExtractionSpec,
    TaskGoal,
)
from src.data_prep.models import RawArtifact  # noqa: E402
from src.evaluation.document_extraction import (  # noqa: E402
    evaluate_document_predictions,
    evaluate_evidence_ocr,
)
from src.parsers.pdf import PdfParser  # noqa: E402
from src.services.document_extraction import (  # noqa: E402
    EvidenceBoundExtractor,
    InstructorQwenCandidateProvider,
)

DEFAULT_GOLDEN = PROJECT_ROOT / "tests" / "fixtures" / "document_golden"
FIELD_DESCRIPTIONS = {
    "contract_no": "合同编号",
    "delivery_days": "合同约定的交付期限",
    "amount": "合同总金额",
    "project_no": "招投标项目编号",
    "deadline": "投标截止日期",
    "bond": "投标保证金",
    "invoice_no": "发票号码",
    "tax_id": "纳税人识别号",
    "total": "发票价税合计",
}
LOCAL_GATE_SUITE = "phase4a-local-gate"


def _artifact(document: dict, raw: bytes) -> RawArtifact:
    digest = hashlib.sha256(raw).hexdigest()
    return RawArtifact(
        artifact_id="golden-" + digest[:16],
        source_id="golden",
        task_id="phase4a-field-eval",
        uri=document["file"],
        media_type="application/pdf",
        size_bytes=len(raw),
        sha256=digest,
        storage_path=document["file"],
    )


def select_documents(
    manifest: dict[str, Any],
    *,
    suite: str,
    domains: set[str],
    modes: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    documents = list(manifest["documents"])
    if suite == LOCAL_GATE_SUITE:
        selected = [
            item
            for item in documents
            if item["mode"] in {"scanned", "mixed"} or item["domain"] == "invoice"
        ]
    elif suite == "all":
        selected = documents
    elif suite == "filters":
        selected = [
            item for item in documents
            if (not domains or item["domain"] in domains)
            and (not modes or item["mode"] in modes)
        ]
    else:
        raise ValueError(f"未知评测套件: {suite}")
    return selected[:limit or None]


def _parse_diagnostic(
    document: dict[str, Any],
    elements: list[DocumentElement],
    records: list[Any],
    rejects: list[dict[str, Any]],
    *,
    parse_seconds: float,
    extraction_seconds: float,
) -> dict[str, Any]:
    parsed_pages = sorted({element.page for element in elements})
    text_elements = [element for element in elements if (element.text or "").strip()]
    bbox_elements = [element for element in text_elements if element.bbox is not None]
    routes = [
        record.meta.get("route") or {}
        for record in records
        if isinstance(record.meta, dict)
    ]
    extractors = sorted({
        f"{element.extractor}@{element.extractor_version}"
        for element in elements
    })
    return {
        "expected_pages": document["pages"],
        "parsed_pages": parsed_pages,
        "page_coverage": len(parsed_pages) / document["pages"],
        "elements": len(elements),
        "text_elements": len(text_elements),
        "bbox_elements": len(bbox_elements),
        "bbox_rate": len(bbox_elements) / len(text_elements) if text_elements else 0.0,
        "extractors": extractors,
        "actual_backends": sorted({
            str(route["actual_backend"])
            for route in routes
            if route.get("actual_backend")
        }),
        "cache_hits": sum(route.get("cache_hit") is True for route in routes),
        "reject_count": len(rejects),
        "parse_seconds": round(parse_seconds, 3),
        "extraction_seconds": round(extraction_seconds, 3),
    }


def run_evaluation(
    golden_dir: Path,
    *,
    suite: str,
    domains: set[str],
    modes: set[str],
    limit: int,
    provider: str,
    model: str,
    work_dir: Path,
) -> dict:
    manifest = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    selected = select_documents(
        manifest,
        suite=suite,
        domains=domains,
        modes=modes,
        limit=limit,
    )
    parser = PdfParser(artifact_store=ArtifactStore(root=str(work_dir)))
    candidate_provider = InstructorQwenCandidateProvider(
        provider=provider,
        model=model,
    )
    extractor = EvidenceBoundExtractor(candidate_provider)
    predictions = {}
    artifact_ids = {}
    parse_rejects = {}
    parsed_elements = {}
    diagnostics = {}
    run_errors = {}
    started = time.perf_counter()

    for document in selected:
        raw = (golden_dir / document["file"]).read_bytes()
        artifact = _artifact(document, raw)
        parse_started = time.perf_counter()
        records, rejects = parser.parse(artifact, raw)
        parse_elapsed = time.perf_counter() - parse_started
        elements = [
            DocumentElement.model_validate(raw_element)
            for record in records
            for raw_element in record.data.get("elements") or []
        ]
        parsed_elements[document["id"]] = elements
        spec = ExtractionSpec(
            goal=TaskGoal(
                objective=f"提取 {document['domain']} 文档的指定字段",
                document_types=[document["domain"]],
                success_criteria=["所有非空字段必须绑定当前文档原文证据"],
            ),
            discovery=DiscoverySpec(artifact_ids=[artifact.artifact_id]),
            fields=[
                ExtractionFieldSpec(
                    name=name,
                    description=FIELD_DESCRIPTIONS.get(name, f"文档字段 {name}"),
                )
                for name in document["expected_fields"]
            ],
        )
        extraction_started = time.perf_counter()
        try:
            result = extractor.extract(spec, elements)
            predictions[document["id"]] = [
                field.model_dump(mode="json") for field in result.fields
            ]
        except Exception as exc:  # noqa: BLE001 评测必须保留单文档失败并继续
            predictions[document["id"]] = []
            run_errors[document["id"]] = {
                "stage": "extract",
                "type": type(exc).__name__,
                "message": str(exc),
            }
        extraction_elapsed = time.perf_counter() - extraction_started
        artifact_ids[document["id"]] = artifact.artifact_id
        if rejects:
            parse_rejects[document["id"]] = rejects
        diagnostics[document["id"]] = _parse_diagnostic(
            document,
            elements,
            records,
            rejects,
            parse_seconds=parse_elapsed,
            extraction_seconds=extraction_elapsed,
        )

    report = evaluate_document_predictions(selected, predictions, artifact_ids)
    report["evidence_quote_ocr_metrics"] = evaluate_evidence_ocr(
        selected,
        parsed_elements,
    )
    expected_pages = sum(item["pages"] for item in selected)
    parsed_pages = sum(len(item["parsed_pages"]) for item in diagnostics.values())
    report["parse_metrics"] = {
        "expected_pages": expected_pages,
        "parsed_pages": parsed_pages,
        "page_coverage": parsed_pages / expected_pages if expected_pages else 0.0,
        "rejects": sum(len(items) for items in parse_rejects.values()),
        "documents_with_errors": len(run_errors),
        "documents": diagnostics,
    }
    report["selection"] = {
        "suite": suite,
        "domains": sorted(domains),
        "modes": sorted(modes),
        "limit": limit,
    }
    report["execution"] = {
        "parser_primary": "mineru",
        "candidate_provider": provider,
        "candidate_model": candidate_provider.model,
        "work_dir": str(work_dir),
    }
    report["predictions"] = predictions
    report["parse_rejects"] = parse_rejects
    report["run_errors"] = run_errors
    report["acceptance_gate"]["parse_pages_complete"] = parsed_pages == expected_pages
    report["acceptance_gate"]["parse_rejects_zero"] = not parse_rejects
    report["acceptance_gate"]["run_errors_zero"] = not run_errors
    report["acceptance_gate"]["passed"] = (
        report["acceptance_gate"]["passed"]
        and parsed_pages == expected_pages
        and not parse_rejects
        and not run_errors
    )
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--suite",
        choices=(LOCAL_GATE_SUITE, "all", "filters"),
        default=LOCAL_GATE_SUITE,
    )
    parser.add_argument("--domains", default="")
    parser.add_argument("--modes", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--provider", default="local")
    parser.add_argument("--model", default="Qwen3.6-35B-A3B")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        if args.work_dir:
            work_dir = args.work_dir.resolve()
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="mangrove-phase4a-")
            work_dir = Path(temporary.name)
        report = run_evaluation(
            args.golden_dir.resolve(),
            suite=args.suite,
            domains={item.strip() for item in args.domains.split(",") if item.strip()},
            modes={item.strip() for item in args.modes.split(",") if item.strip()},
            limit=max(0, args.limit),
            provider=args.provider,
            model=args.model,
            work_dir=work_dir,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        return 0 if report["acceptance_gate"]["passed"] else 2
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
