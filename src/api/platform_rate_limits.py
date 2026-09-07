# -*- coding: utf-8 -*-
"""网页登录的 SQLite 共享滚动限流；事务与提交由调用者持有。"""
from __future__ import annotations

import hashlib
import hmac
import math
import sqlite3
from uuid import uuid4

from src.config.settings import settings


LOGIN_WINDOW = 15 * 60
REQUEST_WINDOW = 60
AUDIT_RETENTION = 180 * 24 * 60 * 60


def _validate(conn: sqlite3.Connection, now: float) -> None:
    # 禁止不带事务的检查/写入组合；调用者必须先 BEGIN IMMEDIATE，并提交拒绝事实。
    if not conn.in_transaction:
        raise ValueError("限流必须在调用者持有的写事务内执行")
    if not math.isfinite(now):
        raise ValueError("限流时间必须为有限值")


def _prune(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM platform_rate_events WHERE occurred_at <= ?", (now - LOGIN_WINDOW,))
    conn.execute("DELETE FROM platform_rate_blocks WHERE blocked_until <= ?", (now,))


def append_security_event(conn: sqlite3.Connection, *, action: str, subject_digest: str,
                          reason: str, result: str, now: float, actor_user_id: str | None = None) -> None:
    """只接收低敏分类和用途隔离摘要，不接收账号、地址、凭证明文或异常正文。"""
    _validate(conn, now)
    conn.execute("DELETE FROM platform_security_audit WHERE occurred_at < ?", (now - AUDIT_RETENTION,))
    conn.execute(
        "INSERT INTO platform_security_audit "
        "(event_id, actor_user_id, action, subject_digest, reason, result, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid4().hex, actor_user_id, action, subject_digest, reason, result, now),
    )


def _threshold(conn, *, bucket, subject, until, now, actor=None):
    current = conn.execute(
        "SELECT blocked_until FROM platform_rate_blocks WHERE bucket=? AND subject_digest=?", (bucket, subject),
    ).fetchone()
    if current is not None and current[0] > now:
        return
    conn.execute(
        "INSERT INTO platform_rate_blocks(bucket, subject_digest, blocked_until) VALUES (?, ?, ?) "
        "ON CONFLICT(bucket, subject_digest) DO UPDATE SET blocked_until=excluded.blocked_until",
        (bucket, subject, until),
    )
    append_security_event(conn, action="rate_limit_threshold", subject_digest=subject,
                          reason=bucket, result="limited", now=now, actor_user_id=actor)


def _window(conn, bucket, subject, now, seconds):
    return conn.execute(
        "SELECT COUNT(*), MIN(occurred_at) FROM platform_rate_events "
        "WHERE bucket=? AND subject_digest=? AND occurred_at > ?",
        (bucket, subject, now - seconds),
    ).fetchone()


def login_retry_after(conn: sqlite3.Connection, *, account_key: str, source_key: str, now: float) -> int:
    """账号或真实来源达到五次失败后，取两桶剩余封锁时间的最大值。"""
    _validate(conn, now)
    _prune(conn, now)
    remaining = 0
    for bucket, subject in (("login.account", account_key), ("login.source", source_key)):
        row = conn.execute(
            "SELECT blocked_until FROM platform_rate_blocks WHERE bucket=? AND subject_digest=?",
            (bucket, subject),
        ).fetchone()
        if row is not None:
            remaining = max(remaining, math.ceil(row[0] - now))
    return remaining


def record_login_result(conn: sqlite3.Connection, *, account_key: str, source_key: str,
                        success: bool, now: float) -> None:
    """验证后由同一写事务再次核对额度，再登记；成功清本次账号和来源桶。"""
    _validate(conn, now)
    _prune(conn, now)
    for bucket, subject in (("login.account", account_key), ("login.source", source_key)):
        if success:
            conn.execute("DELETE FROM platform_rate_events WHERE bucket=? AND subject_digest=?", (bucket, subject))
            conn.execute("DELETE FROM platform_rate_blocks WHERE bucket=? AND subject_digest=?", (bucket, subject))
        else:
            conn.execute("INSERT INTO platform_rate_events VALUES (?, ?, ?)", (bucket, subject, now))
            count, _ = _window(conn, bucket, subject, now, LOGIN_WINDOW)
            if count >= 5:
                _threshold(conn, bucket=bucket, subject=subject, until=now + LOGIN_WINDOW, now=now)


def consume_request_limits(conn: sqlite3.Connection, *, owner_user_id: str, control: bool, now: float) -> int:
    """普通 API 与任务启动共用事务核对；拒绝不续期，管理员使用同样额度。"""
    _validate(conn, now)
    if not isinstance(owner_user_id, str) or not owner_user_id.strip():
        raise ValueError("限流需要有效的 Owner")
    _prune(conn, now)
    secret = settings.require_jwt_secret().encode("utf-8")
    buckets = [("request.api", 120)] + ([("request.control", 10)] if control else [])
    windows = []
    for bucket, limit in buckets:
        subject = hmac.new(secret, f"platform-rate:{bucket}\0{owner_user_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        count, oldest = _window(conn, bucket, subject, now, REQUEST_WINDOW)
        windows.append((bucket, limit, subject, count, oldest))
    retry = max((max(1, math.ceil(oldest + REQUEST_WINDOW - now))
                 for _, limit, _, count, oldest in windows if count >= limit), default=0)
    if retry:
        for bucket, limit, subject, count, oldest in windows:
            if count >= limit:
                _threshold(conn, bucket=bucket, subject=subject, until=oldest + REQUEST_WINDOW,
                           now=now, actor=owner_user_id)
        return retry
    for bucket, limit, subject, count, oldest in windows:
        conn.execute("INSERT INTO platform_rate_events VALUES (?, ?, ?)", (bucket, subject, now))
        if count + 1 == limit:
            _threshold(conn, bucket=bucket, subject=subject,
                       until=(now if oldest is None else oldest) + REQUEST_WINDOW, now=now, actor=owner_user_id)
    return 0
