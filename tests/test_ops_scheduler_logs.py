"""正常结果与异常正文均不进入调度运行日志；测试全部使用合成内容。"""
import asyncio
from datetime import datetime
import logging

import pytest
from src.scheduler.service import SchedulerService
from tests.test_scheduler_owner import owner_stores, _add


@pytest.mark.parametrize("failed", [False, True])
def test_scheduler_logs_exclude_result_and_exception_text(owner_stores, caplog, monkeypatch, failed):
    _, schedules, owners = owner_stores
    task_id = _add(schedules, owners[0])
    canary = "synthetic-private-business-body-143"

    async def runner(*_args, **_kwargs):
        if failed:
            raise RuntimeError(canary)
        return {"reply": canary}

    caplog.set_level(logging.INFO, logger="src.scheduler.service")
    service = SchedulerService(schedules, runner=runner)
    asyncio.run(service.tick(datetime(2026, 1, 2)))
    text = "\n".join(record.getMessage() + (str(record.exc_info) if record.exc_info else "") for record in caplog.records if record.name == "src.scheduler.service")
    assert task_id in text
    assert canary not in text
