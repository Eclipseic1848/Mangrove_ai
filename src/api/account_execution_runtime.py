"""核对账号停止操作；实际清理仍交给各执行器，未知资源绝不假报完成。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from filelock import Timeout

from src import account_execution as execution
from src.api.execution import execution_lock, execution_to_thread
from src.source_acquisition import SourceAcquisitionRepository

_LOGGER = logging.getLogger(__name__)
_ROOTS = (
    ("workspace", "semantic_workspace_tasks", "user_id", "task_id"),
    ("data", "data_prep_tasks", "user_id", "task_id"),
    ("harness", "semantic_harness_runs", "user_id", "run_id"),
    ("source", "source_acquisition_attempts", "owner_id", "attempt_id"),
    ("candidate", "candidate_verification_attempts", "owner_id", "attempt_id"),
    ("validation", "capability_validation_runs", "owner_id", "run_id"),
)


class AccountExecutionManager:
    def __init__(self, store, workspace, scheduler, validation, platform_validation):
        self.store = store
        self.workspace = workspace
        self.scheduler = scheduler
        self.validation = validation
        self.platform_validation = platform_validation
        self._task = None
        self._stopping = asyncio.Event()

    def start(self):
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="account-execution-holds")

    async def stop(self):
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self):
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                _LOGGER.error("账号执行停止状态暂时无法核对，保留持久阻断")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

    def _inventory_complete(self, owner):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            for kind, table, owner_column, id_column in _ROOTS:
                missing = conn.execute(
                    f"SELECT 1 FROM {table} r WHERE r.{owner_column}=? AND NOT EXISTS ("
                    f"SELECT 1 FROM account_execution_bindings b WHERE b.owner_user_id=r.{owner_column} "
                    f"AND b.resource_kind=? AND b.resource_id=r.{id_column}) LIMIT 1",
                    (owner, kind),
                ).fetchone()
                if missing:
                    return False
            for row in conn.execute("SELECT run_id,payload_json FROM capability_platform_validation_runs"):
                payload = json.loads(row["payload_json"])
                if payload.get("actor_id") == owner and conn.execute(
                    "SELECT 1 FROM account_execution_bindings WHERE owner_user_id=? AND resource_kind='validation' AND resource_id=?",
                    (owner, "platform:" + row["run_id"]),
                ).fetchone() is None:
                    return False
        return True

    async def run_once(self):
        with self.store._conn() as conn:
            operations = [dict(row) for row in conn.execute("SELECT * FROM account_execution_holds WHERE status='pending' ORDER BY created_at,generation")]
        for operation in operations:
            owner, generation = operation["owner_user_id"], operation["generation"]
            error, complete = None, False
            try:
                if not self._inventory_complete(owner):
                    error = "holder_unknown"
                else:
                    try:
                        complete = await self.scheduler.reconcile_account_execution(owner, generation)
                    except Exception:
                        error = "scheduler_unavailable"
                    with self.store._conn() as conn:
                        bindings = execution.list_execution_bindings(conn, owner, before_generation=generation)
                    for binding in bindings:
                        if binding["resource_kind"] == "schedule":
                            continue
                        stopped, unknown = await self._reconcile_binding(binding)
                        complete = complete and stopped
                        if unknown:
                            error = error or "holder_unknown"
            except Exception:
                error = error or "reconciliation_failed"
            with self.store._lock, self.store._conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                execution.refresh_hold_operation(conn, operation["operation_id"], reconciliation_complete=bool(complete and not error), error_code=error, now=time.time())

    async def _reconcile_binding(self, binding):
        owner, kind, resource_id, generation = (binding[key] for key in ("owner_user_id", "resource_kind", "resource_id", "generation"))
        if kind == "workspace":
            return await self.workspace.pause_account_execution(owner, resource_id, expected_generation=generation), False
        if binding["state"] in {"idle", "paused"}:
            return self.store.confirm_account_execution_stopped(owner, kind, resource_id, generation), False
        if kind == "candidate":
            return await self.workspace.pause_account_candidate(owner, resource_id, expected_generation=generation), False
        if kind == "validation":
            if resource_id.startswith("platform:"):
                return await execution_to_thread(self.platform_validation.reconcile_account_execution, owner, resource_id, generation), False
            return await self.validation.reconcile_account_execution(owner, resource_id, generation), False
        if kind == "source":
            repository = SourceAcquisitionRepository(self.store.db_path)
            if not repository.cancel_for_account(owner, resource_id, generation):
                return False, False
            try:
                with repository.execution_lock(owner, resource_id).acquire(timeout=0):
                    with execution.execution_context(execution.ExecutionAuthorization(owner, generation)):
                        confirmed = repository.confirm_account_stop(owner, resource_id)
                return confirmed, False
            except Timeout:
                return False, False
        # 仅旧聊天、数据图和 Harness 使用这把锁。来源、Runtime、候选与验证用各自真实租约。
        try:
            with execution_lock(self.store, owner, kind, resource_id):
                row = None
                if kind == "data":
                    row = self.store.get_data_prep_task(resource_id)
                elif kind == "harness":
                    row = self.store.get_semantic_harness_run(owner, resource_id)
                if row and str(row["status"]).lower() in {"ready", "needs_user", "needs_input", "succeeded", "completed", "failed"}:
                    return self.store.confirm_account_execution_stopped(owner, kind, resource_id, generation), False
                # 锁可得只表示本地持有者不存在；崩溃后的未知外部结果仍需保留。
                return False, True
        except Timeout:
            return False, False
