"""
定时任务持久化（标准库 sqlite3，跨平台，无第三方依赖）。

存储新版 Conductor 的定时任务：保存用户原始请求 + 供应商/模型 + 触发方式（once/cron）
+ 下次执行时刻 + 状态与上次运行结果。默认落在 data/scheduler.db。

设计取舍：单实例本地场景不引入分布式锁；多实例部署必须补充跨进程互斥与租约后才能
用于生产。这里用 SQLite 满足本地单实例的可靠记录与触发。
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from src.database_migrations import DatabaseTarget, inspect_database


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(microsecond=0).isoformat() if dt else None


class ScheduleStore:
    """定时任务的 SQLite 存储。线程安全（每次操作自带连接，外加进程内锁）。"""

    def __init__(self, db_path: str = "data/scheduler.db") -> None:
        self.db_path = db_path
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        inspect_database(
            DatabaseTarget(profile="scheduler", path=Path(self.db_path))
        ).require_current()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """打开连接：正常退出时提交，异常时回滚，最终始终关闭（避免 Windows 文件占用）。"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add(
        self,
        *,
        user_input: str,
        provider: Optional[str],
        model: Optional[str],
        trigger_type: str,
        cron_expr: Optional[str],
        run_at: Optional[datetime],
        next_run_at: Optional[datetime],
        owner_user_id: Optional[str] = None,
        name: Optional[str] = None,
        source: str = "auto",
        interval_seconds: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> str:
        """新增一条定时任务，返回 task_id。owner_user_id 用于 Web UI 多用户归属。

        source：auto（对话语义识别自动创建）| manual（手动创建）| template（模板创建）。
        """
        task_id = f"sch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO scheduled_tasks
                   (task_id, user_input, owner_user_id, provider, model, trigger_type, run_at,
                    cron_expr, next_run_at, status, run_count, created_at,
                    name, source, interval_seconds, start_date, end_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, user_input, owner_user_id, provider, model, trigger_type,
                    _iso(run_at), cron_expr, _iso(next_run_at), _iso(datetime.now()),
                    name, source, interval_seconds, start_date, end_date,
                ),
            )
        return task_id

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_active(self, owner_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出进行中的定时任务（含暂停中的，前端靠 status 渲染开关）。

        传 owner_user_id 则只返回该用户的（Web UI 多用户隔离）。cancelled/done 不返回。
        """
        with self._conn() as conn:
            if owner_user_id is not None:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE status IN ('active','paused') "
                    "AND owner_user_id=? ORDER BY next_run_at",
                    (owner_user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE status IN ('active','paused') "
                    "ORDER BY next_run_at"
                ).fetchall()
        return [dict(r) for r in rows]

    def due_tasks(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """返回 next_run_at <= now 且仍 active 的任务（到点待执行）。"""
        now = now or datetime.now()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM scheduled_tasks
                   WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at <= ?
                   ORDER BY next_run_at""",
                (_iso(now),),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_run(
        self,
        task_id: str,
        *,
        success: bool,
        result: str = "",
        error: str = "",
        next_run_at: Optional[datetime] = None,
    ) -> None:
        """记录一次运行结果，并更新 next_run_at；next_run_at 为空表示无后续（置 done）。"""
        new_status = "active" if next_run_at else "done"
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE scheduled_tasks
                   SET last_run_at=?, last_success=?, last_result=?, last_error=?,
                       run_count=run_count+1, next_run_at=?, status=?
                   WHERE task_id=?""",
                (
                    _iso(datetime.now()), 1 if success else 0, result, error,
                    _iso(next_run_at), new_status, task_id,
                ),
            )

    def mark_run_keep_schedule(
        self, task_id: str, *, success: bool, result: str = "", error: str = ""
    ) -> None:
        """立即执行专用：只记 last_*/run_count，不动 next_run_at/status（不影响原定调度）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE scheduled_tasks
                   SET last_run_at=?, last_success=?, last_result=?, last_error=?,
                       run_count=run_count+1
                   WHERE task_id=?""",
                (_iso(datetime.now()), 1 if success else 0, result, error, task_id),
            )

    def add_run(
        self,
        task_id: str,
        *,
        success: bool,
        summary: str = "",
        report_path: str = "",
        json_path: str = "",
    ) -> None:
        """追加一条执行历史（周期任务的多份报告靠它关联查看/下载）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO scheduled_task_runs
                   (task_id, run_at, success, summary, report_path, json_path)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_id, _iso(datetime.now()), 1 if success else 0, summary, report_path, json_path),
            )

    def list_runs(self, task_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """某任务的执行历史，新→旧。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_task_runs WHERE task_id=? ORDER BY run_id DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, task_id: str, run_id: int) -> Optional[Dict[str, Any]]:
        """取单条执行历史（带 task_id 校验，防越权取他人任务的历史）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_task_runs WHERE task_id=? AND run_id=?",
                (task_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def set_status(
        self, task_id: str, status: str, *, next_run_at: Optional[datetime] = None
    ) -> bool:
        """切换任务状态（active/paused）。暂停不动 next_run_at；恢复时传入重算后的值。"""
        with self._lock, self._conn() as conn:
            if next_run_at is not None:
                cur = conn.execute(
                    "UPDATE scheduled_tasks SET status=?, next_run_at=? WHERE task_id=?",
                    (status, _iso(next_run_at), task_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE scheduled_tasks SET status=? WHERE task_id=?", (status, task_id)
                )
            return cur.rowcount > 0

    def edit(
        self,
        task_id: str,
        *,
        name: Optional[str],
        user_input: str,
        provider: Optional[str],
        model: Optional[str],
        trigger_type: str,
        cron_expr: Optional[str],
        interval_seconds: Optional[int],
        run_at: Optional[datetime],
        next_run_at: Optional[datetime],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> bool:
        """整体替换任务的可编辑字段（触发方式/文案/生效区间）+ 重算后的 next_run_at。

        调用方负责把「未改动的字段」与「表单新值」先合并好再传入——这里不做部分更新，
        避免 None 到底是「清空」还是「不改」的歧义。
        """
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE scheduled_tasks
                   SET name=?, user_input=?, provider=?, model=?, trigger_type=?, cron_expr=?,
                       interval_seconds=?, run_at=?, next_run_at=?, start_date=?, end_date=?
                   WHERE task_id=?""",
                (
                    name, user_input, provider, model, trigger_type, cron_expr,
                    interval_seconds, _iso(run_at), _iso(next_run_at), start_date, end_date,
                    task_id,
                ),
            )
            return cur.rowcount > 0

    @staticmethod
    def _recent_runs_where(
        owner_user_id: Optional[str], task_id: Optional[str], success: Optional[bool], q: Optional[str]
    ) -> tuple:
        clauses = ["1=1"]
        params: List[Any] = []
        if owner_user_id is not None:
            clauses.append("t.owner_user_id=?")
            params.append(owner_user_id)
        if task_id:
            clauses.append("r.task_id=?")
            params.append(task_id)
        if success is not None:
            clauses.append("r.success=?")
            params.append(1 if success else 0)
        if q:
            clauses.append("r.summary LIKE ?")
            params.append(f"%{q}%")
        return " AND ".join(clauses), params

    def list_recent_runs(
        self,
        owner_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        task_id: Optional[str] = None,
        success: Optional[bool] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """跨任务聚合的执行历史（新→旧），供“运行记录”Tab 展示，带任务名。

        task_id/success/q 用于筛选（按任务/按成败/按摘要关键词），配合 count_recent_runs
        （相同筛选条件）供前端做后端分页。
        """
        where, params = self._recent_runs_where(owner_user_id, task_id, success, q)
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT r.*, t.name AS task_name, t.user_input AS task_user_input
                    FROM scheduled_task_runs r JOIN scheduled_tasks t ON t.task_id = r.task_id
                    WHERE {where} ORDER BY r.run_id DESC LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_recent_runs(
        self,
        owner_user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        success: Optional[bool] = None,
        q: Optional[str] = None,
    ) -> int:
        """配合 list_recent_runs 分页用的总条数（同一组筛选条件）。"""
        where, params = self._recent_runs_where(owner_user_id, task_id, success, q)
        with self._conn() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) AS c FROM scheduled_task_runs r
                    JOIN scheduled_tasks t ON t.task_id = r.task_id WHERE {where}""",
                params,
            ).fetchone()
        return int(row["c"])

    def cancel(self, task_id: str) -> bool:
        """取消任务（标记 cancelled）。返回是否有改动。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE scheduled_tasks SET status='cancelled' WHERE task_id=? AND status!='cancelled'",
                (task_id,),
            )
            return cur.rowcount > 0
