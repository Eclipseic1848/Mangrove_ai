# -*- coding: utf-8 -*-
"""运行 Phase 4B 批次 1 本地/外部模型语义计划 Golden。

默认只允许 local。外部模型必须显式传 --allow-external，且夹具必须保持公开脱敏。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.semantic_harness.compiler import InstructorPlanDraftGenerator  # noqa: E402
from src.semantic_harness.compiler_graph import compile_semantic_plan  # noqa: E402
from src.semantic_harness.compiler_models import CompileRequest  # noqa: E402


FIXTURE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "semantic_harness"
    / "public"
    / "batch1"
    / "intents.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "docs"
    / "plans"
    / "phase4b-batch1-results"
    / "model-eval.json"
)


def _contains_all(texts: Iterable[str], expected: Iterable[str]) -> bool:
    merged = "\n".join(texts)
    return all(item in merged for item in expected)


def _score(actual: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, bool]:
    checks: Dict[str, bool] = {
        "status": actual["status"] == expected["status"],
    }
    plan = actual.get("plan")
    if expected["status"] != "ready":
        return checks
    if not isinstance(plan, dict):
        checks["plan_present"] = False
        return checks
    checks["task_family"] = plan["task_family"] == expected["task_family"]
    checks["content_policy"] = (
        plan["content_policy"] == expected["content_policy"]
    )
    checks["delivery_formats"] = set(expected["delivery_formats"]).issubset(
        set(plan["delivery"]["formats"])
    )
    if "selection" in expected:
        actual_selection = [
            {
                "field": item["field"],
                "operator": item["operator"],
                "value": item.get("value"),
            }
            for item in plan["selection"]
        ]
        checks["selection"] = all(
            item in actual_selection for item in expected["selection"]
        )
    if "projection" in expected:
        checks["projection"] = [
            item["alias"] or item["name"] for item in plan["projection"]
        ] == expected["projection"]
    if expected.get("record_grain_required"):
        checks["record_grain"] = bool(plan["record_grain"])
    if "combine_mode" in expected:
        checks["combine_mode"] = (
            plan["combine"]["mode"] == expected["combine_mode"]
        )
    if "required_operations" in expected:
        actual_operations = [
            item["operation"] for item in plan["operations"]
        ]
        checks["required_operations"] = set(
            expected["required_operations"]
        ).issubset(set(actual_operations))
    if "required_concepts" in expected:
        semantic_texts = [
            plan["objective"]["normalized_text"],
            *plan["source_scope"]["section_patterns"],
            *[item["field"] for item in plan["selection"]],
            *[item["name"] for item in plan["projection"]],
        ]
        checks["required_concepts"] = _contains_all(
            semantic_texts,
            expected["required_concepts"],
        )
    return checks


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if args.provider != "local" and not args.allow_external:
        raise SystemExit("外部模型评测必须显式传 --allow-external")
    generator = InstructorPlanDraftGenerator(
        provider=args.provider,
        model=args.model,
    )
    case_results: List[Dict[str, Any]] = []
    for case in fixture["cases"]:
        request = CompileRequest(
            task_id=f"batch1_eval_{case['case_id']}",
            objective_text=case["objective"],
            artifact_ids=tuple(case.get("artifact_ids", ())),
            accepted_formats=tuple(case.get("accepted_formats", ())),
            provider=args.provider,
            model=args.model,
            external_api_confirmed=args.allow_external,
        )
        result = await compile_semantic_plan(
            request,
            generator=generator,
            plan_id=f"batch1_eval_{case['case_id']}",
        )
        actual = result.model_dump(mode="json")
        checks = _score(actual, case["expected"])
        case_results.append(
            {
                "case_id": case["case_id"],
                "passed": bool(checks) and all(checks.values()),
                "checks": checks,
                "actual": actual,
            }
        )
    return {
        "schema_version": "1",
        "provider": generator.provider,
        "model": generator.model,
        "prompt_version": generator.prompt_version,
        "prompt_sha256": generator.prompt_sha256,
        "passed": all(item["passed"] for item in case_results),
        "passed_cases": sum(item["passed"] for item in case_results),
        "total_cases": len(case_results),
        "cases": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="local")
    parser.add_argument("--model")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"{report['provider']}/{report['model']}: "
        f"{report['passed_cases']}/{report['total_cases']} passed"
    )
    print(args.output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
