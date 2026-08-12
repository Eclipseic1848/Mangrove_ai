# -*- coding: utf-8 -*-
"""清洗引擎兼容性测试（plan Phase 1 退出标准：clean.py 6 用例不回归）。

把 scripts/test_clean.py 的 6 个用例通过 RecipeEngine + 网页规则跑一遍，
验证迁移后行为与旧 clean_node 严格一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cleaning.engine import RecipeEngine  # noqa: E402
from src.cleaning.rules.web_rules import _denoise  # noqa: E402
from src.data_prep.models import Recipe, RecordEnvelope  # noqa: E402


def _env(url: str, content: str) -> RecordEnvelope:
    return RecordEnvelope(record_id=url or content[:16], data={"url": url, "content": content}, meta={})


def _reasons(result) -> dict:
    """统计隔离原因（对标 clean.py 的 clean_stats）。"""
    out = {}
    for rj in result.rejects:
        out[rj.reason] = out.get(rj.reason, 0) + 1
    return out


def test_denoise_strips_html_and_whitespace():
    raw = "<p>你好<br/>  世界</p>\n\n\n正文"
    out = _denoise(raw)
    assert "<" not in out and ">" not in out
    assert "你好" in out and "世界" in out and "正文" in out
    assert "\n\n\n" not in out


def test_clean_drops_short_boilerplate():
    engine = RecipeEngine()
    records = [
        _env("u1", "首页 登录 注册"),
        _env("u2", "这是一段足够长的真实正文内容，应当保留下来。"),
    ]
    result = engine.execute(records, Recipe())
    contents = [r.data["content"] for r in result.clean]
    assert any("真实正文" in c for c in contents)
    assert not any("首页 登录 注册" in c for c in contents)
    assert _reasons(result).get("样板噪声") == 1


def test_clean_dedup_and_cap():
    """max_items=1：去重 + 封顶各保留 1 条。"""
    engine = RecipeEngine()
    records = [
        _env("u", "重复内容重复内容重复内容重复内容"),
        _env("u", "重复内容重复内容重复内容重复内容"),
        _env("v", "另一条足够长的正文另一条足够长的正文"),
    ]
    result = engine.execute(records, Recipe(), rule_params={"web_cap_max_items": {"max_items": 1}})
    assert len(result.clean) == 1, f"max_items=1 应只留 1 条，实际 {len(result.clean)}"
    assert _reasons(result).get("重复") == 1


def test_clean_drops_captcha_page():
    engine = RecipeEngine()
    records = [
        _env("u1", "请完成安全验证后继续访问，拖动滑块完成拼图。"),
        _env("u2", "403 Forbidden - Access Denied. Please check your permission."),
        _env("u3", "这是一段足够长的真实正文内容，讲述了产品的详细参数与用户体验。"),
    ]
    result = engine.execute(records, Recipe())
    contents = [r.data["content"] for r in result.clean]
    assert len(contents) == 1 and "真实正文" in contents[0], f"验证页应剔除，实际：{contents}"
    assert _reasons(result).get("验证页") == 2, f"应记 2 条验证页，实际：{_reasons(result)}"


def test_clean_keeps_long_text_mentioning_captcha():
    engine = RecipeEngine()
    long_text = "本文详细分析了各平台的反爬策略，其中提到验证码机制的演进。" + "分析内容。" * 100
    result = engine.execute([_env("u", long_text)], Recipe())
    assert len(result.clean) == 1, "长文不应被验证页规则误杀"


def test_clean_drops_garbled():
    engine = RecipeEngine()
    records = [
        _env("u1", "�����������正文片段�����������"),
        _env("u2", "这是一段足够长的正常正文内容，编码完好无乱码。"),
    ]
    result = engine.execute(records, Recipe())
    assert len(result.clean) == 1
    assert _reasons(result).get("乱码") == 1, f"应记 1 条乱码，实际：{_reasons(result)}"


def test_engine_ledger_conservation():
    """账本守恒：输入 = 输出 + 隔离（plan 15.2 账本测试）。"""
    engine = RecipeEngine()
    records = [
        _env("u1", "首页"),                    # 样板噪声 -> 隔离
        _env("u2", "重复内容重复内容重复内容"),  # 保留
        _env("u2", "重复内容重复内容重复内容"),  # 重复 -> 隔离
    ]
    result = engine.execute(records, Recipe())
    assert len(records) == len(result.clean) + len(result.rejects), \
        f"账本不守恒：输入{len(records)} ≠ 输出{len(result.clean)}+隔离{len(result.rejects)}"


TESTS = [
    test_denoise_strips_html_and_whitespace,
    test_clean_drops_short_boilerplate,
    test_clean_dedup_and_cap,
    test_clean_drops_captcha_page,
    test_clean_keeps_long_text_mentioning_captcha,
    test_clean_drops_garbled,
    test_engine_ledger_conservation,
]


def main():
    failed = 0
    for t in TESTS:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(TESTS) - failed}/{len(TESTS)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
