#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RSSHub 采集器单测（离线：路由解析 / feed 解析 / matches / 注册）。
运行：python scripts/test_rsshub.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors import rsshub_collector as rh
from src.collectors.rsshub_collector import RsshubCollector, _resolve_route, _parse_feed
from src.conductor.task_spec import TaskSpec


def test_resolve_route():
    assert _resolve_route(TaskSpec(intent="x", platforms=["微博"], keywords=["k"])) == "/weibo/keyword/{kw}"
    assert _resolve_route(TaskSpec(intent="x", platforms=["什么值得买"], keywords=["k"])) == "/smzdm/keyword/{kw}"
    assert _resolve_route(TaskSpec(intent="x", platforms=["weibo"], keywords=["k"])) == "/weibo/keyword/{kw}"
    # 新增：B站视频搜索、百度全网搜索
    assert _resolve_route(TaskSpec(intent="x", platforms=["B站"], keywords=["k"])) == "/bilibili/vsearch/{kw}"
    assert _resolve_route(TaskSpec(intent="x", platforms=["bilibili"], keywords=["k"])) == "/bilibili/vsearch/{kw}"
    assert _resolve_route(TaskSpec(intent="x", platforms=["百度"], keywords=["k"])) == "/baidu/search/{kw}"
    # 表外平台不接管
    assert _resolve_route(TaskSpec(intent="x", platforms=["淘宝"], keywords=["k"])) is None
    assert _resolve_route(TaskSpec(intent="x", platforms=[], keywords=["k"])) is None


def test_route_url_building():
    """路由 + 关键词拼成正确的 RSSHub URL（关键词 URL 编码）。"""
    from urllib.parse import quote
    kw = "小米SU7"
    route = _resolve_route(TaskSpec(intent="x", platforms=["B站"], keywords=[kw]))
    url = "http://localhost:1200" + route.format(kw=quote(kw, safe=""))
    assert url == f"http://localhost:1200/bilibili/vsearch/{quote(kw, safe='')}", url


def test_matches():
    c = RsshubCollector()
    # 平台命中 + 有关键词 + 无 URL → 匹配
    assert c.matches(TaskSpec(intent="x", platforms=["微博"], keywords=["小米SU7"]))
    # 有显式 URL → 不接管（交 URL 采集器）
    assert not c.matches(TaskSpec(intent="x", platforms=["微博"], keywords=["k"], urls=["https://weibo.com/x"]))
    # 无关键词 → 不匹配
    assert not c.matches(TaskSpec(intent="x", platforms=["微博"]))
    # 表外平台 → 不匹配
    assert not c.matches(TaskSpec(intent="x", platforms=["抖音"], keywords=["k"]))


def test_parse_feed_regex():
    """正则兜底解析 RSS（不依赖 feedparser 是否安装）。"""
    xml = """<rss><channel>
      <item><title><![CDATA[标题一]]></title><link>https://a.com/1</link>
        <description><![CDATA[<p>正文一内容，足够长的一段。</p>]]></description></item>
      <item><title>标题二</title><link>https://a.com/2</link>
        <description>正文二纯文本</description></item>
    </channel></rss>"""
    # 直接测正则路径：临时关掉 feedparser
    old = rh._FEEDPARSER_OK
    rh._FEEDPARSER_OK = False
    try:
        entries = _parse_feed(xml)
    finally:
        rh._FEEDPARSER_OK = old
    assert len(entries) == 2, entries
    assert entries[0]["title"] == "标题一"
    assert entries[0]["link"] == "https://a.com/1"
    assert "正文一内容" in entries[0]["content"]
    assert "<p>" not in entries[0]["content"], "HTML 应被清理为纯文本"
    assert entries[1]["title"] == "标题二"


def test_is_available():
    from src.config import settings
    c = RsshubCollector()
    old = settings.rsshub_base_url
    try:
        settings.rsshub_base_url = ""
        assert not c.is_available()
        settings.rsshub_base_url = "http://localhost:1200"
        assert c.is_available()
    finally:
        settings.rsshub_base_url = old


def test_registered():
    from src.collectors import get_registry
    reg = get_registry()
    assert "rsshub" in reg, "RSSHub 采集器应已注册"
    assert reg["rsshub"].tier == 5


def main():
    tests = [test_resolve_route, test_route_url_building, test_matches, test_parse_feed_regex,
             test_is_available, test_registered]
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
