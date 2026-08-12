"""
Cookie 健康定时巡检：默认关闭的独立轮询协程。

不复用 src/scheduler（那是面向用户 TaskSpec 的 cron 调度，语义不同）；风格参照
src/scheduler/service.py 的 SchedulerService，但更简单：没有 cron 表达式，只按
settings.cookie_health_scan_interval_hours 的固定间隔跑。

每次醒来先热读 settings.cookie_health_scan_enabled（不需要重启进程即可切换开关）：
关闭时只做轻量的开关检查，不产生真实探测开销；开启且到点时，顺序（非并发）扫描
全部 10 个 Cookie，每项之间 sleep 数秒，避免同一时刻集中触发多次浏览器自动化登录，
让巡检行为更接近正常运维节奏而非批量脚本。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

_IDLE_CHECK_SECONDS = 300.0   # 巡检关闭/未到点时，多久再检查一次
_BETWEEN_ITEM_SECONDS = 5.0   # 同一轮巡检内，相邻两项之间的等待


class CookieHealthScanner:
    """异步轮询协程：开关关闭时空转，开启且到点时顺序扫描全部 Cookie 并落库。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_scan_at: float = 0.0

    def start(self) -> None:
        """启动后台轮询（幂等）。需在已有事件循环内调用。"""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def _sleep(self, seconds: float) -> None:
        """可被 stop() 提前打断的 sleep（用 Event.wait 而非 asyncio.sleep，退出更及时）。"""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run_one_scan(self) -> None:
        # 延迟导入，避免 config_routes 与本模块之间的循环导入；_COOKIE_HEALTH_KEYS 是
        # 10 个 Cookie key 的唯一权威列表（config_routes.py 里定义），这里直接复用，不重复声明。
        from src.api.routes.config_routes import _COOKIE_HEALTH_KEYS, _record_cookie_health, _verify_target

        logger.info("Cookie 健康巡检：开始一轮扫描（%d 项）", len(_COOKIE_HEALTH_KEYS))
        for key in _COOKIE_HEALTH_KEYS:
            if self._stop.is_set():
                break
            try:
                detail = await _verify_target(key)
                _record_cookie_health(key, "valid", detail, checked_by="scheduled")
            except Exception as e:  # noqa: BLE001 单项失败不影响本轮其余项，也不该崩掉循环
                detail = str(e)[:500]
                status = "unknown" if "无法判断" in detail else "invalid"
                _record_cookie_health(key, status, detail, checked_by="scheduled")
            await self._sleep(_BETWEEN_ITEM_SECONDS)
        logger.info("Cookie 健康巡检：本轮扫描完成")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if not settings.cookie_health_scan_enabled:
                await self._sleep(_IDLE_CHECK_SECONDS)
                continue
            interval_seconds = max(1, settings.cookie_health_scan_interval_hours) * 3600.0
            if time.time() - self._last_scan_at < interval_seconds:
                await self._sleep(min(_IDLE_CHECK_SECONDS, interval_seconds))
                continue
            try:
                await self._run_one_scan()
            except Exception as e:  # noqa: BLE001 扫描本身意外出错也不能让循环死掉
                logger.exception("Cookie 健康巡检：本轮扫描异常：%s", e)
            self._last_scan_at = time.time()


_scanner: Optional[CookieHealthScanner] = None


def start_cookie_health_scanner() -> None:
    """幂等启动。即使巡检开关关闭也会启动循环本身（循环内部自己空转直到开关打开），
    这样管理员切换开关不需要重启进程。需在已有事件循环内调用（FastAPI 启动钩子）。"""
    global _scanner
    if _scanner is not None:
        return
    _scanner = CookieHealthScanner()
    _scanner.start()
