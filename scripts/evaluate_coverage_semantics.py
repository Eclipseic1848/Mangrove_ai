# -*- coding: utf-8 -*-
"""用当前本地模型评测覆盖语义，不参与生产路由。"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.settings import settings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    cases = json.loads(
        (
            root
            / "tests"
            / "fixtures"
            / "agentic_runtime"
            / "coverage_semantics.json"
        ).read_text(encoding="utf-8")
    )
    prompts = [
        {
            "id": item["id"],
            "context": item.get("context", ""),
            "query": item["query"],
        }
        for item in cases
    ]
    system = """你是文档任务的覆盖语义编译器。不要执行任务，也不要解释推理。
对每条输入只根据完整语境给出：cardinality(first/count/all/ambiguous)、result_count、
completeness(strict/best_effort/ambiguous)、scope(all/page:数字/page:起-止/
attachment:数字)、clarify。明确说一条示例属于 first；审计、不要漏项、每一条属于 all；
明确指定数量 N 属于 count 且 result_count=N；真正会改变结果且没有足够语境时，
    cardinality 和 completeness 都标为 ambiguous，并令 clarify=true。多轮输入应结合
context 继承已经确认的范围与数量，只改变用户本轮明确要求改变的部分。返回与输入同序的 JSON 数组。"""
    payload = {
        "model": settings.llm_model_name,
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(prompts, ensure_ascii=False),
            },
        ],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with httpx.Client(timeout=180, trust_env=False) as client:
        response = client.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer local-runtime"},
        )
        response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    start = content.find("[")
    end = content.rfind("]")
    predictions = json.loads(content[start:end + 1])
    for index, item in enumerate(predictions):
        if "id" not in item and index < len(prompts):
            item["id"] = prompts[index]["id"]
    by_id = {item["id"]: item for item in predictions if item.get("id")}
    failures: list[str] = []
    for expected in cases:
        actual = by_id.get(expected["id"])
        if actual is None:
            failures.append(f"{expected['id']}: 缺少结果")
            continue
        for key in ("cardinality", "completeness", "scope", "clarify"):
            if actual.get(key) != expected.get(key):
                failures.append(
                    f"{expected['id']}.{key}: "
                    f"expected={expected.get(key)!r}, actual={actual.get(key)!r}"
                )
        if expected.get("result_count") != actual.get("result_count"):
            if expected.get("result_count") is not None:
                failures.append(
                    f"{expected['id']}.result_count: "
                    f"expected={expected.get('result_count')!r}, "
                    f"actual={actual.get('result_count')!r}"
                )
    print(
        json.dumps(
            {
                "model": settings.llm_model_name,
                "evaluated": len(prompts),
                "passed": len(prompts) - len({item.split(":", 1)[0].split(".", 1)[0] for item in failures}),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
