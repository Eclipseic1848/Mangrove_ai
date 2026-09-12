"""
定时任务调度服务（异步轮询循环，跨平台，单实例）。

职责：周期性扫描到点任务（store.due_tasks），用新版 Conductor（run_conductor）执行，
记录结果并为 cron 任务续算下次执行时刻。once 任务执行后置 done。

单实例设计：本地/单进程场景无需分布式锁；每个任务执行用 try/except 隔离，
单个任务失败不影响其它任务与循环本身；停止状态未知的执行禁止自动重试。
多实例生产部署时需要引入分布式锁；当前只保证本地单实例语义。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from .cron import Schedule, compute_next_run
from .store import ScheduleStore
from src import account_execution as execution

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
            except Exception as exc:
                logger.error("调度器轮询失败，下一轮继续 error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass  # 正常的轮询间隔到时

    async def tick(self, now: Optional[datetime] = None) -> int:
        """执行一轮：跑完所有到点任务，返回本轮执行的任务数。"""
        now = now or datetime.now()
        from .workspace import reconcile_pending, resume_occurrence
        for occurrence in reconcile_pending(self.store):
            await resume_occurrence(self, occurrence, now)
        due = self.store.due_tasks(now=now)
        for task in due:
            await self._run_one(
                task,
                now,
                keep_next_run=bool(task.get("_credential_resume_manual")),
            )
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
        try:
            # 恢复的手动原次由持久收据授权，不能依赖已结束的 HTTP 请求上下文。
            request_claim = keep_next_run and not task.get("_credential_resume_task_id")
            auth, task = self.store.claim_execution(
                task_id, expected_task=task, manual=request_claim,
            )
        except execution.ExecutionDenied:
            if request_claim:
                raise
            return False
        self._running_ids.add(task_id)
        try:
            with execution.execution_context(auth):
                await self._run_one_body(task, now, keep_next_run=keep_next_run)
        finally:
            self._running_ids.discard(task_id)
        return True

    async def _run_one_body(
        self, task: Dict[str, Any], now: datetime, *, keep_next_run: bool
    ) -> None:
        if task.get("_workspace_occurrence"):
            from .workspace import execute_occurrence
            await execute_occurrence(self,task,now,keep_next_run)
            return
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

        from src.api.auth import get_store

        web = get_store()
        auth = execution.current_authorization()
        returned = False
        discarded_after_return = False
        paused_for_credentials = False
        execution_task_id = self._execution_task_id(task, keep_next_run)

        def record_unknown_stop(message: str) -> None:
            try:
                # 仍获授权时保留失败可见性，但不推进下一次计划；未知停止不自动重跑。
                self.store.mark_run_keep_schedule(task_id, success=False, error=message)
                self.store.add_run(task_id, success=False, summary=message)
            except execution.ExecutionDenied:
                pass

        try:
            # 超时保护：单个卡死的任务不冻住整个调度循环（连带其它定时任务）
            from src.config.settings import settings

            result, ok, summary = await asyncio.wait_for(
                self._invoke_runner(task, execution_task_id=execution_task_id),
                timeout=settings.scheduler_task_timeout_seconds,
            )
            try:
                web.require_account_execution(auth, "schedule", task_id)
            except execution.ExecutionDenied:
                # Runner 已明确返回；账号代际变化只丢弃正文，不冒充停止未知。
                discarded_after_return = True
                raise
            summary = (late_note + summary)[:500]
            credential_key = self._credential_failure_key(result)
            if credential_key:
                # 返回了确定失效事实，先持久暂停收据；失败时按停止未知关闭，绝不自动重跑。
                returned = False
                self.store.pause_for_credentials(
                    task_id,
                    credential_key=credential_key,
                    execution_task_id=execution_task_id,
                    summary=summary,
                    manual=keep_next_run,
                )
                paused_for_credentials = True
                return
            outputs = (result.get("outputs") or {}) if isinstance(result, dict) else {}
            # 结果、历史与恢复收据同事务；任一步失败都不能放开下一次执行。
            self.store.finish_run(
                task_id,
                success=ok,
                result=summary if ok else "",
                error="" if ok else summary,
                next_run_at=next_run,
                keep_schedule=keep_next_run,
                summary=summary,
                report_path=str(outputs.get("report_md") or ""),
                json_path=str(outputs.get("json") or ""),
                credential_execution_task_id=(
                    execution_task_id if task.get("_credential_resume_task_id") else None
                ),
            )
            returned = True
            # 结果正文只保存在Owner任务历史，运行日志不得复制正文。
            logger.info("定时任务%s task_id=%s next=%s",
                        "完成" if ok else "失败（流程内错误）", task_id, next_run)
        except execution.ExecutionDenied:
            # 已返回的执行可以确认停止；安全点抛错尚不能证明子工作者静默。
            # 两者均丢弃旧代正文，且不阻断其它账号的调度。
            pass
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # 取消等待不证明底层线程停止；保留失败状态，禁止下一轮重复启动。
            logger.warning("定时任务停止状态待确认 task_id=%s", task_id)
            record_unknown_stop("执行超时或等待被取消，停止状态待确认")
            if asyncio.current_task().cancelling():
                raise
        except Exception as exc:
            # 异常可能含Provider正文或凭据，仅记录错误类别。
            logger.error("定时任务执行失败，停止状态待确认 task_id=%s error_type=%s", task_id, type(exc).__name__)
            record_unknown_stop("执行失败，停止状态待确认")
        finally:
            if paused_for_credentials:
                pass
            elif returned:
                try:
                    web.set_account_execution_state(auth, "schedule", task_id, "idle")
                except execution.ExecutionDenied:
                    web.confirm_account_execution_stopped(auth.owner_user_id, "schedule", task_id, auth.generation)
            elif discarded_after_return:
                web.confirm_account_execution_stopped(
                    auth.owner_user_id, "schedule", task_id, auth.generation,
                )
            else:
                web.confirm_account_execution_stopped(auth.owner_user_id, "schedule", task_id, auth.generation, cleanup_failed=True)

    async def reconcile_account_execution(self, owner: str, before_generation: int) -> bool:
        # 无 await 期间持 SQL 锁；执行中的任务到安全点后提交自己的停止证明。
        return self.store.reconcile_account_execution(owner, before_generation)

    async def run_task_now(self, task_id: str, request_key: str | None = None) -> str:
        """手动「立即执行一次」，不影响原定 next_run_at/status。

        返回 "started"（已开始执行）| "not_found"（任务不存在）| "running"（正在执行中，跳过）。
        """
        task = self.store.get(task_id)
        if not task:
            return "not_found"
        if task_id in self._running_ids:
            return "running"
        if task.get("source")=="workspace":task["_manual_request_key"]=request_key
        await self._run_one(task, datetime.now(), keep_next_run=True)
        return "started"

    @staticmethod
    def _execution_task_id(task: Dict[str, Any], manual: bool) -> str:
        if task.get("_credential_resume_task_id"):
            return str(task["_credential_resume_task_id"])
        occurrence = task.get("_manual_request_key") if manual else task.get("next_run_at")
        if not occurrence:
            occurrence = uuid.uuid4().hex
        value = f"{task['task_id']}\0{occurrence}".encode("utf-8")
        return "scheduler_" + hashlib.sha256(value).hexdigest()[:32]

    @staticmethod
    def _credential_failure_key(result: Dict[str, Any]) -> str | None:
        if not isinstance(result, dict):
            return None
        for attempt in result.get("collector_attempts") or ():
            if (
                isinstance(attempt, dict)
                and not attempt.get("success")
                and attempt.get("failure_kind") == "auth_invalid"
                and isinstance(attempt.get("credential_key"), str)
            ):
                return attempt["credential_key"]
        return None

    async def _invoke_runner(
        self,
        task: Dict[str, Any],
        *,
        execution_task_id: str | None = None,
    ) -> tuple[Dict[str, Any], bool, str]:
        from src.api.auth import get_store
        from src.config.runtime_config import USER_KEYS
        from src.config.user_ctx import user_memories_context, user_overrides_context

        owner = task.get("owner_user_id")
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("定时任务缺少有效的所属用户，无法执行")
        try:
            store = get_store()
            store.require_account_execution(execution.current_authorization(), "schedule", task["task_id"])
            user = store.get_user(owner)
            if user is None or user.get("pending") or user.get("disabled"):
                raise ValueError("所属用户不可用")
            overrides = {key: value for key, value in store.config_all(owner).items() if key in USER_KEYS}
            memories = [memory["text"] for memory in store.memory_list(owner)]
        except Exception:
            # 身份或本人上下文无法加载时停止，且不把底层凭证错误写入任务历史。
            raise RuntimeError("定时任务无法加载有效的本人执行上下文") from None

        with user_overrides_context(overrides), user_memories_context(memories):
            runner = self._runner
            if runner is None:
                # 有效 Owner 确认后才装载执行器；注入的执行器也遵守同一授权边界。
                from src.conductor.graph import run_conductor
                runner = run_conductor
            # 定时任务不自动业务入库，也不再次生成相同的定时计划。
            result = await runner(
                task["user_input"],
                provider=task.get("provider"),
                model=task.get("model"),
                session_id=f"scheduler:{task['task_id']}",
                approved_db_write=False,
                ignore_schedule=True,
                task_id=execution_task_id,
            )
            ok, summary = self._assess(result)
            from src.config.runtime_config import redact_sensitive_text

            return result, ok, redact_sensitive_text(summary)[:500]

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
