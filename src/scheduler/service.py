"""
定时任务调度服务（异步轮询循环，跨平台，单实例）。

职责：周期性扫描到点任务（store.due_tasks），用新版 Conductor（run_conductor）执行，
记录结果并为 cron 任务续算下次执行时刻。once 任务执行后置 done。

单实例设计：本地/单进程场景无需分布式锁；每个任务执行用 try/except 隔离，
单个任务失败不影响其它任务与循环本身（cron 任务自然在下一周期重试）。
多实例生产部署时需要引入分布式锁；当前只保证本地单实例语义。
"""
from __future__ import annotations

import asyncio
import logging
import platform
from datetime import datetime
from typing import Any, Dict, Optional

from .cron import Schedule, compute_next_run
from .store import ScheduleStore

logger = logging.getLogger(__name__)

# 注入式执行器：默认调用 Conductor，测试时可替换为桩函数
RunnerType = Any  # async callable(user_input, *, provider, model, ...) -> dict


class SchedulerService:
    """异步轮询调度器。"""

    def __init__(
        self,
        store: Optional[ScheduleStore] = None,
        *,
        poll_interval: float = 30.0,
        runner: Optional[RunnerType] = None,
    ) -> None:
        self.store = store or ScheduleStore()
        self.poll_interval = max(1.0, poll_interval)
        self._runner = runner  # None 时懒加载真正的 run_conductor
        self.instance_id = f"{platform.node()}-{id(self)}"
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        # 正在执行的任务 id 集合：防到点触发与手动「立即执行」撞车并发重跑同一任务
        self._running_ids: set = set()

    # ---- 生命周期 ----
    def start(self) -> None:
        """启动后台轮询（幂等）。需在已有事件循环中调用。"""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("调度器已启动 instance=%s poll=%ss", self.instance_id, self.poll_interval)

    async def stop(self) -> None:
        """停止后台轮询。"""
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("调度器已停止 instance=%s", self.instance_id)

    # ---- 循环与执行 ----
    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("调度器轮询出错（已忽略，继续下一轮）")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass  # 正常的轮询间隔到时

    async def tick(self, now: Optional[datetime] = None) -> int:
        """执行一轮：跑完所有到点任务，返回本轮执行的任务数。"""
        now = now or datetime.now()
        due = self.store.due_tasks(now=now)
        for task in due:
            await self._run_one(task, now)
        return len(due)

    async def _run_one(
        self, task: Dict[str, Any], now: datetime, *, keep_next_run: bool = False
    ) -> bool:
        """执行一个任务。keep_next_run=True 时不重算/不改 next_run_at 与 status
        （手动「立即执行」用）。返回是否实际执行了（同一任务正在执行中则跳过返回 False）。
        """
        task_id = task["task_id"]
        if task_id in self._running_ids:
            logger.info("定时任务已在执行中，本次触发跳过 task_id=%s", task_id)
            return False
        self._running_ids.add(task_id)
        try:
            await self._run_one_body(task, now, keep_next_run=keep_next_run)
        finally:
            self._running_ids.discard(task_id)
        return True

    async def _run_one_body(
        self, task: Dict[str, Any], now: datetime, *, keep_next_run: bool
    ) -> None:
        task_id = task["task_id"]
        # 计算下次执行：cron/interval 续算下一匹配（受 start_date/end_date 生效区间钳制）；
        # once 无后续；keep_next_run（立即执行）不重算，沿用原定计划
        next_run = None
        if not keep_next_run and task["trigger_type"] in ("cron", "interval"):
            sched = Schedule(
                trigger_type=task["trigger_type"],
                cron_expr=task.get("cron_expr"),
                interval_seconds=task.get("interval_seconds"),
            )
            next_run = compute_next_run(
                sched, now, start_date=task.get("start_date"), end_date=task.get("end_date"),
            )

        # 错过补跑识别：原定时刻已过去远超轮询周期，说明当时服务没在跑（如 8 点任务
        # 10 点启动服务才补跑）。标注进结果，避免"显示成功、以为是按时跑的"误解。
        late_note = ""
        try:
            sched_dt = datetime.fromisoformat(task.get("next_run_at") or "")
            if (now - sched_dt).total_seconds() > max(120.0, self.poll_interval * 2):
                late_note = f"[补跑，原定 {task['next_run_at']}] "
        except (ValueError, TypeError):
            pass

        def _mark(*, success: bool, result: str = "", error: str = "") -> None:
            if keep_next_run:
                self.store.mark_run_keep_schedule(task_id, success=success, result=result, error=error)
            else:
                self.store.mark_run(task_id, success=success, result=result, error=error, next_run_at=next_run)

        try:
            # 超时保护：单个卡死的任务不冻住整个调度循环（连带其它定时任务）
            from src.config.settings import settings

            result = await asyncio.wait_for(
                self._invoke_runner(task), timeout=settings.scheduler_task_timeout_seconds
            )
            ok, summary = self._assess(result)
            summary = late_note + summary
            _mark(success=ok, result=summary if ok else "", error="" if ok else summary)
            # 执行历史：每次一行，周期任务的多份报告靠它在前端按次查看/下载
            outputs = (result.get("outputs") or {}) if isinstance(result, dict) else {}
            self.store.add_run(
                task_id, success=ok, summary=summary,
                report_path=str(outputs.get("report_md") or ""),
                json_path=str(outputs.get("json") or ""),
            )
            logger.info("定时任务%s task_id=%s next=%s %s",
                        "完成" if ok else "失败（流程内错误）", task_id, next_run, summary[:120])
        except asyncio.TimeoutError:
            logger.warning("定时任务超时 task_id=%s（cron 将下轮重试）", task_id)
            _mark(success=False, error=late_note + "执行超时")
            self.store.add_run(task_id, success=False, summary=late_note + "执行超时")
        except Exception as e:
            logger.exception("定时任务执行失败 task_id=%s", task_id)
            # 失败也按计划续算下次（cron 在下一周期重试）；once 失败置 done 并记录错误
            _mark(success=False, error=late_note + str(e))
            self.store.add_run(task_id, success=False, summary=late_note + str(e))

    async def run_task_now(self, task_id: str) -> str:
        """手动「立即执行一次」，不影响原定 next_run_at/status。

        返回 "started"（已开始执行）| "not_found"（任务不存在）| "running"（正在执行中，跳过）。
        """
        task = self.store.get(task_id)
        if not task:
            return "not_found"
        if task_id in self._running_ids:
            return "running"
        await self._run_one(task, datetime.now(), keep_next_run=True)
        return "started"

    async def _invoke_runner(self, task: Dict[str, Any]) -> Dict[str, Any]:
        runner = self._runner
        if runner is None:
            # 懒加载真正的 Conductor，避免模块循环依赖与测试时的重依赖
            from src.conductor.graph import run_conductor

            runner = run_conductor
        # 任务属主的按用户凭证覆盖（自配 API Key/Cookie）注入执行上下文，与聊天链路同规则
        owner = (task.get("user_id") or "").strip()
        if owner:
            try:
                from src.api.auth import get_store
                from src.config.runtime_config import USER_KEYS
                from src.config.user_ctx import set_user_memories, set_user_overrides
                store = get_store()
                set_user_overrides({k: v for k, v in (store.config_all(owner) or {}).items()
                                    if k in USER_KEYS})
                set_user_memories([m["text"] for m in store.memory_list(owner)])
            except Exception:  # noqa: BLE001 覆盖加载失败回落全局配置，不阻断定时任务
                logger.warning("定时任务加载用户凭证覆盖失败，回落全局配置 task_id=%s", task.get("task_id"))
        # 定时任务不静默入库（敏感动作需人工确认）：approved_db_write 固定 False
        # ignore_schedule=True：到点触发时跑完整采集流程，而非再次短路为"安排定时任务"
        return await runner(
            task["user_input"],
            provider=task.get("provider"),
            model=task.get("model"),
            session_id=f"scheduler:{task['task_id']}",
            approved_db_write=False,
            ignore_schedule=True,
        )

    @staticmethod
    def _assess(result: Dict[str, Any]) -> tuple[bool, str]:
        """真实成败判定 + 可读摘要。

        关键：流水线内部失败不抛异常，而是写在返回 state 里——error/被追问澄清/
        零产出都不能算成功，否则前端"上次成功"就是误报（曾发生：垃圾任务跑出
        空结果仍显示成功）。
        """
        if not isinstance(result, dict):
            return True, str(result)[:500]
        if result.get("error"):
            return False, f"error: {result['error']}"[:500]
        if result.get("needs_clarification"):
            q = str(result.get("clarification_question") or "").strip()
            return False, f"任务描述需澄清（定时执行无人应答，请重新创建更明确的任务）：{q}"[:500]
        outputs = result.get("outputs") or {}
        parts = []
        if outputs.get("report_md"):
            parts.append(f"report={outputs['report_md']}")
        if outputs.get("json"):
            parts.append(f"json={outputs['json']}")
        if parts:
            return True, "; ".join(parts)[:500]
        if result.get("reply"):
            return True, str(result["reply"])[:200]
        return False, "无产出（未生成报告/数据/回复）"
