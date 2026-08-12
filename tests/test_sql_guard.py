# -*- coding: utf-8 -*-
"""单 SELECT 受控 SQL 校验器安全测试（Phase 3 Task 3 TDD）。

约 45 个用例覆盖计划 1 威胁模型 T1–T5 全部攻击面。
"""
from __future__ import annotations

import pytest

from src.connectors.sql_guard import SqlGuardError, validate_select


# ---------------- T1: 多语句拒绝 ----------------


class TestMultiStatement:
    def test_semicolon_split(self):
        with pytest.raises(SqlGuardError, match="多语句|1 条"):
            validate_select("SELECT 1; DROP TABLE users", allowed_tables={"users"})

    def test_trailing_semicolon(self):
        """结尾分号本身不是多语句标记。"""
        validate_select("SELECT * FROM users;", allowed_tables={"users"})

    def test_comment_hiding_semicolon(self):
        with pytest.raises(SqlGuardError, match="多语句|1 条"):
            validate_select("SELECT * FROM users -- first\n; DROP TABLE users", allowed_tables={"users"})

    def test_multiple_statements_with_different_endings(self):
        with pytest.raises(SqlGuardError, match="多语句|1 条"):
            validate_select("SELECT * FROM users;\nUPDATE users SET name='x'", allowed_tables={"users"})


# ---------------- T2: 语句类型必须是 SELECT ----------------


class TestStatementType:
    def test_select_passes(self):
        result = validate_select("SELECT * FROM orders", allowed_tables={"orders"})
        assert "orders" in result.tables

    def test_insert_rejected(self):
        for s in ["INSERT INTO users VALUES (1)", "INSERT INTO users (id) VALUES (1)"]:
            with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
                validate_select(s, allowed_tables={"users"})

    def test_update_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("UPDATE users SET name='x'", allowed_tables={"users"})

    def test_delete_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("DELETE FROM users", allowed_tables={"users"})

    def test_drop_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("DROP TABLE users", allowed_tables={"users"})

    def test_alter_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("ALTER TABLE users ADD COLUMN x INT", allowed_tables={"users"})

    def test_create_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("CREATE TABLE t (id INT)", allowed_tables={"t"})

    def test_truncate_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("TRUNCATE TABLE users", allowed_tables={"users"})

    def test_grant_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("GRANT SELECT ON users TO reader", allowed_tables={"users"})

    def test_set_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("SET TRANSACTION READ ONLY", allowed_tables={"none"})

    def test_use_rejected(self):
        with pytest.raises(SqlGuardError, match="仅允许|SELECT"):
            validate_select("USE mysql", allowed_tables={"none"})


# ---------------- T3: CTE 含 WITH 拒绝 + FOR UPDATE 拒绝 ----------------


class TestCteAndWriteIntent:
    def test_select_with_cte_rejected(self):
        """保守拒绝所有 WITH 开头语句。"""
        with pytest.raises(SqlGuardError, match="CTE|WITH"):
            validate_select("WITH a AS (SELECT 1) SELECT * FROM a", allowed_tables={"a"})

    def test_pg_write_cte_rejected(self):
        """PG 写 CTE: WITH t AS (DELETE ... RETURNING *) SELECT * FROM t。"""
        with pytest.raises(SqlGuardError, match="CTE|WITH"):
            validate_select(
                "WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t",
                allowed_tables={"users"},
            )

    def test_for_update_rejected(self):
        with pytest.raises(SqlGuardError, match="FOR UPDATE|写锁"):
            validate_select("SELECT * FROM orders FOR UPDATE", allowed_tables={"orders"})

    def test_lock_in_share_mode_rejected(self):
        with pytest.raises(SqlGuardError, match="FOR UPDATE|写锁|LOCK"):
            validate_select("SELECT * FROM orders LOCK IN SHARE MODE", allowed_tables={"orders"})


# ---------------- T4: 危险函数与子句（INTO / 文件函数）----------------


class TestDangerousFunctions:
    def test_into_outfile_rejected(self):
        with pytest.raises(SqlGuardError, match="INTO|禁止"):
            validate_select(
                "SELECT * FROM users INTO OUTFILE '/tmp/dump'",
                allowed_tables={"users"},
            )

    def test_into_dumpfile_rejected(self):
        with pytest.raises(SqlGuardError, match="INTO|禁止"):
            validate_select(
                "SELECT * FROM users INTO DUMPFILE '/tmp/dump'",
                allowed_tables={"users"},
            )

    def test_load_file_rejected(self):
        with pytest.raises(SqlGuardError, match="LOAD_FILE|禁止"):
            validate_select("SELECT LOAD_FILE('/etc/passwd')", allowed_tables={"none"})

    def test_pg_read_file_rejected(self):
        with pytest.raises(SqlGuardError, match="pg_read_file|禁止"):
            validate_select("SELECT pg_read_file('/etc/passwd')", allowed_tables={"none"})


# ---------------- T5: 表列白名单  ----------------


class TestTableWhitelist:
    def test_allowed_table_passes(self):
        r = validate_select("SELECT id, name FROM orders", allowed_tables={"orders"})
        assert "orders" in r.tables

    def test_unauthorized_table_rejected(self):
        with pytest.raises(SqlGuardError, match="白名单|允许|users"):
            validate_select("SELECT * FROM users", allowed_tables={"orders"})

    def test_join_table_rejected(self):
        with pytest.raises(SqlGuardError, match="白名单|允许|users"):
            validate_select(
                "SELECT * FROM orders JOIN users ON orders.uid = users.id",
                allowed_tables={"orders"},
            )

    def test_subquery_different_table_rejected(self):
        with pytest.raises(SqlGuardError, match="白名单|允许|users"):
            validate_select(
                "SELECT * FROM orders WHERE id IN (SELECT id FROM users)",
                allowed_tables={"orders"},
            )

    def test_union_table_rejected(self):
        with pytest.raises(SqlGuardError, match="白名单|允许|users"):
            validate_select(
                "SELECT id FROM orders UNION SELECT id FROM users",
                allowed_tables={"orders"},
            )

    def test_no_from_select_rejected(self):
        """探测型无 FROM 的 SELECT 应拒。"""
        with pytest.raises(SqlGuardError, match="FROM|表白名单|无表引用"):
            validate_select("SELECT 1", allowed_tables={"none"})

    def test_select_constant_rejected(self):
        with pytest.raises(SqlGuardError, match="FROM|表白名单|无表引用"):
            validate_select("SELECT 1+2", allowed_tables={"none"})


# ---------------- 混淆与绕过 ----  ----


class TestObfuscation:
    def test_comment_wrapped_keywords(self):
        """SEL/**/ECT 注释包裹。"""
        try:
            validate_select("SEL/**/ECT * FROM orders", allowed_tables={"orders"})
        except SqlGuardError:
            pass  # 解析失败或语法异常均可接受

    def test_line_comment_hiding(self):
        """行注释隐藏分号——应被多语句检测逮住。"""
        with pytest.raises(SqlGuardError):
            validate_select(
                "SELECT * FROM orders -- harmless\n; DELETE FROM orders",
                allowed_tables={"orders"},
            )

    def test_case_insensitive_keywords(self):
        ddl_variants = ["drop table users", "DROP TABLE users", "DrOp TaBlE users"]
        for s in ddl_variants:
            with pytest.raises(SqlGuardError):
                validate_select(s, allowed_tables={"users"})


# ---------------- 合法语句通行 ----------------


class TestValidQueries:
    def test_simple_select(self):
        r = validate_select("SELECT id FROM orders", allowed_tables={"orders"})
        assert "orders" in r.tables

    def test_select_all(self):
        r = validate_select("SELECT * FROM orders", allowed_tables={"orders"})
        assert "orders" in r.tables

    def test_aliased_table(self):
        r = validate_select("SELECT o.id FROM orders o", allowed_tables={"orders"})
        assert "orders" in r.tables

    def test_join_shared_whitelist(self):
        r = validate_select(
            "SELECT * FROM orders o JOIN order_items i ON o.id = i.order_id",
            allowed_tables={"orders", "order_items"},
        )
        assert "orders" in r.tables
        assert "order_items" in r.tables

    def test_legitimate_subquery(self):
        r = validate_select(
            "SELECT * FROM orders WHERE id IN (SELECT order_id FROM order_items)",
            allowed_tables={"orders", "order_items"},
        )
        assert "orders" in r.tables
        assert "order_items" in r.tables

    def test_where_conditions(self):
        r = validate_select(
            "SELECT id, amount FROM orders WHERE status = 'paid' AND amount > 100",
            allowed_tables={"orders"},
        )
        assert "orders" in r.tables

    def test_order_by_limit(self):
        r = validate_select(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT 100",
            allowed_tables={"orders"},
        )
        assert "orders" in r.tables

    def test_group_by_having(self):
        r = validate_select(
            "SELECT status, COUNT(*) FROM orders GROUP BY status HAVING COUNT(*) > 5",
            allowed_tables={"orders"},
        )
        assert "orders" in r.tables
