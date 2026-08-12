#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""电商采集器单元测试（离线纯函数：SKU 抽取 / 评论解析 / 匹配）。

运行：python scripts/test_ecommerce.py
不联网，仅校验解析与路由匹配逻辑；联网冒烟另见 verify_ecommerce.py。
"""
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors import ecommerce_collector as ec
from src.collectors.ecommerce_collector import EcommerceCollector
from src.conductor.task_spec import TaskSpec


def test_extract_product_ids():
    """从 URL 抽取商品 ID（当前实现 _extract_product_ids，京东 SKU 去重保序）。"""
    spec = TaskSpec(intent="x", urls=[
        "https://item.jd.com/100012043978.html",
        "https://item.jd.com/65432198.html?from=ad",
        "https://example.com/not-jd",
    ])
    ids = ec._extract_product_ids(spec)
    assert ids.get("jd") == ["100012043978", "65432198"], ids


def test_parse_comments():
    data = {
        "comments": [
            {"content": "鞋子很舒服，缓震到位", "score": 5, "referenceName": "Brooks 甘油23",
             "productColor": "黑色", "productSize": "42", "creationTime": "2026-06-01 10:00:00",
             "nickname": "user_a"},
            {"content": "   ", "score": 3},          # 空白正文应被丢弃
            {"content": "偏大半码", "score": 4, "referenceName": "Brooks 甘油23"},
        ]
    }
    items = ec._parse_comments(data, "100012043978")
    assert len(items) == 2, len(items)
    first = items[0]
    assert first.content == "鞋子很舒服，缓震到位"
    assert first.url == "https://item.jd.com/100012043978.html"
    assert first.title == "Brooks 甘油23"
    assert first.metadata["platform"] == "jd"
    assert first.metadata["score"] == 5
    assert first.metadata["spec"] == "黑色 42"
    assert first.metadata["sku"] == "100012043978"
    # 空 comments / 异常结构不报错
    assert ec._parse_comments({}, "1") == []
    assert ec._parse_comments({"comments": None}, "1") == []


def test_matches():
    c = EcommerceCollector()
    # 平台名命中京东
    assert c.matches(TaskSpec(intent="x", platforms=["京东"], keywords=["甘油23"]))
    assert c.matches(TaskSpec(intent="x", platforms=["JD"]))
    # 给出京东商品 URL（即便平台名是别的）也接管
    assert c.matches(TaskSpec(intent="x", urls=["https://item.jd.com/100012043978.html"]))
    # 未配置 Cookie 时淘宝/天猫不接管；测试不得读取开发机真实登录态。
    with patch("src.collectors.ecommerce_collector._platform_cookie", return_value=""):
        assert not c.matches(TaskSpec(intent="x", platforms=["淘宝"], keywords=["甘油23"]))
        assert not c.matches(TaskSpec(intent="x", platforms=["天猫"]))


def test_jd_headers_cookie():
    from src.config import settings
    old = settings.jd_cookie
    try:
        # 空 cookie：不注入 Cookie 头
        settings.jd_cookie = ""
        assert "Cookie" not in ec._jd_headers()
        # 配了 cookie：注入，且 extra 头保留
        settings.jd_cookie = "pin=abc; thor=xyz"
        h = ec._jd_headers({"Referer": "https://item.jd.com/1.html"})
        assert h["Cookie"] == "pin=abc; thor=xyz"
        assert h["Referer"] == "https://item.jd.com/1.html"
    finally:
        settings.jd_cookie = old


def test_registered():
    from src.collectors import get_registry
    reg = get_registry()
    assert "ecommerce" in reg, "电商采集器应已注册"
    assert reg["ecommerce"].tier == 10


def main():
    tests = [
        test_extract_product_ids,
        test_parse_comments,
        test_matches,
        test_jd_headers_cookie,
        test_registered,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
