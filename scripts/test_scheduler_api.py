#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务中心 API 路由单测（manual 创建/PATCH 暂停恢复编辑/run_now/templates/runs-recent）。

同项目既有风格（见 test_library_route_permissions.py）：不起 TestClient，直接调用路由
函数——FastAPI 直接函数调用不会触发 Depends()，调用时把 user/body 当成普通参数传入即可。
用 monkeypatch 替换 src.api.services 模块里的 store/service 单例，指向临时 SQLite 库。

运行：python scripts/test_scheduler_api.py
"""
import asyncio
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.api.services as services_module  # noqa: E402
from src.api.routes import tasks as tasks_routes  # noqa: E402
from src.api.schemas import ManualTaskIn, ScheduleIn, TaskPatchIn, TriggerIn  # noqa: E402
from src.api.session_store import pending_store  # noqa: E402
from src.scheduler.service import SchedulerService  # noqa: E402
from src.scheduler.store import ScheduleStore  # noqa: E402
from tests.database_migration_helpers import migrated_profile_database  # noqa: E402

USER = {"user_id": "u1", "username": "u1"}
OTHER = {"user_id": "u2", "username": "u2"}


async def _stub_runner(user_input, **kw):
    return {"outputs": {"report_md": "r.md"}}


def _fresh_store(tmp_dir: str) -> ScheduleStore:
    database = migrated_profile_database(
        Path(tmp_dir) / "s.db", profile="scheduler"
    )
    store = ScheduleStore(str(database))
    services_module._store = store
    services_module._service = SchedulerService(store, poll_interval=1.0, runner=_stub_runner)
    return store


def _status_code(e: Exception):
    return getattr(e, "status_code", None)


def test_create_manual_task_cron():
    with tempfile.TemporaryDirectory() as d:
        _fresh_store(d)
        body = ManualTaskIn(
            name="行业新闻早报", prompt="采集AI行业新闻并汇总",
            trigger=TriggerIn(type="cron", cron_expr="30 8 * * *"),
        )
        res = tasks_routes.create_manual_task(body, user=USER)
        assert res["ok"] is True and res["task_id"]
        task = services_module.get_schedule_store().get(res["task_id"])
        assert task["name"] == "行业新闻早报" and task["source"] == "manual"
        assert task["trigger_type"] == "cron"


def test_create_manual_task_interval():
    with tempfile.TemporaryDirectory() as d:
        _fresh_store(d)
        body = ManualTaskIn(
            name="每2小时监控", prompt="每2小时抓一次新闻",
            trigger=TriggerIn(type="interval", interval_seconds=7200),
        )
        res = tasks_routes.create_manual_task(body, user=USER)
        task = services_module.get_schedule_store().get(res["task_id"])
        assert task["trigger_type"] == "interval" and task["interval_seconds"] == 7200


def test_create_manual_task_once():
    with tempfile.TemporaryDirectory() as d:
        _fresh_store(d)
        future = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
        body = ManualTaskIn(
            name="单次采集", prompt="采集一次某主题",
            trigger=TriggerIn(type="once", run_at=future),
        )
        res = tasks_routes.create_manual_task(body, user=USER)
        task = services_module.get_schedule_store().get(res["task_id"])
        assert task["trigger_type"] == "once"


def test_create_manual_task_from_template_marks_source_template():
    with tempfile.TemporaryDirectory() as d:
        _fresh_store(d)
        body = ManualTaskIn(
            name="每日竞品口碑日报", prompt="采集汽车之家上小米SU7的最新评论并输出口碑分析",
            trigger=TriggerIn(type="cron", cron_expr="0 9 * * *"),
            template_id="daily_voc_report",
        )
        res = tasks_routes.create_manual_task(body, user=USER)
        task = services_module.get_schedule_store().get(res["task_id"])
        assert task["source"] == "template"


def test_create_manual_task_past_once_rejected():
    with tempfile.TemporaryDirectory() as d:
        _fresh_store(d)
        body = ManualTaskIn(name="过期任务", prompt="x",
                             trigger=TriggerIn(type="once", run_at="2000-01-01T00:00"))
        try:
            tasks_routes.create_manual_task(body, user=USER)
        except Exception as e:
            assert _status_code(e) == 422, e
        else:
            raise AssertionError("已过去的单次时间应报错")


def test_create_task_from_pending_sets_auto_name_source():
    """既有语义自动创建链路：落库应补 source='auto'，name 取 planner 概括的 intent。"""
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        pending_store.put("u1", "task123", {"schedule": {
            "schedule": "cron@0 8 * * *", "user_input": "每天8点抓新闻",
            "provider": "deepseek", "model": "deepseek-chat", "intent": "每日新闻抓取",
        }})
        res = tasks_routes.create_task(ScheduleIn(task_id="task123"), user=USER)
        task = store.get(res["task_id"])
        assert task["source"] == "auto"
        assert task["name"] == "每日新闻抓取"


def test_patch_pause_resume():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1")
        tasks_routes.update_task(tid, TaskPatchIn(status="paused"), user=USER)
        assert store.get(tid)["status"] == "paused"
        tasks_routes.update_task(tid, TaskPatchIn(status="active"), user=USER)
        assert store.get(tid)["status"] == "active"


def test_patch_pause_other_user_forbidden():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1")
        try:
            tasks_routes.update_task(tid, TaskPatchIn(status="paused"), user=OTHER)
        except Exception as e:
            assert _status_code(e) == 404, e
        else:
            raise AssertionError("非本人任务应 404")


def test_patch_edit_fields():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="旧文案", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1",
                         name="旧名称")
        body = TaskPatchIn(name="新名称", prompt="新文案",
                            trigger=TriggerIn(type="interval", interval_seconds=1800))
        res = tasks_routes.update_task(tid, body, user=USER)
        assert res["ok"] is True
        row = store.get(tid)
        assert row["name"] == "新名称" and row["user_input"] == "新文案"
        assert row["trigger_type"] == "interval" and row["interval_seconds"] == 1800


def test_list_templates():
    with tempfile.TemporaryDirectory() as d:
        _fresh_store(d)
        res = tasks_routes.list_templates(user=USER)
        ids = {t["id"] for t in res}
        assert "daily_voc_report" in ids


def test_recent_runs():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1",
                         name="任务A")
        store.add_run(tid, success=True, summary="ok")
        res = tasks_routes.recent_runs(user=USER)
        assert res["total"] == 1
        assert len(res["items"]) == 1 and res["items"][0]["task_name"] == "任务A"


def test_recent_runs_filters_and_pagination():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid1 = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                          cron_expr="0 8 * * *", run_at=None,
                          next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1",
                          name="任务A")
        tid2 = store.add(user_input="y", provider=None, model=None, trigger_type="cron",
                          cron_expr="0 9 * * *", run_at=None,
                          next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1",
                          name="任务B")
        store.add_run(tid1, success=True, summary="report=x.md")
        store.add_run(tid1, success=False, summary="采集失败：超时")
        store.add_run(tid2, success=True, summary="report=y.md")

        by_task = tasks_routes.recent_runs(task_id=tid1, success=None, q=None, limit=20, offset=0, user=USER)
        assert by_task["total"] == 2 and all(i["task_id"] == tid1 for i in by_task["items"])

        by_success = tasks_routes.recent_runs(task_id=None, success=False, q=None, limit=20, offset=0, user=USER)
        assert by_success["total"] == 1

        by_q = tasks_routes.recent_runs(task_id=None, success=None, q="超时", limit=20, offset=0, user=USER)
        assert by_q["total"] == 1

        page1 = tasks_routes.recent_runs(task_id=None, success=None, q=None, limit=2, offset=0, user=USER)
        page2 = tasks_routes.recent_runs(task_id=None, success=None, q=None, limit=2, offset=2, user=USER)
        assert page1["total"] == 3
        assert len(page1["items"]) == 2 and len(page2["items"]) == 1


def test_run_now_started():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1")
        res = asyncio.run(tasks_routes.run_task_now_endpoint(tid, user=USER))
        assert res["ok"] is True
        assert store.get(tid)["run_count"] == 1


def test_run_now_not_owned_forbidden():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1")
        try:
            asyncio.run(tasks_routes.run_task_now_endpoint(tid, user=OTHER))
        except Exception as e:
            assert _status_code(e) == 404, e
        else:
            raise AssertionError("非本人任务应 404")


def test_run_now_running_conflict():
    with tempfile.TemporaryDirectory() as d:
        store = _fresh_store(d)
        tid = store.add(user_input="x", provider=None, model=None, trigger_type="cron",
                         cron_expr="0 8 * * *", run_at=None,
                         next_run_at=datetime.now() + timedelta(days=1), owner_user_id="u1")
        services_module.get_scheduler_service()._running_ids.add(tid)
        try:
            asyncio.run(tasks_routes.run_task_now_endpoint(tid, user=USER))
        except Exception as e:
            assert _status_code(e) == 409, e
        else:
            raise AssertionError("正在执行中的任务应 409")


def main():
    tests = [
        test_create_manual_task_cron,
        test_create_manual_task_interval,
        test_create_manual_task_once,
        test_create_manual_task_from_template_marks_source_template,
        test_create_manual_task_past_once_rejected,
        test_create_task_from_pending_sets_auto_name_source,
        test_patch_pause_resume,
        test_patch_pause_other_user_forbidden,
        test_patch_edit_fields,
        test_list_templates,
        test_recent_runs,
        test_recent_runs_filters_and_pagination,
        test_run_now_started,
        test_run_now_not_owned_forbidden,
        test_run_now_running_conflict,
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
