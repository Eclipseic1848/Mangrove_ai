#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清洗节点单测。运行：python scripts/test_clean.py

UTF-8 基线（plan 4.3 / Phase 0）：Windows 控制台默认 cp1252/cp936，
打印中文汇总行会抛 UnicodeEncodeError，导致功能通过却被判失败。
此处显式重配标准流为 UTF-8，使脚本无需 -X utf8 / PYTHONUTF8 即可独立运行。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# UTF-8 基线：reconfigure 标准流（Python 3.7+）。已在 utf-8 模式时跳过。
for _s in (sys.stdout, sys.stderr):
    _enc = (getattr(_s, "encoding", "") or "").lower().replace("-", "")
    if _enc != "utf8":
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

from src.conductor.nodes.clean import clean_node, _denoise
from src.conductor.task_spec import TaskSpec


def test_denoise_strips_html_and_whitespace():
    raw = "<p>你好<br/>  世界</p>\n\n\n正文"
    out = _denoise(raw)
    assert "<" not in out and ">" not in out
    assert "你好" in out and "世界" in out and "正文" in out
    assert "\n\n\n" not in out  # 连续空白被折叠


def test_clean_drops_short_boilerplate():
    spec = TaskSpec(intent="x", max_items=10)
    raw = [
        {"url": "u1", "content": "首页 登录 注册"},          # 超短导航样板 → 丢弃
        {"url": "u2", "content": "这是一段足够长的真实正文内容，应当保留下来。"},
    ]
    out = asyncio.run(clean_node({"raw_dataset": raw, "task_spec": spec}))
    contents = [i["content"] for i in out["cleaned_dataset"]]
    assert any("真实正文" in c for c in contents)
    assert not any("首页 登录 注册" in c for c in contents)


def test_clean_dedup_and_cap():
    spec = TaskSpec(intent="x", max_items=1)
    raw = [
        {"url": "u", "content": "重复内容重复内容重复内容重复内容"},
        {"url": "u", "content": "重复内容重复内容重复内容重复内容"},  # 同 url+前缀 → 去重
        {"url": "v", "content": "另一条足够长的正文另一条足够长的正文"},
    ]
    out = asyncio.run(clean_node({"raw_dataset": raw, "task_spec": spec}))
    assert len(out["cleaned_dataset"]) == 1  # max_items 封顶


def test_clean_drops_captcha_page():
    """质量门（P0-2）：反爬验证页/错误页被剔除，且原因进 clean_stats。"""
    spec = TaskSpec(intent="x", max_items=10)
    raw = [
        {"url": "u1", "content": "请完成安全验证后继续访问，拖动滑块完成拼图。"},
        {"url": "u2", "content": "403 Forbidden - Access Denied. Please check your permission."},
        {"url": "u3", "content": "这是一段足够长的真实正文内容，讲述了产品的详细参数与用户体验。"},
    ]
    out = asyncio.run(clean_node({"raw_dataset": raw, "task_spec": spec}))
    contents = [i["content"] for i in out["cleaned_dataset"]]
    assert len(contents) == 1 and "真实正文" in contents[0], f"验证页应被剔除，实际：{contents}"
    assert out["clean_stats"].get("验证页") == 2, f"stats 应记 2 条验证页，实际：{out['clean_stats']}"


def test_clean_keeps_long_text_mentioning_captcha():
    """长文提到"验证码"不误杀（只有短正文命中特征词才判脏）。"""
    spec = TaskSpec(intent="x", max_items=10)
    long_text = "本文详细分析了各平台的反爬策略，其中提到验证码机制的演进。" + "分析内容。" * 100
    out = asyncio.run(clean_node({"raw_dataset": [{"url": "u", "content": long_text}], "task_spec": spec}))
    assert len(out["cleaned_dataset"]) == 1, "长文不应被验证页规则误杀"


def test_clean_drops_garbled():
    """质量门（P0-2）：乱码内容（替换符占比高）被剔除。"""
    spec = TaskSpec(intent="x", max_items=10)
    raw = [
        {"url": "u1", "content": "�����������正文片段�����������"},
        {"url": "u2", "content": "这是一段足够长的正常正文内容，编码完好无乱码。"},
    ]
    out = asyncio.run(clean_node({"raw_dataset": raw, "task_spec": spec}))
    assert len(out["cleaned_dataset"]) == 1
    assert out["clean_stats"].get("乱码") == 1, f"stats 应记 1 条乱码，实际：{out['clean_stats']}"


def main():
    tests = [test_denoise_strips_html_and_whitespace,
             test_clean_drops_short_boilerplate, test_clean_dedup_and_cap,
             test_clean_drops_captcha_page, test_clean_keeps_long_text_mentioning_captcha,
             test_clean_drops_garbled]
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
