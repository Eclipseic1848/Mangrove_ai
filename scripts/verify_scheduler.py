#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scheduler 端到端验证（用桩 runner，不调真 LLM）。

流程：注册一个"周一三五 09:30"的 cron 任务（next_run_at 设在过去）→ 调度器 tick()
触发执行 → 校验：runner 被调用、结果记录成功、cron 续算出下一匹配时刻、该任务不再到点。

运行：python scripts/verify_scheduler.py
"""
import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.scheduler.service import SchedulerService
from src.scheduler.store import ScheduleStore


async def main() -> int:
    calls = []

    async def stub_runner(user_input, *, provider=None, model=None, session_id="",
                          approved_db_write=False, ignore_schedule=False):
        # 记录调用，校验调度器透传的参数
        calls.append(
            {"user_input": user_input, "provider": provider, "model": model,
             "session_id": session_id, "approved_db_write": approved_db_write,
             "ignore_schedule": ignore_schedule}
        )
        return {"outputs": {"report_md": f"downloads/{session_id}/report.md"}, "reply": "已完成"}

    with tempfile.TemporaryDirectory() as d:
        store = ScheduleStore(str(Path(d) / "sched.db"))
        svc = SchedulerService(store, poll_interval=1.0, runner=stub_runner)

        # 注册：周一三五 09:30，next_run_at 故意设在过去 → 立即到点
        task_id = store.add(
            user_input="抓某招投标网最新标讯并提炼摘要",
            provider="deepseek", model="deepseek-chat",
            trigger_type="cron", cron_expr="30 9 * * 1,3,5", run_at=None,
            next_run_at=datetime(2026, 6, 22, 9, 30),  # 周一
        )

        # 用一个固定 now（周一 10:00）执行一轮
        now = datetime(2026, 6, 22, 10, 0)
        ran = await svc.tick(now=now)

        assert ran == 1, f"应执行 1 个到点任务，实际 {ran}"
        assert len(calls) == 1, f"runner 应被调用 1 次，实际 {len(calls)}"
        assert calls[0]["provider"] == "deepseek" and calls[0]["model"] == "deepseek-chat"
        assert calls[0]["approved_db_write"] is False, "定时任务不应静默入库"
        assert calls[0]["ignore_schedule"] is True, "调度器触发应跑完整流程（ignore_schedule=True）"
        assert calls[0]["session_id"] == f"scheduler:{task_id}"

        row = store.get(task_id)
        assert row["last_success"] == 1, "应记录运行成功"
        assert "report.md" in (row["last_result"] or ""), f"摘要应含产出路径，实得 {row['last_result']!r}"
        assert row["run_count"] == 1
        # 续算：周一 10:00 之后的下一次是周三 09:30
        assert row["next_run_at"] == "2026-06-24T09:30:00", f"续算下次错误: {row['next_run_at']}"
        assert row["status"] == "active", "cron 任务仍应 active"

        # 同一 now 再 tick：不应再次到点（next 已推到周三）
        ran2 = await svc.tick(now=now)
        assert ran2 == 0 and len(calls) == 1, "续算后同一时刻不应重复触发"

        # once 任务：执行后应 done
        once_id = store.add(
            user_input="一次性抓取并汇总", provider="deepseek", model=None,
            trigger_type="once", cron_expr=None, run_at=datetime(2026, 6, 22, 9, 0),
            next_run_at=datetime(2026, 6, 22, 9, 0),
        )
        await svc.tick(now=now)
        once_row = store.get(once_id)
        assert once_row["status"] == "done", "once 任务执行后应置 done"
        assert once_row["next_run_at"] is None

    print("PASS  scheduler 端到端：cron 触发→续算→不重复触发；once 执行→done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
