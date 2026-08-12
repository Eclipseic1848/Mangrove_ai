#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用公开脱敏样例比较 Phase 4B 代表性本地/云模型。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.llm.provider import get_chat_model  # noqa: E402


DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "docs"
    / "plans"
    / "phase4b-batch0-results"
    / "model-probe.json"
)
CONTRACT = """示例商务合同
付款条款：验收通过后十五个工作日内支付合同金额的百分之六十。
交付条款：乙方应在二零二六年九月三十日前完成全部成果交付。
违约责任：逾期交付的，每日按未交付部分金额的千分之一承担违约责任。"""
EXPECTED_CLAUSES = {
    "付款条款": "验收通过后十五个工作日内支付合同金额的百分之六十。",
    "交付条款": "乙方应在二零二六年九月三十日前完成全部成果交付。",
    "违约责任": "逾期交付的，每日按未交付部分金额的千分之一承担违约责任。",
}
USER_TASK = (
    "帮我抽取示例人员甲相关的数据，并且我只需要看核销工作量天数和工作量费用两列，"
    "然后输出一个整表给我"
)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1)
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("模型根输出不是 JSON object")
    return value


def _score(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("table_plan") or {}
    plan_checks = {
        "selection": plan.get("selection") == {"姓名": "示例人员甲"},
        "projection": plan.get("projection")
        == ["核销工作量天数", "工作量费用"],
        "row_policy": plan.get("row_policy") == "filter_only",
        "table_count": plan.get("table_count") == 1,
        "no_extra_columns": plan.get("include_extra_columns") is False,
    }
    actual_clauses = {
        str(item.get("title")): item
        for item in payload.get("clauses") or []
        if isinstance(item, dict)
    }
    clause_checks: dict[str, bool] = {}
    for title, expected_text in EXPECTED_CLAUSES.items():
        item = actual_clauses.get(title, {})
        clause_checks[f"{title}_text"] = item.get("text") == expected_text
        clause_checks[f"{title}_evidence"] = (
            item.get("source_quote") == f"{title}：{expected_text}"
        )
    clause_checks["no_invented_clause"] = set(actual_clauses) == set(EXPECTED_CLAUSES)
    checks = {**plan_checks, **clause_checks}
    return {
        "passed": sum(checks.values()),
        "total": len(checks),
        "score": round(sum(checks.values()) / len(checks), 6),
        "checks": checks,
    }


def _probe(provider: str, model: str) -> dict[str, Any]:
    prompt = f"""你是受约束的语义计划器。只输出 JSON，不要解释。
必须返回：
{{
  "table_plan": {{
    "selection": {{"姓名": "字符串"}},
    "projection": ["列名"],
    "row_policy": "filter_only",
    "table_count": 1,
    "include_extra_columns": false
  }},
  "clauses": [
    {{"title": "原文标题", "text": "原文内容", "source_quote": "标题：原文内容"}}
  ]
}}
规则：不得把“输出整表”理解为取消筛选或恢复其他列；条款必须逐字摘录，不得总结、补写。

用户表格任务：
{USER_TASK}

文档原文：
{CONTRACT}
"""
    started = time.perf_counter()
    result: dict[str, Any] = {"provider": provider, "model": model}
    try:
        token_budget = 8192 if provider == "local" else 1800
        message = get_chat_model(
            provider=provider,
            model=model,
            temperature=0,
            max_tokens=token_budget,
        ).invoke(prompt)
        content = (
            message.content
            if isinstance(message.content, str)
            else json.dumps(message.content, ensure_ascii=False)
        )
        payload = _extract_json(content)
        score = _score(payload)
        result.update(
            {
                "status": "pass" if score["score"] == 1.0 else "fail",
                "score": score,
                "normalized_output": payload,
                "usage_metadata": getattr(message, "usage_metadata", None),
                "response_metadata": {
                    key: value
                    for key, value in dict(
                        getattr(message, "response_metadata", {}) or {}
                    ).items()
                    if key in {"model_name", "finish_reason", "token_usage"}
                },
            }
        )
    except Exception as exc:  # noqa: BLE001 - 赛马必须保留单项错误继续
        result.update(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--candidates",
        default=(
            "local:Qwen3.6-35B-A3B,"
            "deepseek:deepseek-v4-pro,"
            "qwen:qwen3.7-plus"
        ),
    )
    args = parser.parse_args()
    candidates = []
    for raw in args.candidates.split(","):
        provider, model = raw.strip().split(":", 1)
        candidates.append((provider, model))
    results = [_probe(provider, model) for provider, model in candidates]
    report = {
        "schema_version": "1",
        "fixture": "public_deidentified_combined_plan_and_clause",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "results": [
                    {
                        "provider": item["provider"],
                        "model": item["model"],
                        "status": item["status"],
                        "score": item.get("score", {}).get("score"),
                        "duration_ms": item["duration_ms"],
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
