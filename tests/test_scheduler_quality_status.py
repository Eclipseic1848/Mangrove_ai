"""诊断文件可以保留，核验失败不能被自动化记为成功。"""
import json
from datetime import datetime

import pytest

from src.account_execution import execution_context
from src.scheduler.service import SchedulerService
from tests.test_account_execution_scheduler import fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("quality", [{"passed": False}, {"passed": True}, {},
    {"passed": False, "review_complete": False},
    {"passed": False, "review_complete": True, "source_checks": [{"usable": False}]}])
async def test_run_quality_keeps_artifacts_and_controls_success(fixture, tmp_path, quality):
    web, store, owner, auth = fixture
    report = tmp_path / "report.md"
    report.write_text("# 合成诊断报告", encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text("[]", encoding="utf-8")
    async def runner(*args, **kwargs):
        return {"reply": "报告已生成", "quality": quality,
                "outputs": {"report_md": str(report), "json": str(data)}}
    service = SchedulerService(store, runner=runner)
    with execution_context(auth):
        task_id = store.add(user_input="合成核验任务", provider="local", model="synthetic",
                            trigger_type="cron", cron_expr="0 9 * * *", run_at=None,
                            next_run_at=datetime(2099, 1, 1), owner_user_id=owner)
        assert await service.run_task_now(task_id) == "started"
    expected = quality.get("passed") is not False
    task, run = store.get(task_id), store.list_runs(task_id)[0]
    assert bool(task["last_success"]) is expected
    assert bool(run["success"]) is expected
    assert run["state"] == ("succeeded" if expected else "failed")
    assert run["report_path"] == str(report) and run["json_path"] == str(data)
    if not expected:
        assert "核验未通过" in run["summary"]
        if quality.get("review_complete") is False:
            assert "未完整完成" in run["summary"]
        elif quality.get("source_checks"):
            assert "来源均不符合任务要求" in run["summary"]
    assert task["next_run_at"] == datetime(2099, 1, 1).isoformat()
    assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated", "unknown", "timeout"])
async def test_known_output_truncation_is_failed_without_changing_unknown_stops(fixture, failure):
    from types import SimpleNamespace
    from src.llm.provider import _collect_usage
    from src.model_connections.text_protocol import ModelOutputTruncatedError

    web, store, owner, auth = fixture
    calls = []

    async def runner(*args, **kwargs):
        calls.append(1)
        _collect_usage(SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 8192,
                                                       "total_tokens": 8292}))
        error = {"truncated": ModelOutputTruncatedError, "unknown": ValueError,
                 "timeout": TimeoutError}[failure]
        raise error("供应商响应含私有正文，不应写入历史")

    service = SchedulerService(store, runner=runner)
    planned = datetime(2099, 1, 1)
    with execution_context(auth):
        task_id = store.add(user_input="合成截断任务", provider="local", model="synthetic",
                            trigger_type="cron", cron_expr="0 9 * * *", run_at=None,
                            next_run_at=planned, owner_user_id=owner)
        assert await service.run_task_now(task_id) == "started"
    run = store.list_runs(task_id)[0]
    assert calls == [1]
    assert run["success"] == 0 and not run["report_path"] and not run["json_path"]
    assert "私有正文" not in run["summary"]
    assert store.get(task_id)["next_run_at"] == planned.isoformat()
    if failure == "truncated":
        assert run["state"] == "failed" and "截断" in run["summary"]
        assert json.loads(run["usage_json"])["total_tokens"] == 8292
        assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "idle"
    else:
        assert run["state"] == "unknown"
        assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "cleanup_failed"
