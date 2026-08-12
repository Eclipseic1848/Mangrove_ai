#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平台词表与归一单测。运行：python scripts/test_platforms.py"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.platforms import (
    known_platforms,
    normalize_platform,
    normalize_domain,
    resolve_domains,
    url_in_domains,
)
from src.collectors.social_media_collector import _resolve_platform
from src.conductor.task_spec import TaskSpec


def test_known_platforms_nonempty():
    ps = known_platforms()
    assert isinstance(ps, list) and "抖音" in ps and "小红书" in ps


def test_normalize_alias():
    assert normalize_platform("douyin") == "抖音"
    assert normalize_platform("XHS") == "小红书"      # 大小写
    assert normalize_platform(" 哔哩哔哩 ") == "B站"   # 空格 + 中文别名


def test_normalize_contains():
    # 包含规范名子串也能命中
    assert normalize_platform("小红书App") == "小红书"


def test_normalize_unknown_returns_stripped():
    assert normalize_platform("  汽车之家 ") == "汽车之家"
    assert normalize_platform("") == ""


def test_resolve_platform_variants():
    # 别名 / 大小写 / 含噪声都应解析到 MediaCrawler 代码
    assert _resolve_platform(TaskSpec(intent="x", platforms=["douyin"])) == "dy"
    assert _resolve_platform(TaskSpec(intent="x", platforms=["小红书App"])) == "xhs"
    assert _resolve_platform(TaskSpec(intent="x", platforms=["B站"])) == "bili"
    # 未支持平台返回空串（交给通用引擎）
    assert _resolve_platform(TaskSpec(intent="x", platforms=["汽车之家"])) == ""


def test_normalize_domain():
    assert normalize_domain("https://www.stats.gov.cn/sj/zxfb/") == "stats.gov.cn"
    assert normalize_domain("autohome.com.cn") == "autohome.com.cn"
    assert normalize_domain("www.AutoHome.com.cn:443") == "autohome.com.cn"
    assert normalize_domain("") == ""


def test_resolve_domains_map_then_llm():
    # 内置表命中（汽车之家）
    assert resolve_domains(["汽车之家"], None) == ["autohome.com.cn"]
    # 表外站点：用 planner 给的 site_domains 兜底
    assert resolve_domains(["国家统计局"], ["https://www.stats.gov.cn"]) == ["stats.gov.cn"]
    # 两者合并去重、内置在前
    got = resolve_domains(["汽车之家", "国家统计局"], ["stats.gov.cn", "autohome.com.cn"])
    assert got == ["autohome.com.cn", "stats.gov.cn"]
    # 不限定
    assert resolve_domains([], None) == []


def test_url_in_domains():
    doms = ["autohome.com.cn"]
    assert url_in_domains("https://www.autohome.com.cn/123", doms) is True
    assert url_in_domains("https://k.autohome.com.cn/a", doms) is True  # 子域名
    assert url_in_domains("https://dongchedi.com/x", doms) is False
    assert url_in_domains("https://anything.com", []) is True  # 空=不限定


def main():
    tests = [test_known_platforms_nonempty, test_normalize_alias,
             test_normalize_contains, test_normalize_unknown_returns_stripped,
             test_resolve_platform_variants,
             test_normalize_domain, test_resolve_domains_map_then_llm, test_url_in_domains]
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
