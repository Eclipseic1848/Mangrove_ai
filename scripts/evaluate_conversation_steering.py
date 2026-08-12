# -*- coding: utf-8 -*-
"""用真实当前模型重复评测冻结的对话语义语料。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from src.conversation_steering import (
    RawUserTurn,
    SemanticDiffGate,
    SteeringRequest,
    build_context_rewriter,
)


async def evaluate_case(case: dict, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        request = SteeringRequest(
            owner_id="evaluation-owner",
            task_id=f"evaluation-{case['id']}",
            revision=2,
            run_id="evaluation-run",
            text=case["text"],
            current_status="running",
            status_summary="正在检查来源，已检查 2 个文件",
            current_goal=case.get(
                "current_goal",
                "处理附件一，提取王总的全部报销记录并输出 JSON",
            ),
            selection_reason="扫描页需要 OCR；数字页直接读取文本层",
            event_summaries=("已理解任务", "正在检查来源"),
            provider="local",
        )
        turn = RawUserTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            owner_id=request.owner_id,
            task_id=request.task_id,
            revision=request.revision,
            text=request.text,
        )
        delta = await build_context_rewriter(request).rewrite(turn, request)
        actual_action = SemanticDiffGate.classify(delta).value
        actual_material = list(SemanticDiffGate.material_changes(delta))
        checks = {
            "intent": delta.intent.value == case["intent"],
            "action": actual_action == case["action"],
        }
        if case["intent"] in {"normalization", "task_refinement", "permission_request"}:
            checks["material_changes"] = (
                actual_material == case["material_changes"]
            )
        if case["action"] == "answer_only":
            checks["direct_answer"] = bool(delta.direct_answer)
        if case["intent"] == "status_question":
            checks["grounded_answer"] = bool(
                delta.direct_answer
                and (
                    "检查来源" in delta.direct_answer
                    or "2 个文件" in delta.direct_answer
                    or "2个文件" in delta.direct_answer
                )
            )
        if case.get("grounding_markers"):
            checks["grounded_answer"] = bool(
                delta.direct_answer
                and any(
                    marker in delta.direct_answer
                    for marker in case["grounding_markers"]
                )
            )
        return {
            "id": case["id"],
            "passed": all(checks.values()),
            "checks": checks,
            "expected": case,
            "actual": {
                "intent": delta.intent.value,
                "action": actual_action,
                "material_changes": actual_material,
                "normalized_text": delta.normalized_text,
                "direct_answer": delta.direct_answer,
                "open_questions": list(delta.open_questions),
            },
        }


async def run(args: argparse.Namespace) -> int:
    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    rounds: list[dict] = []
    for round_number in range(1, args.rounds + 1):
        semaphore = asyncio.Semaphore(args.concurrency)
        results = await asyncio.gather(
            *(evaluate_case(case, semaphore) for case in cases)
        )
        passed = sum(item["passed"] for item in results)
        rounds.append(
            {
                "round": round_number,
                "passed": passed,
                "total": len(results),
                "results": results,
            }
        )
        print(f"round {round_number}: {passed}/{len(results)}", flush=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": str(args.fixture),
        "rounds": rounds,
        "all_passed": all(item["passed"] == item["total"] for item in rounds),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if report["all_passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/conversation_steering/rewrite_cases.json"),
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".artifacts/conversation-steering-evaluation.json"),
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
