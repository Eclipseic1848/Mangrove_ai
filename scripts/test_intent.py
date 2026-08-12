#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""意图节点单测：产 understanding / 追问 / 解析失败重试。运行：python scripts/test_intent.py"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.conductor.nodes.intent as intent_mod


def _patch_achat(responses):
    """用预设回复序列替换节点内的 achat（每次调用弹出一个）。"""
    seq = list(responses)

    async def fake(*args, **kwargs):
        return seq.pop(0)

    intent_mod.achat = fake


def test_understanding_ok():
    _patch_achat(['{"need_clarification": false, "understanding": {"intent": "分析SU7口碑", "what": "评论", "where": "汽车之家", "output": "报告"}}'])
    out = asyncio.run(intent_mod.intent_node({"user_input": "分析小米SU7在汽车之家口碑", "messages": []}))
    assert out["needs_clarification"] is False
    assert out["understanding"]["intent"] == "分析SU7口碑"


def test_clarification():
    _patch_achat(['{"need_clarification": true, "question": "你想采哪个平台？"}'])
    out = asyncio.run(intent_mod.intent_node({"user_input": "帮我搞点数据", "messages": []}))
    assert out["needs_clarification"] is True
    assert "平台" in out["clarification_question"]


def test_parse_retry_then_ok():
    # 第一次返回非 JSON（解析失败），重试第二次成功
    _patch_achat(["抱歉我不会输出JSON", '{"need_clarification": false, "understanding": {"intent": "x"}}'])
    out = asyncio.run(intent_mod.intent_node({"user_input": "抓取某网址正文", "messages": []}))
    assert out["needs_clarification"] is False
    assert out["understanding"]["intent"] == "x"


def test_parse_fail_twice_falls_to_clarify():
    _patch_achat(["乱码1", "乱码2"])
    out = asyncio.run(intent_mod.intent_node({"user_input": "??", "messages": []}))
    assert out["needs_clarification"] is True


def main():
    tests = [test_understanding_ok, test_clarification,
             test_parse_retry_then_ok, test_parse_fail_twice_falls_to_clarify]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
