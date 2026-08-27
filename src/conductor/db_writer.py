"""
入库实现（MVP）：把清洗后的数据写入数据库。

按 settings.db_backend 选择后端：sqlite（默认，本地）| mysql（用 mysql_* 连接，PyMySQL）。
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
_MYSQL_COLUMN_DEFINITIONS = dict(_INSERT_COLUMN_SPEC)
_MYSQL_SCHEMA_DDL = (
    f"CREATE TABLE {_TABLE} (\n"
    + ",\n".join(
        f"    {name} {definition}"
        for name, definition, _inserted in _MYSQL_COLUMN_SPEC
    )
    + "\n) CHARACTER SET utf8mb4;"
)


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
    """写入已经由 DBA 显式安装 Schema 的 MySQL。"""
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            # 远程 MySQL 不允许在业务写入路径隐式建表；缺 Schema 由数据库明确拒绝。
            try:
                cur.executemany(
                    f"INSERT INTO {_TABLE} ({_INSERT_COLUMNS_SQL}) "
                    f"VALUES ({', '.join('%s' for _ in _INSERT_COLUMN_SPEC)})",
                    rows,
                )
            except pymysql.MySQLError as exc:
                if exc.args and exc.args[0] == 1146:
                    raise RuntimeError(
                        "Legacy Conductor MySQL 缺少 collected_items 表；"
                        "未执行自动建表。请由数据库管理员在目标库显式执行：\n"
                        f"{_MYSQL_SCHEMA_DDL}"
                    ) from exc
                if exc.args and exc.args[0] == 1054:
                    missing_column = next(
                        (
                            column
                            for column in _MYSQL_COLUMN_DEFINITIONS
                            if f"'{column}'" in str(exc)
                        ),
                        None,
                    )
                    if missing_column is None:
                        dba_command = "SHOW COLUMNS FROM collected_items;"
                    else:
                        column_type = _MYSQL_COLUMN_DEFINITIONS[missing_column]
                        dba_command = (
                            "ALTER TABLE collected_items ADD COLUMN "
                            f"{missing_column} {column_type};"
                        )
                    raise RuntimeError(
                        "Legacy Conductor MySQL 缺少项目要求的列；"
                        "未执行自动改表。请由数据库管理员核对后显式执行：\n"
                        f"{dba_command}"
                    ) from exc
                raise
        conn.commit()
        return len(rows)
    finally:
        conn.close()


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
