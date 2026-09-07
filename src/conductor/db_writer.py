"""
入库实现（MVP）：把清洗后的数据写入数据库。

保留内部 SQLite 结果落库；旧 MySQL 外部业务写入口永久拒绝。
仅在用户经 HITL 确认后调用（见前端 / output 节点）。
表结构通用，按 db_target 区分逻辑归属（记录在 source 列）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.config.settings import PROJECT_ROOT, settings
from src.database_migrations import DatabaseTarget, inspect_database
from src.external_readonly import reject_external_write

_DB_PATH = PROJECT_ROOT / "data" / "app.db"
_TABLE = "collected_items"
_MYSQL_COLUMN_SPEC = (
    ("id", "BIGINT PRIMARY KEY AUTO_INCREMENT", False),
    ("task_id", "VARCHAR(64)", True),
    ("source", "VARCHAR(255)", True),
    ("url", "TEXT", True),
    ("title", "TEXT", True),
    ("content", "LONGTEXT", True),
    ("metadata", "LONGTEXT", True),
    ("created_at", "VARCHAR(32)", True),
)
_INSERT_COLUMN_SPEC = tuple(
    (name, definition)
    for name, definition, inserted in _MYSQL_COLUMN_SPEC
    if inserted
)
_INSERT_COLUMNS_SQL = ", ".join(name for name, _ in _INSERT_COLUMN_SPEC)


def _connect() -> sqlite3.Connection:
    inspect_database(
        DatabaseTarget(profile="legacy_app", path=_DB_PATH)
    ).require_current()
    connection = sqlite3.connect(str(_DB_PATH))
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _write_sqlite(rows: List[tuple]) -> int:
    conn = _connect()
    try:
        conn.executemany(
            f"INSERT INTO {_TABLE} ({_INSERT_COLUMNS_SQL}) "
            f"VALUES ({', '.join('?' for _ in _INSERT_COLUMN_SPEC)})",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _write_mysql(rows: List[tuple]) -> int:
    """旧 MySQL 接缝不再允许外部业务写；连接或凭据读取之前拒绝。"""
    reject_external_write()


def write_items(
    task_id: str, items: List[Dict[str, Any]], source: str = ""
) -> int:
    """写入数据，返回写入条数。按 settings.db_backend 选择 sqlite / mysql。"""
    if not items:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in items:
        values = {
            "task_id": task_id,
            "source": source or "",
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "content": item.get("content", ""),
            "metadata": json.dumps(
                item.get("metadata", {}),
                ensure_ascii=False,
            ),
            "created_at": now,
        }
        rows.append(tuple(values[name] for name, _ in _INSERT_COLUMN_SPEC))
    if (settings.db_backend or "sqlite").lower() == "mysql":
        return _write_mysql(rows)
    return _write_sqlite(rows)
