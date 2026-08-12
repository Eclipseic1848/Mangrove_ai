#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行 Phase 4B 批次 2 公开 Golden，并输出机器可读结果。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_prep.document_models import DocumentElement, ElementType  # noqa: E402
from src.semantic_harness.binder import bind_semantic_plan  # noqa: E402
from src.semantic_harness.inspectors.document import (  # noqa: E402
    inspect_document_elements,
)
from src.semantic_harness.inspectors.tabular import inspect_tabular_path  # noqa: E402
from src.semantic_harness.models import (  # noqa: E402
    CombineMode,
    CombineSpec,
    DeliveryFormat,
    DeliverySpec,
    InputContract,
    ObjectiveSpec,
    PostconditionSpec,
    PredicateOperator,
    PredicatePostcondition,
    ProjectionField,
    SemanticTaskPlan,
    SourceScope,
    TaskFamily,
)


FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "semantic_harness"
    / "public"
    / "batch2"
    / "cases.json"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _table_plan(artifact_ids: tuple[str, ...], case_id: str) -> SemanticTaskPlan:
    return SemanticTaskPlan(
        plan_id=f"plan_{case_id}",
        task_id=f"task_{case_id}",
        revision=1,
        task_family=TaskFamily.TABULAR_TRANSFORM,
        objective=ObjectiveSpec(
            original_text="只保留谢超群并输出核销工作量天数和工作量费用",
            normalized_text="筛选姓名后投影两列",
        ),
        source_scope=SourceScope(
            artifact_ids=artifact_ids,
            table_scope="all_detected_tables",
        ),
        input_contract=InputContract(accepted_formats=("csv",)),
        selection=(
            {
                "field": "姓名",
                "operator": PredicateOperator.EQ,
                "value": "谢超群",
            },
        ),
        projection=(
            ProjectionField(name="核销工作量天数"),
            ProjectionField(name="工作量费用"),
        ),
        record_grain="source_detail_row",
        combine=CombineSpec(mode=CombineMode.ONE_TABLE),
        delivery=DeliverySpec(formats=(DeliveryFormat.XLSX,)),
        postconditions=PostconditionSpec(
            table_count=1,
            exact_visible_columns=("核销工作量天数", "工作量费用"),
            predicates=(
                PredicatePostcondition(
                    field="姓名",
                    operator=PredicateOperator.EQ,
                    value="谢超群",
                ),
            ),
        ),
    )


def _document_plan(case_id: str) -> SemanticTaskPlan:
    return SemanticTaskPlan(
        plan_id=f"plan_{case_id}",
        task_id=f"task_{case_id}",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="提取商务条款",
            normalized_text="提取商务条款并保留原文证据",
        ),
        source_scope=SourceScope(
            artifact_ids=("document_1",),
            section_patterns=("商务条款",),
        ),
        input_contract=InputContract(accepted_formats=("docx",)),
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
        postconditions=PostconditionSpec(minimum_evidence_coverage=1.0),
    )


def _run_table(case: dict, root: Path):
    reports = []
    artifact_ids = []
    for index, headers in enumerate(case["artifacts"], start=1):
        artifact_id = f"{case['case_id']}_{index}"
        artifact_ids.append(artifact_id)
        path = root / f"{artifact_id}.csv"
        values = ["谢超群", *(["0.5"] * max(len(headers) - 2, 0)), "1200"]
        path.write_text(
            f"{','.join(headers)}\n{','.join(values)}\n",
            encoding="utf-8",
        )
        data = path.read_bytes()
        reports.append(
            inspect_tabular_path(
                artifact_id=artifact_id,
                artifact_sha256=_sha(data),
                path=path,
                original_name=path.name,
                declared_media_type="text/csv",
            )
        )
    return bind_semantic_plan(
        _table_plan(tuple(artifact_ids), case["case_id"]),
        tuple(reports),
    )


def _run_document(case: dict):
    weak = bool(case.get("weak_evidence"))
    elements = tuple(
        DocumentElement(
            element_id=f"section_{index}",
            artifact_id="document_1",
            page=index,
            element_type=ElementType.SECTION,
            text=text,
            reading_order=index,
            extractor="mineru" if weak else "python-docx",
            extractor_version="1",
            review_required=weak,
            metadata={} if weak else {"paragraph_index": index},
        )
        for index, text in enumerate(case["sections"], start=1)
    )
    report = inspect_document_elements(
        artifact_id="document_1",
        artifact_sha256="d" * 64,
        original_name="contract.docx",
        declared_media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=100,
        elements=elements,
    )
    return bind_semantic_plan(_document_plan(case["case_id"]), (report,))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "docs"
        / "plans"
        / "phase4b-batch2-results"
        / "golden-results.json",
    )
    args = parser.parse_args()
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    results = []
    wrong_auto_bindings = 0
    started = perf_counter()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for case in manifest["cases"]:
            before = perf_counter()
            result = (
                _run_table(case, root)
                if case["kind"] == "table"
                else _run_document(case)
            )
            actual = result.status.value
            target_count = sum(
                len(binding.targets)
                for binding in (
                    result.bound_plan.bindings
                    if result.bound_plan is not None
                    else ()
                )
            )
            expected_target_count = case.get("expected_bound_target_count")
            passed = actual == case["expected_status"] and (
                expected_target_count is None
                or target_count == expected_target_count
            )
            if (
                actual == "ready"
                and (
                    case["expected_status"] != "ready"
                    or (
                        expected_target_count is not None
                        and target_count != expected_target_count
                    )
                )
            ):
                wrong_auto_bindings += 1
            results.append(
                {
                    "case_id": case["case_id"],
                    "expected_status": case["expected_status"],
                    "actual_status": actual,
                    "passed": passed,
                    "duration_ms": round((perf_counter() - before) * 1000, 3),
                    "bound_target_count": target_count,
                }
            )
    summary = {
        "fixture_version": manifest["version"],
        "case_count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "wrong_auto_bindings": wrong_auto_bindings,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 and wrong_auto_bindings == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
