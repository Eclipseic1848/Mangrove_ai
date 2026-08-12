"""
scheduler —— 定时任务模块（期2）。

把已验证的 cron 纯逻辑抽象出来，配合 SQLite 持久化与异步轮询循环，
让新版 Conductor 的任务可按 once/cron 周期执行（支撑"周一三五抓标讯"类需求）。

- cron.py    cron 解析/匹配/续算 + once@/cron@ 调度串解析
- store.py   定时任务 SQLite 持久化
- service.py 异步轮询循环：到点用 run_conductor 执行任务
"""
from .cron import Schedule, compute_next_run, parse_schedule
from .service import SchedulerService
from .store import ScheduleStore
from .templates import TASK_TEMPLATES, get_template

__all__ = [
    "Schedule",
    "parse_schedule",
    "compute_next_run",
    "ScheduleStore",
    "SchedulerService",
    "TASK_TEMPLATES",
    "get_template",
]
