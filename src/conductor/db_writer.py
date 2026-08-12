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

_DB_PATH = PROJECT_ROOT / "data" / "app.db"
_TABLE = "collected_items"


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            source TEXT,
            url TEXT,
            title TEXT,
            content TEXT,
            metadata TEXT,
            created_at TEXT
        )
        """
    )
    return conn


def _write_sqlite(rows: List[tuple]) -> int:
    conn = _connect()
    try:
        conn.executemany(
            f"INSERT INTO {_TABLE} (task_id, source, url, title, content, metadata, created_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _write_mysql(rows: List[tuple]) -> int:
    """写入 MySQL（PyMySQL）。表不存在则自动创建。"""
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
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_id VARCHAR(64),
                    source VARCHAR(255),
                    url TEXT,
                    title TEXT,
                    content LONGTEXT,
                    metadata LONGTEXT,
                    created_at VARCHAR(32)
                ) CHARACTER SET utf8mb4
                """
            )
            cur.executemany(
                f"INSERT INTO {_TABLE} (task_id, source, url, title, content, metadata, created_at) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
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
    rows = [
        (
            task_id,
            source or "",
            it.get("url", ""),
            it.get("title", ""),
            it.get("content", ""),
            json.dumps(it.get("metadata", {}), ensure_ascii=False),
            now,
        )
        for it in items
    ]
    if (settings.db_backend or "sqlite").lower() == "mysql":
        return _write_mysql(rows)
    return _write_sqlite(rows)
