#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""时效工具单测。运行：python scripts/test_recency.py"""
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors._recency import (
    apply_recency,
    extract_date_from_html,
    extract_publish_date,
    parse_time_range,
)
from src.collectors.base import CollectedItem


def test_parse_latest_no_window():
    w = parse_time_range("最新")
    assert w is not None and w.days is None and w.label is None  # 仅排序，不设硬窗口


def test_parse_relative_windows():
    assert parse_time_range("最近7天").days == 7
    assert parse_time_range("近三天").days == 3       # 中文数字
    assert parse_time_range("最近2周").days == 14
    assert parse_time_range("最近3个月").days == 90
    assert parse_time_range("今天").label == "day"
    assert parse_time_range("本周").label == "week"
    assert parse_time_range("2017年至今").days > 0
    assert parse_time_range(None) is None
    assert parse_time_range("随便写写") is None       # 不可识别→不处理


def test_extract_date_from_meta_and_url():
    it = CollectedItem(url="https://x.com/a", title="t", content="c",
                       metadata={"publishedTime": "2023-05-06T08:00:00Z"})
    assert extract_publish_date(it) == date(2023, 5, 6)
    it2 = CollectedItem(url="https://stats.gov.cn/2017/03/15/report.html", title="t",
                        content="无日期正文", metadata={})
    assert extract_publish_date(it2) == date(2017, 3, 15)
    it3 = CollectedItem(url="https://x.com/n", title="t", content="发布于 2026年06月 的最新公告", metadata={})
    assert extract_publish_date(it3) == date(2026, 6, 1)
    it4 = CollectedItem(url="https://x.com/none", title="t", content="完全没有日期", metadata={})
    assert extract_publish_date(it4) is None


def _item(name, content="", meta=None):
    return CollectedItem(url=f"https://x.com/{name}", title=name, content=content, metadata=meta or {})


def test_apply_recency_filter_and_sort():
    today = date.today()
    new = _item("new", meta={"publishedTime": today.isoformat()})
    old = _item("old2017", meta={"publishedTime": "2017-03-01"})
    undated = _item("undated", content="没有日期")
    mid = _item("mid", meta={"publishedTime": (today - timedelta(days=3)).isoformat()})

    # 窗口=最近7天：2017 的被丢弃；无日期的保留
    w = parse_time_range("最近7天")
    out = apply_recency([old, undated, new, mid], w)
    names = [i.title for i in out]
    assert "old2017" not in names, names                 # 确信过旧→丢弃
    assert "undated" in names, names                     # 无日期→保留
    assert names.index("new") < names.index("mid"), names  # 新→旧排序
    assert names[-1] == "undated", names                 # 无日期垫后

    # "最新"（无硬窗口）：不丢弃旧的，但按日期倒序
    w2 = parse_time_range("最新")
    out2 = apply_recency([old, new], w2)
    assert [i.title for i in out2] == ["new", "old2017"]

    # window=None：原样返回
    assert apply_recency([old, new], None) == [old, new]


def test_extract_date_from_html_meta():
    """HTML meta 标签多路解析（P1-3）：property/name/itemprop 与属性顺序无关。"""
    h1 = '<html><head><meta property="article:published_time" content="2026-07-01T08:00:00+08:00"></head></html>'
    assert extract_date_from_html(h1) == date(2026, 7, 1)
    # content 在前、name 在后的属性顺序
    h2 = '<meta content="2025-12-31" name="pubdate">'
    assert extract_date_from_html(h2) == date(2025, 12, 31)
    h3 = '<meta itemprop="datePublished" content="2026年6月15日">'
    assert extract_date_from_html(h3) == date(2026, 6, 15)
    # 非日期 meta 键不误命中
    h4 = '<meta name="keywords" content="2020-01-01 大促">'
    assert extract_date_from_html(h4) is None


def test_extract_date_from_html_jsonld_and_time():
    """JSON-LD 与 <time> 标签解析（P1-3）。"""
    jl = '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-07-03T10:00:00"}</script>'
    assert extract_date_from_html(jl) == date(2026, 7, 3)
    tt = '<article><time datetime="2026-06-20T12:00:00">6月20日</time></article>'
    assert extract_date_from_html(tt) == date(2026, 6, 20)
    assert extract_date_from_html("") is None
    assert extract_date_from_html("<div>无任何日期标记</div>") is None


def test_relative_and_yearless_dates():
    """相对日期与无年份日期（P1-3）：中文站常见发布时间格式。"""
    today = date.today()
    assert extract_publish_date(_item("a", content="3天前 发布")) == today - timedelta(days=3)
    assert extract_publish_date(_item("b", content="昨天 12:30")) == today - timedelta(days=1)
    assert extract_publish_date(_item("c", content="刚刚")) == today
    assert extract_publish_date(_item("d", content="2小时前")) == today
    # 无年份"MM月DD日"：按今年算（不晚于明天），跨年自动回退
    d = extract_publish_date(_item("e", content="发布于 07月05日"))
    assert d is not None and d <= today + timedelta(days=1) and (d.month, d.day) == (7, 5)
    # "MM-DD hh:mm"（微博式）：必须带时间部分才认
    d2 = extract_publish_date(_item("f", content="06-30 09:15 来自微博网页版"))
    assert d2 is not None and (d2.month, d2.day) == (6, 30)
    # 纯"3-5"这类数字段不带时间不误认
    assert extract_publish_date(_item("g", content="价格区间 3-5 万元")) is None


def test_extract_content_backfills_date():
    """extract_content 提取正文时回填 metadata['date']（P1-3 布线验证）。"""
    from src.collectors._extract import extract_content
    html = (
        '<html><head><title>测试页</title>'
        '<meta property="article:published_time" content="2026-07-02T09:00:00+08:00"></head>'
        '<body><article><p>' + "这是正文内容。" * 30 + '</p></article></body></html>'
    )
    _, text, meta = extract_content(html, "https://x.com/t")
    assert text.strip(), "应提取到正文"
    assert meta.get("date", "").startswith("2026-07-02"), f"metadata 应回填日期，实际：{meta}"


def main():
    tests = [test_parse_latest_no_window, test_parse_relative_windows,
             test_extract_date_from_meta_and_url, test_apply_recency_filter_and_sort,
             test_extract_date_from_html_meta, test_extract_date_from_html_jsonld_and_time,
             test_relative_and_yearless_dates, test_extract_content_backfills_date]
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
