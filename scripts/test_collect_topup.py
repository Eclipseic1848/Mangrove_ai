#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集补采逻辑单测（P0-1：数据量不足自动降级补齐+跨采集器去重）。
运行：python scripts/test_collect_topup.py
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors import register
from src.collectors.base import BaseCollector, CollectedItem, CollectResult
from src.conductor.nodes.collect import collect_node
from src.conductor.task_spec import TaskSpec


class FakeCollector(BaseCollector):
    """可控的假采集器：返回预设条目，并记录是否被调用。"""

    def __init__(self, name: str, items: list, success: bool = True):
        self.name = name
        self.tier = 50
        self._items = items
        self._success = success
        self.called = False

    async def collect(self, spec: TaskSpec) -> CollectResult:
        self.called = True
        return CollectResult(
            success=self._success, collector=self.name, items=self._items,
            message="" if self._success else "模拟失败",
        )


def _items(prefix: str, n: int) -> list:
    return [CollectedItem(url=f"https://x.com/{prefix}{i}", title=f"{prefix}{i}", content="正文" * 10) for i in range(n)]


def _run(state):
    return asyncio.run(collect_node(state))


def test_topup_when_insufficient():
    """关键词任务：首个采集器数据不足 60% 时继续补采，跨采集器按 URL 去重。"""
    a = FakeCollector("fake_a", _items("a", 3))
    # b 含 1 条与 a 重复的 URL + 4 条新数据
    b_items = [CollectedItem(url="https://x.com/a0", title="dup", content="重复")] + _items("b", 4)
    b = FakeCollector("fake_b", b_items)
    register(a); register(b)
    spec = TaskSpec(intent="搜关键词", keywords=["k"], max_items=10)  # enough = 6
    out = _run({"task_spec": spec, "collector_candidates": ["fake_a", "fake_b"]})
    assert len(out["raw_dataset"]) == 7, f"3+4(去重1) 应为 7 条，实际 {len(out['raw_dataset'])}"
    assert out["collector_used"] == "fake_a+fake_b", f"实际：{out['collector_used']}"
    assert b.called, "数据不足应触发补采"
    assert any("补采" in n for n in out["collector_notes"]), "应有补采提示"


def test_stop_when_enough():
    """首个采集器数据已达 60% 阈值：不再补采。"""
    a = FakeCollector("fake_a2", _items("a", 8))
    b = FakeCollector("fake_b2", _items("b", 5))
    register(a); register(b)
    spec = TaskSpec(intent="搜关键词", keywords=["k"], max_items=10)  # enough = 6, 8 >= 6
    out = _run({"task_spec": spec, "collector_candidates": ["fake_a2", "fake_b2"]})
    assert len(out["raw_dataset"]) == 8
    assert out["collector_used"] == "fake_a2"
    assert not b.called, "数据足够不应再调下一采集器"


def test_url_task_no_topup():
    """URL 任务：命中即停，不补采（各引擎抓同一批 URL 无意义）。"""
    a = FakeCollector("fake_a3", _items("a", 1))
    b = FakeCollector("fake_b3", _items("b", 5))
    register(a); register(b)
    spec = TaskSpec(intent="抓这个网址", urls=["https://x.com/page"], max_items=30)
    out = _run({"task_spec": spec, "collector_candidates": ["fake_a3", "fake_b3"]})
    assert len(out["raw_dataset"]) == 1
    assert out["collector_used"] == "fake_a3"
    assert not b.called, "URL 任务不应补采"


def test_cap_at_max_items():
    """总量不超过 max_items 上限。"""
    a = FakeCollector("fake_a4", _items("a", 20))
    register(a)
    spec = TaskSpec(intent="搜关键词", keywords=["k"], max_items=10)
    out = _run({"task_spec": spec, "collector_candidates": ["fake_a4"]})
    assert len(out["raw_dataset"]) == 10, f"应裁剪到 10 条，实际 {len(out['raw_dataset'])}"


def test_all_failed():
    """全部失败：返回 error 且数据为空。"""
    a = FakeCollector("fake_a5", [], success=False)
    register(a)
    spec = TaskSpec(intent="搜关键词", keywords=["k"], max_items=10)
    out = _run({"task_spec": spec, "collector_candidates": ["fake_a5"]})
    assert out["raw_dataset"] == []
    assert "error" in out and out["error"]
    assert out["collector_attempts"] == [
        {"collector": "fake_a5", "success": False, "count": 0, "message": "模拟失败"}
    ]


def test_mediacrawler_failure_is_visible():
    """社媒详情失败必须透出真实原因，不能被后续兜底引擎掩盖。"""
    collector = FakeCollector("mediacrawler", [], success=False)
    register(collector)
    spec = TaskSpec(intent="总结指定视频", platforms=["抖音"], urls=["https://v.douyin.com/test/"], max_items=1)
    out = _run({"task_spec": spec, "collector_candidates": ["mediacrawler"]})
    assert any("模拟失败" in note for note in out["collector_notes"])


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
