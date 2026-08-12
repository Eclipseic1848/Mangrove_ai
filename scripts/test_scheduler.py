#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scheduler 模块单元测试（无 pytest，纯断言；失败抛异常并以非零退出）。

运行：python scripts/test_scheduler.py
覆盖：cron 纯逻辑（解析/匹配/续算）、schedule 串解析、store 持久化（SQLite 临时库）。
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 确保可导入项目 src
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.scheduler import cron


def test_parse_cron_field():
    assert cron.parse_cron_field("*", 0, 59) == set(range(0, 60))
    assert cron.parse_cron_field("*/15", 0, 59) == {0, 15, 30, 45}
    assert cron.parse_cron_field("1,3,5", 0, 6) == {1, 3, 5}
    assert cron.parse_cron_field("9-11", 0, 23) == {9, 10, 11}
    for bad in ("", "*/0", "5-1", "60", "-1"):
        try:
            cron.parse_cron_field(bad, 0, 59)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法 cron 字段应报错: {bad!r}")


def test_cron_matches():
    # 周一三五 09:30
    expr = "30 9 * * 1,3,5"
    assert cron.cron_matches(expr, datetime(2026, 6, 22, 9, 30))  # 2026-06-22 是周一
    assert cron.cron_matches(expr, datetime(2026, 6, 24, 9, 30))  # 周三
    assert cron.cron_matches(expr, datetime(2026, 6, 26, 9, 30))  # 周五
    assert not cron.cron_matches(expr, datetime(2026, 6, 23, 9, 30))  # 周二
    assert not cron.cron_matches(expr, datetime(2026, 6, 22, 9, 31))  # 分钟不符
    # 周日 0 与 7 等价
    assert cron.cron_matches("0 0 * * 0", datetime(2026, 6, 28, 0, 0))  # 周日
    assert cron.cron_matches("0 0 * * 7", datetime(2026, 6, 28, 0, 0))
    try:
        cron.cron_matches("1 2 3", datetime(2026, 6, 22))  # 段数不对
    except ValueError:
        pass
    else:
        raise AssertionError("cron 段数不足应报错")


def test_next_cron_time():
    expr = "30 9 * * 1,3,5"
    # 从周一 09:30 之后算，下次应是周三 09:30
    nxt = cron.next_cron_time(expr, datetime(2026, 6, 22, 9, 30))
    assert nxt == datetime(2026, 6, 24, 9, 30), nxt
    # 从周一 08:00 算，下次应是当天 09:30
    nxt2 = cron.next_cron_time(expr, datetime(2026, 6, 22, 8, 0))
    assert nxt2 == datetime(2026, 6, 22, 9, 30), nxt2


def test_parse_schedule():
    # once
    s = cron.parse_schedule("once@2026-06-25T09:00")
    assert s.trigger_type == "once"
    assert s.run_at == datetime(2026, 6, 25, 9, 0)
    assert s.cron_expr is None
    # cron
    c = cron.parse_schedule("cron@30 9 * * 1,3,5")
    assert c.trigger_type == "cron"
    assert c.cron_expr == "30 9 * * 1,3,5"
    assert c.run_at is None
    # 容错：裸 cron 串（无前缀，5 段）按 cron 处理
    c2 = cron.parse_schedule("0 8 * * *")
    assert c2.trigger_type == "cron" and c2.cron_expr == "0 8 * * *"
    # 非法
    for bad in ("", "cron@bad", "once@notadate", "weekly@x"):
        try:
            cron.parse_schedule(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法 schedule 应报错: {bad!r}")


def test_compute_next_run():
    # once 在未来 → 返回该时刻；已过 → None
    s_future = cron.parse_schedule("once@2999-01-01T00:00")
    assert cron.compute_next_run(s_future, datetime(2026, 6, 22)) == datetime(2999, 1, 1, 0, 0)
    s_past = cron.parse_schedule("once@2000-01-01T00:00")
    assert cron.compute_next_run(s_past, datetime(2026, 6, 22)) is None
    # cron → 下次匹配
    c = cron.parse_schedule("cron@30 9 * * 1,3,5")
    assert cron.compute_next_run(c, datetime(2026, 6, 22, 8, 0)) == datetime(2026, 6, 22, 9, 30)


def test_store_crud():
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "sched.db"
        store = ScheduleStore(str(db))
        # 新增一个 cron 任务
        tid = store.add(
            user_input="抓某招投标网标讯并提炼摘要",
            provider="deepseek",
            model="deepseek-chat",
            trigger_type="cron",
            cron_expr="30 9 * * 1,3,5",
            run_at=None,
            next_run_at=datetime(2026, 6, 22, 9, 30),
        )
        assert tid
        # 列出
        tasks = store.list_active()
        assert len(tasks) == 1 and tasks[0]["task_id"] == tid
        # 到点任务查询（截止时间晚于 next_run_at）
        due = store.due_tasks(now=datetime(2026, 6, 22, 9, 31))
        assert len(due) == 1
        none_due = store.due_tasks(now=datetime(2026, 6, 22, 9, 0))
        assert len(none_due) == 0
        # 记录一次运行结果并续算下次
        store.mark_run(tid, success=True, result="ok", next_run_at=datetime(2026, 6, 24, 9, 30))
        row = store.get(tid)
        assert row["last_success"] == 1 and row["last_result"] == "ok"
        assert row["next_run_at"] == "2026-06-24T09:30:00"
        # 取消
        store.cancel(tid)
        assert store.list_active() == []
        assert store.get(tid)["status"] == "cancelled"


def test_parse_schedule_every():
    """按间隔触发：every@<秒数>（手动创建任务中心新增的三种触发方式之一）。"""
    s = cron.parse_schedule("every@7200")
    assert s.trigger_type == "interval"
    assert s.interval_seconds == 7200
    assert s.cron_expr is None and s.run_at is None
    for bad in ("every@0", "every@-5", "every@abc"):
        try:
            cron.parse_schedule(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法 interval 调度串应报错: {bad!r}")


def test_compute_next_run_interval():
    """interval 首次触发 = from_dt + interval_seconds。"""
    s = cron.parse_schedule("every@3600")
    nxt = cron.compute_next_run(s, datetime(2026, 7, 13, 10, 0))
    assert nxt == datetime(2026, 7, 13, 11, 0), nxt


def test_compute_next_run_date_range_clamp():
    """生效日期区间：算出的下次时刻早于 start_date 则钳到 start_date；晚于 end_date 则返回 None。"""
    c = cron.parse_schedule("cron@0 8 * * *")
    # 算出 2026-07-14 08:00，但 start_date 定在 07-20 之后 -> 钳到 07-20 08:00... 实际按当天 00:00 起算
    nxt = cron.compute_next_run(c, datetime(2026, 7, 13, 7, 0), start_date="2026-07-20", end_date=None)
    assert nxt is not None and nxt >= datetime(2026, 7, 20, 0, 0), nxt
    # end_date 已过 -> 无后续
    nxt2 = cron.compute_next_run(c, datetime(2026, 7, 13, 7, 0), start_date=None, end_date="2026-07-10")
    assert nxt2 is None, nxt2
    # 区间内正常 -> 不受影响
    nxt3 = cron.compute_next_run(c, datetime(2026, 7, 13, 7, 0), start_date="2026-07-01", end_date="2026-12-31")
    assert nxt3 == datetime(2026, 7, 13, 8, 0), nxt3


def test_store_add_with_name_source_and_new_fields():
    """手动/模板创建需要落 name/source/interval_seconds/start_date/end_date。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(
            user_input="每2小时抓一次新闻", provider=None, model=None,
            trigger_type="interval", cron_expr=None, run_at=None,
            next_run_at=datetime(2026, 7, 13, 10, 0),
            name="行业新闻监控", source="manual",
            interval_seconds=7200, start_date="2026-07-13", end_date="2026-08-13",
        )
        row = store.get(tid)
        assert row["name"] == "行业新闻监控"
        assert row["source"] == "manual"
        assert row["interval_seconds"] == 7200
        assert row["start_date"] == "2026-07-13"
        assert row["end_date"] == "2026-08-13"
        # 旧调用方式（无新字段）仍要能用，source 应有默认值 'auto'
        tid2 = store.add(user_input="老式自动创建", provider=None, model=None,
                          trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                          next_run_at=datetime(2026, 7, 14, 8, 0))
        row2 = store.get(tid2)
        assert row2["source"] == "auto"
        assert row2["name"] is None


def test_store_list_active_includes_paused():
    """暂停中的任务仍属于“进行中”，应出现在任务中心列表（前端靠 status 渲染开关）。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="任务A", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 14, 8, 0))
        store.set_status(tid, "paused")
        tasks = store.list_active()
        assert len(tasks) == 1 and tasks[0]["status"] == "paused"
        # cancelled/done 仍不出现
        store.set_status(tid, "cancelled")
        assert store.list_active() == []


def test_store_set_status_pause_resume():
    """暂停：只改 status，不动 next_run_at；恢复：改回 active 并可带新的 next_run_at。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="任务B", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 14, 8, 0))
        assert store.set_status(tid, "paused") is True
        row = store.get(tid)
        assert row["status"] == "paused" and row["next_run_at"] == "2026-07-14T08:00:00"
        # 暂停后 due_tasks 不会再选中它（即便到点）
        assert store.due_tasks(now=datetime(2026, 7, 14, 9, 0)) == []
        assert store.set_status(tid, "active", next_run_at=datetime(2026, 7, 21, 8, 0)) is True
        row2 = store.get(tid)
        assert row2["status"] == "active" and row2["next_run_at"] == "2026-07-21T08:00:00"
        # 不存在的任务返回 False
        assert store.set_status("no_such_id", "paused") is False


def test_store_edit_replaces_fields():
    """编辑：整体替换触发方式/文案/生效区间，重算的 next_run_at 一并写入。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="旧文案", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 14, 8, 0), name="旧名称")
        ok = store.edit(
            tid, name="新名称", user_input="新文案", provider="deepseek", model="deepseek-chat",
            trigger_type="interval", cron_expr=None, interval_seconds=3600, run_at=None,
            next_run_at=datetime(2026, 7, 13, 12, 0), start_date="2026-07-13", end_date=None,
        )
        assert ok is True
        row = store.get(tid)
        assert row["name"] == "新名称" and row["user_input"] == "新文案"
        assert row["trigger_type"] == "interval" and row["cron_expr"] is None
        assert row["interval_seconds"] == 3600
        assert row["next_run_at"] == "2026-07-13T12:00:00"
        assert row["start_date"] == "2026-07-13" and row["end_date"] is None
        assert store.edit("no_such_id", name="x", user_input="x", provider=None, model=None,
                           trigger_type="cron", cron_expr="0 8 * * *", interval_seconds=None,
                           run_at=None, next_run_at=None, start_date=None, end_date=None) is False


def test_store_list_recent_runs():
    """运行记录 Tab：跨任务聚合执行历史，新→旧，带任务名便于展示。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid1 = store.add(user_input="任务A", provider=None, model=None,
                          trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                          next_run_at=datetime(2026, 7, 14, 8, 0), name="任务A",
                          owner_user_id="u1")
        tid2 = store.add(user_input="任务B", provider=None, model=None,
                          trigger_type="cron", cron_expr="0 9 * * *", run_at=None,
                          next_run_at=datetime(2026, 7, 14, 9, 0), name="任务B",
                          owner_user_id="u1")
        store.add_run(tid1, success=True, summary="ok1")
        store.add_run(tid2, success=False, summary="fail1")
        recent = store.list_recent_runs(owner_user_id="u1")
        assert len(recent) == 2
        assert recent[0]["run_id"] > recent[1]["run_id"], "应按新→旧排序"
        names = {r["task_name"] for r in recent}
        assert names == {"任务A", "任务B"}
        # 隔离：别的用户看不到
        assert store.list_recent_runs(owner_user_id="u2") == []


def test_list_recent_runs_filters_and_pagination():
    """运行记录筛选（按任务/按成败/按摘要关键词）+ 分页（limit/offset）+ 配套计数。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid1 = store.add(user_input="任务A", provider=None, model=None,
                          trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                          next_run_at=datetime(2026, 7, 14, 8, 0), name="任务A",
                          owner_user_id="u1")
        tid2 = store.add(user_input="任务B", provider=None, model=None,
                          trigger_type="cron", cron_expr="0 9 * * *", run_at=None,
                          next_run_at=datetime(2026, 7, 14, 9, 0), name="任务B",
                          owner_user_id="u1")
        store.add_run(tid1, success=True, summary="report=x.md")
        store.add_run(tid1, success=False, summary="采集失败：连接超时")
        store.add_run(tid2, success=True, summary="report=y.md")

        # 按任务筛选
        only_tid1 = store.list_recent_runs(owner_user_id="u1", task_id=tid1)
        assert len(only_tid1) == 2 and all(r["task_id"] == tid1 for r in only_tid1)

        # 按成败筛选
        only_failed = store.list_recent_runs(owner_user_id="u1", success=False)
        assert len(only_failed) == 1 and "超时" in only_failed[0]["summary"]

        # 按摘要关键词
        matched = store.list_recent_runs(owner_user_id="u1", q="超时")
        assert len(matched) == 1
        assert store.list_recent_runs(owner_user_id="u1", q="不存在的词") == []

        # 分页：limit/offset
        page1 = store.list_recent_runs(owner_user_id="u1", limit=2, offset=0)
        page2 = store.list_recent_runs(owner_user_id="u1", limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 1
        assert {r["run_id"] for r in page1} & {r["run_id"] for r in page2} == set()

        # 计数：与筛选条件同构
        assert store.count_recent_runs(owner_user_id="u1") == 3
        assert store.count_recent_runs(owner_user_id="u1", task_id=tid1) == 2
        assert store.count_recent_runs(owner_user_id="u1", success=False) == 1
        assert store.count_recent_runs(owner_user_id="u2") == 0


def test_task_templates_well_formed():
    """自动化任务模板：字段齐全、id 唯一、cron 模板带合法 cron_expr。"""
    from src.scheduler.templates import TASK_TEMPLATES

    assert len(TASK_TEMPLATES) >= 6, "模板库应有一定数量的场景化预设"
    ids = [t["id"] for t in TASK_TEMPLATES]
    assert len(ids) == len(set(ids)), "模板 id 不应重复"
    for t in TASK_TEMPLATES:
        assert t.get("name") and t.get("description") and t.get("prompt")
        assert t.get("trigger_type") in ("cron", "once")
        if t["trigger_type"] == "cron":
            assert t.get("cron_expr"), f"{t['id']} 是 cron 模板但缺 cron_expr"
            cron.cron_matches(t["cron_expr"], datetime.now())  # 校验合法（非法抛异常）


def test_get_template():
    from src.scheduler.templates import get_template

    found = get_template("one_time_collection")
    assert found is not None and found["trigger_type"] == "once"
    assert get_template("no_such_template") is None


def test_store_mark_run_keep_schedule():
    """立即执行专用：只记 last_*/run_count，不动 next_run_at/status（供 run_task_now 使用）。"""
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="任务C", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 14, 8, 0))
        store.set_status(tid, "paused")  # 暂停中的任务也可能被立即执行一次
        store.mark_run_keep_schedule(tid, success=True, result="report=x.md")
        row = store.get(tid)
        assert row["status"] == "paused", "立即执行不应把暂停任务改回 active"
        assert row["next_run_at"] == "2026-07-14T08:00:00", "立即执行不应改动原定下次时刻"
        assert row["run_count"] == 1 and row["last_success"] == 1
        assert row["last_result"] == "report=x.md"


def test_run_one_interval_reschedule():
    """interval 型任务续算：next = 执行时刻 + interval_seconds。"""
    import asyncio

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="每2小时", provider=None, model=None,
                         trigger_type="interval", cron_expr=None, run_at=None,
                         next_run_at=datetime(2026, 7, 13, 10, 0), interval_seconds=7200)

        async def stub_runner(user_input, **kw):
            return {"reply": "done"}

        svc = SchedulerService(store=store, poll_interval=1.0, runner=stub_runner)
        asyncio.run(svc._run_one(store.get(tid), datetime(2026, 7, 13, 10, 0, 5)))
        row = store.get(tid)
        assert row["status"] == "active"
        assert row["next_run_at"] == "2026-07-13T12:00:05", row["next_run_at"]


def test_run_one_end_date_expiry():
    """end_date 已过 -> 续算返回 None -> 任务置 done（不再被 due_tasks 选中）。"""
    import asyncio

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="限时监控", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 13, 8, 0), end_date="2026-07-13")

        async def stub_runner(user_input, **kw):
            return {"reply": "done"}

        svc = SchedulerService(store=store, poll_interval=1.0, runner=stub_runner)
        asyncio.run(svc._run_one(store.get(tid), datetime(2026, 7, 13, 8, 0, 5)))
        row = store.get(tid)
        assert row["status"] == "done", row["status"]
        assert row["next_run_at"] is None


def test_run_task_now_keeps_schedule_and_returns_started():
    """立即执行：成功返回 started，last_* 更新，但不改 next_run_at/status。"""
    import asyncio

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="任务D", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 20, 8, 0))

        async def stub_runner(user_input, **kw):
            return {"outputs": {"report_md": "r.md"}}

        svc = SchedulerService(store=store, poll_interval=1.0, runner=stub_runner)
        outcome = asyncio.run(svc.run_task_now(tid))
        assert outcome == "started", outcome
        row = store.get(tid)
        assert row["next_run_at"] == "2026-07-20T08:00:00", "立即执行不应改动原定下次时刻"
        assert row["run_count"] == 1
        runs = store.list_runs(tid)
        assert len(runs) == 1, "立即执行也应留一条执行历史"


def test_run_task_now_not_found():
    import asyncio

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        svc = SchedulerService(store=store, poll_interval=1.0, runner=lambda *a, **k: None)
        assert asyncio.run(svc.run_task_now("no_such_id")) == "not_found"


def test_run_task_now_concurrent_guard():
    """定时到点与手动触发撞车：同一任务正在执行时，另一次触发应跳过而不是并发重跑。"""
    import asyncio

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="任务E", provider=None, model=None,
                         trigger_type="cron", cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime(2026, 7, 20, 8, 0))
        svc = SchedulerService(store=store, poll_interval=1.0, runner=lambda *a, **k: None)
        svc._running_ids.add(tid)  # 模拟 tick() 正在执行该任务
        assert asyncio.run(svc.run_task_now(tid)) == "running"
        assert store.get(tid)["run_count"] == 0, "应跳过，不重复执行"


def test_route_after_planner():
    import asyncio

    from src.conductor.graph import _route_after_planner
    from src.conductor.nodes.schedule import schedule_node
    from src.conductor.task_spec import TaskSpec

    spec_sched = TaskSpec(intent="周期抓标讯", schedule="cron@30 9 * * 1,3,5")
    spec_plain = TaskSpec(intent="抓网页")

    # 带 schedule 且非调度器触发 → schedule
    assert _route_after_planner({"task_spec": spec_sched}) == "schedule"
    # 调度器触发和普通任务都先解析显式目标，再进入 router 跑完整流程。
    assert _route_after_planner({"task_spec": spec_sched, "ignore_schedule": True}) == "target_resolve"
    assert _route_after_planner({"task_spec": spec_plain}) == "target_resolve"

    # schedule 节点：透传原始 schedule 串并给出回执
    out = asyncio.run(schedule_node({"task_spec": spec_sched}))
    assert out["schedule_request"] == "cron@30 9 * * 1,3,5"
    assert out.get("reply")


def test_assess_real_success():
    """成败判定：流程内 error/需澄清/零产出都不算成功（修"上次成功"误报）。"""
    from src.scheduler.service import SchedulerService

    assess = SchedulerService._assess
    # 流程内部错误（不抛异常、写在 state）→ 失败
    ok, msg = assess({"error": "所有采集器均未取得数据"})
    assert not ok and "error" in msg
    # 被追问澄清（定时执行无人应答）→ 失败
    ok, msg = assess({"needs_clarification": True, "clarification_question": "要采集多少条？"})
    assert not ok and "澄清" in msg
    # 零产出（无报告/无数据/无回复）→ 失败
    ok, msg = assess({})
    assert not ok and "无产出" in msg
    # 有报告产出 → 成功且摘要含路径
    ok, msg = assess({"outputs": {"report_md": "downloads/x/report.md", "json": "downloads/x/data.json"}})
    assert ok and "report=" in msg and "json=" in msg
    # 仅有文本回复 → 成功（部分任务合法只回文本）
    ok, msg = assess({"reply": "已完成汇总"})
    assert ok and "已完成汇总" in msg


def test_run_one_marks_failure_and_catchup():
    """_run_one 落库：流程内失败记 last_success=0；错过补跑加 [补跑] 标注。"""
    import asyncio
    from datetime import timedelta

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    async def _case(runner_result, next_run_at, now):
        with tempfile.TemporaryDirectory() as d:
            store = ScheduleStore(str(Path(d) / "s.db"))
            tid = store.add(user_input="测试任务", provider=None, model=None,
                            trigger_type="cron", cron_expr="0 8 * * *",
                            run_at=None, next_run_at=next_run_at)

            async def stub_runner(user_input, **kw):
                return runner_result

            svc = SchedulerService(store=store, poll_interval=1.0, runner=stub_runner)
            task = store.get(tid)
            await svc._run_one(task, now)
            return store.get(tid)

    # 场景1：按时执行但流程内报错 → last_success=0、错误入 last_error
    sched = datetime(2026, 7, 6, 8, 0)
    row = asyncio.run(_case({"error": "采集失败"}, sched, sched + timedelta(seconds=30)))
    assert row["last_success"] == 0, "流程内 error 不应记成功"
    assert "error" in (row["last_error"] or "")
    assert "补跑" not in (row["last_error"] or ""), "按时执行不应标注补跑"

    # 场景2：错过 2 小时后补跑且成功 → last_success=1、结果带 [补跑] 标注
    row2 = asyncio.run(_case({"outputs": {"report_md": "r.md"}}, sched, sched + timedelta(hours=2)))
    assert row2["last_success"] == 1
    assert "补跑" in (row2["last_result"] or ""), f"应标注补跑，实际：{row2['last_result']}"

    # 场景3：补跑但零产出 → last_success=0 且错误里同时有补跑标注与原因
    row3 = asyncio.run(_case({}, sched, sched + timedelta(hours=2)))
    assert row3["last_success"] == 0
    assert "补跑" in (row3["last_error"] or "") and "无产出" in (row3["last_error"] or "")


def test_run_history():
    """执行历史表：add_run/list_runs/get_run + _run_one 每次执行落一行。"""
    import asyncio

    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "s.db"))
        tid = store.add(user_input="历史测试", provider=None, model=None,
                        trigger_type="cron", cron_expr="0 8 * * *",
                        run_at=None, next_run_at=datetime(2026, 7, 6, 8, 0))

        async def stub_runner(user_input, **kw):
            return {"outputs": {"report_md": "downloads/x/report.md", "json": "downloads/x/data.json"}}

        svc = SchedulerService(store=store, poll_interval=1.0, runner=stub_runner)
        asyncio.run(svc._run_one(store.get(tid), datetime(2026, 7, 6, 8, 0, 30)))
        asyncio.run(svc._run_one(store.get(tid), datetime(2026, 7, 7, 8, 0, 30)))

        runs = store.list_runs(tid)
        assert len(runs) == 2, f"两次执行应有两条历史，实际 {len(runs)}"
        assert runs[0]["run_id"] > runs[1]["run_id"], "应按新→旧排序"
        assert all(r["report_path"] == "downloads/x/report.md" for r in runs)
        assert all(r["json_path"] == "downloads/x/data.json" for r in runs)
        one = store.get_run(tid, runs[0]["run_id"])
        assert one and one["success"] == 1
        # 越权防护：task_id 不匹配取不到
        assert store.get_run("other_task", runs[0]["run_id"]) is None


def test_run_history_backfill():
    """runs 表上线前的老任务：初始化时把 last_* 那次回填为一条历史。"""
    import sqlite3

    from src.scheduler.store import ScheduleStore

    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "s.db")
        # 先建一个"老版本"库：只有主表 + 一条已执行过的任务
        store = ScheduleStore(db)
        tid = store.add(user_input="老任务", provider=None, model=None,
                        trigger_type="cron", cron_expr="0 9 * * *",
                        run_at=None, next_run_at=datetime(2026, 7, 7, 9, 0))
        store.mark_run(tid, success=True,
                       result="report=D:\\x\\report.md; json=D:\\x\\data.json",
                       next_run_at=datetime(2026, 7, 8, 9, 0))
        # 模拟旧库：清空 runs 表（相当于 runs 表上线前的状态）
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM scheduled_task_runs")
        conn.commit()
        conn.close()
        # 重新初始化（新版本启动）→ 回填
        store2 = ScheduleStore(db)
        runs = store2.list_runs(tid)
        assert len(runs) == 1, "应回填一条历史"
        assert runs[0]["report_path"] == "D:\\x\\report.md"
        assert runs[0]["json_path"] == "D:\\x\\data.json"
        # 再次初始化不重复回填
        ScheduleStore(db)
        assert len(store2.list_runs(tid)) == 1, "回填应幂等"


def main():
    tests = [
        test_parse_cron_field,
        test_cron_matches,
        test_next_cron_time,
        test_parse_schedule,
        test_compute_next_run,
        test_parse_schedule_every,
        test_compute_next_run_interval,
        test_compute_next_run_date_range_clamp,
        test_store_crud,
        test_store_add_with_name_source_and_new_fields,
        test_store_list_active_includes_paused,
        test_store_set_status_pause_resume,
        test_store_edit_replaces_fields,
        test_store_list_recent_runs,
        test_list_recent_runs_filters_and_pagination,
        test_task_templates_well_formed,
        test_get_template,
        test_store_mark_run_keep_schedule,
        test_run_one_interval_reschedule,
        test_run_one_end_date_expiry,
        test_run_task_now_keeps_schedule_and_returns_started,
        test_run_task_now_not_found,
        test_run_task_now_concurrent_guard,
        test_route_after_planner,
        test_assess_real_success,
        test_run_one_marks_failure_and_catchup,
        test_run_history,
        test_run_history_backfill,
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
