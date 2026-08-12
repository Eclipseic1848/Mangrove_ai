"""网关共享的后台服务单例：定时任务调度器，全进程共用一个轮询循环。"""
from __future__ import annotations

from typing import Optional

from src.config.settings import settings
from src.scheduler import ScheduleStore, SchedulerService

_store: Optional[ScheduleStore] = None
_service: Optional[SchedulerService] = None


def get_schedule_store() -> ScheduleStore:
    """无论调度是否启用，都提供一个 ScheduleStore 供读写（列表/创建/取消）。"""
    global _store
    if _store is None:
        _store = ScheduleStore(settings.scheduler_db_path)
    return _store


def get_scheduler_service() -> SchedulerService:
    """提供 SchedulerService 单例，供「立即执行」等 API 直接调用（不依赖后台轮询是否已启用）。"""
    global _service
    if _service is None:
        _service = SchedulerService(get_schedule_store(), poll_interval=settings.scheduler_poll_seconds)
    return _service


def start_scheduler() -> None:
    """启用时拉起后台轮询（幂等）。需在事件循环内调用（FastAPI 启动钩子）。"""
    if not settings.scheduler_enabled:
        return
    get_scheduler_service().start()
