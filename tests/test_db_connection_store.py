# -*- coding: utf-8 -*-
"""数据库连接存储与凭证加解密测试（Phase 3 Task 2 TDD）。

覆盖：加密往返、错误密钥拒解、CRUD、跨用户隔离、脱敏、checkpoint_json 迁移幂等。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.api.store import WebUIStore, _now
from src.database_migrations import SchemaNotCurrentError
from src.services.db_connections import (
    DbConnectionIn,
    DbConnectionPublic,
    DbCredentials,
    decrypt_password,
    encrypt_password,
    resolve_credential,
    to_public_dict,
)
from tests.database_migration_helpers import migrated_webui_database


# ---------------- helpers ----------------


def _make_store(tmp_path: Path) -> WebUIStore:
    db = str(migrated_webui_database(tmp_path / "test.db"))
    store = WebUIStore(db)
    return store


def test_data_prep_task_transition_is_compare_and_set(tmp_path: Path):
    store = _make_store(tmp_path)
    task_id = "task-cas"
    store.create_data_prep_task(
        "user-a",
        task_id,
        {"task_type": "document_extraction"},
        status="READY",
    )

    assert store.transition_data_prep_task(
        task_id,
        from_statuses={"READY"},
        to_status="EXTRACTING",
    )
    assert not store.transition_data_prep_task(
        task_id,
        from_statuses={"READY"},
        to_status="EXTRACTING",
    )
    assert store.get_data_prep_task(task_id)["status"] == "EXTRACTING"


# ---------------- Fernet 往返测试 ----------------


class TestFernetRoundtrip:
    def test_encrypt_decrypt_roundtrip(self):
        """加密→解密往返一致。"""
        pw = "s3cr3t!@#"
        token = encrypt_password(pw)
        assert decrypt_password(token) == pw

    def test_decrypt_wrong_key_raises(self, monkeypatch):
        """篡改 Fernet token 后解密失败。"""
        token = encrypt_password("original")
        # 替换 token 中部分字符破坏 HMAC
        bad_token = "AA" + token[2:]
        with pytest.raises(Exception):
            result = decrypt_password(bad_token)
            assert result != "original", "篡改后的 token 不应还原出原密码"

    def test_different_cases_produce_different_tokens(self):
        """相同明文每次加密应产生不同 token（时间戳+随机 IV）。"""
        t1 = encrypt_password("same")
        t2 = encrypt_password("same")
        assert t1 != t2
        assert decrypt_password(t1) == decrypt_password(t2)

    def test_empty_password(self):
        """空密码加密往返。"""
        token = encrypt_password("")
        assert decrypt_password(token) == ""


# ---------------- connection CRUD ----------------


class TestDbConnectionCrud:
    def test_create_and_get(self, tmp_path):
        store = _make_store(tmp_path)
        conn = store.create_db_connection(
            "user-a",
            name="my-pg",
            dialect="postgresql",
            host="db.example.com",
            port=5432,
            database_name="analytics",
            username="reader",
            password="secret",
        )
        assert conn["connection_id"]
        assert conn["name"] == "my-pg"
        assert conn["dialect"] == "postgresql"
        assert conn["host"] == "db.example.com"
        assert conn["database_name"] == "analytics"
        assert conn["username"] == "reader"
        assert "password" not in conn  # 公开展示永不返回明文
        assert conn["user_id"] == "user-a"

        # get 同一条
        got = store.get_db_connection(conn["connection_id"])
        assert got["connection_id"] == conn["connection_id"]
        assert got["name"] == "my-pg"

    def test_list_by_user(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_db_connection("user-a", name="a1", dialect="sqlite", sqlite_relpath="sales.db")
        store.create_db_connection("user-a", name="a2", dialect="mysql", host="h", database_name="d", username="u", password="p")
        store.create_db_connection("user-b", name="b1", dialect="postgresql", host="h", database_name="d", username="u", password="p")

        a_list = store.list_db_connections("user-a")
        assert len(a_list) == 2
        b_list = store.list_db_connections("user-b")
        assert len(b_list) == 1
        assert b_list[0]["name"] == "b1"

    def test_cross_user_invisible(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="a", dialect="sqlite", sqlite_relpath="a.db")
        got = store.get_db_connection(c["connection_id"])
        # 无 user_id 参数——store 层 get 不过滤归属（归属校验在 API 层），但返回记录含 user_id 供 API 判断
        assert got is not None
        assert got["user_id"] == "user-a"

    def test_delete(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="del-me", dialect="sqlite", sqlite_relpath="x.db")
        store.delete_db_connection(c["connection_id"], "user-a")
        # 删除后再次获取应返回 None
        assert store.get_db_connection(c["connection_id"]) is None

    def test_delete_wrong_user_noop(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="a", dialect="sqlite", sqlite_relpath="x.db")
        store.delete_db_connection(c["connection_id"], "user-b")  # 不同用户
        assert store.get_db_connection(c["connection_id"]) is not None  # 仍存在

    def test_update(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="old", dialect="sqlite", sqlite_relpath="x.db")
        store.update_db_connection(
            c["connection_id"], "user-a", name="new-name", host="new-host", port=9999
        )
        got = store.get_db_connection(c["connection_id"])
        assert got["name"] == "new-name"
        assert got["host"] == "new-host"
        assert got["port"] == 9999


# ---------------- sqlite 连接 ----------------


class TestSqliteConnection:
    def test_sqlite_relpath(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="sq", dialect="sqlite", sqlite_relpath="orders.db")
        assert c["dialect"] == "sqlite"
        assert c["sqlite_relpath"] == "orders.db"
        assert c.get("host") is None or c["host"] == ""

    def test_sqlite_no_host_port(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="sq2", dialect="sqlite", sqlite_relpath="data/report.db")
        # sqlite 连接 host/port 均为空或默认
        assert c.get("host") is None or c["host"] == ""
        assert c.get("port") == 0


# ---------------- resolve_credential ----------------


class TestResolveCredential:
    def test_resolve_returns_credentials(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection(
            "user-a", name="pg", dialect="postgresql",
            host="10.0.0.1", port=5432, database_name="db", username="u", password="pw",
        )
        creds = resolve_credential(f"dbconn:{c['connection_id']}", "user-a", _store=store)
        assert isinstance(creds, DbCredentials)
        assert creds.dialect == "postgresql"
        assert creds.host == "10.0.0.1"
        assert creds.port == 5432
        assert creds.database == "db"
        assert creds.username == "u"
        assert creds.password == "pw"

    def test_resolve_bad_ref_format(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="credential_ref"):
            resolve_credential("not-a-uuid", "user-a", _store=store)

    def test_resolve_nonexistent(self, tmp_path):
        store = _make_store(tmp_path)
        import uuid as _uuid
        fake_id = str(_uuid.uuid4())
        with pytest.raises(ValueError, match="不存在|找不到"):
            resolve_credential(f"dbconn:{fake_id}", "user-a", _store=store)

    def test_resolve_wrong_user(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="a", dialect="sqlite", sqlite_relpath="a.db")
        with pytest.raises(ValueError, match="不存在|找不到"):
            resolve_credential(f"dbconn:{c['connection_id']}", "user-b", _store=store)

    def test_sqlite_credential_no_password(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="sq", dialect="sqlite", sqlite_relpath="data.db")
        creds = resolve_credential(f"dbconn:{c['connection_id']}", "user-a", _store=store)
        assert creds.password == ""
        assert creds.database == ""  # sqlite path 在 sqlite_relpath


# ---------------- to_public_dict ----------------


class TestPublicDict:
    def test_no_password_field(self, tmp_path):
        store = _make_store(tmp_path)
        c = store.create_db_connection("user-a", name="x", dialect="mysql", host="h", database_name="d", username="u", password="pw")
        pub = to_public_dict(c)
        assert "password" not in pub
        assert pub["dialect"] == "mysql"
        assert pub["host"] == "h"

    def test_lists_never_contain_password(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_db_connection("user-a", name="x", dialect="mysql", host="h1", database_name="d", username="u", password="pw1")
        store.create_db_connection("user-a", name="y", dialect="mysql", host="h2", database_name="d", username="u", password="pw2")
        items = store.list_db_connections("user-a")
        for item in items:
            pub = to_public_dict(item)
            assert "password" not in pub
            assert "password_enc" not in pub


# ---------------- checkpoint_json 向后兼容迁移 ----------------


class TestCheckpointMigration:
    def test_unknown_partial_schema_fails_closed(self, tmp_path):
        """中央迁移拒绝猜测只有局部旧表的未知 Schema。"""
        db_path = str(tmp_path / "test.db")
        # 直接裸 sqlite3 建库不含新列
        bare = sqlite3.connect(db_path)
        bare.executescript("""
        CREATE TABLE IF NOT EXISTS data_prep_tasks (
            task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, spec_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'RUNNING', record_counts TEXT, quality_json TEXT,
            manifest_path TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dpt_user ON data_prep_tasks(user_id);
        """)
        bare.close()
        with pytest.raises(SchemaNotCurrentError, match="Schema 未被识别"):
            migrated_webui_database(db_path)

    def test_checkpoint_column_present_after_init(self, tmp_path):
        db_path = str(migrated_webui_database(tmp_path / "test.db"))
        store = WebUIStore(db_path)
        with store._conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(data_prep_tasks)").fetchall()}
        assert "checkpoint_json" in cols
