"""调度外层只服从已校验的小红书不限时规格，其他执行保持超时隔离。"""
import asyncio
from datetime import datetime

import pytest

from src.conductor import graph
from src.conductor.task_spec import TaskSpec
from src.config.settings import settings
from src.scheduler.service import SchedulerService
from tests.test_scheduler_owner import owner_stores, _add


@pytest.mark.parametrize("unlimited", [False, True])
def test_real_scheduler_passes_timeout_to_conductor_only_validated_spec_disables_it(
    owner_stores, monkeypatch, unlimited,
):
    web, schedules, owners = owner_stores
    task_id = _add(schedules, owners[0])
    monkeypatch.setattr(settings, "scheduler_task_timeout_seconds", .2)
    entered = []

    async def invoke(init):
        spec = TaskSpec(intent="合成任务", platforms=["小红书"])
        if unlimited:
            spec.enable_unlimited_xhs_execution()

        async def planned(state):
            return {"task_spec": spec}

        result = await graph._traced("planner", planned)(init)
        entered.append(graph._execution_timeout.get().when())
        await asyncio.sleep(.3)
        return {**result, "needs_clarification": True}

    monkeypatch.setattr(graph, "_ainvoke", invoke)
    asyncio.run(SchedulerService(schedules).tick(datetime(2026, 1, 2)))
    assert len(entered) == 1
    assert (entered[0] is None) is unlimited
    binding = web.account_execution_binding(owners[0], "schedule", task_id)
    assert binding["state"] == ("idle" if unlimited else "cleanup_failed")
    assert graph._execution_timeout.get() is None


@pytest.mark.asyncio
async def test_concurrent_unlimited_execution_does_not_disable_other_task_timeout(monkeypatch):
    ready = asyncio.Event()
    released = []

    async def invoke(init):
        spec = TaskSpec(intent="合成任务", platforms=["小红书"])
        if init["user_input"] == "unlimited":
            spec.enable_unlimited_xhs_execution()

        async def planned(state):
            return {"task_spec": spec}

        await graph._traced("planner", planned)(init)
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.append(init["user_input"])

    monkeypatch.setattr(graph, "_ainvoke", invoke)

    async def run(name):
        async with asyncio.timeout(.1) as timeout:
            await graph.run_conductor(name, execution_timeout=timeout)

    work = asyncio.create_task(run("unlimited"))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        with pytest.raises(TimeoutError):
            await run("finite")
        assert not work.done()
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert sorted(released) == ["finite", "unlimited"]
        assert graph._execution_timeout.get() is None
    finally:
        if not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
