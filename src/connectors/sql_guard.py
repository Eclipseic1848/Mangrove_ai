# -*- coding: utf-8 -*-
"""基于 SQLGlot AST 的单 SELECT 安全校验器。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Set

from sqlglot import exp, parse
from sqlglot.errors import ParseError


@dataclass
class ValidatedQuery:
    tables: Set[str] = field(default_factory=set)


class SqlGuardError(ValueError):
    """受控 SQL 校验被拒。"""


_DANGEROUS_FUNCTIONS = {
    "LOAD_FILE", "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR",
    "LO_IMPORT", "LO_EXPORT",
}
_SYSTEM_SCHEMAS = {
    "mysql", "pg_catalog", "information_schema", "sys", "performance_schema",
    "sqlite_master", "sqlite_schema",
}


def _table_names(tree: exp.Expression) -> Set[str]:
    tables: Set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        db = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        qualified = ".".join(x for x in (catalog, db, name) if x)
        tables.add(qualified or name)
    return tables


def validate_select(
    sql: str,
    *,
    allowed_tables: Set[str],
    dialect: Optional[str] = None,
) -> ValidatedQuery:
    """仅允许一条、无 CTE/锁/危险函数且表引用全部在白名单内的 SELECT。"""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlGuardError("SQL 必须为非空字符串")
    try:
        statements = [item for item in parse(sql, read=dialect) if item is not None]
    except (ParseError, ValueError) as exc:
        raise SqlGuardError(f"SQL 解析失败: {exc}") from exc
    if len(statements) != 1:
        raise SqlGuardError(f"仅允许 1 条语句，检测到 {len(statements)} 条")

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SqlGuardError(f"仅允许 SELECT 语句，检测到: {type(tree).__name__}")
    if tree.args.get("with_") is not None:
        raise SqlGuardError("禁止使用 WITH 子句（CTE 包 DML 风险）")

    rendered = tree.sql(dialect=dialect)
    if re.search(r"\bFOR\s+(UPDATE|SHARE|KEY\s+SHARE|NO\s+KEY\s+UPDATE)\b", sql, re.I):
        raise SqlGuardError("禁止 FOR UPDATE / FOR SHARE / 写锁")
    if re.search(r"\bLOCK\s+IN\s+SHARE\s+MODE\b|\bINTO\s+(OUTFILE|DUMPFILE)\b", sql, re.I):
        raise SqlGuardError("禁止 LOCK 或文件写入子句")
    for name in _DANGEROUS_FUNCTIONS:
        if re.search(rf"\b{re.escape(name)}\s*\(", sql, re.I):
            raise SqlGuardError(f"禁止的危险函数: {name}")

    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                             exp.Alter, exp.Command, exp.Transaction, exp.Merge)):
            raise SqlGuardError(f"禁止的 SQL 节点: {type(node).__name__}")
        if isinstance(node, exp.Func):
            name = (getattr(node, "name", "") or node.sql_name() or "").upper()
            if name in _DANGEROUS_FUNCTIONS:
                raise SqlGuardError(f"禁止的危险函数: {name}")

    tables = _table_names(tree)
    if not tables:
        raise SqlGuardError("未检测到有效的表引用（无 FROM 子句）；探测型 SELECT 拒绝")

    normalized_allowed = {item.strip().lower() for item in allowed_tables if item.strip()}
    for table in tables:
        parts = table.split(".")
        if any(part in _SYSTEM_SCHEMAS for part in parts[:-1]) or table in _SYSTEM_SCHEMAS:
            raise SqlGuardError(f"禁止访问系统表或系统 schema: {table}")
        if table not in normalized_allowed and parts[-1] not in normalized_allowed:
            raise SqlGuardError(
                f"访问未授权的表: {table}。允许的表: "
                f"{', '.join(sorted(normalized_allowed)) if normalized_allowed else '（无）'}"
            )
    return ValidatedQuery(tables=tables)
