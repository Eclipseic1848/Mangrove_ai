"""自然调度的固定小并发与执行屏障；仅使用临时库和注入执行器。"""
import asyncio
from datetime import datetime

import pytest

from src.scheduler.service import SchedulerService
from tests.test_account_execution_scheduler import add, fixture
from tests.test_scheduler_owner import owner_stores


NOW = datetime(2026, 9, 6, 1)


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    async def synthetic_notification(*args, **kwargs):
        return None

    monkeypatch.setattr("src.notifications.auto_conductor", synthetic_notification)


@pytest.mark.asyncio
async def test_tick_starts_second_due_task_while_first_waits(fixture):
    _, store, _, authorization = fixture
    ids = [add(store, authorization), add(store, authorization)]
    entered = {task_id: asyncio.Event() for task_id in ids}
    release = asyncio.Event()
    finished = set()

    async def runner(user_input, *, session_id, **kwargs):
        task_id = session_id.removeprefix("scheduler:")
        entered[task_id].set()
        try:
            await release.wait()
            return {"reply": "合成完成"}
        finally:
            finished.add(task_id)

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(NOW))
    try:
        # timeout只限制失败等待；证据是两个执行器都已进入且释放屏障仍关闭。
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 3)
        assert not release.is_set()
        assert all(service.is_running(task_id) for task_id in ids)
        assert not work.done()
    finally:
        release.set()
        count = await asyncio.wait_for(work, 5)
    assert count == 2
    assert finished == set(ids)
    assert all(not service.is_running(task_id) for task_id in ids)
    assert all(store.list_runs(task_id)[0]["state"] == "succeeded" for task_id in ids)


async def _wait_until_not_running(service, task_ids):
    async def settled():
        while any(service.is_running(task_id) for task_id in task_ids):
            await asyncio.sleep(0)

    await asyncio.wait_for(settled(), 5)


@pytest.mark.asyncio
async def test_natural_tick_limits_inflight_work_to_two(fixture):
    _, store, _, authorization = fixture
    ids = [add(store, authorization) for _ in range(3)]
    releases = {task_id: asyncio.Event() for task_id in ids}
    two_entered, three_entered, overflow = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered = []
    active = peak = 0

    async def runner(user_input, *, session_id, **kwargs):
        nonlocal active, peak
        task_id = session_id.removeprefix("scheduler:")
        entered.append(task_id)
        active += 1
        peak = max(peak, active)
        if active > 2:
            overflow.set()
        if len(entered) >= 2:
            two_entered.set()
        if len(entered) == 3:
            three_entered.set()
        try:
            await releases[task_id].wait()
            return {"reply": "合成完成"}
        finally:
            active -= 1

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(NOW))
    try:
        await asyncio.wait_for(two_entered.wait(), 3)
        # 同时保留最终峰值断言；短等待仅检查关闭屏障期间不能进入第三个执行器。
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(overflow.wait(), 0.05)
        assert len(entered) == active == 2
        assert not three_entered.is_set()
        releases[entered[0]].set()
        await asyncio.wait_for(three_entered.wait(), 3)
        assert active == peak == 2
        assert not work.done()
    finally:
        for event in releases.values():
            event.set()
        count = await asyncio.wait_for(work, 5)
    assert count == 3
    assert set(entered) == set(ids) and len(entered) == 3
    assert active == 0 and peak == 2
    assert all(len(store.list_runs(task_id)) == 1 for task_id in ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True], ids=["manual-wait", "manual-background"])
async def test_natural_work_is_not_reclaimed_by_manual_or_future_tick(fixture, background):
    from src.account_execution import execution_context

    _, store, _, authorization = fixture
    task_id = add(store, authorization)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(*args, **kwargs):
        calls.append(kwargs["session_id"])
        entered.set()
        await release.wait()
        return {"reply": "合成完成"}

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(NOW))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with execution_context(authorization):
            assert await service.run_task_now(task_id, background=background) == "running"
        assert await service.tick(datetime(2027, 9, 6, 1)) == 0
        assert len(calls) == len(store.list_runs(task_id)) == 1
        assert store.list_runs(task_id)[0]["state"] == "running"
    finally:
        release.set()
        count = await asyncio.wait_for(work, 5)
    assert count == 1 and len(store.list_runs(task_id)) == 1
    assert not service.is_running(task_id)


@pytest.mark.asyncio
async def test_manual_future_schedule_keeps_its_plan_and_excludes_natural_reclaim(fixture):
    from src.account_execution import execution_context

    _, store, _, authorization = fixture
    task_id = add(store, authorization)
    with execution_context(authorization):
        store.set_status(task_id, "active", next_run_at=datetime(2026, 9, 7, 1))
    planned = store.get(task_id)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(*args, **kwargs):
        calls.append(kwargs["session_id"])
        entered.set()
        await release.wait()
        return {"reply": "合成完成"}

    service = SchedulerService(store, runner=runner)
    assert await service.tick(NOW) == 0
    try:
        with execution_context(authorization):
            started = await service.run_task_now(task_id, background=True)
        assert started["state"] == "running"
        await asyncio.wait_for(entered.wait(), 3)
        assert await service.tick(datetime(2026, 9, 8, 1)) == 0
        with execution_context(authorization):
            assert await service.run_task_now(task_id) == "running"
        assert len(calls) == len(store.list_runs(task_id)) == 1
    finally:
        release.set()
        await _wait_until_not_running(service, [task_id])
        await service.stop()
    saved = store.get(task_id)
    assert (saved["status"], saved["next_run_at"]) == (planned["status"], planned["next_run_at"])
    assert len(store.list_runs(task_id)) == 1 and store.list_runs(task_id)[0]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_concurrent_owner_context_and_revocation_remain_isolated(owner_stores):
    from src.account_execution import current_authorization
    from src.config import user_ctx
    from src.memory.loader import personal_context
    from tests.test_scheduler_owner import _add

    web, store, owners = owner_stores
    authorizations = {owner: web.capture_account_execution(owner) for owner in owners}
    ids = [_add(store, owner) for owner in owners]
    entered = {task_id: asyncio.Event() for task_id in ids}
    release = asyncio.Event()
    observations = {}

    def context():
        return (current_authorization().owner_user_id,
                user_ctx.effective("deepseek_api_key"), personal_context())

    async def runner(user_input, *, session_id, **kwargs):
        task_id = session_id.removeprefix("scheduler:")
        before = context()
        entered[task_id].set()
        await release.wait()
        observations[task_id] = (before, context())
        return {"reply": "合成完成正文"}

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(NOW))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 3)
        web.update_user(owners[0], disabled=True)
        assert await service.reconcile_account_execution(owners[0], authorizations[owners[0]].generation + 1) is False
    finally:
        release.set()
        count = await asyncio.wait_for(work, 5)
    assert count == 2
    for task_id, owner, name in zip(ids, owners, ("alice", "bob")):
        before, after = observations[task_id]
        assert before == after
        assert before[0] == owner and before[1] == f"synthetic-{name}-deepseek_api_key"
        assert f"{name}的个人记忆" in before[2]
        assert ("bob" if name == "alice" else "alice") + "的个人记忆" not in before[2]
    discarded = store.list_runs(ids[0])[0]
    assert discarded["state"] == "cancelled" and discarded["summary"] == "执行授权已失效，结果未保存"
    assert not discarded["report_path"] and not discarded["json_path"]
    assert store.get(ids[0])["last_result"] in (None, "")
    assert web.account_execution_binding(owners[0], "schedule", ids[0])["state"] == "paused"
    assert store.list_runs(ids[1])[0]["state"] == "succeeded"
    assert web.account_execution_binding(owners[1], "schedule", ids[1])["state"] == "idle"


@pytest.mark.asyncio
async def test_unknown_bindings_do_not_replay_or_block_independent_work(fixture):
    web, store, owner, authorization = fixture
    blocked = [add(store, authorization), add(store, authorization)]
    healthy = add(store, authorization)
    for task_id, state in zip(blocked, ("active", "cleanup_failed")):
        task = next(row for row in store.due_tasks(NOW) if row["task_id"] == task_id)
        store.claim_execution(task_id, expected_task=task)
        if state == "cleanup_failed":
            assert web.confirm_account_execution_stopped(
                owner, "schedule", task_id, authorization.generation, cleanup_failed=True,
            )
    calls = []

    async def runner(*args, **kwargs):
        calls.append(kwargs["session_id"])
        return {"reply": "合成完成"}

    service = SchedulerService(store, runner=runner)
    try:
        assert await asyncio.wait_for(service.tick(NOW), 3) == 1
        assert await asyncio.wait_for(service.tick(datetime(2027, 9, 6, 1)), 3) == 0
    finally:
        await service.stop()
    assert calls == [f"scheduler:{healthy}"]
    for task_id, state in zip(blocked, ("active", "cleanup_failed")):
        assert store.list_runs(task_id) == []
        assert web.account_execution_binding(owner, "schedule", task_id)["state"] == state


@pytest.mark.asyncio
async def test_stop_waits_for_natural_and_manual_work_without_cancelling(fixture):
    from src.account_execution import execution_context

    web, store, owner, authorization = fixture
    ids = [add(store, authorization) for _ in range(3)]
    with execution_context(authorization):
        store.set_status(ids[2], "active", next_run_at=datetime(2026, 9, 7, 1))
    entered = {task_id: asyncio.Event() for task_id in ids}
    natural_release, manual_release = asyncio.Event(), asyncio.Event()
    cancelled = set()
    closed = set()

    async def runner(user_input, *, session_id, **kwargs):
        task_id = session_id.removeprefix("scheduler:")
        entered[task_id].set()
        try:
            await (manual_release if task_id == ids[2] else natural_release).wait()
            return {"reply": "合成完成"}
        except asyncio.CancelledError:
            cancelled.add(task_id)
            raise
        finally:
            closed.add(task_id)

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(NOW))
    stopping = None
    stop_results = []
    try:
        await asyncio.wait_for(asyncio.gather(*(entered[task_id].wait() for task_id in ids[:2])), 3)
        with execution_context(authorization):
            assert (await service.run_task_now(ids[2], background=True))["state"] == "running"
        await asyncio.wait_for(entered[ids[2]].wait(), 3)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not stopping.done() and closed == cancelled == set()
        assert all(service.is_running(task_id) for task_id in ids)
        natural_release.set()
        assert await asyncio.wait_for(work, 5) == 2
        assert not stopping.done() and cancelled == set()
        assert service.is_running(ids[2]) and ids[2] not in closed
    finally:
        natural_release.set()
        manual_release.set()
        if stopping is not None:
            stop_results = await asyncio.wait_for(asyncio.gather(stopping, return_exceptions=True), 5)
        await asyncio.wait_for(asyncio.gather(work, return_exceptions=True), 5)
        await _wait_until_not_running(service, ids)
    assert stop_results == [None]
    assert closed == set(ids) and cancelled == set()
    assert all(not service.is_running(task_id) for task_id in ids)
    for task_id in ids:
        assert store.list_runs(task_id)[0]["state"] == "succeeded"
        assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_target", ["tick", "stop"])
async def test_repeated_parent_cancellation_waits_for_workers_and_leaves_queue_unclaimed(fixture, cancel_target):
    web, store, owner, authorization = fixture
    ids = [add(store, authorization) for _ in range(3)]
    entered, closed, cancelled = set(), set(), set()
    two_entered, release = asyncio.Event(), asyncio.Event()

    async def runner(user_input, *, session_id, **kwargs):
        task_id = session_id.removeprefix("scheduler:")
        entered.add(task_id)
        if len(entered) == 2:
            two_entered.set()
        try:
            await release.wait()
            return {"reply": "合成完成"}
        except asyncio.CancelledError:
            cancelled.add(task_id)
            raise
        finally:
            closed.add(task_id)

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(NOW))
    stopping = None
    claimed = set()
    results = []
    try:
        await asyncio.wait_for(two_entered.wait(), 3)
        claimed = set(entered)
        assert len(claimed) == 2
        if cancel_target == "stop":
            stopping = asyncio.create_task(service.stop())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        parent = stopping if cancel_target == "stop" else work
        # 重复取消等待者不能把仍在执行的子任务误报为已停止。
        for _ in range(2):
            parent.cancel()
            await asyncio.sleep(0)
            assert not parent.done()
        assert closed == cancelled == set()
        assert entered == claimed
        assert all(service.is_running(task_id) for task_id in claimed)
        assert all(store.list_runs(task_id) == [] for task_id in set(ids) - claimed)
    finally:
        release.set()
        parents = [work] + ([stopping] if stopping is not None else [])
        results = await asyncio.wait_for(asyncio.gather(*parents, return_exceptions=True), 5)
        await _wait_until_not_running(service, ids)
        await service.stop()
    assert isinstance(results[-1], asyncio.CancelledError)
    if cancel_target == "stop":
        assert results[0] == 2
    assert entered == closed == claimed and cancelled == set()
    for task_id in ids:
        runs = store.list_runs(task_id)
        assert len(runs) == int(task_id in claimed)
        if runs:
            assert runs[0]["state"] == "succeeded"
        assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "idle"


@pytest.mark.asyncio
async def test_overlapping_ticks_share_two_slots_for_disjoint_due_snapshots(fixture, monkeypatch):
    _, store, _, authorization = fixture
    ids = [add(store, authorization) for _ in range(4)]
    rows = {row["task_id"]: row for row in store.due_tasks(NOW)}
    snapshots = [[rows[task_id] for task_id in ids[:2]], [rows[task_id] for task_id in ids[2:]]]
    snapshot_calls = []
    second_snapshot = asyncio.Event()

    def due_subset(*, now):
        snapshot = snapshots[len(snapshot_calls)]
        snapshot_calls.append(snapshot)
        if len(snapshot_calls) == 2:
            second_snapshot.set()
        return snapshot

    monkeypatch.setattr(store, "due_tasks", due_subset)
    first_release, second_release = asyncio.Event(), asyncio.Event()
    two_entered, four_entered, overflow = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered, finished = [], set()
    active = peak = 0

    async def runner(user_input, *, session_id, **kwargs):
        nonlocal active, peak
        task_id = session_id.removeprefix("scheduler:")
        entered.append(task_id)
        active += 1
        peak = max(peak, active)
        if active > 2:
            overflow.set()
        if len(entered) == 2:
            two_entered.set()
        if len(entered) == 4:
            four_entered.set()
        try:
            await (first_release if task_id in ids[:2] else second_release).wait()
            return {"reply": "合成完成"}
        finally:
            active -= 1
            finished.add(task_id)

    service = SchedulerService(store, runner=runner)
    first = asyncio.create_task(service.tick(NOW))
    second = None
    results = []
    try:
        await asyncio.wait_for(two_entered.wait(), 3)
        second = asyncio.create_task(service.tick(NOW))
        await asyncio.wait_for(second_snapshot.wait(), 3)
        # 第二轮确已拿到不同快照；第一轮未释放时仍不能多启动执行器。
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(overflow.wait(), 0.05)
        assert set(entered) == set(ids[:2]) and active == peak == 2
        first_release.set()
        await asyncio.wait_for(four_entered.wait(), 3)
        assert active == peak == 2
    finally:
        first_release.set()
        second_release.set()
        ticks = [first] + ([second] if second is not None else [])
        results = await asyncio.wait_for(asyncio.gather(*ticks, return_exceptions=True), 5)
    assert results == [2, 2]
    assert snapshot_calls == snapshots
    assert len(entered) == 4 and set(entered) == finished == set(ids)
    assert active == 0 and peak == 2
    for task_id in ids:
        runs = store.list_runs(task_id)
        assert len(runs) == 1 and runs[0]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_claim_failure_does_not_block_an_independent_due_task(fixture, monkeypatch):
    web, store, owner, authorization = fixture
    broken, healthy = [add(store, authorization) for _ in range(2)]
    real_claim = store.claim_execution
    attempted, entered = [], []

    def claim(task_id, *args, **kwargs):
        attempted.append(task_id)
        if task_id == broken:
            raise RuntimeError("合成领取失败")
        return real_claim(task_id, *args, **kwargs)

    monkeypatch.setattr(store, "claim_execution", claim)

    async def runner(user_input, *, session_id, **kwargs):
        entered.append(session_id.removeprefix("scheduler:"))
        return {"reply": "合成完成"}

    service = SchedulerService(store, runner=runner)
    try:
        assert await asyncio.wait_for(service.tick(NOW), 3) == 1
    finally:
        await service.stop()
    assert sorted(attempted) == sorted([broken, healthy])
    assert entered == [healthy]
    assert store.list_runs(broken) == []
    runs = store.list_runs(healthy)
    assert len(runs) == 1 and runs[0]["state"] == "succeeded"
    assert store.get(broken)["status"] == "active"
    for task_id in (broken, healthy):
        assert not service.is_running(task_id)
        assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "idle"
