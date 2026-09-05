"""
Web UI 用户与会话持久化（标准库 sqlite3，跨平台、无第三方依赖）。

- users          账号（用户名 + pbkdf2 密码哈希）
- conversations  会话（归属某 user，多用户隔离）
- messages       会话内消息（role/content/时间）

与业务 app.db / scheduler.db 分开，落在 settings.webui_db_path（默认 data/webui.db）。
连接模式沿用 scheduler/store.py：每次操作自带连接 + 进程内锁，规避 Windows 文件占用。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from src.database_migrations import DatabaseTarget, inspect_database
from src.config.secret_refs import (
    RUNTIME_CONFIG_SECRET_KEYS,
    SecretRefResolutionError,
    load_or_create_vault,
    load_vault,
    parse_secret_ref,
    secret_ref,
)
from src.model_connections.vault import VaultDecryptionError
from src.services.managed_paths import ManagedPathCodec


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class WebUIStore:
    """用户与会话的 SQLite 存储。"""

    def __init__(
        self,
        db_path: str = "data/webui.db",
        *,
        semantic_paths: ManagedPathCodec | None = None,
    ) -> None:
        self.db_path = db_path
        self.semantic_paths = semantic_paths
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        inspect_database(
            DatabaseTarget(profile="webui", path=Path(self.db_path))
        ).require_current()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
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

    # ---------- 运行时配置（全局/按用户两级覆盖，.env 为兜底） ----------
    def _resolve_existing_config_secret(
        self,
        conn: sqlite3.Connection,
        *,
        scope: str,
        key: str,
        value: str,
    ) -> tuple[str, object]:
        """在写操作前完整验证旧 Ref 的身份与可解密性。"""

        secret_id = parse_secret_ref(value)
        row = conn.execute(
            "SELECT ciphertext FROM runtime_config_secrets "
            "WHERE secret_id=? AND owner_scope=? AND config_key=?",
            (secret_id, scope, key),
        ).fetchone()
        if row is None:
            raise SecretRefResolutionError("SecretRef 无法解析")
        vault = load_vault(self.db_path)
        try:
            vault.decrypt(str(row["ciphertext"]))
        except VaultDecryptionError as exc:
            raise SecretRefResolutionError(
                "运行时配置 Vault 无法解密 SecretRef"
            ) from exc
        return secret_id, vault

    def config_all(self, scope: str) -> Dict[str, str]:
        """取某作用域的全部覆盖：{key: value}。scope='global' 或 user_id。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM runtime_config WHERE scope=?", (scope,)).fetchall()
            result: Dict[str, str] = {}
            vault = None
            for row in rows:
                key = str(row["key"])
                value = str(row["value"])
                if key not in RUNTIME_CONFIG_SECRET_KEYS:
                    result[key] = value
                    continue
                secret_id = parse_secret_ref(value)
                secret_row = conn.execute(
                    "SELECT ciphertext FROM runtime_config_secrets "
                    "WHERE secret_id=? AND owner_scope=? AND config_key=?",
                    (secret_id, scope, key),
                ).fetchone()
                if secret_row is None:
                    # Owner 与 key 都属于 SecretRef 身份；任一不匹配均失败关闭，
                    # 且不能向调用者区分“不存在”和“越权”。
                    raise SecretRefResolutionError("SecretRef 无法解析")
                vault = vault or load_vault(self.db_path)
                try:
                    result[key] = vault.decrypt(str(secret_row["ciphertext"]))
                except VaultDecryptionError as exc:
                    raise SecretRefResolutionError(
                        "运行时配置 Vault 无法解密 SecretRef"
                    ) from exc
        return result

    def config_set(self, scope: str, key: str, value: str, updated_by: str = "") -> None:
        with self._lock, self._conn() as conn:
            if key in RUNTIME_CONFIG_SECRET_KEYS:
                previous = conn.execute(
                    "SELECT value FROM runtime_config WHERE scope=? AND key=?",
                    (scope, key),
                ).fetchone()
                previous_id = None
                if previous is not None:
                    previous_id, vault = self._resolve_existing_config_secret(
                        conn,
                        scope=scope,
                        key=key,
                        value=str(previous["value"]),
                    )
                else:
                    vault = load_or_create_vault(self.db_path)
                secret_id = str(uuid.uuid4())
                opaque_ref = secret_ref(secret_id)
                ciphertext = vault.encrypt(value)
                conn.execute(
                    "INSERT INTO runtime_config_secrets "
                    "(secret_id, owner_scope, config_key, ciphertext, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (secret_id, scope, key, ciphertext, _now()),
                )
                conn.execute(
                    "INSERT INTO runtime_config "
                    "(scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(scope, key) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                    (scope, key, opaque_ref, _now(), updated_by),
                )
                if previous_id is not None:
                    deleted = conn.execute(
                        "DELETE FROM runtime_config_secrets "
                        "WHERE secret_id=? AND owner_scope=? AND config_key=?",
                        (previous_id, scope, key),
                    ).rowcount
                    if deleted != 1:
                        # 旧引用的 Owner/key 绑定不匹配时必须回滚整个替换事务，
                        # 不能用新值覆盖来掩盖已有的越权或损坏状态。
                        raise SecretRefResolutionError("SecretRef 无法解析")
                return
            conn.execute(
                "INSERT INTO runtime_config (scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope, key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (scope, key, value, _now(), updated_by),
            )

    def config_delete(self, scope: str, key: str) -> None:
        with self._lock, self._conn() as conn:
            previous = conn.execute(
                "SELECT value FROM runtime_config WHERE scope=? AND key=?",
                (scope, key),
            ).fetchone()
            if key in RUNTIME_CONFIG_SECRET_KEYS and previous is not None:
                secret_id, _vault = self._resolve_existing_config_secret(
                    conn,
                    scope=scope,
                    key=key,
                    value=str(previous["value"]),
                )
            conn.execute("DELETE FROM runtime_config WHERE scope=? AND key=?", (scope, key))
            if key in RUNTIME_CONFIG_SECRET_KEYS and previous is not None:
                deleted = conn.execute(
                    "DELETE FROM runtime_config_secrets "
                    "WHERE secret_id=? AND owner_scope=? AND config_key=?",
                    (secret_id, scope, key),
                ).rowcount
                if deleted != 1:
                    raise SecretRefResolutionError("SecretRef 无法解析")

    # ---------- Cookie 健康状态（手动/定时验证结果落库，供配置中心展示） ----------
    def cookie_health_set(self, key: str, status: str, message: str, checked_by: str) -> None:
        """写入/覆盖某 Cookie 的最近一次验证结果（key 唯一，覆盖旧记录）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO cookie_health (key, status, message, checked_at, checked_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET status=excluded.status, message=excluded.message, "
                "checked_at=excluded.checked_at, checked_by=excluded.checked_by",
                (key, status, message, _now(), checked_by),
            )

    def cookie_health_all(self) -> Dict[str, Dict[str, str]]:
        """返回 {key: {status, message, checked_at, checked_by}}；没验证过的 key 不出现在字典里。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, status, message, checked_at, checked_by FROM cookie_health"
            ).fetchall()
        return {r["key"]: dict(r) for r in rows}

    # ---------- 个人记忆（按用户隔离，区别于全局共享的 memory/user-preferences.md） ----------
    def memory_add(
        self,
        user_id: str,
        text: str,
        *,
        purpose: str = "general",
        source: str = "user_entered",
    ) -> Dict[str, Any]:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO user_memory "
                "(user_id, text, purpose, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, text, purpose, source, _now()),
            )
            row_id = cur.lastrowid
        return {
            "id": row_id,
            "user_id": user_id,
            "text": text,
            "purpose": purpose,
            "source": source,
        }

    def memory_list(self, user_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, text, purpose, source, created_at FROM user_memory "
                "WHERE user_id=? AND deleted_at IS NULL ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def memory_delete(self, user_id: str, memory_id: int) -> bool:
        """按 Owner 软删除；已冻结 TaskRevision 仍保留当时的脱敏摘要。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE user_memory SET deleted_at=? "
                "WHERE id=? AND user_id=? AND deleted_at IS NULL",
                (_now(), memory_id, user_id),
            )
        return cur.rowcount > 0

    # ---------- 模板库/教训库定时巡检日志 ----------
    def library_dedup_scan_log_add(
        self,
        templates_scanned: int,
        templates_merged: int,
        lessons_scanned: int,
        lessons_merged: int,
        stale_drafts_deleted: int,
        details: str = "",
    ) -> None:
        """每轮巡检结束后写一行记录，即便本轮计数全为0也写（用于确认巡检确实在跑）。
        details 为该轮每步操作（合并/清理）的 JSON 明细数组，供前端"查看详情"展示。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO library_dedup_scan_log "
                "(ran_at, templates_scanned, templates_merged, lessons_scanned, "
                "lessons_merged, stale_drafts_deleted, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), templates_scanned, templates_merged, lessons_scanned,
                 lessons_merged, stale_drafts_deleted, details),
            )

    def library_dedup_scan_log_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回最近 limit 轮巡检记录，按写入顺序倒序（最新的在前）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, ran_at, templates_scanned, templates_merged, lessons_scanned, "
                "lessons_merged, stale_drafts_deleted, details FROM library_dedup_scan_log "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 记忆命中埋点（方案 D：评估闭环可观测性） ----------
    def memory_hit_log_add(
        self, hit_type: str, slug: str, threshold: float,
        degrade_path: str, task_id: str = "", hit: bool = True,
    ) -> None:
        """记录一次记忆召回尝试（教训/模板/技能），供概览页聚合统计命中率与降级路径分布。
        hit=False 时为未命中埋点（E3）：slug 通常为空串，degrade_path 保留召回过程走到哪一步
        （none=库中无同类型候选，semantic=语义召回执行过但候选被 rerank/阈值筛空），
        用于诊断"记忆库有内容但注入不出去"这类召回判据问题，而不只是记录命中瞬间。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_hit_log (hit_at, hit_type, slug, threshold, degrade_path, task_id, hit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), hit_type, slug, threshold, degrade_path, task_id, 1 if hit else 0),
            )

    def memory_hit_log_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """返回最近 limit 条召回记录（含命中与未命中），按时间倒序。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, hit_at, hit_type, slug, threshold, degrade_path, task_id, hit "
                "FROM memory_hit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def memory_hit_log_stats(self) -> List[Dict[str, Any]]:
        """按 hit_type 聚合总尝试数/命中数/降级路径分布，供概览页展示命中率。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT hit_type, "
                "COUNT(*) AS count, "
                "SUM(CASE WHEN hit=1 THEN 1 ELSE 0 END) AS hit_count, "
                "SUM(CASE WHEN degrade_path='semantic' THEN 1 ELSE 0 END) AS semantic_count, "
                "SUM(CASE WHEN degrade_path='keyword' THEN 1 ELSE 0 END) AS keyword_count, "
                "ROUND(AVG(CASE WHEN hit=1 THEN threshold ELSE NULL END), 3) AS avg_threshold "
                "FROM memory_hit_log GROUP BY hit_type ORDER BY count DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 用户 ----------
    def bootstrap_super_admin(
        self, username: str, password_hash: str, display_name: str = "",
    ) -> Dict[str, Any]:
        """仅供本机维护者显式初始化；已有首管时不修改任何账号。"""
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as conn:
            # 单条条件写入让跨实例、跨进程的并发初始化也只能成功一次。
            inserted = conn.execute(
                "INSERT INTO users (user_id, username, password_hash, display_name, role, pending, created_at) "
                "SELECT ?, ?, ?, ?, 'super_admin', 0, ? "
                "WHERE NOT EXISTS (SELECT 1 FROM users WHERE role='super_admin')",
                (user_id, username, password_hash, display_name or username, _now()),
            )
            if inserted.rowcount != 1:
                raise ValueError("超级管理员已存在，禁止重复初始化")
        return {"user_id": user_id, "username": username, "role": "super_admin"}

    def create_user(
        self, username: str, password_hash: str, display_name: str = "",
        role: str = "user", pending: bool = False,
    ) -> Dict[str, Any]:
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO users (user_id, username, password_hash, display_name, role, pending, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, password_hash, display_name or username, role, 1 if pending else 0, _now()),
            )
        return {
            "user_id": user_id, "username": username,
            "display_name": display_name or username, "role": role, "pending": pending,
        }

    def get_user_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def count_users(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def count_admins(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'").fetchone()["c"]

    def list_users(
        self, q: str = "", role: str = "", status: str = "",
        page: int = 1, page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按关键词/角色/状态过滤 + 分页列出用户（不含密码哈希），供管理员后台。

        q: 匹配 username 或 display_name 的子串，空串不过滤。
        role: "" | super_admin | admin | user，空串不过滤。
        status: "" | normal | disabled | pending，空串不过滤。
        返回 (当前页用户列表, 过滤后总数)。
        """
        where: List[str] = []
        params: List[Any] = []
        if q:
            where.append("(username LIKE ? OR display_name LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        if role:
            where.append("role=?")
            params.append(role)
        if status == "normal":
            where.append("disabled=0 AND pending=0")
        elif status == "disabled":
            where.append("disabled=1")
        elif status == "pending":
            where.append("pending=1")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        offset = max(0, (page - 1) * page_size)
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS c FROM users {clause}", params).fetchone()["c"]
            rows = conn.execute(
                f"SELECT user_id, username, display_name, role, disabled, pending, created_at "
                f"FROM users {clause} ORDER BY created_at, rowid LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def count_pending(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users WHERE pending=1").fetchone()["c"]

    def update_user(
        self,
        user_id: str,
        *,
        role: Optional[str] = None,
        disabled: Optional[bool] = None,
        pending: Optional[bool] = None,
        password_hash: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> None:
        """更新用户角色/禁用/审批/密码/昵称（仅设置传入的字段）。"""
        sets: List[str] = []
        vals: List[Any] = []
        if role is not None:
            sets.append("role=?"); vals.append(role)
        if disabled is not None:
            sets.append("disabled=?"); vals.append(1 if disabled else 0)
        if pending is not None:
            sets.append("pending=?"); vals.append(1 if pending else 0)
        if password_hash is not None:
            sets.append("password_hash=?"); vals.append(password_hash)
        if display_name is not None:
            sets.append("display_name=?"); vals.append(display_name)
        if not sets:
            return
        vals.append(user_id)
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id=?", vals)

    def delete_user(self, user_id: str) -> None:
        """删除用户及其全部会话/消息/个人记忆。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT conv_id FROM conversations WHERE user_id=?", (user_id,)
            ).fetchall()
            for r in rows:
                conn.execute("DELETE FROM messages WHERE conv_id=?", (r["conv_id"],))
            conn.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM user_memory WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM user_ui_state WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))

    # ---------- 应用设置（KV，运行时可改）----------
    def get_setting(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---------- 用户界面状态（按用户隔离，不承载业务配置或 Secret） ----------
    def get_user_ui_state(self, user_id: str, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM user_ui_state WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()
        return row["value"] if row else None

    def set_user_ui_state(self, user_id: str, key: str, value: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO user_ui_state (user_id, key, value, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (user_id, key, value, _now()),
            )

    # ---------- 会话 ----------
    def create_conversation(self, user_id: str, title: str = "新会话") -> Dict[str, Any]:
        conv_id = f"c_{uuid.uuid4().hex[:12]}"
        ts = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (conv_id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, title, ts, ts),
            )
        return {"conv_id": conv_id, "user_id": user_id, "title": title, "created_at": ts, "updated_at": ts}

    def list_conversations(self, user_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE conv_id=?", (conv_id,)).fetchone()
        return dict(row) if row else None

    def rename_conversation(self, conv_id: str, title: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE conv_id=?",
                (title, _now(), conv_id),
            )

    def delete_conversation(self, conv_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE conv_id=?", (conv_id,))

    # ---------- 消息 ----------
    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        *,
        task_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """追加一条消息并返回新消息 id（供前端反馈定位）。"""
        meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conv_id, role, content, task_id, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, role, content, task_id, meta_json, _now()),
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE conv_id=?", (_now(), conv_id))
            return cur.lastrowid

    def list_messages(self, conv_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, role, content, task_id, meta_json, created_at FROM messages "
                "WHERE conv_id=? ORDER BY id",
                (conv_id,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["meta"] = json.loads(d.pop("meta_json")) if d.get("meta_json") else None
            out.append(d)
        return out

    # ---------- 消息反馈（点赞/点踩） ----------
    def upsert_feedback(
        self, message_id: int, conv_id: str, user_id: str,
        rating: str, reasons: Optional[str] = None, comment: Optional[str] = None,
    ) -> None:
        """提交/更新一条消息反馈（UNIQUE(message_id,user_id) 天然覆盖）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO message_feedback (message_id, conv_id, user_id, rating, reasons, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(message_id, user_id) DO UPDATE SET "
                "rating=excluded.rating, reasons=excluded.reasons, comment=excluded.comment, created_at=excluded.created_at",
                (message_id, conv_id, user_id, rating, reasons, comment, _now()),
            )

    def list_feedback(self, conv_id: str, user_id: str) -> Dict[int, Dict[str, Any]]:
        """返回当前用户在某会话内的反馈映射 {message_id: {rating, reasons, comment}}。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT message_id, rating, reasons, comment FROM message_feedback "
                "WHERE conv_id=? AND user_id=?",
                (conv_id, user_id),
            ).fetchall()
        return {
            r["message_id"]: {"rating": r["rating"], "reasons": r["reasons"], "comment": r["comment"]}
            for r in rows
        }

    def delete_feedback(self, message_id: int, user_id: str) -> None:
        """删除一条消息反馈（取消点赞/点踩）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM message_feedback WHERE message_id=? AND user_id=?",
                (message_id, user_id),
            )

    # ---------- 反馈统计与明细（开发者视角，管理员只读） ----------
    def feedback_overview(self) -> Dict[str, Any]:
        """全局反馈统计：赞/踩总数、点踩率、点踩原因分布、按天趋势（最近30天）。"""
        from collections import Counter, defaultdict

        with self._conn() as conn:
            total_up = conn.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE rating='up'"
            ).fetchone()[0]
            total_down = conn.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE rating='down'"
            ).fetchone()[0]
            total_pending = conn.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE status='pending'"
            ).fetchone()[0]
            total_sessions = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            # 点踩原因分布（reasons 是 JSON 数组字符串，Python 层解析统计）
            down_rows = conn.execute(
                "SELECT reasons FROM message_feedback WHERE rating='down' "
                "AND reasons IS NOT NULL AND reasons != ''"
            ).fetchall()
            reason_counts: Counter = Counter()
            for r in down_rows:
                try:
                    for reason in json.loads(r["reasons"]):
                        reason_counts[reason] += 1
                except Exception:
                    pass
            # 按天趋势
            all_rows = conn.execute(
                "SELECT rating, created_at FROM message_feedback ORDER BY created_at"
            ).fetchall()
            daily_map: Dict[str, Dict[str, int]] = defaultdict(lambda: {"up": 0, "down": 0})
            for r in all_rows:
                day = (r["created_at"] or "")[:10]  # YYYY-MM-DD
                if day:
                    daily_map[day][r["rating"]] += 1
            daily = [{"date": d, "up": v["up"], "down": v["down"]} for d, v in sorted(daily_map.items())][-30:]
        total = total_up + total_down
        return {
            "total_up": total_up,
            "total_down": total_down,
            "total_pending": total_pending,
            "total_sessions": total_sessions,
            "down_rate": round(total_down / total, 4) if total else 0.0,
            "reason_counts": dict(reason_counts),
            "daily": daily,
        }

    def feedback_list(
        self, *, limit: int = 20, offset: int = 0,
        rating: Optional[str] = None, reason: Optional[str] = None,
        user_id: Optional[str] = None,
        date_from: Optional[str] = None, date_to: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """反馈明细分页列表（join messages 拿原始问答），带筛选。"""
        where = []
        params: list = []
        if rating:
            where.append("f.rating = ?")
            params.append(rating)
        if reason:
            where.append("f.reasons LIKE ?")
            params.append(f"%{reason}%")
        if user_id:
            where.append("f.user_id = ?")
            params.append(user_id)
        if date_from:
            where.append("f.created_at >= ?")
            params.append(date_from)
        if date_to:
            # 兼容仅传日期的情况：补到当天末尾
            params.append(date_to if "T" in date_to or " " in date_to else date_to + " 23:59:59")
            where.append("f.created_at <= ?")
        if status:
            where.append("f.status = ?")
            params.append(status)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        with self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM message_feedback f{where_sql}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT f.id, f.message_id, f.conv_id, f.user_id, f.rating,
                           f.reasons, f.comment, f.created_at, f.status, f.admin_note,
                           m.content AS answer,
                           m.meta_json AS meta_json,
                           u.display_name AS display_name, u.username AS username,
                           (SELECT content FROM messages
                            WHERE conv_id = f.conv_id AND role='user' AND id < f.message_id
                            ORDER BY id DESC LIMIT 1) AS question
                    FROM message_feedback f
                    LEFT JOIN messages m ON f.message_id = m.id
                    LEFT JOIN users u ON f.user_id = u.user_id
                    {where_sql}
                    ORDER BY f.id DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            reasons = d.get("reasons")
            try:
                d["reasons"] = json.loads(reasons) if reasons else []
            except Exception:
                d["reasons"] = []
            # 从消息 meta_json 解析模型（assistant 消息产生时记录的实际模型）
            meta_raw = d.pop("meta_json", None)
            d["model"] = None
            if meta_raw:
                try:
                    d["model"] = json.loads(meta_raw).get("model")
                except Exception:
                    pass
            items.append(d)
        return {"total": total, "items": items}

    def update_feedback_status(self, fb_id: int, status: str, admin_note: Optional[str] = None) -> None:
        """管理员更新反馈处理状态与备注。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE message_feedback SET status=?, admin_note=? WHERE id=?",
                (status, admin_note, fb_id),
            )

    def delete_feedback_admin(self, fb_id: int) -> None:
        """管理员删除一条反馈（按 feedback id，区别于用户取消自己的反馈）。"""
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM message_feedback WHERE id=?", (fb_id,))

    def user_owns_task(self, user_id: str, task_id: str) -> bool:
        """该 task_id 是否属于用户的会话任务或数据准备任务。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM messages m JOIN conversations c ON m.conv_id=c.conv_id "
                "WHERE m.task_id=? AND c.user_id=? LIMIT 1",
                (task_id, user_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT 1 FROM data_prep_tasks WHERE task_id=? AND user_id=? LIMIT 1",
                    (task_id, user_id),
                ).fetchone()
        return row is not None

    # ---------- 数据准备任务（Phase 2 Task 10）----------
    def create_data_prep_task(
        self,
        user_id: str,
        task_id: str,
        spec_dict: Dict[str, Any],
        *,
        status: str = "RUNNING",
    ) -> Dict[str, Any]:
        """创建数据准备任务记录；旧调用默认仍为 RUNNING。"""
        now = _now()
        unit_id = spec_dict.get("unit_id")
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO data_prep_tasks "
                "(task_id, user_id, unit_id, spec_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    user_id,
                    unit_id,
                    json.dumps(spec_dict, ensure_ascii=False),
                    status,
                    now,
                    now,
                ),
            )
            if spec_dict.get("task_type") == "document_extraction":
                for ordinal, upload_id in enumerate(spec_dict.get("upload_ids") or []):
                    conn.execute(
                        "INSERT INTO data_prep_task_uploads "
                        "(task_id, upload_id, ordinal) VALUES (?, ?, ?)",
                        (task_id, str(upload_id), ordinal),
                    )
            if unit_id:
                conn.execute(
                    "UPDATE document_task_units SET updated_at=? "
                    "WHERE unit_id=? AND user_id=?",
                    (now, unit_id, user_id),
                )
        return self.get_data_prep_task(task_id) or {"task_id": task_id, "status": status}

    def get_data_prep_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """查询单个数据准备任务（含 spec 反序列化）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT task_id, user_id, unit_id, spec_json, status, record_counts, quality_json, "
                "manifest_path, error, created_at, updated_at FROM data_prep_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["spec"] = json.loads(d.pop("spec_json")) if d.get("spec_json") else {}
        d["record_counts"] = json.loads(d["record_counts"]) if d.get("record_counts") else {}
        d["quality"] = json.loads(d["quality_json"]) if d.get("quality_json") else None
        d.pop("quality_json", None)
        return d

    def update_data_prep_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        record_counts: Optional[Dict[str, Any]] = None,
        quality: Optional[Dict[str, Any]] = None,
        manifest_path: Optional[str] = None,
        error: Optional[str] = None,
        spec: Optional[Dict[str, Any]] = None,
    ) -> None:
        """更新任务执行结果字段。仅更新非 None 字段。"""
        sets, args = [], []
        if status is not None:
            sets.append("status=?"); args.append(status)
        if record_counts is not None:
            sets.append("record_counts=?"); args.append(json.dumps(record_counts, ensure_ascii=False))
        if quality is not None:
            sets.append("quality_json=?"); args.append(json.dumps(quality, ensure_ascii=False))
        if manifest_path is not None:
            sets.append("manifest_path=?"); args.append(manifest_path)
        if error is not None:
            sets.append("error=?"); args.append(error)
        if spec is not None:
            sets.append("spec_json=?")
            args.append(json.dumps(spec, ensure_ascii=False))
            sets.append("unit_id=?")
            args.append(spec.get("unit_id"))
        if not sets:
            return
        sets.append("updated_at=?"); args.append(_now())
        args.append(task_id)
        with self._lock, self._conn() as conn:
            conn.execute(
                f"UPDATE data_prep_tasks SET {', '.join(sets)} WHERE task_id=?", args
            )
            unit_row = conn.execute(
                "SELECT unit_id, user_id FROM data_prep_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if unit_row and unit_row["unit_id"]:
                conn.execute(
                    "UPDATE document_task_units SET updated_at=? "
                    "WHERE unit_id=? AND user_id=?",
                    (_now(), unit_row["unit_id"], unit_row["user_id"]),
                )
            if spec is not None and spec.get("task_type") == "document_extraction":
                conn.execute(
                    "DELETE FROM data_prep_task_uploads WHERE task_id=?",
                    (task_id,),
                )
                for ordinal, upload_id in enumerate(spec.get("upload_ids") or []):
                    conn.execute(
                        "INSERT INTO data_prep_task_uploads "
                        "(task_id, upload_id, ordinal) VALUES (?, ?, ?)",
                        (task_id, str(upload_id), ordinal),
                    )

    def transition_data_prep_task(
        self,
        task_id: str,
        *,
        from_statuses: set[str],
        to_status: str,
    ) -> bool:
        """以比较并交换方式转换任务状态，避免并发请求重复执行。"""
        if not from_statuses:
            return False
        placeholders = ",".join("?" for _ in from_statuses)
        now = _now()
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE data_prep_tasks SET status=?, updated_at=? "
                f"WHERE task_id=? AND status IN ({placeholders})",
                (to_status, now, task_id, *sorted(from_statuses)),
            )
            if cursor.rowcount != 1:
                return False
            unit_row = conn.execute(
                "SELECT unit_id, user_id FROM data_prep_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if unit_row and unit_row["unit_id"]:
                conn.execute(
                    "UPDATE document_task_units SET updated_at=? "
                    "WHERE unit_id=? AND user_id=?",
                    (now, unit_row["unit_id"], unit_row["user_id"]),
                )
            return True

    def list_data_prep_tasks(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """列出某用户的数据准备任务（最新优先）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT task_id, status, record_counts, manifest_path, error, created_at, updated_at "
                "FROM data_prep_tasks WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["record_counts"] = json.loads(d["record_counts"]) if d.get("record_counts") else {}
            out.append(d)
        return out

    def list_document_runs_for_upload(
        self,
        user_id: str,
        upload_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """列出某文件参与过的文档任务，最新版本优先。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT t.task_id FROM data_prep_tasks t "
                "JOIN data_prep_task_uploads u ON u.task_id=t.task_id "
                "WHERE t.user_id=? AND u.upload_id=? "
                "ORDER BY t.updated_at DESC, t.rowid DESC LIMIT ?",
                (user_id, upload_id, limit),
            ).fetchall()
        return [
            task
            for row in rows
            if (task := self.get_data_prep_task(row["task_id"])) is not None
        ]

    def create_document_unit(
        self,
        user_id: str,
        *,
        unit_type: str,
        name: str,
        upload_ids: List[str],
        business_type: str = "",
    ) -> Dict[str, Any]:
        """创建独立文件或文件集；原文件只建立关联，不复制。"""
        unique_ids = list(dict.fromkeys(str(item) for item in upload_ids))
        if unit_type not in {"single_file", "file_set"}:
            raise ValueError("不支持的任务单位类型")
        if unit_type == "single_file" and len(unique_ids) != 1:
            raise ValueError("独立文件任务必须且只能包含一个文件")
        if unit_type == "file_set" and len(unique_ids) < 2:
            raise ValueError("文件集至少需要两个文件")
        if unit_type == "single_file":
            with self._conn() as conn:
                existing = conn.execute(
                    "SELECT u.unit_id FROM document_task_units u "
                    "JOIN document_task_unit_members m ON m.unit_id=u.unit_id "
                    "WHERE u.user_id=? AND u.unit_type='single_file' "
                    "AND m.upload_id=? LIMIT 1",
                    (user_id, unique_ids[0]),
                ).fetchone()
            if existing:
                with self._lock, self._conn() as conn:
                    conn.execute(
                        "UPDATE document_task_units "
                        "SET archived_at=NULL, updated_at=? "
                        "WHERE unit_id=? AND user_id=?",
                        (_now(), existing["unit_id"], user_id),
                    )
                return self.get_document_unit(user_id, existing["unit_id"]) or {}
        unit_id = f"du_{uuid.uuid4().hex[:16]}"
        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO document_task_units "
                "(unit_id, user_id, unit_type, name, business_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    unit_id,
                    user_id,
                    unit_type,
                    name.strip(),
                    business_type.strip(),
                    now,
                    now,
                ),
            )
            for ordinal, upload_id in enumerate(unique_ids):
                conn.execute(
                    "INSERT INTO document_task_unit_members "
                    "(unit_id, upload_id, ordinal, added_at) VALUES (?, ?, ?, ?)",
                    (unit_id, upload_id, ordinal, now),
                )
        return self.get_document_unit(user_id, unit_id) or {}

    def get_document_unit(
        self,
        user_id: str,
        unit_id: str,
    ) -> Optional[Dict[str, Any]]:
        """读取一个任务单位及成员上传 ID。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT unit_id, user_id, unit_type, name, business_type, "
                "archived_at, created_at, updated_at FROM document_task_units "
                "WHERE unit_id=? AND user_id=?",
                (unit_id, user_id),
            ).fetchone()
            if not row:
                return None
            members = conn.execute(
                "SELECT upload_id, ordinal, added_at "
                "FROM document_task_unit_members WHERE unit_id=? ORDER BY ordinal",
                (unit_id,),
            ).fetchall()
        result = dict(row)
        result["upload_ids"] = [str(item["upload_id"]) for item in members]
        return result

    def create_document_scope_revision_task(
        self,
        user_id: str,
        task_id: str,
        spec_dict: Dict[str, Any],
        *,
        upload_ids: List[str],
        status: str = "READY",
    ) -> Dict[str, Any]:
        """原子创建新任务版本并更新任务单元当前成员。"""
        unit_id = str(spec_dict.get("unit_id") or "")
        unique_ids = list(dict.fromkeys(str(item) for item in upload_ids))
        if not unit_id or not unique_ids:
            raise ValueError("任务单元和文件范围不能为空")
        now = _now()
        with self._lock, self._conn() as conn:
            owned = conn.execute(
                "SELECT 1 FROM document_task_units WHERE unit_id=? AND user_id=?",
                (unit_id, user_id),
            ).fetchone()
            if not owned:
                raise ValueError("任务单元不存在")
            conn.execute(
                "INSERT INTO data_prep_tasks "
                "(task_id, user_id, unit_id, spec_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    user_id,
                    unit_id,
                    json.dumps(spec_dict, ensure_ascii=False),
                    status,
                    now,
                    now,
                ),
            )
            for ordinal, upload_id in enumerate(unique_ids):
                conn.execute(
                    "INSERT INTO data_prep_task_uploads "
                    "(task_id, upload_id, ordinal) VALUES (?, ?, ?)",
                    (task_id, upload_id, ordinal),
                )
            conn.execute(
                "DELETE FROM document_task_unit_members WHERE unit_id=?",
                (unit_id,),
            )
            for ordinal, upload_id in enumerate(unique_ids):
                conn.execute(
                    "INSERT INTO document_task_unit_members "
                    "(unit_id, upload_id, ordinal, added_at) VALUES (?, ?, ?, ?)",
                    (unit_id, upload_id, ordinal, now),
                )
            conn.execute(
                "UPDATE document_task_units SET updated_at=? "
                "WHERE unit_id=? AND user_id=?",
                (now, unit_id, user_id),
            )
        return self.get_data_prep_task(task_id) or {
            "task_id": task_id,
            "status": status,
        }

    def list_document_units(
        self,
        user_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """列出独立文件和文件集，最近活动优先。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT unit_id FROM document_task_units "
                "WHERE user_id=? AND archived_at IS NULL "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [
            unit
            for row in rows
            if (unit := self.get_document_unit(user_id, row["unit_id"])) is not None
        ]

    def archive_document_unit(self, user_id: str, unit_id: str) -> bool:
        """从工作区软移除任务单位；原文件、任务和结果全部保留。"""
        now = _now()
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                "UPDATE document_task_units "
                "SET archived_at=COALESCE(archived_at, ?), updated_at=? "
                "WHERE unit_id=? AND user_id=?",
                (now, now, unit_id, user_id),
            )
            if cursor.rowcount == 0:
                return False
            conn.execute(
                "UPDATE document_workspaces "
                "SET active_unit_id=NULL, active_task_id=NULL, "
                "selected_upload_id=NULL, updated_at=? "
                "WHERE user_id=? AND active_unit_id=?",
                (now, user_id, unit_id),
            )
        return True

    def list_document_runs_for_unit(
        self,
        user_id: str,
        unit_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """列出任务单位的不可变执行版本，最新优先。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT task_id FROM data_prep_tasks "
                "WHERE user_id=? AND unit_id=? "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (user_id, unit_id, limit),
            ).fetchall()
        return [
            task
            for row in rows
            if (task := self.get_data_prep_task(row["task_id"])) is not None
        ]

    def touch_document_unit(self, user_id: str, unit_id: str) -> None:
        """任务发生变化时更新单位排序时间。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE document_task_units SET updated_at=? "
                "WHERE unit_id=? AND user_id=?",
                (_now(), unit_id, user_id),
            )

    def document_workspace_get(self, user_id: str) -> Dict[str, Any]:
        """读取当前文档工作区；工作区范围独立于不可变历史任务。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT upload_ids_json, checked_upload_ids_json, "
                "active_unit_id, active_task_id, selected_upload_id, updated_at "
                "FROM document_workspaces WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return {
                "upload_ids": [],
                "checked_upload_ids": [],
                "active_unit_id": None,
                "active_task_id": None,
                "selected_upload_id": None,
                "updated_at": None,
            }
        upload_ids = json.loads(row["upload_ids_json"] or "[]")
        checked_upload_ids = json.loads(row["checked_upload_ids_json"] or "[]")
        return {
            "upload_ids": upload_ids,
            "checked_upload_ids": [
                upload_id for upload_id in checked_upload_ids if upload_id in upload_ids
            ],
            "active_unit_id": row["active_unit_id"],
            "active_task_id": row["active_task_id"],
            "selected_upload_id": row["selected_upload_id"],
            "updated_at": row["updated_at"],
        }

    def document_workspace_set(
        self,
        user_id: str,
        *,
        upload_ids: List[str],
        checked_upload_ids: Optional[List[str]] = None,
        active_unit_id: Optional[str] = None,
        active_task_id: Optional[str] = None,
        selected_upload_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """覆盖当前工作区范围；不删除上传原件和历史任务。"""
        unique_ids = list(dict.fromkeys(str(item) for item in upload_ids))
        if checked_upload_ids is None:
            checked_upload_ids = unique_ids
        unique_checked_ids = [
            upload_id
            for upload_id in dict.fromkeys(str(item) for item in checked_upload_ids)
            if upload_id in unique_ids
        ]
        if selected_upload_id not in unique_ids:
            selected_upload_id = unique_ids[0] if unique_ids else None
        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO document_workspaces "
                "(user_id, upload_ids_json, checked_upload_ids_json, "
                "active_unit_id, active_task_id, selected_upload_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "upload_ids_json=excluded.upload_ids_json, "
                "checked_upload_ids_json=excluded.checked_upload_ids_json, "
                "active_unit_id=excluded.active_unit_id, "
                "active_task_id=excluded.active_task_id, "
                "selected_upload_id=excluded.selected_upload_id, "
                "updated_at=excluded.updated_at",
                (
                    user_id,
                    json.dumps(unique_ids, ensure_ascii=False),
                    json.dumps(unique_checked_ids, ensure_ascii=False),
                    active_unit_id,
                    active_task_id,
                    selected_upload_id,
                    now,
                ),
            )
        return self.document_workspace_get(user_id)

    # ---------- checkpoint 读写（Phase 3 Task 2/7）----------
    def get_task_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取数据准备任务的 checkpoint_json，无则返回 None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT checkpoint_json FROM data_prep_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if not row or not row["checkpoint_json"]:
            return None
        return json.loads(row["checkpoint_json"])

    def set_task_checkpoint(self, task_id: str, checkpoint: Dict[str, Any]) -> None:
        """写入/更新数据准备任务的 checkpoint_json。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE data_prep_tasks SET checkpoint_json=?, updated_at=? WHERE task_id=?",
                (json.dumps(checkpoint, ensure_ascii=False), _now(), task_id),
            )

    # ---------- 数据库命名连接（Phase 3 Task 2）----------
    def create_db_connection(
        self,
        user_id: str,
        *,
        name: str,
        dialect: str,
        host: str = "",
        port: int = 0,
        database_name: str = "",
        username: str = "",
        password: str = "",
        sqlite_relpath: str = "",
    ) -> Dict[str, Any]:
        """创建数据库命名连接（密码 Fernet 加密落库）。返回公开 dict（无 password/password_enc）。"""
        from src.services.db_connections import encrypt_password, to_public_dict

        conn_id = str(uuid.uuid4())
        now = _now()
        enc = encrypt_password(password) if password else ""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO db_connections (connection_id, user_id, name, dialect, host, port, "
                "database_name, username, password_enc, sqlite_relpath, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conn_id, user_id, name, dialect, host, port, database_name, username,
                 enc, sqlite_relpath, now, now),
            )
        return to_public_dict(self.get_db_connection(conn_id)) if self.get_db_connection(conn_id) else {}

    def get_db_connection(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """获取单条连接（不校验归属，由 API 层校验）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT connection_id, user_id, name, dialect, host, port, database_name, "
                "username, password_enc, sqlite_relpath, created_at, updated_at "
                "FROM db_connections WHERE connection_id=?", (connection_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_db_connections(self, user_id: str) -> List[Dict[str, Any]]:
        """列出某用户的全部连接（不返回 password_enc 列）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT connection_id, user_id, name, dialect, host, port, database_name, "
                "username, sqlite_relpath, created_at, updated_at "
                "FROM db_connections WHERE user_id=? ORDER BY updated_at DESC", (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_db_connection(
        self,
        connection_id: str,
        user_id: str,
        *,
        name: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database_name: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        sqlite_relpath: Optional[str] = None,
    ) -> None:
        """部分更新连接字段。仅更新非 None 字段；password 非空时加密覆盖。"""
        sets, args = [], []
        if name is not None:
            sets.append("name=?"); args.append(name)
        if host is not None:
            sets.append("host=?"); args.append(host)
        if port is not None:
            sets.append("port=?"); args.append(port)
        if database_name is not None:
            sets.append("database_name=?"); args.append(database_name)
        if username is not None:
            sets.append("username=?"); args.append(username)
        if password is not None:
            from src.services.db_connections import encrypt_password
            enc = encrypt_password(password) if password else ""
            sets.append("password_enc=?")
            args.append(enc)
        if sqlite_relpath is not None:
            sets.append("sqlite_relpath=?"); args.append(sqlite_relpath)
        if not sets:
            return
        sets.append("updated_at=?"); args.append(_now())
        args += [connection_id, user_id]
        with self._lock, self._conn() as conn:
            conn.execute(
                f"UPDATE db_connections SET {', '.join(sets)} "
                "WHERE connection_id=? AND user_id=?", args
            )

    def delete_db_connection(self, connection_id: str, user_id: str) -> bool:
        """按 connection_id + user_id 匹配删除，防止越权。返回是否删除了行。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM db_connections WHERE connection_id=? AND user_id=?",
                (connection_id, user_id),
            )
        return cur.rowcount > 0

    # ---------- Phase 4B 语义计划 revision ----------
    @staticmethod
    def _semantic_plan_row(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "plan_id": row["plan_id"],
            "revision": row["revision"],
            "task_id": row["task_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "plan": json.loads(row["plan_json"]) if row["plan_json"] else None,
            "summary": row["summary"],
            "diagnostics": json.loads(row["diagnostics_json"] or "[]"),
            "clarification": (
                json.loads(row["clarification_json"])
                if row["clarification_json"]
                else None
            ),
            "provenance": json.loads(row["provenance_json"]),
            "plan_hash": row["plan_hash"],
            "created_at": row["created_at"],
        }

    def save_semantic_plan_revision(
        self,
        user_id: str,
        *,
        request: Any,
        result: Any,
    ) -> Dict[str, Any]:
        """只追加一个不可变 revision；同一 plan/revision 禁止覆盖。"""

        request_dict = request.model_dump(mode="json")
        result_dict = result.model_dump(mode="json")
        plan = result.plan
        plan_dict = plan.model_dump(mode="json") if plan is not None else None
        plan_hash = plan.canonical_hash() if plan is not None else None
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT INTO semantic_plan_revisions "
                    "(plan_id, revision, task_id, user_id, status, request_json, "
                    "plan_json, summary, diagnostics_json, clarification_json, "
                    "provenance_json, plan_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.plan_id,
                        result.revision,
                        result.task_id,
                        user_id,
                        result.status.value,
                        json.dumps(request_dict, ensure_ascii=False),
                        (
                            json.dumps(plan_dict, ensure_ascii=False)
                            if plan_dict is not None
                            else None
                        ),
                        result.summary,
                        json.dumps(
                            result_dict["diagnostics"],
                            ensure_ascii=False,
                        ),
                        (
                            json.dumps(
                                result_dict["clarification"],
                                ensure_ascii=False,
                            )
                            if result_dict["clarification"] is not None
                            else None
                        ),
                        json.dumps(
                            result_dict["provenance"],
                            ensure_ascii=False,
                        ),
                        plan_hash,
                        _now(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该语义计划 revision 已存在，禁止覆盖") from exc
        saved = self.get_semantic_plan_revision(
            user_id,
            result.plan_id,
            result.revision,
        )
        assert saved is not None
        return saved

    def get_semantic_plan_revision(
        self,
        user_id: str,
        plan_id: str,
        revision: int,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT plan_id, revision, task_id, status, request_json, "
                "plan_json, summary, diagnostics_json, clarification_json, "
                "provenance_json, plan_hash, created_at "
                "FROM semantic_plan_revisions "
                "WHERE user_id=? AND plan_id=? AND revision=?",
                (user_id, plan_id, revision),
            ).fetchone()
        return self._semantic_plan_row(row)

    def list_semantic_plan_revisions(
        self,
        user_id: str,
        plan_id: str,
    ) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT plan_id, revision, task_id, status, request_json, "
                "plan_json, summary, diagnostics_json, clarification_json, "
                "provenance_json, plan_hash, created_at "
                "FROM semantic_plan_revisions "
                "WHERE user_id=? AND plan_id=? ORDER BY revision DESC",
                (user_id, plan_id),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._semantic_plan_row(row)) is not None
        ]

    def latest_semantic_plan_revision(
        self,
        user_id: str,
        plan_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT plan_id, revision, task_id, status, request_json, "
                "plan_json, summary, diagnostics_json, clarification_json, "
                "provenance_json, plan_hash, created_at "
                "FROM semantic_plan_revisions "
                "WHERE user_id=? AND plan_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (user_id, plan_id),
            ).fetchone()
        return self._semantic_plan_row(row)

    # ---------- Phase 4B 批次 2 来源检查与绑定 ----------
    @staticmethod
    def _semantic_binding_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "plan_id": row["plan_id"],
            "binding_revision": row["binding_revision"],
            "logical_revision": row["logical_revision"],
            "status": row["status"],
            "reports": json.loads(row["reports_json"]),
            "result": json.loads(row["result_json"]),
            "bound_plan": (
                json.loads(row["bound_plan_json"])
                if row["bound_plan_json"]
                else None
            ),
            "bound_plan_hash": row["bound_plan_hash"],
            "resolutions": json.loads(row["resolutions_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def save_semantic_binding_revision(
        self,
        user_id: str,
        *,
        reports: Any,
        result: Any,
        resolutions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """原子保存检查报告和一个不可变 binding revision。"""

        report_dicts = [
            report.model_dump(mode="json") for report in reports
        ]
        result_dict = result.model_dump(mode="json")
        bound_plan = result.bound_plan
        bound_plan_dict = (
            bound_plan.model_dump(mode="json")
            if bound_plan is not None
            else None
        )
        bound_plan_hash = (
            bound_plan.canonical_hash()
            if bound_plan is not None
            else None
        )
        now = _now()
        try:
            with self._lock, self._conn() as conn:
                for report, report_dict in zip(reports, report_dicts):
                    conn.execute(
                        "INSERT OR IGNORE INTO source_inspection_reports "
                        "(inspection_id, user_id, plan_id, logical_revision, "
                        "artifact_id, artifact_sha256, inspector_version, "
                        "report_hash, report_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            report.inspection_id,
                            user_id,
                            result.logical_plan_id,
                            result.logical_plan_revision,
                            report.artifact_id,
                            report.artifact_sha256,
                            report.inspector_version,
                            report.canonical_hash(),
                            json.dumps(report_dict, ensure_ascii=False),
                            now,
                        ),
                    )
                conn.execute(
                    "INSERT INTO semantic_binding_revisions "
                    "(plan_id, binding_revision, logical_revision, user_id, "
                    "status, reports_json, result_json, bound_plan_json, "
                    "bound_plan_hash, resolutions_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.logical_plan_id,
                        result.binding_revision,
                        result.logical_plan_revision,
                        user_id,
                        result.status.value,
                        json.dumps(report_dicts, ensure_ascii=False),
                        json.dumps(result_dict, ensure_ascii=False),
                        (
                            json.dumps(bound_plan_dict, ensure_ascii=False)
                            if bound_plan_dict is not None
                            else None
                        ),
                        bound_plan_hash,
                        json.dumps(resolutions or {}, ensure_ascii=False),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "该语义绑定 revision 已存在，禁止覆盖"
            ) from exc
        saved = self.get_semantic_binding_revision(
            user_id,
            result.logical_plan_id,
            result.binding_revision,
        )
        assert saved is not None
        return saved

    def cached_source_inspection_report(
        self,
        user_id: str,
        *,
        artifact_id: str,
        artifact_sha256: str,
        inspector_version: str,
    ) -> Optional[Dict[str, Any]]:
        """读取同一用户、上传制品和检查器版本的不可变检查缓存。"""

        with self._conn() as conn:
            row = conn.execute(
                "SELECT report_json FROM source_inspection_reports "
                "WHERE user_id=? AND artifact_id=? AND artifact_sha256=? "
                "AND inspector_version=? ORDER BY created_at DESC LIMIT 1",
                (
                    user_id,
                    artifact_id,
                    artifact_sha256,
                    inspector_version,
                ),
            ).fetchone()
        return json.loads(row["report_json"]) if row is not None else None

    def get_semantic_binding_revision(
        self,
        user_id: str,
        plan_id: str,
        binding_revision: int,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT plan_id, binding_revision, logical_revision, status, "
                "reports_json, result_json, bound_plan_json, bound_plan_hash, "
                "resolutions_json, created_at "
                "FROM semantic_binding_revisions "
                "WHERE user_id=? AND plan_id=? AND binding_revision=?",
                (user_id, plan_id, binding_revision),
            ).fetchone()
        return self._semantic_binding_row(row)

    def list_semantic_binding_revisions(
        self,
        user_id: str,
        plan_id: str,
    ) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT plan_id, binding_revision, logical_revision, status, "
                "reports_json, result_json, bound_plan_json, bound_plan_hash, "
                "resolutions_json, created_at "
                "FROM semantic_binding_revisions "
                "WHERE user_id=? AND plan_id=? "
                "ORDER BY binding_revision DESC",
                (user_id, plan_id),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._semantic_binding_row(row)) is not None
        ]

    def latest_semantic_binding_revision(
        self,
        user_id: str,
        plan_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT plan_id, binding_revision, logical_revision, status, "
                "reports_json, result_json, bound_plan_json, bound_plan_hash, "
                "resolutions_json, created_at "
                "FROM semantic_binding_revisions "
                "WHERE user_id=? AND plan_id=? "
                "ORDER BY binding_revision DESC LIMIT 1",
                (user_id, plan_id),
            ).fetchone()
        return self._semantic_binding_row(row)

    # ---------- Phase 4B 批次 3 确定性表格执行 ----------
    @staticmethod
    def _physical_plan_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "physical_plan_id": row["physical_plan_id"],
            "plan_id": row["plan_id"],
            "logical_revision": row["logical_revision"],
            "binding_revision": row["binding_revision"],
            "status": row["status"],
            "physical_plan_hash": row["physical_plan_hash"],
            "physical_plan": json.loads(row["physical_plan_json"]),
            "created_at": row["created_at"],
        }

    def save_physical_plan(
        self,
        user_id: str,
        physical_plan: Any,
    ) -> Dict[str, Any]:
        payload = physical_plan.model_dump(mode="json")
        now = _now()
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT INTO physical_plan_revisions "
                    "(physical_plan_id, plan_id, logical_revision, "
                    "binding_revision, user_id, status, physical_plan_hash, "
                    "physical_plan_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        physical_plan.physical_plan_id,
                        physical_plan.logical_plan_id,
                        physical_plan.logical_plan_revision,
                        physical_plan.binding_revision,
                        user_id,
                        physical_plan.status.value,
                        physical_plan.canonical_hash(),
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该 PhysicalPlan 已存在，禁止覆盖") from exc
        saved = self.get_physical_plan(
            user_id, physical_plan.physical_plan_id
        )
        assert saved is not None
        return saved

    def get_physical_plan(
        self,
        user_id: str,
        physical_plan_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT physical_plan_id, plan_id, logical_revision, "
                "binding_revision, status, physical_plan_hash, "
                "physical_plan_json, created_at "
                "FROM physical_plan_revisions "
                "WHERE user_id=? AND physical_plan_id=?",
                (user_id, physical_plan_id),
            ).fetchone()
        return self._physical_plan_row(row)

    def list_physical_plans(
        self,
        user_id: str,
        plan_id: str,
    ) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT physical_plan_id, plan_id, logical_revision, "
                "binding_revision, status, physical_plan_hash, "
                "physical_plan_json, created_at "
                "FROM physical_plan_revisions "
                "WHERE user_id=? AND plan_id=? ORDER BY created_at DESC",
                (user_id, plan_id),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._physical_plan_row(row)) is not None
        ]

    @staticmethod
    def _execution_run_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "plan_id": row["plan_id"],
            "physical_plan_id": row["physical_plan_id"],
            "status": row["status"],
            "tool_result": json.loads(row["tool_result_json"]),
            "verification": json.loads(row["verification_json"]),
            # 服务端物理路径不通过 API 返回。
            "created_at": row["created_at"],
        }

    def save_table_execution_run(
        self,
        user_id: str,
        *,
        run_id: str,
        plan_id: str,
        physical_plan_id: str,
        tool_result: Any,
        verification: Any,
        artifact_paths: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO table_execution_runs "
                "(run_id, user_id, plan_id, physical_plan_id, status, "
                "tool_result_json, verification_json, artifact_paths_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    user_id,
                    plan_id,
                    physical_plan_id,
                    verification.status.value,
                    json.dumps(
                        tool_result.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        verification.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    json.dumps(artifact_paths or {}, ensure_ascii=False),
                    now,
                ),
            )
        saved = self.get_table_execution_run(user_id, run_id)
        assert saved is not None
        return saved

    def get_table_execution_run(
        self,
        user_id: str,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id, plan_id, physical_plan_id, status, "
                "tool_result_json, verification_json, created_at "
                "FROM table_execution_runs WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
        return self._execution_run_row(row)

    @staticmethod
    def _document_execution_run_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "plan_id": row["plan_id"],
            "physical_plan_id": row["physical_plan_id"],
            "status": row["status"],
            "result": json.loads(row["result_json"]),
            "tool_result": json.loads(row["tool_result_json"]),
            "verification": json.loads(row["verification_json"]),
            # 服务端物理路径不通过 API 返回。
            "created_at": row["created_at"],
        }

    def save_document_execution_run(
        self,
        user_id: str,
        *,
        run_id: str,
        plan_id: str,
        physical_plan_id: str,
        result: Any,
        tool_result: Any,
        verification: Any,
        artifact_paths: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO document_execution_runs "
                "(run_id, user_id, plan_id, physical_plan_id, status, "
                "result_json, tool_result_json, verification_json, "
                "artifact_paths_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    user_id,
                    plan_id,
                    physical_plan_id,
                    verification.status.value,
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                    json.dumps(
                        tool_result.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        verification.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    json.dumps(artifact_paths or {}, ensure_ascii=False),
                    now,
                ),
            )
        saved = self.get_document_execution_run(user_id, run_id)
        assert saved is not None
        return saved

    def get_document_execution_run(
        self,
        user_id: str,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id, plan_id, physical_plan_id, status, "
                "result_json, tool_result_json, verification_json, created_at "
                "FROM document_execution_runs WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
        return self._document_execution_run_row(row)

    # ---------- Phase 4B 批次 5 有界 Harness ----------
    @staticmethod
    def _semantic_harness_run_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "thread_id": row["thread_id"],
            "logical_plan_id": row["logical_plan_id"],
            "logical_plan_revision": row["logical_revision"],
            "logical_plan_hash": row["logical_plan_hash"],
            "binding_revision": row["binding_revision"],
            "binding_hash": row["binding_hash"],
            "capability_id": row["capability_id"],
            "capability_version": row["capability_version"],
            "runtime_profile": row["runtime_profile"],
            "policy": json.loads(row["policy_json"]),
            "status": row["status"],
            "current_node": row["current_node"],
            "repair_rounds": row["repair_rounds"],
            "semantic_replans": row["semantic_replans"],
            "transient_retries": row["transient_retries"],
            "same_failure_count": row["same_failure_count"],
            "last_failure_fingerprint": row["last_failure_fingerprint"],
            "question": (
                json.loads(row["question_json"])
                if row["question_json"]
                else None
            ),
            "final_verification": (
                json.loads(row["final_verification_json"])
                if row["final_verification_json"]
                else None
            ),
            "eligible_for_delivery": bool(row["eligible_for_delivery"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_semantic_harness_run(
        self,
        run: Any,
    ) -> Dict[str, Any]:
        payload = run.model_dump(mode="json")
        with self._lock, self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO semantic_harness_runs "
                    "(run_id, user_id, thread_id, logical_plan_id, "
                    "logical_revision, logical_plan_hash, binding_revision, "
                    "binding_hash, capability_id, capability_version, "
                    "runtime_profile, policy_json, status, current_node, "
                    "repair_rounds, semantic_replans, transient_retries, "
                    "same_failure_count, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.user_id,
                        run.thread_id,
                        run.logical_plan_id,
                        run.logical_plan_revision,
                        run.logical_plan_hash,
                        run.binding_revision,
                        run.binding_hash,
                        run.capability_id,
                        run.capability_version,
                        run.runtime_profile,
                        json.dumps(payload["policy"], ensure_ascii=False),
                        run.status.value,
                        run.current_node.value,
                        run.repair_rounds,
                        run.semantic_replans,
                        run.transient_retries,
                        run.same_failure_count,
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Harness run 已存在，禁止覆盖") from exc
        saved = self.get_semantic_harness_run(run.user_id, run.run_id)
        assert saved is not None
        return saved

    def get_semantic_harness_run(
        self,
        user_id: str,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_harness_runs "
                "WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
        return self._semantic_harness_run_row(row)

    def update_semantic_harness_run(
        self,
        user_id: str,
        run_id: str,
        *,
        status: str,
        current_node: str,
        repair_rounds: int,
        semantic_replans: int,
        transient_retries: int,
        same_failure_count: int,
        last_failure_fingerprint: str | None = None,
        question: Any = None,
        final_verification: Any = None,
        eligible_for_delivery: bool = False,
    ) -> Dict[str, Any]:
        def payload(value: Any) -> Any:
            if value is None:
                return None
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            return json.dumps(value, ensure_ascii=False)

        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                "UPDATE semantic_harness_runs SET status=?, current_node=?, "
                "repair_rounds=?, semantic_replans=?, transient_retries=?, "
                "same_failure_count=?, last_failure_fingerprint=?, "
                "question_json=?, final_verification_json=?, "
                "eligible_for_delivery=?, updated_at=? "
                "WHERE user_id=? AND run_id=?",
                (
                    status,
                    current_node,
                    repair_rounds,
                    semantic_replans,
                    transient_retries,
                    same_failure_count,
                    last_failure_fingerprint,
                    payload(question),
                    payload(final_verification),
                    int(eligible_for_delivery),
                    _now(),
                    user_id,
                    run_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError("Harness run 不存在或无权访问")
        saved = self.get_semantic_harness_run(user_id, run_id)
        assert saved is not None
        return saved

    def append_semantic_harness_event(
        self,
        user_id: str,
        run_id: str,
        *,
        event_key: str,
        node: str,
        event_type: str,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock, self._conn() as conn:
            owner = conn.execute(
                "SELECT 1 FROM semantic_harness_runs "
                "WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
            if owner is None:
                raise KeyError("Harness run 不存在或无权访问")
            existing = conn.execute(
                "SELECT * FROM semantic_harness_events "
                "WHERE user_id=? AND event_key=?",
                (user_id, event_key),
            ).fetchone()
            if existing is None:
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS value "
                    "FROM semantic_harness_events WHERE run_id=?",
                    (run_id,),
                ).fetchone()["value"]
                event_id = f"event_{uuid.uuid4().hex[:16]}"
                conn.execute(
                    "INSERT INTO semantic_harness_events "
                    "(event_id, event_key, run_id, user_id, sequence, node, "
                    "event_type, summary, details_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        event_key,
                        run_id,
                        user_id,
                        sequence,
                        node,
                        event_type,
                        summary[:500],
                        json.dumps(details or {}, ensure_ascii=False),
                        _now(),
                    ),
                )
                existing = conn.execute(
                    "SELECT * FROM semantic_harness_events "
                    "WHERE event_id=?",
                    (event_id,),
                ).fetchone()
        assert existing is not None
        return {
            "event_id": existing["event_id"],
            "sequence": existing["sequence"],
            "node": existing["node"],
            "event_type": existing["event_type"],
            "summary": existing["summary"],
            "details": json.loads(existing["details_json"] or "{}"),
            "created_at": existing["created_at"],
        }

    def list_semantic_harness_events(
        self,
        user_id: str,
        run_id: str,
    ) -> List[Dict[str, Any]]:
        if self.get_semantic_harness_run(user_id, run_id) is None:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_id, sequence, node, event_type, summary, "
                "details_json, created_at FROM semantic_harness_events "
                "WHERE user_id=? AND run_id=? ORDER BY sequence",
                (user_id, run_id),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "sequence": row["sequence"],
                "node": row["node"],
                "event_type": row["event_type"],
                "summary": row["summary"],
                "details": json.loads(row["details_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _persist_semantic_path(self, value: str | Path) -> str:
        if self.semantic_paths is None:
            # 仅兼容不触及语义路径的既有单测；生产 get_store 始终注入 codec。
            return str(value)
        return self.semantic_paths.encode(value)

    def _resolve_semantic_path(self, value: str) -> str:
        if self.semantic_paths is None:
            # 不在这里静默构造默认根，避免测试配置与生产配置出现两套路径语义。
            return value
        return str(self.semantic_paths.decode(value))

    def _persist_semantic_artifact_paths(
        self,
        paths: Dict[str, str],
    ) -> Dict[str, str]:
        return {
            name: self._persist_semantic_path(path)
            for name, path in paths.items()
        }

    def _resolve_semantic_artifact_paths(
        self,
        paths: Dict[str, str],
    ) -> Dict[str, str]:
        return {
            name: self._resolve_semantic_path(path)
            for name, path in paths.items()
        }

    def save_semantic_harness_attempt(
        self,
        user_id: str,
        run_id: str,
        *,
        attempt_id: str,
        node: str,
        attempt_number: int,
        idempotency_key: str,
        input_hash: str,
        status: str,
        failure_kind: str | None = None,
        tool_result: Any = None,
        verification: Any = None,
        repair_decision: Any = None,
        artifact_paths: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        def payload(value: Any) -> Any:
            if value is None:
                return None
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            return json.dumps(value, ensure_ascii=False)

        with self._lock, self._conn() as conn:
            owner = conn.execute(
                "SELECT 1 FROM semantic_harness_runs "
                "WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
            if owner is None:
                raise KeyError("Harness run 不存在或无权访问")
            conn.execute(
                "INSERT OR IGNORE INTO semantic_harness_attempts "
                "(attempt_id, run_id, user_id, node, attempt_number, "
                "idempotency_key, input_hash, status, failure_kind, "
                "tool_result_json, verification_json, "
                "repair_decision_json, artifact_paths_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    run_id,
                    user_id,
                    node,
                    attempt_number,
                    idempotency_key,
                    input_hash,
                    status,
                    failure_kind,
                    payload(tool_result),
                    payload(verification),
                    payload(repair_decision),
                    json.dumps(
                        self._persist_semantic_artifact_paths(
                            artifact_paths or {}
                        ),
                        ensure_ascii=False,
                    ),
                    _now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM semantic_harness_attempts "
                "WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
        assert row is not None
        return self._semantic_harness_attempt_row(row, include_private=True)

    def _semantic_harness_attempt_row(
        self,
        row: sqlite3.Row,
        *,
        include_private: bool = False,
    ) -> Dict[str, Any]:
        payload = {
            "attempt_id": row["attempt_id"],
            "node": row["node"],
            "attempt_number": row["attempt_number"],
            "idempotency_key": row["idempotency_key"],
            "input_hash": row["input_hash"],
            "status": row["status"],
            "failure_kind": row["failure_kind"],
            "tool_result": (
                json.loads(row["tool_result_json"])
                if row["tool_result_json"]
                else None
            ),
            "verification": (
                json.loads(row["verification_json"])
                if row["verification_json"]
                else None
            ),
            "repair_decision": (
                json.loads(row["repair_decision_json"])
                if row["repair_decision_json"]
                else None
            ),
            "created_at": row["created_at"],
        }
        if include_private:
            payload["artifact_paths"] = self._resolve_semantic_artifact_paths(
                json.loads(row["artifact_paths_json"] or "{}")
            )
        return payload

    def get_semantic_harness_attempt_by_key(
        self,
        user_id: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_harness_attempts "
                "WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
        return (
            self._semantic_harness_attempt_row(row, include_private=True)
            if row is not None
            else None
        )

    def list_semantic_harness_attempts(
        self,
        user_id: str,
        run_id: str,
    ) -> List[Dict[str, Any]]:
        if self.get_semantic_harness_run(user_id, run_id) is None:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_harness_attempts "
                "WHERE user_id=? AND run_id=? "
                "ORDER BY attempt_number, created_at",
                (user_id, run_id),
            ).fetchall()
        return [self._semantic_harness_attempt_row(row) for row in rows]

    def latest_semantic_harness_artifact_paths(
        self,
        user_id: str,
        run_id: str,
    ) -> Dict[str, str]:
        """工作台预览专用；物理路径只在服务端内部返回。"""

        if self.get_semantic_harness_run(user_id, run_id) is None:
            return {}
        with self._conn() as conn:
            row = conn.execute(
                "SELECT artifact_paths_json FROM semantic_harness_attempts "
                "WHERE user_id=? AND run_id=? "
                "AND artifact_paths_json!='{}' "
                "ORDER BY attempt_number DESC, created_at DESC LIMIT 1",
                (user_id, run_id),
            ).fetchone()
        return (
            self._resolve_semantic_artifact_paths(
                json.loads(row["artifact_paths_json"])
            )
            if row
            else {}
        )

    def save_semantic_delivery(
        self,
        *,
        user_id: str,
        run_id: str,
        manifest: Any,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """在文件原子发布后一次性登记 Manifest 与不透明输出 ID。"""

        payload = manifest.model_dump(mode="json")
        with self._lock, self._conn() as conn:
            owner = conn.execute(
                "SELECT 1 FROM semantic_harness_runs "
                "WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
            if owner is None:
                raise KeyError("Harness run 不存在或无权访问")
            conn.execute(
                "INSERT INTO semantic_delivery_runs "
                "(delivery_id, run_id, user_id, status, manifest_json, "
                "output_dir, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["delivery_id"],
                    run_id,
                    user_id,
                    payload["status"],
                    json.dumps(payload, ensure_ascii=False),
                    self._persist_semantic_path(output_dir.resolve()),
                    _now(),
                ),
            )
            for output in payload["outputs"]:
                file_path = (output_dir / output["filename"]).resolve()
                conn.execute(
                    "INSERT INTO semantic_delivery_outputs "
                    "(output_id, delivery_id, run_id, user_id, format, "
                    "filename, media_type, sha256, size_bytes, file_path, "
                    "qa_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        output["output_id"],
                        payload["delivery_id"],
                        run_id,
                        user_id,
                        output["format"],
                        output["filename"],
                        output["media_type"],
                        output["sha256"],
                        output["size_bytes"],
                        self._persist_semantic_path(file_path),
                        json.dumps(output["qa"], ensure_ascii=False),
                        _now(),
                    ),
                )
        return payload

    def get_semantic_delivery(
        self,
        user_id: str,
        delivery_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM semantic_delivery_runs "
                "WHERE user_id=? AND delivery_id=?",
                (user_id, delivery_id),
            ).fetchone()
        if row is None:
            # vNext 不伪造 Legacy Harness 外键；公共读取接口兼容查询通用交付表。
            from src.delivery_publishing.repository import (
                DeliveryPublishingRepository,
            )

            return DeliveryPublishingRepository(
                self.db_path,
                semantic_paths=self.semantic_paths,
            ).get_delivery(user_id, delivery_id)
        payload = json.loads(row["manifest_json"])
        payload.pop("user_id", None)
        return payload

    def latest_semantic_delivery(
        self,
        user_id: str,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM semantic_delivery_runs "
                "WHERE user_id=? AND run_id=? ORDER BY created_at DESC LIMIT 1",
                (user_id, run_id),
            ).fetchone()
        if row is None:
            from src.delivery_publishing.repository import (
                DeliveryPublishingRepository,
            )

            return DeliveryPublishingRepository(
                self.db_path,
                semantic_paths=self.semantic_paths,
            ).latest_delivery(user_id, run_id)
        payload = json.loads(row["manifest_json"])
        payload.pop("user_id", None)
        return payload

    def get_semantic_delivery_output(
        self,
        user_id: str,
        output_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_delivery_outputs "
                "WHERE user_id=? AND output_id=?",
                (user_id, output_id),
            ).fetchone()
        if row is None:
            from src.delivery_publishing.repository import (
                DeliveryPublishingRepository,
            )

            return DeliveryPublishingRepository(
                self.db_path,
                semantic_paths=self.semantic_paths,
            ).get_output(user_id, output_id)
        return {
            "output_id": row["output_id"],
            "delivery_id": row["delivery_id"],
            "run_id": row["run_id"],
            "format": row["format"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "file_path": self._resolve_semantic_path(row["file_path"]),
            "qa": json.loads(row["qa_json"]),
            "created_at": row["created_at"],
        }

    # ---------- Phase 4B 批次 7 正式数据工作台 ----------
    @staticmethod
    def _semantic_workspace_task_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "title": row["title"],
            "objective_text": row["objective_text"],
            "upload_ids": json.loads(row["upload_ids_json"] or "[]"),
            "source_refs": json.loads(row["source_refs_json"] or "[]"),
            "output_formats": json.loads(
                row["output_formats_json"] or "[]"
            ),
            "table_output_contracts": json.loads(
                row["table_output_contracts_json"] or "[]"
            ),
            "provider": row["provider"],
            "model": row["model"],
            "external_api_confirmed": bool(
                row["external_api_confirmed"]
            ),
            "status": row["status"],
            "active_revision": row["active_revision"],
            "plan_id": row["plan_id"],
            "logical_revision": row["logical_revision"],
            "binding_revision": row["binding_revision"],
            "run_id": row["run_id"],
            "summary": row["summary"],
            "error": row["error"],
            "failure": (
                json.loads(row["failure_json"])
                if row["failure_json"]
                else None
            ),
            "question": (
                json.loads(row["question_json"])
                if row["question_json"]
                else None
            ),
            "cancel_requested": bool(row["cancel_requested"]),
            "deleted_at": row["deleted_at"],
            "purge_after": row["purge_after"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_semantic_workspace_task(
        self,
        user_id: str,
        *,
        task_id: str,
        title: str,
        objective_text: str,
        upload_ids: List[str],
        output_formats: List[str],
        provider: str,
        model: str | None,
        external_api_confirmed: bool,
        source_refs: List[Dict[str, str]] | None = None,
        table_output_contracts: List[Dict[str, Any]] | None = None,
        transaction_hook: Callable[[sqlite3.Connection], None] | None = None,
    ) -> Dict[str, Any]:
        now = _now()
        with self._lock, self._conn() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO semantic_workspace_tasks "
                    "(task_id, user_id, title, objective_text, "
                    "upload_ids_json, source_refs_json, output_formats_json, "
                    "table_output_contracts_json, "
                    "provider, model, "
                    "external_api_confirmed, status, active_revision, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "'queued', 1, ?, ?)",
                    (
                        task_id,
                        user_id,
                        title,
                        objective_text,
                        json.dumps(upload_ids, ensure_ascii=False),
                        json.dumps(source_refs or [], ensure_ascii=False),
                        json.dumps(output_formats, ensure_ascii=False),
                        json.dumps(
                            table_output_contracts or [],
                            ensure_ascii=False,
                        ),
                        provider,
                        model,
                        int(external_api_confirmed),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO semantic_workspace_revisions "
                    "(task_id, revision, user_id, objective_text, "
                    "output_formats_json, table_output_contracts_json, "
                    "source_refs_json, "
                    "status, created_at, updated_at) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (
                        task_id,
                        user_id,
                        objective_text,
                        json.dumps(output_formats, ensure_ascii=False),
                        json.dumps(
                            table_output_contracts or [],
                            ensure_ascii=False,
                        ),
                        json.dumps(source_refs or [], ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                if transaction_hook is not None:
                    transaction_hook(conn)
            except sqlite3.IntegrityError as exc:
                raise ValueError("工作台任务已存在，禁止覆盖") from exc
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    raise RuntimeError("工作台数据库繁忙，请稍后重试") from exc
                raise
        saved = self.get_semantic_workspace_task(user_id, task_id)
        assert saved is not None
        return saved

    def get_semantic_workspace_task(
        self,
        user_id: str,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            ).fetchone()
        return self._semantic_workspace_task_row(row)

    def get_web_task_contract(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> Optional[Dict[str, Any]]:
        """返回 Owner 隔离的冻结网页任务合同。"""

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM web_task_contracts "
                "WHERE owner_id=? AND task_id=? AND revision=?",
                (user_id, task_id, revision),
            ).fetchone()
        if row is None:
            return None
        return {
            "source_snapshot_id": row["source_snapshot_id"],
            "goal_contract": json.loads(row["goal_contract_json"]),
            "delivery_spec": json.loads(row["delivery_spec_json"]),
            "runtime_binding": json.loads(row["runtime_binding_json"]),
            "created_at": row["created_at"],
        }

    def find_web_task_revision_by_snapshot(
        self,
        user_id: str,
        task_id: str,
        snapshot_id: str,
    ) -> int | None:
        """查找已绑定该快照的版本，供刷新请求安全恢复。"""

        with self._conn() as conn:
            row = conn.execute(
                "SELECT revision FROM web_task_contracts "
                "WHERE owner_id=? AND task_id=? AND source_snapshot_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (user_id, task_id, snapshot_id),
            ).fetchone()
        return int(row["revision"]) if row is not None else None

    def claim_source_refresh_intent(
        self,
        user_id: str,
        task_id: str,
        idempotency_key: str,
        *,
        request_hash: str,
        expected_revision: int,
        attempt_id: str,
        snapshot_id: str,
        resume_unknown: bool = False,
    ) -> tuple[Dict[str, Any], bool]:
        """串行化同一刷新请求，避免并发预分配两个 RuntimeBinding。"""

        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM source_refresh_intents WHERE owner_id=? "
                "AND task_id=? AND idempotency_key=?",
                (user_id, task_id, idempotency_key),
            ).fetchone()
            if row is not None:
                saved = dict(row)
                if (
                    saved["request_hash"] != request_hash
                    or int(saved["expected_revision"]) != expected_revision
                    or saved["attempt_id"] != attempt_id
                    or saved["snapshot_id"] != snapshot_id
                ):
                    raise ValueError("该刷新幂等键已绑定另一份来源请求")
                if saved["status"] == "failed":
                    conn.execute(
                        "UPDATE source_refresh_intents SET status='binding', "
                        "updated_at=? WHERE owner_id=? AND task_id=? "
                        "AND idempotency_key=? AND status='failed'",
                        (now, user_id, task_id, idempotency_key),
                    )
                    saved["status"] = "binding"
                    saved["updated_at"] = now
                    return saved, True
                if saved["status"] == "binding" and resume_unknown:
                    updated_at = datetime.fromisoformat(str(saved["updated_at"]))
                    if updated_at <= datetime.now() - timedelta(seconds=30):
                        cursor = conn.execute(
                            "UPDATE source_refresh_intents SET updated_at=? "
                            "WHERE owner_id=? AND task_id=? AND idempotency_key=? "
                            "AND status='binding' AND updated_at=?",
                            (
                                now,
                                user_id,
                                task_id,
                                idempotency_key,
                                saved["updated_at"],
                            ),
                        )
                        if cursor.rowcount == 1:
                            saved["updated_at"] = now
                            return saved, True
                return saved, False
            conn.execute(
                "INSERT INTO source_refresh_intents "
                "(owner_id, task_id, idempotency_key, request_hash, "
                "expected_revision, attempt_id, snapshot_id, status, "
                "started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
                "'binding', ?, ?)",
                (
                    user_id,
                    task_id,
                    idempotency_key,
                    request_hash,
                    expected_revision,
                    attempt_id,
                    snapshot_id,
                    now,
                    now,
                ),
            )
        return {
            "owner_id": user_id,
            "task_id": task_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "expected_revision": expected_revision,
            "attempt_id": attempt_id,
            "snapshot_id": snapshot_id,
            "status": "binding",
            "created_revision": None,
            "started_at": now,
            "updated_at": now,
        }, True

    def finish_source_refresh_intent(
        self,
        user_id: str,
        task_id: str,
        idempotency_key: str,
        *,
        revision: int | None,
    ) -> None:
        status_value = "completed" if revision is not None else "failed"
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                "UPDATE source_refresh_intents SET status=?, "
                "created_revision=?, updated_at=? WHERE owner_id=? "
                "AND task_id=? AND idempotency_key=? AND status='binding'",
                (
                    status_value,
                    revision,
                    _now(),
                    user_id,
                    task_id,
                    idempotency_key,
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("来源刷新幂等状态已变化")

    def list_semantic_workspace_tasks(
        self,
        user_id: str,
        *,
        status: str | None = None,
        deleted: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        where = ["user_id=?"]
        args: List[Any] = [user_id]
        where.append("deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL")
        if status:
            where.append("status=?")
            args.append(status)
        args.append(max(1, min(limit, 500)))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_workspace_tasks WHERE "
                + " AND ".join(where)
                + " ORDER BY updated_at DESC LIMIT ?",
                args,
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._semantic_workspace_task_row(row)) is not None
        ]

    def list_pending_semantic_workspace_tasks(
        self,
    ) -> List[Dict[str, Any]]:
        """启动恢复专用；仅返回尚未终结且未删除的任务及 owner。"""

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_workspace_tasks "
                "WHERE deleted_at IS NULL "
                "AND status IN ('queued', 'running', 'cancelling') "
                "ORDER BY created_at"
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = self._semantic_workspace_task_row(row)
            if item is not None:
                item["user_id"] = row["user_id"]
                result.append(item)
        return result

    def update_semantic_workspace_task(
        self,
        user_id: str,
        task_id: str,
        *,
        expected_active_revision: int | None = None,
        **changes: Any,
    ) -> Dict[str, Any]:
        allowed = {
            "title",
            "objective_text",
            "upload_ids",
            "output_formats",
            "table_output_contracts",
            "provider",
            "model",
            "external_api_confirmed",
            "status",
            "active_revision",
            "plan_id",
            "logical_revision",
            "binding_revision",
            "run_id",
            "summary",
            "error",
            "failure",
            "question",
            "cancel_requested",
            "deleted_at",
            "purge_after",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不允许更新工作台字段：{sorted(unknown)}")
        if not changes:
            current = self.get_semantic_workspace_task(user_id, task_id)
            if current is None:
                raise KeyError("工作台任务不存在或无权访问")
            return current

        column_map = {
            "upload_ids": "upload_ids_json",
            "output_formats": "output_formats_json",
            "table_output_contracts": "table_output_contracts_json",
            "failure": "failure_json",
            "question": "question_json",
        }
        json_fields = {
            "upload_ids",
            "output_formats",
            "table_output_contracts",
            "failure",
            "question",
        }
        bool_fields = {"external_api_confirmed", "cancel_requested"}
        sets: List[str] = []
        values: List[Any] = []
        for key, value in changes.items():
            column = column_map.get(key, key)
            sets.append(f"{column}=?")
            if key in json_fields:
                values.append(
                    json.dumps(value, ensure_ascii=False)
                    if value is not None
                    else None
                )
            elif key in bool_fields:
                values.append(int(bool(value)))
            else:
                values.append(value)
        sets.append("updated_at=?")
        values.extend([_now(), user_id, task_id])
        where = " WHERE user_id=? AND task_id=?"
        if expected_active_revision is not None:
            where += " AND active_revision=?"
            values.append(expected_active_revision)
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                "UPDATE semantic_workspace_tasks SET "
                + ", ".join(sets)
                + where,
                values,
            )
        if cursor.rowcount != 1:
            if (
                expected_active_revision is not None
                and self.get_semantic_workspace_task(user_id, task_id)
                is not None
            ):
                raise RuntimeError("活动版本已变化，任务状态未更新")
            raise KeyError("工作台任务不存在或无权访问")
        saved = self.get_semantic_workspace_task(user_id, task_id)
        assert saved is not None
        return saved

    def create_semantic_workspace_revision(
        self,
        user_id: str,
        task_id: str,
        *,
        objective_text: str,
        output_formats: List[str],
        change_summary: str,
        source_refs: List[Dict[str, str]] | None = None,
        table_output_contracts: List[Dict[str, Any]] | None = None,
        expected_revision: int | None = None,
        transaction_hook: Callable[[sqlite3.Connection], None] | None = None,
    ) -> Dict[str, Any]:
        now = _now()
        with self._lock, self._conn() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    raise RuntimeError("工作台数据库繁忙，请稍后重试") from exc
                raise
            row = conn.execute(
                "SELECT * FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            ).fetchone()
            task = self._semantic_workspace_task_row(row)
            if task is None:
                raise KeyError("工作台任务不存在或无权访问")
            revision = int(task["active_revision"]) + 1
            if expected_revision is not None and revision != expected_revision:
                raise RuntimeError("活动版本已变化，禁止创建路由错配的 Revision")
            publish_intents = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='delivery_publish_intents'"
            ).fetchone()
            if publish_intents is not None:
                committing = conn.execute(
                    "SELECT 1 FROM delivery_publish_intents "
                    "WHERE owner_id=? AND task_id=? AND task_revision=? "
                    "AND status='committing' LIMIT 1",
                    (user_id, task_id, revision - 1),
                ).fetchone()
                if committing is not None:
                    raise RuntimeError("正式交付已越过提交点，禁止切换活动版本")
            frozen_contracts = table_output_contracts
            if frozen_contracts is None:
                frozen_contracts = [
                    item
                    for item in task.get("table_output_contracts", [])
                    if item.get("format") in output_formats
                ]
            frozen_source_refs = (
                source_refs
                if source_refs is not None
                else task.get("source_refs", [])
            )
            try:
                conn.execute(
                    "INSERT INTO semantic_workspace_revisions "
                    "(task_id, revision, user_id, objective_text, "
                    "output_formats_json, table_output_contracts_json, "
                    "source_refs_json, "
                    "status, change_summary, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (
                        task_id,
                        revision,
                        user_id,
                        objective_text,
                        json.dumps(output_formats, ensure_ascii=False),
                        json.dumps(frozen_contracts or [], ensure_ascii=False),
                        json.dumps(frozen_source_refs, ensure_ascii=False),
                        change_summary,
                        now,
                        now,
                    ),
                )
                # revision、活动指针和 Runtime assignment 必须同事务提交。
                cursor = conn.execute(
                    "UPDATE semantic_workspace_tasks SET objective_text=?, "
                    "output_formats_json=?, table_output_contracts_json=?, "
                    "source_refs_json=?, "
                    "active_revision=?, status='queued', "
                    "plan_id=?, logical_revision=NULL, binding_revision=NULL, "
                    "run_id=NULL, summary='', error=NULL, failure_json=NULL, "
                    "question_json=NULL, cancel_requested=0, deleted_at=NULL, "
                    "purge_after=NULL, updated_at=? "
                    "WHERE user_id=? AND task_id=? AND active_revision=?",
                    (
                        objective_text,
                        json.dumps(output_formats, ensure_ascii=False),
                        json.dumps(frozen_contracts or [], ensure_ascii=False),
                        json.dumps(frozen_source_refs, ensure_ascii=False),
                        revision,
                        task["plan_id"],
                        now,
                        user_id,
                        task_id,
                        revision - 1,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("活动版本已变化，禁止创建半应用 Revision")
                if transaction_hook is not None:
                    transaction_hook(conn)
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("活动版本已并发变化，Revision 未创建") from exc
        saved = self.get_semantic_workspace_revision(
            user_id, task_id, revision
        )
        assert saved is not None
        return saved

    @staticmethod
    def _semantic_workspace_revision_row(
        row: sqlite3.Row | None,
    ) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "revision": row["revision"],
            "objective_text": row["objective_text"],
            "output_formats": json.loads(
                row["output_formats_json"] or "[]"
            ),
            "table_output_contracts": json.loads(
                row["table_output_contracts_json"] or "[]"
            ),
            "source_refs": json.loads(row["source_refs_json"] or "[]"),
            "plan_id": row["plan_id"],
            "logical_revision": row["logical_revision"],
            "binding_revision": row["binding_revision"],
            "run_id": row["run_id"],
            "status": row["status"],
            "summary": row["summary"],
            "change_summary": row["change_summary"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_semantic_workspace_revision(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (user_id, task_id, revision),
            ).fetchone()
        return self._semantic_workspace_revision_row(row)

    def list_semantic_workspace_revisions(
        self,
        user_id: str,
        task_id: str,
    ) -> List[Dict[str, Any]]:
        if self.get_semantic_workspace_task(user_id, task_id) is None:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? ORDER BY revision DESC",
                (user_id, task_id),
            ).fetchall()
        return [
            item
            for row in rows
            if (
                item := self._semantic_workspace_revision_row(row)
            ) is not None
        ]

    def update_semantic_workspace_revision(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        **changes: Any,
    ) -> Dict[str, Any]:
        allowed = {
            "plan_id",
            "logical_revision",
            "binding_revision",
            "run_id",
            "status",
            "summary",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不允许更新 revision 字段：{sorted(unknown)}")
        if not changes:
            current = self.get_semantic_workspace_revision(
                user_id, task_id, revision
            )
            if current is None:
                raise KeyError("工作台 revision 不存在或无权访问")
            return current
        sets = [f"{key}=?" for key in changes]
        values = list(changes.values())
        sets.append("updated_at=?")
        values.extend([_now(), user_id, task_id, revision])
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                "UPDATE semantic_workspace_revisions SET "
                + ", ".join(sets)
                + " WHERE user_id=? AND task_id=? AND revision=?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError("工作台 revision 不存在或无权访问")
        saved = self.get_semantic_workspace_revision(
            user_id, task_id, revision
        )
        assert saved is not None
        return saved

    def append_semantic_workspace_event(
        self,
        user_id: str,
        task_id: str,
        *,
        event_id: Optional[str] = None,
        stage: str,
        event_type: str,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock, self._conn() as conn:
            # 跨进程串行分配 sequence，避免两个 Web 进程读取到相同 MAX(sequence)。
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                "SELECT 1 FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            ).fetchone()
            if owner is None:
                raise KeyError("工作台任务不存在或无权访问")
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS value "
                "FROM semantic_workspace_events WHERE task_id=?",
                (task_id,),
            ).fetchone()["value"]
            resolved_event_id = event_id or f"workspace_event_{uuid.uuid4().hex[:16]}"
            created_at = _now()
            cursor = conn.execute(
                ("INSERT OR IGNORE" if event_id is not None else "INSERT")
                + " INTO semantic_workspace_events "
                "(event_id, task_id, user_id, sequence, stage, event_type, "
                "summary, details_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resolved_event_id,
                    task_id,
                    user_id,
                    sequence,
                    stage,
                    event_type,
                    summary[:500],
                    json.dumps(details or {}, ensure_ascii=False),
                    created_at,
                ),
            )
            if event_id is not None and cursor.rowcount == 0:
                existing = conn.execute(
                    "SELECT event_id, sequence, stage, event_type, summary, "
                    "details_json, created_at FROM semantic_workspace_events "
                    "WHERE event_id=? AND user_id=? AND task_id=?",
                    (resolved_event_id, user_id, task_id),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("工作台事件序号冲突")
                return {
                    "event_id": existing["event_id"],
                    "sequence": existing["sequence"],
                    "stage": existing["stage"],
                    "event_type": existing["event_type"],
                    "summary": existing["summary"],
                    "details": json.loads(existing["details_json"]),
                    "created_at": existing["created_at"],
                }
        return {
            "event_id": resolved_event_id,
            "sequence": sequence,
            "stage": stage,
            "event_type": event_type,
            "summary": summary[:500],
            "details": details or {},
            "created_at": created_at,
        }

    def list_semantic_workspace_events(
        self,
        user_id: str,
        task_id: str,
        *,
        after: int = 0,
    ) -> List[Dict[str, Any]]:
        if self.get_semantic_workspace_task(user_id, task_id) is None:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_id, sequence, stage, event_type, summary, "
                "details_json, created_at FROM semantic_workspace_events "
                "WHERE user_id=? AND task_id=? AND sequence>? "
                "ORDER BY sequence",
                (user_id, task_id, max(0, after)),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "sequence": row["sequence"],
                "stage": row["stage"],
                "event_type": row["event_type"],
                "summary": row["summary"],
                "details": json.loads(row["details_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def soft_delete_semantic_workspace_task(
        self,
        user_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        now = datetime.now()
        return self.update_semantic_workspace_task(
            user_id,
            task_id,
            deleted_at=now.isoformat(timespec="seconds"),
            purge_after=(now + timedelta(days=30)).isoformat(
                timespec="seconds"
            ),
        )

    def restore_semantic_workspace_task(
        self,
        user_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        return self.update_semantic_workspace_task(
            user_id,
            task_id,
            deleted_at=None,
            purge_after=None,
        )

    def purge_semantic_workspace_task(
        self,
        user_id: str,
        task_id: str,
    ) -> bool:
        with self._lock, self._conn() as conn:
            owner = conn.execute(
                "SELECT * FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=? AND deleted_at IS NOT NULL",
                (user_id, task_id),
            ).fetchone()
            if owner is None:
                return False
            self._create_semantic_workspace_audit_tombstone(
                conn,
                owner,
                purge_reason="user_permanent_delete",
            )
            conn.execute(
                "DELETE FROM semantic_workspace_events "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            )
            conn.execute(
                "DELETE FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            )
            cursor = conn.execute(
                "DELETE FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            )
        return cursor.rowcount == 1

    def purge_expired_semantic_workspace_tasks(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """清理已超过 30 天保留期的工作台记录，底层审计产物继续保留。"""
        cutoff = (now or datetime.now()).isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_workspace_tasks "
                "WHERE deleted_at IS NOT NULL AND purge_after IS NOT NULL "
                "AND purge_after<=?",
                (cutoff,),
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if not task_ids:
                return 0
            for row in rows:
                self._create_semantic_workspace_audit_tombstone(
                    conn,
                    row,
                    purge_reason="retention_expired",
                )
            placeholders = ",".join("?" for _ in task_ids)
            conn.execute(
                "DELETE FROM semantic_workspace_events "
                f"WHERE task_id IN ({placeholders})",
                task_ids,
            )
            conn.execute(
                "DELETE FROM semantic_workspace_revisions "
                f"WHERE task_id IN ({placeholders})",
                task_ids,
            )
            cursor = conn.execute(
                "DELETE FROM semantic_workspace_tasks "
                f"WHERE task_id IN ({placeholders})",
                task_ids,
            )
        return cursor.rowcount

    @staticmethod
    def _create_semantic_workspace_audit_tombstone(
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        *,
        purge_reason: str,
    ) -> None:
        """在清理工作台聚合记录前写入不含业务正文的最小审计墓碑。"""
        failure = json.loads(task["failure_json"] or "{}")
        result_rows = []
        if task["run_id"]:
            result_rows = conn.execute(
                "SELECT output_id, format, sha256 FROM "
                "semantic_delivery_outputs WHERE user_id=? AND run_id=? "
                "ORDER BY output_id",
                (task["user_id"], task["run_id"]),
            ).fetchall()
        result_refs = [
            {
                "output_id": row["output_id"],
                "format": row["format"],
                "sha256": row["sha256"],
            }
            for row in result_rows
        ]
        conn.execute(
            "INSERT OR IGNORE INTO semantic_workspace_audit_tombstones "
            "(task_id, user_id, objective_sha256, source_refs_json, "
            "result_refs_json, requested_formats_json, terminal_status, "
            "error_code, task_created_at, deleted_at, purged_at, "
            "purge_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task["task_id"],
                task["user_id"],
                hashlib.sha256(
                    task["objective_text"].encode("utf-8")
                ).hexdigest(),
                task["source_refs_json"] or "[]",
                json.dumps(result_refs, ensure_ascii=False),
                task["output_formats_json"] or "[]",
                task["status"],
                failure.get("error_code"),
                task["created_at"],
                task["deleted_at"],
                _now(),
                purge_reason,
            ),
        )

    def get_semantic_workspace_audit_tombstone(
        self,
        user_id: str,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        """按所有者读取精简审计墓碑；普通工作台列表不会暴露该记录。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_workspace_audit_tombstones "
                "WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "objective_sha256": row["objective_sha256"],
            "source_refs": json.loads(row["source_refs_json"] or "[]"),
            "result_refs": json.loads(row["result_refs_json"] or "[]"),
            "requested_formats": json.loads(
                row["requested_formats_json"] or "[]"
            ),
            "terminal_status": row["terminal_status"],
            "error_code": row["error_code"],
            "task_created_at": row["task_created_at"],
            "deleted_at": row["deleted_at"],
            "purged_at": row["purged_at"],
            "purge_reason": row["purge_reason"],
        }
