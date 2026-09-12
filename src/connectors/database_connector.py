# -*- coding: utf-8 -*-
"""SQLite/MySQL/PostgreSQL 安全只读数据库连接器。"""
from __future__ import annotations

import asyncio
import base64
import inspect as pyinspect
import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

from sqlalchemy import MetaData, Table, and_, func, or_, select, tuple_
from sqlalchemy.pool import NullPool

from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.models import ConnectorCapability, SourceSpec

from .base import ProbeResult, RecordBatch, SourceConnector
from .db_dialects import (
    DbCredentials,
    SchemaInfo,
    classify_error,
    classify_source_error,
    get_dialect,
    introspect_schema,
    make_engine,
)

logger = logging.getLogger(__name__)

_VALID_FILTER_OPS: Set[str] = {"eq", "ne", "gt", "ge", "lt", "le", "in", "is_null", "not_null", "contains"}
_VALID_MODES = {"table", "sql"}
_CONNECTION_GATE = threading.BoundedSemaphore(settings.data_prep_db_max_connections)


@dataclass
class DbConfig:
    mode: str
    table: str = ""
    schema: str = ""
    fields: List[str] = dc_field(default_factory=list)
    filters: List[Dict[str, Any]] = dc_field(default_factory=list)
    time_range: Optional[Tuple[Any, Any]] = None
    time_field: Optional[str] = None
    sql: str = ""
    allowed_tables: List[str] = dc_field(default_factory=list)
    incremental: Optional[Dict[str, Any]] = None
    sqlite_db_path: str = ""

    @classmethod
    def from_spec(cls, spec: SourceSpec, *, require_table: bool = True) -> "DbConfig":
        opts = spec.options
        mode = opts.get("mode", "table")
        if mode not in _VALID_MODES:
            raise ValueError(f"不支持的数据库读取 mode: {mode}")
        filters = opts.get("filters") or []
        for item in filters:
            if not isinstance(item, dict) or not item.get("field"):
                raise ValueError("数据库过滤条件必须包含 field")
            if item.get("op", "eq") not in _VALID_FILTER_OPS:
                raise ValueError(f"不支持的操作符: {item.get('op')}")

        if mode == "sql":
            if not settings.data_prep_db_custom_sql_enabled:
                raise ValueError("受控 SQL 模式当前未启用（data_prep_db_custom_sql_enabled=False）")
            if not opts.get("sql"):
                raise ValueError("sql 模式必须提供 sql")
            return cls(
                mode=mode, sql=opts["sql"],
                allowed_tables=list(opts.get("allowed_tables") or []),
                sqlite_db_path=opts.get("sqlite_db_path", ""),
            )

        table = opts.get("table", "")
        if require_table and not table:
            raise ValueError("table 模式必须提供 table 参数")
        tr = opts.get("time_range") or {}
        time_range = None
        if tr.get("field") and tr.get("start") is not None and tr.get("end") is not None:
            time_range = (tr["start"], tr["end"])
        incremental = opts.get("incremental")
        if spec.incremental is not None:
            incremental = spec.incremental.model_dump(mode="python")
        return cls(
            mode=mode, table=table, schema=opts.get("schema", ""),
            fields=list(opts.get("fields") or []), filters=filters,
            time_range=time_range, time_field=tr.get("field"),
            incremental=incremental, sqlite_db_path=opts.get("sqlite_db_path", ""),
        )


class DatabaseConnector(SourceConnector):
    name = "database"
    source_type = "database"

    def __init__(self, artifact_store: Optional[ArtifactStore] = None, *, credentials=None,
                 credential_resolver=None, engine_factory=None):
        self.artifact_store = artifact_store or ArtifactStore()
        self._credentials = credentials
        self._credential_resolver = credential_resolver
        self._engine_factory = engine_factory
        self._engines = []

    async def _resolve_creds(self, spec: SourceSpec):
        if self._credentials is not None:
            return self._credentials
        if self._credential_resolver and spec.credential_ref:
            result = self._credential_resolver(spec.credential_ref, spec.options.get("user_id"))
            return await result if pyinspect.isawaitable(result) else result
        if spec.options.get("sqlite_db_path"):
            return DbCredentials(dialect="sqlite", sqlite_relpath=spec.options["sqlite_db_path"])
        raise ValueError("无法解析数据库凭证：缺少 credentials 或 credential_resolver")

    async def probe(self, spec: SourceSpec) -> ProbeResult:
        eng = None
        try:
            cfg = DbConfig.from_spec(spec, require_table=False)
            creds = await self._resolve_creds(spec)
            eng = self._make_engine(creds, cfg)
            info = await asyncio.to_thread(self._introspect, eng, cfg.schema or None)
            return ProbeResult(
                reachable=True, message="连接成功", capabilities=self.capabilities(),
                sample={"dialect": creds.dialect, "table_count": len(info.tables)},
            )
        except Exception as exc:
            return ProbeResult(reachable=False, message=_sanitize_error(exc, await self._safe_creds(spec)))
        finally:
            if eng is not None:
                await asyncio.to_thread(eng.dispose)

    async def discover(self, spec: SourceSpec) -> Dict[str, Any]:
        cfg = DbConfig.from_spec(spec, require_table=False)
        creds = await self._resolve_creds(spec)
        eng = self._make_engine(creds, cfg)
        try:
            info = await asyncio.to_thread(self._introspect, eng, cfg.schema or None)
            return _schema_to_dict(info)
        finally:
            await asyncio.to_thread(eng.dispose)

    async def read(self, spec: SourceSpec, checkpoint: Optional[Checkpoint] = None) -> AsyncIterator[RecordBatch]:
        cfg = DbConfig.from_spec(spec)
        creds = await self._resolve_creds(spec)
        eng = self._make_engine(creds, cfg)
        started = time.monotonic()
        rows_read = 0
        bytes_read = 0
        part_no = 0
        last_key: Optional[Tuple[Any, ...]] = None
        try:
            saved = {}
            if checkpoint and (checkpoint.cursor is not None or checkpoint.is_final):
                saved = json.loads(checkpoint.cursor)
                required = {"mode", "table", "key_cols", "last_key", "rows_read", "bytes_read", "part_no", "done"}
                if (not isinstance(saved, dict) or not required.issubset(saved)
                    or not isinstance(saved["key_cols"], list)
                    or not isinstance(saved["last_key"], list)
                    or type(saved["done"]) is not bool):
                    raise ValueError("数据库 checkpoint 格式错误，请重新读取")
                if any(type(saved[key]) is not int or saved[key] < 0
                       for key in ("rows_read", "bytes_read", "part_no")):
                    raise ValueError("数据库 checkpoint 计数错误，请重新读取")
                rows_read, bytes_read, part_no = saved["rows_read"], saved["bytes_read"], saved["part_no"]
                last_key = tuple(saved["last_key"]) or None
                expected_table = cfg.table if cfg.mode == "table" else "custom_sql"
                if saved["mode"] != "table" or saved["table"] != expected_table:
                    raise ValueError("数据库 checkpoint 表不匹配，请重新读取")
            if cfg.mode == "table":
                table = await asyncio.to_thread(self._reflect_table, eng, cfg)
                key_cols = list(table.primary_key.columns.keys())
                cursor_field = (cfg.incremental or {}).get("cursor_field")
                if cursor_field:
                    _require_columns(table, [cursor_field])
                    # 水位可能重复，完整主键负责区分同一水位内的每一行。
                    key_cols = list(dict.fromkeys([cursor_field, *key_cols]))
                _validate_config_columns(table, cfg, key_cols)
                check_unique = bool(cursor_field and not table.primary_key.columns)
                if saved:
                    if saved.get("key_cols") != key_cols:
                        if cursor_field and saved.get("key_cols") == [cursor_field]:
                            # 旧单水位只有确实唯一时才能继续，不能补空主键跳过并列行。
                            key_cols = [cursor_field]
                            check_unique = True
                        else:
                            raise ValueError("数据库 checkpoint 排序键不匹配，请重新读取")
                    if last_key is not None:
                        if len(last_key) != len(key_cols) or any(value is None for value in last_key):
                            raise ValueError("数据库 checkpoint 水位不完整，请重新读取")
                        last_key = tuple(_coerce_cursor(value, table.c[name])
                                         for name, value in zip(key_cols, last_key))
                    elif rows_read and key_cols:
                        raise ValueError("数据库 checkpoint 缺少水位，请重新读取")
                await asyncio.to_thread(self._validate_keyset, eng, table, cfg, key_cols, check_unique)
                if saved.get("done"):
                    yield RecordBatch(checkpoint=checkpoint)
                    return
                if not key_cols:
                    no_key_warning = "表无主键且未指定水位线，使用 OFFSET 全量读取；源表变化时不保证断点一致性"
                else:
                    no_key_warning = ""

                while True:
                    limit, stop_warning = _next_limit(spec, rows_read, bytes_read, started)
                    if limit <= 0:
                        yield RecordBatch(checkpoint=_checkpoint(cfg.table, key_cols, last_key, part_no,
                                                                 rows_read, bytes_read, True),
                                          warnings=[stop_warning] if stop_warning else [])
                        break
                    retries = 0
                    while True:
                        try:
                            rows, raw_last_key, cell_warnings = await asyncio.to_thread(
                                self._fetch_table_batch, eng, table, cfg, key_cols, last_key,
                                rows_read if not key_cols else 0, limit,
                            )
                            break
                        except Exception as exc:
                            if classify_error(exc, creds.dialect) != "retryable" or retries >= settings.data_prep_db_max_retries:
                                kind = classify_error(exc, creds.dialect)
                                message = _sanitize_error(exc, creds)
                                if kind == "retryable":
                                    yield RecordBatch(retryable_error=message, checkpoint=_checkpoint(
                                        cfg.table, key_cols, last_key, part_no, rows_read, bytes_read, False))
                                else:
                                    yield RecordBatch(
                                        fatal_error=message,
                                        error_code=classify_source_error(exc, creds.dialect),
                                        checkpoint=_checkpoint(
                                            cfg.table, key_cols, last_key, part_no,
                                            rows_read, bytes_read, False,
                                        ),
                                    )
                                return
                            retries += 1
                            await asyncio.sleep(min(2 ** (retries - 1), 4))

                    if not rows:
                        yield RecordBatch(checkpoint=_checkpoint(cfg.table, key_cols, last_key, part_no,
                                                                 rows_read, bytes_read, True))
                        break
                    rows, payload, byte_warning = _fit_rows_to_bytes(rows, spec, bytes_read)
                    warnings = cell_warnings + ([no_key_warning] if no_key_warning and part_no == 0 else [])
                    if byte_warning:
                        warnings.append(byte_warning)
                    if not rows:
                        yield RecordBatch(checkpoint=_checkpoint(cfg.table, key_cols, last_key, part_no,
                                                                 rows_read, bytes_read, True), warnings=warnings)
                        break

                    part_no += 1
                    rows_read += len(rows)
                    bytes_read += len(payload)
                    if key_cols:
                        last_key = raw_last_key
                    source_exhausted = len(rows) < limit
                    _, limit_warning = _next_limit(spec, rows_read, bytes_read, started)
                    done = source_exhausted or bool(limit_warning)
                    if limit_warning:
                        warnings.append(limit_warning)
                    artifact = self.artifact_store.write_raw(
                        task_id=spec.options.get("task_id", "unknown"), source_id=spec.source_id,
                        data=payload, ext=".jsonl", media_type="application/x-ndjson",
                        uri=_safe_uri(creds, cfg.table, part_no),
                        request_snapshot={
                            "mode": "table", "dialect": creds.dialect, "schema": cfg.schema,
                            "table": cfg.table, "fields": list(cfg.fields), "part_no": part_no,
                            "rows": len(rows), "filter_digest": _filter_digest(cfg.filters),
                        },
                    )
                    yield RecordBatch(
                        artifacts=[artifact], byte_count=len(payload), warnings=_dedupe(warnings),
                        checkpoint=_checkpoint(cfg.table, key_cols, last_key, part_no,
                                               rows_read, bytes_read, done),
                    )
                    if done:
                        break
            else:
                if saved.get("done"):
                    yield RecordBatch(checkpoint=checkpoint)
                    return
                async for batch in self._read_sql(spec, cfg, creds, eng, started, rows_read, bytes_read, part_no):
                    yield batch
        except Exception as exc:
            # 预览和任务图都消费 fatal_error；预检或恢复错误也必须走同一脱敏失败路径。
            yield RecordBatch(
                fatal_error=_sanitize_error(exc, creds),
                error_code=classify_source_error(exc, creds.dialect),
            )
        finally:
            await asyncio.to_thread(eng.dispose)

    async def _read_sql(self, spec, cfg, creds, eng, started, rows_read, bytes_read, part_no):
        from sqlglot import parse_one
        from .sql_guard import validate_select

        info = await asyncio.to_thread(self._introspect, eng, cfg.schema or None)
        discovered = {t.name for t in info.tables}
        allowed = set(cfg.allowed_tables) if cfg.allowed_tables else discovered
        dialect_name = "postgres" if creds.dialect == "postgresql" else creds.dialect
        validate_select(cfg.sql, allowed_tables=allowed, dialect=dialect_name)
        tree = parse_one(cfg.sql, read=dialect_name)
        while True:
            limit, stop_warning = _next_limit(spec, rows_read, bytes_read, started)
            if limit <= 0:
                yield RecordBatch(checkpoint=_checkpoint("custom_sql", [], None, part_no, rows_read, bytes_read, True),
                                  warnings=[stop_warning] if stop_warning else [])
                return
            paged_sql = tree.copy().limit(limit).offset(rows_read).sql(dialect=dialect_name)
            try:
                rows, cell_warnings = await asyncio.to_thread(self._fetch_sql_batch, eng, creds, paged_sql)
            except Exception as exc:
                yield RecordBatch(
                    fatal_error=_sanitize_error(exc, creds),
                    error_code=classify_source_error(exc, creds.dialect),
                )
                return
            if not rows:
                yield RecordBatch(checkpoint=_checkpoint("custom_sql", [], None, part_no, rows_read, bytes_read, True))
                return
            rows, payload, byte_warning = _fit_rows_to_bytes(rows, spec, bytes_read)
            warnings = cell_warnings + ([byte_warning] if byte_warning else [])
            part_no += 1
            rows_read += len(rows)
            bytes_read += len(payload)
            done = len(rows) < limit or bool(_next_limit(spec, rows_read, bytes_read, started)[1])
            artifact = self.artifact_store.write_raw(
                task_id=spec.options.get("task_id", "unknown"), source_id=spec.source_id,
                data=payload, media_type="application/x-ndjson", ext=".jsonl",
                uri=_safe_uri(creds, "custom_sql", part_no),
                request_snapshot={"mode": "sql", "tables": sorted(allowed), "part_no": part_no},
            )
            yield RecordBatch(artifacts=[artifact], byte_count=len(payload), warnings=_dedupe(warnings),
                              checkpoint=_checkpoint("custom_sql", [], None, part_no, rows_read, bytes_read, done))
            if done:
                return

    def _make_engine(self, creds, cfg):
        if self._engine_factory:
            eng = self._engine_factory(creds)
        elif cfg.sqlite_db_path:
            path = Path(cfg.sqlite_db_path).resolve()
            eng = __import__("sqlalchemy").create_engine(
                "sqlite://", poolclass=NullPool,
                creator=lambda: sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True),
            )
        else:
            eng = make_engine(creds, connect_timeout=settings.data_prep_db_connect_timeout_seconds)
        self._engines.append(eng)
        return eng

    def _introspect(self, eng, schema):
        with _CONNECTION_GATE:
            return introspect_schema(eng, schema)

    def _reflect_table(self, eng, cfg):
        with _CONNECTION_GATE:
            return Table(cfg.table, MetaData(), schema=cfg.schema or None, autoload_with=eng)

    def _validate_keyset(self, eng, table, cfg, key_cols, check_unique):
        nullable = [table.c[name] for name in key_cols if table.c[name].nullable]
        if not nullable and not check_unique:
            return
        # 检查原读取范围；续页谓词会提前排除 NULL，不能用于证明水位完整。
        predicates = _table_predicates(table, cfg)
        dialect = get_dialect(eng.url.get_backend_name())
        with _CONNECTION_GATE, eng.connect() as conn:
            dialect.apply_readonly(conn)
            dialect.apply_statement_timeout(conn, settings.data_prep_db_query_timeout_seconds)
            if nullable and conn.execute(select(1).select_from(table).where(
                *predicates, or_(*(col.is_(None) for col in nullable)),
            ).limit(1)).first():
                raise ValueError("读取范围内的排序键含 NULL，请过滤空水位或选择非空排序键")
            if check_unique:
                # ponytail: 无主键/旧单水位做一次只读唯一性扫描；大表应使用完整主键分页。
                cols = [table.c[name] for name in key_cols]
                if conn.execute(select(*cols).where(*predicates).group_by(*cols).having(
                    func.count() > 1,
                ).limit(1)).first():
                    raise ValueError("水位不唯一，无法安全续读，请使用完整主键重新读取")

    def _fetch_table_batch(self, eng, table, cfg, key_cols, last_key, offset, limit):
        stmt = select(*table.c)
        predicates = _table_predicates(table, cfg)
        if key_cols and last_key is not None:
            cols = [table.c[name] for name in key_cols]
            predicates.append(cols[0] > last_key[0] if len(cols) == 1 else tuple_(*cols) > tuple(last_key))
        if predicates:
            stmt = stmt.where(and_(*predicates))
        if key_cols:
            stmt = stmt.order_by(*[table.c[name] for name in key_cols])
        elif offset:
            stmt = stmt.offset(offset)
        stmt = stmt.limit(limit)
        dialect = get_dialect(eng.url.get_backend_name())
        with _CONNECTION_GATE, eng.connect() as conn:
            dialect.apply_readonly(conn)
            dialect.apply_statement_timeout(conn, settings.data_prep_db_query_timeout_seconds)
            mappings = list(conn.execute(stmt).mappings())
        warnings: List[str] = []
        rows = []
        for mapping in mappings:
            full = {key: _normalize_cell(value, key, warnings) for key, value in mapping.items()}
            rows.append({name: full[name] for name in cfg.fields} if cfg.fields else full)
        raw_last = tuple(mappings[-1][name] for name in key_cols) if mappings and key_cols else last_key
        return rows, raw_last, _dedupe(warnings)

    def _fetch_sql_batch(self, eng, creds, sql):
        dialect = get_dialect(creds.dialect)
        with _CONNECTION_GATE, eng.connect() as conn:
            dialect.apply_readonly(conn)
            dialect.apply_statement_timeout(conn, settings.data_prep_db_query_timeout_seconds)
            mappings = list(conn.exec_driver_sql(sql).mappings())
        warnings: List[str] = []
        rows = [{key: _normalize_cell(value, key, warnings) for key, value in row.items()} for row in mappings]
        return rows, _dedupe(warnings)

    async def _safe_creds(self, spec):
        try:
            return await self._resolve_creds(spec)
        except Exception:
            return None

    def capabilities(self):
        return {
            ConnectorCapability.READ_ONLY, ConnectorCapability.SUPPORTS_CHECKPOINT,
            ConnectorCapability.STREAMING, ConnectorCapability.INCREMENTAL,
            ConnectorCapability.SCHEMA_PROBE, ConnectorCapability.RANDOM_ACCESS,
        }

    async def close(self):
        engines, self._engines = self._engines, []
        for eng in engines:
            await asyncio.to_thread(eng.dispose)



def _table_predicates(table, cfg):
    predicates = []
    for item in cfg.filters:
        col = table.c[item["field"]]
        op, value = item.get("op", "eq"), item.get("value")
        # 只构造当前操作，NULL 过滤不应触发无关的大小比较或 contains。
        if op == "eq":
            predicate = col == value
        elif op == "ne":
            predicate = col != value
        elif op == "gt":
            predicate = col > value
        elif op == "ge":
            predicate = col >= value
        elif op == "lt":
            predicate = col < value
        elif op == "le":
            predicate = col <= value
        elif op == "is_null":
            predicate = col.is_(None)
        elif op == "not_null":
            predicate = col.is_not(None)
        elif op == "contains":
            predicate = col.contains(value)
        else:
            predicate = col.in_(value if isinstance(value, list) else [])
        predicates.append(predicate)
    if cfg.time_range and cfg.time_field:
        col = table.c[cfg.time_field]
        predicates.extend((col >= cfg.time_range[0], col < cfg.time_range[1]))
    incremental = cfg.incremental or {}
    if incremental.get("cursor_field") and incremental.get("last_value") is not None:
        col = table.c[incremental["cursor_field"]]
        predicates.append(col > _coerce_cursor(incremental["last_value"], col))
    return predicates


def _validate_config_columns(table, cfg, key_cols):
    names = set(table.c.keys())
    requested = set(cfg.fields) | set(key_cols)
    requested |= {item["field"] for item in cfg.filters}
    if cfg.time_field:
        requested.add(cfg.time_field)
    missing = requested - names
    if missing:
        raise ValueError(f"字段不在表 {table.name} 的白名单中: {', '.join(sorted(missing))}")


def _require_columns(table, names):
    missing = set(names) - set(table.c.keys())
    if missing:
        raise ValueError(f"水位线字段不存在: {', '.join(sorted(missing))}")


def _coerce_cursor(value, column):
    try:
        pytype = column.type.python_type
        # 只恢复明确编码；截断浮点、字符串真值或把对象转文本都会改变续读边界。
        if type(value) is pytype:
            restored = value
        elif pytype in (datetime, date, dt_time) and isinstance(value, str):
            restored = pytype.fromisoformat(value)
        elif pytype is int and isinstance(value, str):
            restored = int(value)
        elif pytype in (float, Decimal) and type(value) in (int, str):
            restored = pytype(value)
            if type(value) is int and restored != value:
                raise ValueError("水位转换丢失精度")
        elif pytype is bytes and isinstance(value, dict) and set(value) == {"base64"}:
            restored = base64.b64decode(value["base64"], validate=True)
        else:
            raise ValueError("水位编码不匹配")
        if ((isinstance(restored, float) and not math.isfinite(restored))
            or (isinstance(restored, Decimal) and not restored.is_finite())):
            raise ValueError("水位必须为有限值")
        return restored
    except (AttributeError, TypeError, ValueError, NotImplementedError) as exc:
        raise ValueError(f"水位值与字段 {column.name} 类型不匹配") from exc


def _normalize_cell(value, field, warnings):
    max_bytes = settings.data_prep_db_max_cell_bytes
    if isinstance(value, bytes):
        if len(value) > max_bytes:
            value = value[:max_bytes]
            warnings.append(f"字段 {field} 的 BLOB 超过 {max_bytes} 字节，已截断")
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, str):
        raw = value.encode("utf-8")
        if len(raw) > max_bytes:
            warnings.append(f"字段 {field} 超过 {max_bytes} 字节，已截断")
            return raw[:max_bytes].decode("utf-8", errors="ignore")
        return value
    if value is None or isinstance(value, (int, float, bool, list, dict)):
        return value
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    text = str(value)
    return _normalize_cell(text, field, warnings)


def _next_limit(spec, rows_read, bytes_read, started):
    limit = max(1, int(spec.options.get("batch_size", settings.data_prep_db_batch_size)))
    limits = spec.limits
    if not limits:
        return limit, ""
    if limits.max_seconds and time.monotonic() - started >= limits.max_seconds:
        return 0, f"达到 max_seconds={limits.max_seconds}，已安全截断"
    if limits.max_records:
        remaining = limits.max_records - rows_read
        if remaining <= 0:
            return 0, f"达到 max_records={limits.max_records}，已安全截断"
        limit = min(limit, remaining)
    if limits.max_bytes and bytes_read >= limits.max_bytes:
        return 0, f"达到 max_bytes={limits.max_bytes}，已安全截断"
    return limit, ""


def _fit_rows_to_bytes(rows, spec, bytes_read):
    max_bytes = spec.limits.max_bytes if spec.limits else None
    output = []
    chunks = []
    warning = ""
    for row in rows:
        chunk = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if max_bytes and bytes_read + sum(map(len, chunks)) + len(chunk) > max_bytes:
            warning = f"达到 max_bytes={max_bytes}，已在完整记录边界截断"
            break
        output.append(row)
        chunks.append(chunk)
    return output, b"".join(chunks), warning


def _checkpoint(table, key_cols, last_key, part_no, rows_read, bytes_read, done):
    data = {
        "mode": "table", "table": table, "key_cols": key_cols,
        "last_key": _serializable_key(last_key), "part_no": part_no,
        "rows_read": rows_read, "bytes_read": bytes_read, "done": done,
    }
    watermark = str(data["last_key"][0]) if data["last_key"] else None
    return Checkpoint(cursor=json.dumps(data, ensure_ascii=False), watermark=watermark,
                      next_part_no=part_no + 1, is_final=done)


def _serializable_key(key):
    if not key:
        return []
    return [value.isoformat() if isinstance(value, (datetime, date, dt_time))
            else str(value) if isinstance(value, Decimal)
            else {"base64": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes)
            else value for value in key]


def _safe_uri(creds, table, part_no):
    if creds.dialect == "sqlite":
        return f"sqlite:{table}/part-{part_no:05d}.jsonl"
    return f"{creds.dialect}://{creds.host}:{creds.port}/{creds.database}/{table}/part-{part_no:05d}.jsonl"


def _filter_digest(filters):
    import hashlib
    safe = [{"field": item.get("field"), "op": item.get("op"),
             "value_sha256": hashlib.sha256(json.dumps(item.get("value"), ensure_ascii=False,
                                                        sort_keys=True).encode("utf-8")).hexdigest()}
            for item in filters]
    return safe


def _sanitize_error(exc, creds):
    message = str(exc)
    if creds:
        for secret in (getattr(creds, "password", ""), getattr(creds, "username", "")):
            if secret:
                message = message.replace(secret, "***")
    message = __import__("re").sub(r"(?i)(password|pwd)\s*[=:]\s*[^\s,;]+", r"\1=***", message)
    return message[:1000]


def _schema_to_dict(info: SchemaInfo) -> Dict[str, Any]:
    schemas: Dict[str, int] = {}
    for table in info.tables:
        schemas[table.schema or info.default_schema or "main"] = schemas.get(
            table.schema or info.default_schema or "main", 0) + 1
    return {
        "dialect": info.dialect, "server_version": info.server_version,
        "default_schema": info.default_schema,
        "schemas": [{"name": name, "table_count": count} for name, count in sorted(schemas.items())],
        "tables": [{
            "name": table.name, "schema": table.schema, "estimated_rows": table.estimated_rows,
            "columns": [{"name": col["name"], "type": str(col.get("type", "")),
                         "nullable": bool(col.get("nullable", True))} for col in table.columns],
            "primary_key": table.primary_key,
        } for table in info.tables],
    }


def _dedupe(items):
    return list(dict.fromkeys(item for item in items if item))
