"""账户执行授权与停止证明；复用调用者的 SQLite 业务事务。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import sqlite3
from uuid import uuid4

RESOURCE_KINDS = frozenset({"workspace", "data", "harness", "source", "validation", "chat", "candidate", "schedule"})
STATES = frozenset({"active", "idle", "paused", "cleanup_failed"})
ERROR_CODES = frozenset({"scheduler_unavailable", "holder_unknown", "cleanup_failed", "reconciliation_failed"})
_UNCHANGED = object()


class ExecutionDenied(PermissionError):
    """执行授权已经失效或无法核对。"""


@dataclass(frozen=True)
class ExecutionAuthorization:
    owner_user_id: str
    generation: int

    def __post_init__(self):
        if not isinstance(self.owner_user_id, str) or not self.owner_user_id.strip():
            raise ValueError("执行需要有效 Owner")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise ValueError("执行代数无效")


_authorization: ContextVar[ExecutionAuthorization | None] = ContextVar("account_execution_authorization", default=None)


@contextmanager
def execution_context(auth: ExecutionAuthorization):
    if not isinstance(auth, ExecutionAuthorization):
        raise ValueError("需要冻结执行授权")
    token = _authorization.set(auth)
    try:
        yield auth
    finally:
        _authorization.reset(token)


def current_authorization(*, required: bool = True) -> ExecutionAuthorization | None:
    auth = _authorization.get()
    if auth is None and required:
        raise ExecutionDenied("缺少冻结的执行授权")
    return auth


def _row(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    value = cursor.fetchone()
    return dict(zip((item[0] for item in cursor.description), value)) if value is not None else None


def _transaction(conn, now=None):
    if not conn.in_transaction:
        raise ValueError("执行变更必须使用调用者持有的事务")
    if now is not None and (isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now)):
        raise ValueError("执行时间无效")


def _resource(kind, resource_id):
    if kind not in RESOURCE_KINDS or not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("执行资源类型或编号无效")


def capture_authorization(conn: sqlite3.Connection, owner_user_id: str) -> ExecutionAuthorization:
    user = _row(conn, "SELECT execution_generation, disabled, pending FROM users WHERE user_id=?", (owner_user_id,))
    if user is None or user["disabled"] or user["pending"]:
        raise ExecutionDenied("所属账号不可执行")
    return ExecutionAuthorization(owner_user_id, user["execution_generation"])


def require_authorized(conn: sqlite3.Connection, auth: ExecutionAuthorization) -> None:
    _transaction(conn)
    if not isinstance(auth, ExecutionAuthorization) or capture_authorization(conn, auth.owner_user_id) != auth:
        raise ExecutionDenied("执行授权已变化")


def _binding(conn, owner_user_id, kind, resource_id):
    _resource(kind, resource_id)
    return _row(conn, "SELECT * FROM account_execution_bindings WHERE owner_user_id=? AND resource_kind=? AND resource_id=?", (owner_user_id, kind, resource_id))


def require_binding(conn, auth, resource_kind, resource_id) -> dict:
    require_authorized(conn, auth)
    binding = _binding(conn, auth.owner_user_id, resource_kind, resource_id)
    if binding is None or binding["generation"] != auth.generation or binding["state"] not in {"active", "idle"}:
        raise ExecutionDenied("执行绑定缺失或已阻断")
    return binding


def bind_execution(conn, auth, resource_kind, resource_id, *, state="active", now) -> dict:
    _transaction(conn, now)
    _resource(resource_kind, resource_id)
    if state not in {"active", "idle"}:
        raise ValueError("首次绑定必须明确是否有在途执行")
    require_authorized(conn, auth)
    if _binding(conn, auth.owner_user_id, resource_kind, resource_id) is not None:
        return require_binding(conn, auth, resource_kind, resource_id)
    conn.execute("INSERT INTO account_execution_bindings(owner_user_id, resource_kind, resource_id, generation, state, updated_at) VALUES (?,?,?,?,?,?)", (auth.owner_user_id, resource_kind, resource_id, auth.generation, state, now))
    return require_binding(conn, auth, resource_kind, resource_id)


def set_execution_state(conn, auth, kind, resource_id, *, state, now) -> dict:
    _transaction(conn, now)
    if state not in {"active", "idle"}:
        raise ValueError("停止确认与显式恢复必须使用独立原语")
    require_binding(conn, auth, kind, resource_id)
    conn.execute("UPDATE account_execution_bindings SET state=?, updated_at=? WHERE owner_user_id=? AND resource_kind=? AND resource_id=? AND generation=?", (state, now, auth.owner_user_id, kind, resource_id, auth.generation))
    return require_binding(conn, auth, kind, resource_id)


def confirm_execution_stopped(conn, owner_user_id, kind, resource_id, *, expected_generation, cleanup_failed=False, now) -> bool:
    _transaction(conn, now)
    ExecutionAuthorization(owner_user_id, expected_generation)
    binding = _binding(conn, owner_user_id, kind, resource_id)
    if binding is None or binding["generation"] != expected_generation:
        return False
    # 已确认静默后，迟到失败不能再次污染收口；新代绑定更不能被旧回调覆盖。
    if binding["state"] == "paused":
        return not cleanup_failed
    conn.execute("UPDATE account_execution_bindings SET state=?, updated_at=? WHERE owner_user_id=? AND resource_kind=? AND resource_id=? AND generation=?", ("cleanup_failed" if cleanup_failed else "paused", now, owner_user_id, kind, resource_id, expected_generation))
    return True


def resume_execution(conn, auth, kind, resource_id, *, expected_generation, now) -> dict:
    _transaction(conn, now)
    require_authorized(conn, auth)
    ExecutionAuthorization(auth.owner_user_id, expected_generation)
    binding = _binding(conn, auth.owner_user_id, kind, resource_id)
    if binding is None or binding["generation"] != expected_generation or expected_generation > auth.generation or binding["state"] != "paused":
        raise ExecutionDenied("执行尚未确认静默，不能恢复")
    conn.execute("UPDATE account_execution_bindings SET generation=?, state='idle', updated_at=? WHERE owner_user_id=? AND resource_kind=? AND resource_id=? AND generation=? AND state='paused'", (auth.generation, now, auth.owner_user_id, kind, resource_id, expected_generation))
    return require_binding(conn, auth, kind, resource_id)


def list_execution_bindings(conn, owner_user_id, *, before_generation=None) -> list[dict]:
    where, args = "owner_user_id=?", [owner_user_id]
    if before_generation is not None:
        where += " AND generation<?"
        args.append(before_generation)
    cursor = conn.execute("SELECT * FROM account_execution_bindings WHERE " + where + " ORDER BY resource_kind,resource_id", args)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def update_account_status(conn, owner_user_id, *, disabled=None, pending=None, actor_user_id, now) -> dict | None:
    _transaction(conn, now)
    if any(value is not None and not isinstance(value, bool) for value in (disabled, pending)):
        raise ValueError("账号状态必须为布尔值")
    if not isinstance(actor_user_id, str) or not actor_user_id.strip():
        raise ValueError("账号治理必须记录真实操作者")
    user = _row(conn, "SELECT disabled,pending,execution_generation FROM users WHERE user_id=?", (owner_user_id,))
    if user is None:
        raise ExecutionDenied("所属账号不存在")
    next_disabled = user["disabled"] if disabled is None else int(disabled)
    next_pending = user["pending"] if pending is None else int(pending)
    entering_hold = not (user["disabled"] or user["pending"]) and (next_disabled or next_pending)
    generation = user["execution_generation"] + int(bool(entering_hold))
    conn.execute("UPDATE users SET disabled=?,pending=?,execution_generation=? WHERE user_id=?", (next_disabled, next_pending, generation, owner_user_id))
    if entering_hold:
        # 撤销业务 Grant 与停用同事务；重新启用不会恢复旧 Run 的外发权利。
        conn.execute("UPDATE model_connection_grants SET revoked_at=strftime('%Y-%m-%dT%H:%M:%f',?,'unixepoch'),revoke_reason='account_execution_hold' WHERE owner_user_id=? AND revoked_at IS NULL", (now, owner_user_id))
        conn.execute("INSERT INTO account_execution_holds(operation_id,owner_user_id,generation,actor_user_id,reason,status,reconciliation_complete,created_at,updated_at) VALUES (?,?,?,?,?,'pending',0,?,?)", (uuid4().hex, owner_user_id, generation, actor_user_id, "disabled" if next_disabled else "pending", now, now))
        # idle 已由业务方确认无在途执行；active 必须等待实际停止证明。
        conn.execute("UPDATE account_execution_bindings SET state='paused',updated_at=? WHERE owner_user_id=? AND generation<? AND state='idle'", (now, owner_user_id, generation))
    return _row(conn, "SELECT * FROM account_execution_holds WHERE owner_user_id=? AND generation=?", (owner_user_id, generation)) if next_disabled or next_pending else None


def refresh_hold_operation(conn, operation_id, *, reconciliation_complete=None, error_code=_UNCHANGED, now) -> dict:
    _transaction(conn, now)
    if error_code is not _UNCHANGED and error_code is not None and error_code not in ERROR_CODES:
        raise ValueError("停用错误必须为低敏固定代码")
    if reconciliation_complete is not None and not isinstance(reconciliation_complete, bool):
        raise ValueError("收口确认必须为布尔值")
    operation = _row(conn, "SELECT * FROM account_execution_holds WHERE operation_id=?", (operation_id,))
    if operation is None:
        raise KeyError("停用操作不存在")
    # 普通刷新不能抹去持久失败；成功重试必须显式清除错误。
    if error_code is _UNCHANGED:
        error_code = operation["error_code"]
    counts = dict(conn.execute("SELECT state,count(*) FROM account_execution_bindings WHERE owner_user_id=? AND generation<? GROUP BY state", (operation["owner_user_id"], operation["generation"])).fetchall())
    complete = operation["reconciliation_complete"] if reconciliation_complete is None else int(reconciliation_complete)
    status = "failed" if error_code or counts.get("cleanup_failed") else ("pending" if counts.get("active") or not complete else "completed")
    conn.execute("UPDATE account_execution_holds SET status=?,reconciliation_complete=?,error_code=?,updated_at=? WHERE operation_id=?", (status, complete, error_code, now, operation_id))
    return _row(conn, "SELECT * FROM account_execution_holds WHERE operation_id=?", (operation_id,))


def list_hold_operations(conn, owner_user_id) -> list[dict]:
    cursor = conn.execute("SELECT * FROM account_execution_holds WHERE owner_user_id=? ORDER BY generation DESC", (owner_user_id,))
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
