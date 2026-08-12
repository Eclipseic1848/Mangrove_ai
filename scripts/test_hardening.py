#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""并发与稳定性加固单元测试（期4-⑤）。

覆盖：① task_id 并行唯一（不踩踏）；② 采集器超时自动降级；③ 定时任务执行超时记失败。
运行：python scripts/test_hardening.py
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings


def test_task_id_unique():
    """同一秒内连续构造的任务 task_id 不应相同（避免并行任务目录互相覆盖）。"""
    from src.conductor.graph import _build_init
    ids = {
        _build_init("x", None, None, None, "s", False, False)["task_id"]
        for _ in range(50)
    }
    assert len(ids) == 50, f"task_id 出现重复，仅 {len(ids)}/50 唯一"
    # 仍保留可读的时间前缀
    assert any(i.startswith(datetime.now().strftime("%Y%m%d")) for i in ids)


def test_collect_timeout_fallback():
    """前一个采集器卡死超时 → 自动降级到下一个可用采集器。"""
    from src.collectors.base import BaseCollector, CollectedItem, CollectResult
    from src.collectors.registry import register
    from src.conductor.nodes.collect import collect_node
    from src.conductor.task_spec import TaskSpec

    class _SlowCollector(BaseCollector):
        name = "_test_slow"
        tier = 1

        async def collect(self, spec):
            await asyncio.sleep(10)  # 远超测试超时，必被 wait_for 打断
            return CollectResult(True, self.name, items=[CollectedItem(content="不该到这")])

    class _FastCollector(BaseCollector):
        name = "_test_fast"
        tier = 2

        async def collect(self, spec):
            return CollectResult(True, self.name, items=[CollectedItem(content="快采到了")])

    register(_SlowCollector())
    register(_FastCollector())

    old = settings.collect_timeout_seconds
    settings.collect_timeout_seconds = 0.05  # 收紧超时，让慢采集器必超时
    try:
        spec = TaskSpec(intent="x")
        state = {"task_spec": spec, "collector_candidates": ["_test_slow", "_test_fast"]}
        result = asyncio.run(collect_node(state))
    finally:
        settings.collect_timeout_seconds = old

    assert result["collector_used"] == "_test_fast", result.get("collector_used")
    assert len(result["raw_dataset"]) == 1


def test_collect_all_timeout():
    """所有采集器都超时 → 返回无数据 + 错误信息含超时。"""
    from src.collectors.base import BaseCollector, CollectResult
    from src.collectors.registry import register
    from src.conductor.nodes.collect import collect_node
    from src.conductor.task_spec import TaskSpec

    class _SlowOnly(BaseCollector):
        name = "_test_slow_only"
        tier = 1

        async def collect(self, spec):
            await asyncio.sleep(10)
            return CollectResult(True, self.name)

    register(_SlowOnly())
    old = settings.collect_timeout_seconds
    settings.collect_timeout_seconds = 0.05
    try:
        spec = TaskSpec(intent="x")
        state = {"task_spec": spec, "collector_candidates": ["_test_slow_only"]}
        result = asyncio.run(collect_node(state))
    finally:
        settings.collect_timeout_seconds = old

    assert result["collector_used"] == ""
    assert "超时" in (result.get("error") or "")


def test_scheduler_task_timeout():
    """定时任务执行卡死 → 超时记为失败（success=False），不抛出、不冻住循环。"""
    from src.scheduler.service import SchedulerService

    captured = {}

    class _FakeStore:
        def mark_run(self, task_id, *, success, result=None, error=None, next_run_at=None):
            captured["task_id"] = task_id
            captured["success"] = success
            captured["error"] = error

        def add_run(self, task_id, *, success, summary, report_path="", json_path=""):
            captured["history_task_id"] = task_id
            captured["history_success"] = success
            captured["history_summary"] = summary

    async def _hang_runner(user_input, **kwargs):
        await asyncio.sleep(10)
        return {"reply": "不该完成"}

    svc = SchedulerService(store=_FakeStore(), runner=_hang_runner)
    old = settings.scheduler_task_timeout_seconds
    settings.scheduler_task_timeout_seconds = 0.05
    try:
        task = {"task_id": "t1", "trigger_type": "once", "user_input": "x"}
        asyncio.run(svc._run_one(task, datetime.now()))
    finally:
        settings.scheduler_task_timeout_seconds = old

    assert captured.get("task_id") == "t1"
    assert captured.get("success") is False
    assert "超时" in (captured.get("error") or "")
    assert captured.get("history_task_id") == "t1"
    assert captured.get("history_success") is False
    assert "超时" in (captured.get("history_summary") or "")


def main():
    tests = [
        test_task_id_unique,
        test_collect_timeout_fallback,
        test_collect_all_timeout,
        test_scheduler_task_timeout,
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
