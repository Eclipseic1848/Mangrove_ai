#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""search 采集器跨后端累积补齐单测。

验证：单个搜索后端结果不足 limit 时，继续用后续后端补齐（按 URL 去重）；
凑够即提前返回；穷尽后有多少交多少并如实说明。
运行：python scripts/test_search_topup.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.base import CollectedItem
from src.collectors.search_collector import SearchDiscoveryCollector
from src.conductor.task_spec import TaskSpec


def _spec(n: int = 10) -> TaskSpec:
    return TaskSpec(intent="搜关键词", keywords=["测试词"], max_items=n)


def _items(prefix: str, n: int) -> list:
    return [
        CollectedItem(url=f"https://x.com/{prefix}{i}", title=f"{prefix}{i}", content="正文" * 20)
        for i in range(n)
    ]


def _run(coro):
    return asyncio.run(coro)


def _collector() -> SearchDiscoveryCollector:
    return SearchDiscoveryCollector()


def test_topup_across_backends():
    """后端 A 不足 limit：继续用后端 B 补齐，两者合并去重。"""
    c = _collector()
    calls = []

    async def fake_discover(backend, query, domain, *, limit, time_label=None):
        calls.append(backend)
        if backend == "searxng":
            return [f"https://x.com/a{i}" for i in range(4)]  # 只给 4 条
        if backend == "ddg":
            # 1 条与 A 重复 + 6 条新链接
            return ["https://x.com/a0"] + [f"https://x.com/b{i}" for i in range(6)]
        return []

    async def fake_fetch(links, deadline=None):
        return [CollectedItem(url=u, title=u, content="正文" * 20) for u in links]

    with patch.object(c, "_discovery_backends", return_value=["searxng", "ddg"]), \
         patch.object(c, "_use_anysearch", return_value=False), \
         patch.object(c, "_use_tavily", return_value=False), \
         patch.object(c, "_discover_one", side_effect=fake_discover), \
         patch.object(c, "_fetch_all", side_effect=fake_fetch):
        res = _run(c.collect(_spec(10)))

    assert res.success, res.message
    # a0~a3 (4) + b0~b5 (6，重复的 a0 被去重) = 10
    assert len(res.items) == 10, f"应合并为 10 条，实际 {len(res.items)}"
    assert "searxng" in calls and "ddg" in calls, "两个后端都应被调用"
    urls = [it.url for it in res.items]
    assert len(urls) == len(set(urls)), "合并结果不应有重复 URL"


def test_stop_when_enough():
    """首个后端已凑够 limit：不再调后续后端。"""
    c = _collector()
    calls = []

    async def fake_discover(backend, query, domain, *, limit, time_label=None):
        calls.append(backend)
        return [f"https://x.com/a{i}" for i in range(10)]

    async def fake_fetch(links, deadline=None):
        return [CollectedItem(url=u, title=u, content="正文" * 20) for u in links]

    with patch.object(c, "_discovery_backends", return_value=["searxng", "ddg"]), \
         patch.object(c, "_use_anysearch", return_value=False), \
         patch.object(c, "_use_tavily", return_value=False), \
         patch.object(c, "_discover_one", side_effect=fake_discover), \
         patch.object(c, "_fetch_all", side_effect=fake_fetch):
        res = _run(c.collect(_spec(10)))

    assert res.success
    assert len(res.items) == 10
    assert calls == ["searxng"], f"凑够后不应再调 ddg，实际调用: {calls}"


def test_exhausted_returns_partial():
    """全部后端穷尽仍不足：有多少交多少，message 如实说明。"""
    c = _collector()

    async def fake_discover(backend, query, domain, *, limit, time_label=None):
        if backend == "searxng":
            return [f"https://x.com/a{i}" for i in range(3)]
        return []  # 其余后端全空

    async def fake_fetch(links, deadline=None):
        return [CollectedItem(url=u, title=u, content="正文" * 20) for u in links]

    with patch.object(c, "_discovery_backends", return_value=["searxng", "ddg"]), \
         patch.object(c, "_use_anysearch", return_value=False), \
         patch.object(c, "_use_tavily", return_value=False), \
         patch.object(c, "_discover_one", side_effect=fake_discover), \
         patch.object(c, "_fetch_all", side_effect=fake_fetch):
        res = _run(c.collect(_spec(10)))

    assert res.success, "有部分数据仍应视为成功"
    assert len(res.items) == 3
    assert "已尽力补采" in res.message, f"message 应说明未达目标: {res.message}"


def test_deadline_returns_partial():
    """时间预算耗尽但已有数据：立即带着现有结果返回，不再调后续后端（防外层 wait_for 掐死丢数据）。"""
    from src.config.settings import settings
    c = _collector()
    calls = []

    async def fake_discover(backend, query, domain, *, limit, time_label=None):
        calls.append(backend)
        return [f"https://x.com/{backend}{i}" for i in range(3)]

    async def fake_fetch(links, deadline=None):
        await asyncio.sleep(0.5)  # 模拟慢站抓取烧穿预算
        return [CollectedItem(url=u, title=u, content="正文" * 20) for u in links]

    with patch.object(settings, "collect_timeout_seconds", 0.5), \
         patch.object(c, "_discovery_backends", return_value=["searxng", "ddg"]), \
         patch.object(c, "_use_anysearch", return_value=False), \
         patch.object(c, "_use_tavily", return_value=False), \
         patch.object(c, "_discover_one", side_effect=fake_discover), \
         patch.object(c, "_fetch_all", side_effect=fake_fetch):
        res = _run(c.collect(_spec(10)))

    assert res.success, f"有数据应视为成功: {res.message}"
    assert len(res.items) == 3
    assert calls == ["searxng"], f"预算尽后不应再调后续后端，实际: {calls}"


def test_deadline_no_data_fails_fast():
    """时间预算耗尽且一无所获：如实返回失败（而非继续穷尽全部兜底被外层超时掐死）。"""
    from src.config.settings import settings
    c = _collector()
    calls = []

    async def fake_discover(backend, query, domain, *, limit, time_label=None):
        calls.append(backend)
        return [f"https://x.com/{backend}{i}" for i in range(3)]

    async def fake_fetch(links, deadline=None):
        await asyncio.sleep(0.5)  # 烧穿预算且没抓到任何正文
        return []

    with patch.object(settings, "collect_timeout_seconds", 0.5), \
         patch.object(c, "_discovery_backends", return_value=["searxng", "ddg"]), \
         patch.object(c, "_use_anysearch", return_value=False), \
         patch.object(c, "_use_tavily", return_value=False), \
         patch.object(c, "_discover_one", side_effect=fake_discover), \
         patch.object(c, "_fetch_all", side_effect=fake_fetch):
        res = _run(c.collect(_spec(10)))

    assert not res.success
    assert "时间预算" in res.message, f"应说明预算内未发现: {res.message}"
    assert calls == ["searxng"], f"预算尽后不应再穷尽兜底，实际: {calls}"


def test_all_empty_fails():
    """所有后端全空：返回失败。"""
    c = _collector()

    async def fake_discover(backend, query, domain, *, limit, time_label=None):
        return []

    with patch.object(c, "_discovery_backends", return_value=["searxng", "ddg"]), \
         patch.object(c, "_use_anysearch", return_value=False), \
         patch.object(c, "_use_tavily", return_value=False), \
         patch.object(c, "_discover_one", side_effect=fake_discover):
        res = _run(c.collect(_spec(10)))

    assert not res.success
    assert "未发现" in res.message


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
