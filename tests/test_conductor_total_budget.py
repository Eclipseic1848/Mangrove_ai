"""共享图入口的总耗时预算，已完成的数据保留而后续节点不再执行。"""
import asyncio
import time

import pytest

from src.conductor.graph import _traced
from src.conductor.task_spec import TaskSpec


@pytest.mark.asyncio
async def test_total_budget_stops_next_stage_without_discarding_prior_data():
    calls = []
    async def collect(state):
        calls.append("collect")
        return {"raw_dataset": [{"text": "已完成"}]}
    spec = TaskSpec.from_draft({"intent": "采集", "time_budget_seconds": 1})
    state = {"task_spec": spec, "execution_started_at": time.time() - 2, "raw_dataset": [{"text": "保留"}]}
    result = await _traced("collect", collect)(state)
    assert result["budget_exhausted"]
    assert not calls and state["raw_dataset"] == [{"text": "保留"}]


@pytest.mark.asyncio
async def test_inflight_stage_obeys_remaining_total_budget():
    stopped = asyncio.Event()
    async def slow(state):
        try:
            await asyncio.sleep(10)
        finally:
            stopped.set()
    state = {"task_spec": TaskSpec.from_draft({"intent": "分析", "time_budget_seconds": 1}),
             "execution_started_at": time.time() - .95}
    result = await _traced("analyze", slow)(state)
    assert result["budget_exhausted"] and stopped.is_set()


@pytest.mark.parametrize("value", [0, -1, 14401, 1.5, True, "120"])
def test_total_budget_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        TaskSpec.from_draft({"intent": "采集", "time_budget_seconds": value})


def test_unspecified_budget_preserves_historical_serialization():
    historical = TaskSpec(intent="采集").model_dump(mode="json")
    historical.pop("time_budget_seconds", None)
    assert TaskSpec.model_validate(historical).model_dump(mode="json") == historical
    specified = TaskSpec.model_validate({**historical, "time_budget_seconds": 120})
    assert specified.model_dump(mode="json")["time_budget_seconds"] == 120


@pytest.mark.asyncio
async def test_budget_preserves_completed_collector_when_topup_is_cancelled(monkeypatch):
    from src.conductor.nodes import collect
    from src.collectors.base import CollectResult, CollectedItem
    class Collector:
        def __init__(self, slow=False):
            self.slow = slow
        async def collect(self, spec):
            if self.slow:
                await asyncio.sleep(10)
            return CollectResult(True, "first", items=[CollectedItem(content="已取得的资料")])
    monkeypatch.setattr(collect, "get_registry", lambda: {"first": Collector(), "slow": Collector(True)})
    monkeypatch.setattr(collect, "metrics_record", lambda *args: None)
    monkeypatch.setattr(collect, "domain_health_record", lambda *args: None)
    state = {"task_spec": TaskSpec(intent="采集", max_items=10, time_budget_seconds=1),
        "collector_candidates": ["first", "slow"], "execution_started_at": time.time() - .9}
    result = await _traced("collect", collect.collect_node)(state)
    assert result["budget_exhausted"]
    assert result["raw_dataset"][0]["content"] == "已取得的资料"
    assert result["collector_used"] == "first"


@pytest.mark.asyncio
async def test_user_cancellation_is_not_reported_as_budget_exhaustion():
    from src.conductor.nodes.collect import CollectionInterrupted
    async def cancelled(state):
        raise CollectionInterrupted({"raw_dataset": []})
    state = {"task_spec": TaskSpec(intent="采集", time_budget_seconds=120), "execution_started_at": time.time()}
    with pytest.raises(asyncio.CancelledError):
        await _traced("collect", cancelled)(state)
