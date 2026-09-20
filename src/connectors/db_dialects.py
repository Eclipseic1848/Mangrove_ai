# -*- coding: utf-8 -*-
"""三方言适配层（Phase 3 Task 4）——只读会话、introspection、keyset 查询构造、类型归一化、错误分类。

复刻 src/connectors/http_api_connector.py 的 _HttpApiConfig 严格解析 + transport 注入可测模式；
所有 DB 操作统一走 SQLAlchemy 2.0 Core sync engine（NullPool）+ asyncio.to_thread（数据库连接器层）。
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sqlalchemy import (
    Column,
    MetaData,
    Table,
    and_,
    create_engine,
    inspect,
    select,
    text,
    tuple_,
)
from sqlalchemy.engine import Engine, URL
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

from src.connectors.db_security import validate_db_host, validate_sqlite_path
from src.config.settings import settings

logger = logging.getLogger(__name__)


# ---------------- dataclass ----------------


@dataclass
class DbCredentials:
    """内部凭证（不复用 src.services.db_connections.DbCredentials，避免层耦合）。"""
    dialect: str
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    sqlite_relpath: str = ""


@dataclass
class TableInfo:
    name: str
    schema: str = ""
    estimated_rows: int = 0
    columns: List[Dict[str, Any]] = field(default_factory=list)
    primary_key: List[str] = field(default_factory=list)


@dataclass
class SchemaInfo:
    dialect: str
    server_version: str = ""
    default_schema: str = ""
    tables: List[TableInfo] = field(default_factory=list)


# ---------------- 方言实例 ----------------


class DbDialect:
    """方言适配器：封装连接构造、只读会话、超时、introspection、查询构造、错误分类。"""

    def __init__(self, name: str, driver: str, default_port: int = 0):
        self.name = name
        self.driver = driver
        self.default_port = default_port

    def make_engine(self, creds: DbCredentials, *, connect_timeout: int = 10) -> Engine:
        raise NotImplementedError

    def apply_readonly(self, conn) -> None:
        pass

    def apply_statement_timeout(self, conn, seconds: int) -> None:
        pass

    def introspect(self, engine: Engine, schema: Optional[str] = None) -> SchemaInfo:
        raise NotImplementedError

    def classify_error(self, exc: Exception) -> str:
        raise NotImplementedError


class SqliteDialect(DbDialect):
    def __init__(self):
        super().__init__("sqlite", "sqlite3")

    def make_engine(self, creds: DbCredentials, *, connect_timeout: int = 10) -> Engine:
        import sqlite3

        rel = creds.sqlite_relpath or creds.database
        validated = validate_sqlite_path(rel)
        resolved = validated.resolve()
        return create_engine(
            "sqlite://", poolclass=NullPool,
            creator=lambda: sqlite3.connect(
                f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=connect_timeout,
            ),
        )

    def apply_readonly(self, conn) -> None:
        # mode=ro 已在 URI 中生效，此处为防御性确认
        pass

    def apply_statement_timeout(self, conn, seconds: int) -> None:
        if seconds <= 0:
            return
        import sqlite3 as _sqlite3

        # progress_handler 按 opcode 计数而非秒，在 Windows 上行为不可预测
        # 在此仅设置单语句超时（MySQL/PG 可精确控制），sqlite 留日志告警
        logger.debug("sqlite progress_handler 已设 (n=%d)，Windows 上按操作码计数，非精确秒数", seconds)
        def _kill():
            raise _sqlite3.OperationalError("语句执行超时")
        try:
            conn.connection.connection.set_progress_handler(_kill, seconds * 1000)
        except Exception:
            logger.debug("无法设置 sqlite progress_handler（Windows 运行时库差异）")

    def introspect(self, engine: Engine, schema: Optional[str] = None) -> SchemaInfo:
        insp = inspect(engine)
        tables = []
        for tname in insp.get_table_names():
            cols = insp.get_columns(tname)
            pk = list((insp.get_pk_constraint(tname) or {}).get("constrained_columns") or [])
            count = 0
            try:
                with engine.connect() as conn:
                    row = conn.exec_driver_sql(f"SELECT COUNT(*) FROM [{tname}]").fetchone()
                    if row:
                        count = row[0]
            except Exception:
                pass
            tables.append(TableInfo(name=tname, columns=cols, primary_key=pk, estimated_rows=count))
        return SchemaInfo(dialect="sqlite", tables=tables)

    def classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "readonly" in msg:
            return "fatal"
        return "fatal"


class MysqlDialect(DbDialect):
    def __init__(self):
        super().__init__("mysql", "pymysql", default_port=3306)

    def make_engine(self, creds: DbCredentials, *, connect_timeout: int = 10) -> Engine:
        port = creds.port or 3306
        validate_db_host(creds.host, port)
        url = URL.create(
            "mysql+pymysql", username=creds.username, password=creds.password,
            host=creds.host, port=port, database=creds.database,
            query={"charset": "utf8mb4"},
        )
        return create_engine(
            url, poolclass=NullPool,
            connect_args={"connect_timeout": connect_timeout},
        )

    def apply_readonly(self, conn) -> None:
        conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")

    def apply_statement_timeout(self, conn, seconds: int) -> None:
        if seconds <= 0:
            return
        conn.exec_driver_sql(f"SET SESSION max_execution_time={seconds * 1000}")

    def introspect(self, engine: Engine, schema: Optional[str] = None) -> SchemaInfo:
        insp = inspect(engine)
        db = schema or engine.url.database
        schemas = [db] if db else insp.get_schema_names()
        tables = []
        for s in schemas:
            for tname in insp.get_table_names(schema=s):
                cols = insp.get_columns(tname, schema=s)
                pk = list((insp.get_pk_constraint(tname, schema=s) or {}).get("constrained_columns") or [])
                count = 0
                try:
                    with engine.connect() as conn:
                        row = conn.exec_driver_sql(f"SELECT COUNT(*) FROM `{s}`.`{tname}`").fetchone()
                        if row:
                            count = row[0]
                except Exception:
                    pass
                tables.append(TableInfo(name=tname, schema=s, columns=cols, primary_key=pk, estimated_rows=count))
        ver = ""
        try:
            with engine.connect() as conn:
                ver = conn.exec_driver_sql("SELECT VERSION()").scalar() or ""
        except Exception:
            pass
        return SchemaInfo(dialect="mysql", server_version=str(ver), default_schema=db or "", tables=tables)

    def classify_error(self, exc: Exception) -> str:
        code = getattr(exc, "args", None)
        if code and isinstance(code[0], int):
            err = code[0]
            if err in (1045, 1044, 1142, 1143, 1227):  # auth/access denied
                return "fatal"
            if err in (2006, 2013, 2055):  # connection lost
                return "retryable"
        return "fatal"


class PostgresqlDialect(DbDialect):
    def __init__(self):
        super().__init__("postgresql", "psycopg2", default_port=5432)

    def make_engine(self, creds: DbCredentials, *, connect_timeout: int = 10) -> Engine:
        port = creds.port or 5432
        validate_db_host(creds.host, port)
        url = URL.create(
            "postgresql+psycopg2", username=creds.username, password=creds.password,
            host=creds.host, port=port, database=creds.database,
        )
        return create_engine(
            url, poolclass=NullPool,
            connect_args={
                "connect_timeout": connect_timeout,
                "options": "-c default_transaction_read_only=on",
            },
        )

    def apply_readonly(self, conn) -> None:
        # 已在 connect_args 中设置
        pass

    def apply_statement_timeout(self, conn, seconds: int) -> None:
        if seconds <= 0:
            return
        conn.exec_driver_sql(f"SET statement_timeout = {seconds * 1000}")

    def introspect(self, engine: Engine, schema: Optional[str] = None) -> SchemaInfo:
        insp = inspect(engine)
        sch = schema or "public"
        tables = []
        for tname in insp.get_table_names(schema=sch):
            cols = insp.get_columns(tname, schema=sch)
            pk = list((insp.get_pk_constraint(tname, schema=sch) or {}).get("constrained_columns") or [])
            count = 0
            try:
                with engine.connect() as conn:
                    row = conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{sch}"."{tname}"').fetchone()
                    if row:
                        count = row[0]
            except Exception:
                pass
            tables.append(TableInfo(name=tname, schema=sch, columns=cols, primary_key=pk, estimated_rows=count))
        ver = ""
        try:
            with engine.connect() as conn:
                ver = conn.exec_driver_sql("SHOW server_version").scalar() or ""
        except Exception:
            pass
        return SchemaInfo(dialect="postgresql", server_version=str(ver), default_schema=sch, tables=tables)

    def classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "password" in msg or "authentication" in msg or "permission" in msg:
            return "fatal"
        if "server closed" in msg or "connection" in msg:
            return "retryable"
        return "fatal"


SQLITE = SqliteDialect()
MYSQL = MysqlDialect()
POSTGRESQL = PostgresqlDialect()

_DIALECTS: Dict[str, DbDialect] = {
    "sqlite": SQLITE,
    "mysql": MYSQL,
    "postgresql": POSTGRESQL,
}


def get_dialect(name: str) -> DbDialect:
    d = _DIALECTS.get(name)
    if not d:
        raise ValueError(f"不支持的数据库方言: {name}")
    return d


# ---------------- 统一入口（供 DatabaseConnector 调用）----------------


def make_engine(creds: DbCredentials, *, connect_timeout: int = 10) -> Engine:
    return get_dialect(creds.dialect).make_engine(creds, connect_timeout=connect_timeout)


def apply_readonly_session(conn, dialect: str) -> None:
    get_dialect(dialect).apply_readonly(conn)


def apply_statement_timeout(conn, dialect: str, seconds: int) -> None:
    get_dialect(dialect).apply_statement_timeout(conn, seconds)


def introspect_schema(engine: Engine, schema: Optional[str] = None) -> SchemaInfo:
    dialect = engine.url.get_backend_name()
    d = get_dialect(dialect)
    info = d.introspect(engine, schema)
    if info.tables and len(info.tables) > settings.data_prep_db_max_discovery_tables:
        logger.warning("introspection 发现 %d 张表，截断至 %d", len(info.tables), settings.data_prep_db_max_discovery_tables)
        info.tables = info.tables[:settings.data_prep_db_max_discovery_tables]
    return info


def build_keyset_query(
    *,
    table: str,
    fields: Union[List[str], str],
    key_cols: List[str],
    last_key: Optional[Tuple],
    batch_size: int,
    filters: List[Dict[str, Any]],
    time_range: Optional[Tuple[str, str]],
    meta=None,
    field_name: Optional[str] = None,
) -> Select:
    """构造 keyset 分页 SELECT（参数绑定，零 SQL 注入面）。

    key_cols：排序键列列表（通常为主键或 cursor_field）。
    last_key：上一批最后一行的排序键值元组，None=首页。
    filters：[{field, op, value}] 操作符白名单过滤条件。
    time_range：(start_iso, end_iso) 时间范围。
    """
    t = Table(table, meta or MetaData(), autoload_with=None)
    # 列选择
    if fields == "*" or fields is None:
        sel_cols = [t]
    else:
        sel_cols = [getattr(t.c, f, Column(f)) for f in fields]

    stmt = select(*sel_cols)

    # keyset 分页
    if key_cols and last_key is not None:
        cols = tuple(getattr(t.c, k, Column(k)) for k in key_cols)
        # 复合主键 row-value 比较：(`col1`, `col2`) > (:v1, :v2)
        if len(cols) == 1:
            stmt = stmt.where(cols[0] > last_key[0])
        else:
            stmt = stmt.where(tuple_(*cols) > tuple(last_key[:len(cols)]))

    # filters 操作符映射
    for f in filters:
        col = getattr(t.c, f["field"], Column(f["field"]))
        op = f.get("op", "eq")
        val = f.get("value")
        if op == "eq":
            stmt = stmt.where(col == val)
        elif op == "ne":
            stmt = stmt.where(col != val)
        elif op == "gt":
            stmt = stmt.where(col > val)
        elif op == "ge":
            stmt = stmt.where(col >= val)
        elif op == "lt":
            stmt = stmt.where(col < val)
        elif op == "le":
            stmt = stmt.where(col <= val)
        elif op == "in":
            if isinstance(val, list):
                stmt = stmt.where(col.in_(val))
        elif op == "is_null":
            stmt = stmt.where(col.is_(None))
        elif op == "not_null":
            stmt = stmt.where(col.isnot(None))
        elif op == "contains":
            stmt = stmt.where(col.contains(val))
        else:
            raise ValueError(f"不支持的操作符: {op}")

    # 时间范围
    if time_range and field_name:
        ts_col = getattr(t.c, field_name, Column(field_name))
        start, end = time_range
        stmt = stmt.where(and_(ts_col >= start, ts_col < end))

    # 排序（keyset 分页必须一致）
    if key_cols:
        stmt = stmt.order_by(*[getattr(t.c, k, Column(k)) for k in key_cols])

    stmt = stmt.limit(batch_size)
    return stmt


def classify_error(exc: Exception, dialect: str) -> str:
    """把驱动异常分类为 'fatal' 或 'retryable'。"""
    return get_dialect(dialect).classify_error(exc)


def normalize_value(v: Any) -> Any:
    """DB 值 → JSONL 值（按计划 §3.4 类型归一化表）。"""
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes):
        return base64.b64encode(v).decode("ascii")
    if isinstance(v, (list, dict)):
        return v
    return str(v)
