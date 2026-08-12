#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规划节点单测：LLM 产合法 TaskSpec / 平台归一 / LLM 失败降级。运行：python scripts/test_planner.py"""
import asyncio
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.conductor.nodes.planner as planner_mod
from src.conductor.task_spec import DataType


def _patch_achat_return(text):
    async def fake(*args, **kwargs):
        return text
    planner_mod.achat = fake


def _patch_achat_raise():
    async def fake(*args, **kwargs):
        raise RuntimeError("LLM down")
    planner_mod.achat = fake


def test_plan_ok_and_normalize_platform():
    _patch_achat_return(
        '{"intent": "分析SU7口碑", "platforms": ["douyin"], "keywords": ["小米SU7"], '
        '"data_type": "comment", "analysis_type": "voc", "outputs": ["report_md"], '
        '"reasoning": "口碑→VOC；抖音社媒"}'
    )
    state = {"understanding": {"intent": "分析SU7口碑", "where": "抖音"}, "user_input": "分析小米SU7口碑"}
    out = asyncio.run(planner_mod.planner_node(state))
    spec = out["task_spec"]
    assert spec.data_type == DataType.COMMENT
    assert "抖音" in spec.platforms  # douyin → 归一为规范名
    assert out["plan_reasoning"]


def test_plan_llm_fail_falls_back():
    _patch_achat_raise()
    state = {"understanding": {"intent": "x"}, "user_input": "分析小米SU7口碑"}
    out = asyncio.run(planner_mod.planner_node(state))
    spec = out["task_spec"]
    # 降级仍产出合法 TaskSpec（关键词回落到用户输入）
    assert spec is not None and spec.keywords


def test_plan_defaults_quantity_when_unspecified():
    # 用户没提数量、也没给URL → 不再追问，静默使用默认 20 并透明告知
    _patch_achat_return('{"intent": "x", "keywords": ["小米SU7"], "outputs": ["report_md"]}')
    state = {"understanding": {"intent": "x"}, "user_input": "在汽车之家收集小米SU7的评价"}
    out = asyncio.run(planner_mod.planner_node(state))
    assert not out.get("needs_clarification")
    assert out["task_spec"].max_items == 20
    assert out.get("inferred_quantity") == 20


def test_plan_asks_quantity_when_massive():
    # 用户表达了"全部""所有"等大规模语义但没给具体数量 → 仍追问上限
    _patch_achat_return('{"intent": "x", "keywords": ["小米SU7"], "outputs": ["report_md"]}')
    state = {"understanding": {"intent": "x"}, "user_input": "把所有小米SU7的差评全部找出来"}
    out = asyncio.run(planner_mod.planner_node(state))
    assert out.get("needs_clarification") is True
    assert "多少条" in (out.get("clarification_question") or "")


def test_plan_explicit_quantity_in_draft():
    # LLM 解析出明确数量 → 不追问，按上限封顶
    cap = planner_mod.settings.collector_max_items
    _patch_achat_return('{"intent": "x", "keywords": ["k"], "max_items": 50, "outputs": ["report_md"]}')
    state = {"understanding": {"intent": "x"}, "user_input": "收集k的50条数据"}
    out = asyncio.run(planner_mod.planner_node(state))
    assert not out.get("needs_clarification")
    assert out["task_spec"].max_items == min(50, cap)


def test_plan_bare_number_reply():
    # 澄清后用户直接回数字 → 确定性识别为数量，不再追问
    _patch_achat_return('{"intent": "x", "keywords": ["k"], "outputs": ["report_md"]}')
    state = {"understanding": {"intent": "x"}, "user_input": "20"}
    out = asyncio.run(planner_mod.planner_node(state))
    assert not out.get("needs_clarification")
    assert out["task_spec"].max_items == 20


def test_plan_default_hint_uses_cap():
    # 用户回"默认" → 按上限默认数量
    cap = planner_mod.settings.collector_max_items
    _patch_achat_return('{"intent": "x", "keywords": ["k"], "outputs": ["report_md"]}')
    state = {"understanding": {"intent": "x"}, "user_input": "默认就行"}
    out = asyncio.run(planner_mod.planner_node(state))
    assert not out.get("needs_clarification")
    assert out["task_spec"].max_items == cap


def test_plan_skips_quantity_when_urls_given():
    # 给了具体URL → 数量≈URL数，不追问
    _patch_achat_return('{"intent": "x", "urls": ["http://a.com/1"], "outputs": ["report_md"]}')
    state = {"understanding": {"intent": "x"}, "user_input": "抓取 http://a.com/1"}
    out = asyncio.run(planner_mod.planner_node(state))
    assert not out.get("needs_clarification")


def test_plan_includes_planner_skills():
    # 用临时 SKILLS_DIR 隔离，不依赖真实 skills/ 目录已有哪些文件
    import src.memory.loader as loader_mod
    d = Path(tempfile.mkdtemp(prefix="mg_skills_planner_"))
    old_dir = loader_mod.SKILLS_DIR
    loader_mod.SKILLS_DIR = d
    (d / "demo-platform.md").write_text(
        "---\ntitle: 演示平台技能\ninject: planner\ntrigger:\n  always: true\n---\n演示平台正文标记XYZ",
        encoding="utf-8",
    )
    try:
        captured = {}

        async def fake(messages, *args, **kwargs):
            captured["system"] = messages[0]["content"]
            return '{"intent": "x", "keywords": ["k"], "outputs": ["report_md"]}'

        planner_mod.achat = fake
        state = {"understanding": {"intent": "x"}, "user_input": "x"}
        asyncio.run(planner_mod.planner_node(state))
        assert "演示平台正文标记XYZ" in captured["system"]
    finally:
        loader_mod.SKILLS_DIR = old_dir


def main():
    tests = [test_plan_ok_and_normalize_platform, test_plan_llm_fail_falls_back,
             test_plan_defaults_quantity_when_unspecified, test_plan_asks_quantity_when_massive,
             test_plan_explicit_quantity_in_draft,
             test_plan_bare_number_reply, test_plan_default_hint_uses_cap,
             test_plan_skips_quantity_when_urls_given, test_plan_includes_planner_skills]
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
