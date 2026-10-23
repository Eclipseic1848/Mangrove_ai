"""调度工作簿的真实持久化与公开历史下载，只用临时库和本机原件。"""
from datetime import datetime
from contextlib import closing
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.account_execution import execution_context
from src.api import auth
from src.api.routes import tasks
from src.conductor.project_export import write_workbook
from src.config.settings import settings
from src.scheduler.store import ScheduleStore
from tests.test_account_execution_scheduler import fixture


@pytest.fixture
def xlsx_api(fixture, tmp_path, monkeypatch):
    web, store, owner, authorization = fixture
    root = tmp_path / "artifacts"
    output = root / "synthetic-run"
    output.mkdir(parents=True)
    workbook = output / "data.xlsx"
    write_workbook(workbook, [("合成资料", [{"title": "本轮工作簿"}], {"title": "标题"})])
    report, data = output / "report.md", output / "data.json"
    report.write_text("# 合成报告", encoding="utf-8")
    data.write_text('[{"title":"本轮工作簿"}]', encoding="utf-8")
    with execution_context(authorization):
        task_id = store.add(
            user_input="合成工作簿任务", provider="local", model="synthetic-model",
            trigger_type="once", cron_expr=None, run_at=datetime(2099, 1, 1),
            next_run_at=datetime(2099, 1, 1), owner_user_id=owner,
        )
        run_id = store.begin_run(store.get(task_id))
        store.finish_run(task_id, run_id, success=True, summary="合成结果",
                         outputs={"report_md": str(report), "json": str(data), "xlsx": str(workbook)},
                         notification={"status": "unknown", "message": "合成未知回执"},
                         usage={"total_tokens": 7})
    # 重开Store证明从数据库恢复，不依赖runner的内存结果。
    store = ScheduleStore(store.db_path)
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(root))
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: store)
    monkeypatch.setattr(tasks, "get_scheduler_service", lambda: SimpleNamespace(is_running=lambda *args: False))
    user = {"user_id": owner, "role": "user"}
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, store, web, owner, task_id, run_id, workbook, user


def test_workbook_path_survives_store_reopen(xlsx_api):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    run = store.get_run(task_id, run_id)
    assert run.get("xlsx_path") == str(workbook)
    assert run["notification_json"] == '{"status": "unknown", "message": "合成未知回执"}'
    assert run["usage_json"] == '{"total_tokens": 7}'


@pytest.mark.parametrize("recent", [False, True])
def test_history_only_exposes_workbook_availability(xlsx_api, recent):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    response = client.get("/api/tasks/runs/recent" if recent else f"/api/tasks/{task_id}/runs")
    assert response.status_code == 200, response.text
    rows = response.json()["items"] if recent else response.json()
    assert rows[0].get("has_xlsx") is True
    assert "xlsx_path" not in rows[0]


def test_owner_downloads_exact_run_workbook_bytes(xlsx_api):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    response = client.get(f"/api/tasks/{task_id}/runs/{run_id}/download", params={"kind": "xlsx"})
    assert response.status_code == 200, response.text
    assert response.content == workbook.read_bytes()
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert ".xlsx" in response.headers["content-disposition"]


def test_two_runs_keep_their_own_versioned_workbook(xlsx_api):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    original = store.get_run(task_id, run_id)
    second = workbook.with_name("data_2.xlsx")
    write_workbook(second, [("合成资料", [{"title": "第二轮不同工作簿"}], {"title": "标题"})])
    with execution_context(web.capture_account_execution(owner)):
        second_run = store.begin_run(store.get(task_id))
        store.finish_run(task_id, second_run, success=True, summary="第二轮", outputs={"xlsx": str(second)})
    assert run_id != second_run and workbook.read_bytes() != second.read_bytes()
    for identity, expected in ((run_id, workbook), (second_run, second)):
        response = client.get(f"/api/tasks/{task_id}/runs/{identity}/download?kind=xlsx")
        assert response.status_code == 200, response.text
        assert response.content == expected.read_bytes()
    assert store.get_run(task_id, run_id) == original


def test_other_schedule_or_owner_cannot_download_run_workbook(xlsx_api):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    with execution_context(web.capture_account_execution(owner)):
        other_plan = store.add(user_input="另一个合成计划", provider=None, model=None,
                               trigger_type="once", cron_expr=None, run_at=datetime(2099, 1, 1),
                               next_run_at=datetime(2099, 1, 1), owner_user_id=owner)
    wrong_plan = client.get(f"/api/tasks/{other_plan}/runs/{run_id}/download?kind=xlsx")
    assert wrong_plan.status_code == 404
    user["user_id"] = web.create_user("other-xlsx-owner", "synthetic-unused")["user_id"]
    assert client.get(f"/api/tasks/{task_id}/runs/{run_id}/download?kind=xlsx").status_code == 404
    assert client.get("/api/tasks/runs/recent").json()["items"] == []


@pytest.mark.parametrize("stored", [None, "", "missing", "wrong-extension", "outside", "managed:v1/../outside.xlsx"])
def test_unavailable_workbook_has_no_flag_or_download(xlsx_api, stored):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    if stored == "missing":
        stored = str(workbook.with_name("missing.xlsx"))
    elif stored == "wrong-extension":
        stored = str(workbook.with_name("report.md"))
    elif stored == "outside":
        outside = workbook.parents[2] / "outside.xlsx"
        outside.write_bytes(workbook.read_bytes())
        stored = str(outside)
    with closing(sqlite3.connect(store.db_path)) as conn:
        conn.execute("UPDATE scheduled_task_runs SET xlsx_path=? WHERE task_id=? AND run_id=?", (stored, task_id, run_id))
        conn.commit()
    # 即使同目录已有data.xlsx，旧NULL/空记录也不能靠猜文件名补出入口。
    for url in (f"/api/tasks/{task_id}/runs", "/api/tasks/runs/recent"):
        response = client.get(url)
        assert response.status_code == 200, response.text
        rows = response.json() if isinstance(response.json(), list) else response.json()["items"]
        assert rows[0]["has_xlsx"] is False
    assert client.get(f"/api/tasks/{task_id}/runs/{run_id}/download?kind=xlsx").status_code == 404


def test_deleted_workbook_does_not_leave_download_entry(xlsx_api):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    workbook.unlink()
    assert client.get(f"/api/tasks/{task_id}/runs").json()[0]["has_xlsx"] is False
    assert client.get(f"/api/tasks/{task_id}/runs/{run_id}/download?kind=xlsx").status_code == 404


@pytest.mark.parametrize("inside", [True, False])
def test_workbook_links_keep_existing_managed_path_boundary(xlsx_api, inside):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    target = workbook if inside else workbook.parents[2] / "outside.xlsx"
    if not inside:
        target.write_bytes(workbook.read_bytes())
    link = workbook.with_name("linked.xlsx")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("本机没有创建符号链接的权限；Linux CI验证真实链接边界")
    with closing(sqlite3.connect(store.db_path)) as conn:
        conn.execute("UPDATE scheduled_task_runs SET xlsx_path=? WHERE task_id=? AND run_id=?", (str(link), task_id, run_id))
        conn.commit()
    assert client.get(f"/api/tasks/{task_id}/runs").json()[0]["has_xlsx"] is inside
    response = client.get(f"/api/tasks/{task_id}/runs/{run_id}/download?kind=xlsx")
    assert response.status_code == (200 if inside else 404)
    if inside:
        assert response.content == workbook.read_bytes()


@pytest.mark.parametrize("kind,filename,media", [("report", "report.md", "text/markdown"), ("json", "data.json", "application/json")])
def test_other_download_formats_remain_available(xlsx_api, kind, filename, media):
    client, store, web, owner, task_id, run_id, workbook, user = xlsx_api
    response = client.get(f"/api/tasks/{task_id}/runs/{run_id}/download", params={"kind": kind})
    assert response.status_code == 200, response.text
    assert response.content == workbook.with_name(filename).read_bytes()
    assert response.headers["content-type"].split(";", 1)[0] == media
