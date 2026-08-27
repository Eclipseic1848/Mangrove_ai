"""Mangrove 显式数据库迁移的唯一公开 Seam。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from filelock import FileLock, Timeout
from sqlalchemy import create_engine
from sqlalchemy.engine import URL


class SchemaNotCurrentError(RuntimeError):
    """数据库 Schema 未达到当前代码要求。"""


@dataclass(frozen=True)
class DatabaseTarget:
    """一个由 Mangrove 管理的数据库及其 Schema Profile。"""

    profile: str
    path: Path


@dataclass(frozen=True)
class DatabaseStatus:
    """只读 Schema 检查结果。"""

    target: DatabaseTarget
    state: Literal["uninitialized", "legacy", "unknown", "drift", "current"]
    current_revision: str | None
    target_revision: str
    pending_revisions: tuple[str, ...]
    gaps: tuple[str, ...] = ()

    def require_current(self) -> None:
        if self.state == "current":
            return
        raise SchemaNotCurrentError(
            "数据库 Schema 未达到当前版本；请先执行显式迁移："
            "python -m src.database_migrations apply "
            f"--profile {self.target.profile} --database <path> --backup <path>"
        )


@dataclass(frozen=True)
class PlannedRevision:
    """一个已经冻结内容摘要的待执行 revision。"""

    revision: str
    content_sha256: str
    requires_copy_validation: bool
    operations: tuple[str, ...]


@dataclass(frozen=True)
class DatabasePlan:
    """只读迁移计划；不会连接写库或生成文件。"""

    target: DatabaseTarget
    state: str
    source_revision: str | None
    target_revision: str
    pending_revisions: tuple[str, ...]
    revisions: tuple[PlannedRevision, ...]


@dataclass(frozen=True)
class MigrationReceipt:
    """一次显式迁移返回给调用方的可核验证据。"""

    target: DatabaseTarget
    source_revision: str | None
    source_database_sha256: str
    target_revision: str
    applied_revisions: tuple[str, ...]
    backup_path: Path
    backup_sha256: str
    receipt_path: Path
    outcome: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True)
class RestoreVerification:
    """恢复副本的只读验证结果；不覆盖任何源库。"""

    restored_path: Path
    verification_receipt_path: Path
    backup_sha256: str
    integrity_check: str
    foreign_key_violations: int
    schema_state: str


_PROFILE_HEADS = {
    "webui": "webui_0004",
    "scheduler": "scheduler_0001",
    "legacy_app": "legacy_app_0001",
    "qualification_ledger": "qualification_ledger_0001",
}
_PROFILE_REQUIRED_COLUMNS = {
    "webui": {
        "users": ("role", "disabled", "pending"),
        "messages": ("task_id", "meta_json"),
        "memory_hit_log": ("hit",),
        "library_dedup_scan_log": ("details",),
        "message_feedback": ("status", "admin_note"),
        "data_prep_tasks": ("checkpoint_json", "unit_id"),
        "document_workspaces": (
            "checked_upload_ids_json",
            "active_unit_id",
        ),
        "semantic_harness_attempts": ("artifact_paths_json",),
        "semantic_workspace_tasks": (
            "failure_json",
            "source_refs_json",
            "table_output_contracts_json",
        ),
        "semantic_workspace_revisions": ("table_output_contracts_json",),
        "document_task_units": ("archived_at",),
        "agentic_runtime_runs": (
            "verification_json",
            "verified_candidate_set_hash",
            "model_connection_id",
            "model_connection_version",
            "model_connection_model",
            "external_api_confirmed",
        ),
        "agentic_runtime_events": ("event_id", "event_type", "details_json"),
        "agentic_runtime_idempotency": ("idempotency_key", "request_hash"),
        "agentic_runtime_coverage": ("contract_json", "ledger_json"),
        "model_connections": ("connection_id", "compatibility_slot"),
        "model_connection_models": ("connection_id", "model_id", "status"),
        "model_connection_grants": ("grant_id", "token_hash", "expires_at"),
        "model_provider_usage": ("usage_id", "status", "native_json"),
        "model_usage_preferences": ("owner_user_id", "connection_id", "model_id"),
        "model_connection_imports": ("source_scope", "source_fingerprint"),
        "runtime_config_secrets": (
            "secret_id", "owner_scope", "config_key", "ciphertext", "created_at",
        ),
        "conversation_raw_turns": ("turn_id", "owner_id", "revision"),
        "conversation_context_deltas": ("delta_id", "turn_id", "payload_json"),
        "conversation_revision_proposals": ("proposal_id", "payload_json"),
        "conversation_revision_decisions": ("decision_id", "status", "payload_json"),
        "conversation_steering_results": ("result_id", "turn_id", "payload_json"),
        "delivery_publish_intents": (
            "publication_key",
            "request_idempotency_hash",
            "status",
        ),
        "formal_delivery_runs": ("delivery_id", "publication_key", "status"),
        "formal_delivery_outputs": ("output_id", "delivery_id", "sha256"),
        "capability_pack_versions": ("owner_key", "pack_id", "digest"),
        "automation_procedure_versions": ("owner_key", "procedure_id", "digest"),
        "capability_validations": ("owner_key", "validation_id", "payload_json"),
        "capability_components": ("owner_key", "component_id", "digest"),
        "capability_selections": ("owner_id", "task_id", "revision"),
        "candidate_verification_migrations": (
            "migration_id", "backup_sha256", "applied_at", "ddl_sha256",
        ),
        "candidate_verification_attempts": (
            "attempt_id", "owner_id", "task_id", "revision",
            "rebaseline_authorization_json", "rebaseline_authorization_hash",
            "status",
        ),
        "candidate_reverification_authorities": (
            "authority_id", "evidence_hash", "idempotency_key",
        ),
        "runtime_routing_migrations": (
            "migration_id", "ddl_sha256", "backup_sha256", "applied_at",
        ),
        "runtime_gate_snapshots": ("snapshot_id", "payload_json"),
        "runtime_rollout_state": (
            "state_id", "mode", "p0_blocked", "active_gate_snapshot_id",
        ),
        "runtime_rollout_approvals": ("approval_id", "payload_json"),
        "runtime_rollout_events": ("event_id", "event_type", "payload_json"),
        "runtime_assignments": (
            "owner_id", "task_id", "revision", "runtime_version",
        ),
        "capability_acquisition_runs": (
            "acquisition_id", "owner_id", "status", "payload_json",
        ),
        "capability_acquisition_migrations": (
            "migration_id", "backup_sha256",
        ),
        "capability_governance_events": (
            "event_id", "event_type", "idempotency_key",
        ),
        "capability_validation_runs": (
            "run_id", "owner_id", "digest", "status",
        ),
        "capability_validation_idempotency": (
            "owner_id", "digest", "idempotency_key",
        ),
        "capability_validation_leases": ("digest", "run_id", "worker_id"),
        "capability_supply_chain_evidence": (
            "evidence_id", "digest", "status",
        ),
        "capability_platform_validation_runs": (
            "run_id", "digest", "status",
        ),
        "capability_platform_validation_leases": (
            "digest", "run_id", "worker_id",
        ),
    },
    "scheduler": {
        "scheduled_tasks": (
            "owner_user_id",
            "name",
            "source",
            "interval_seconds",
            "start_date",
            "end_date",
        ),
        "scheduled_task_runs": (
            "task_id",
            "run_at",
            "success",
            "report_path",
            "json_path",
        ),
    },
    "legacy_app": {
        "collected_items": (
            "id",
            "task_id",
            "source",
            "url",
            "title",
            "content",
            "metadata",
            "created_at",
        ),
    },
    "qualification_ledger": {
        "qualification_ledger_metadata": (
            "schema_version",
            "ledger_id",
            "revision",
        ),
        "qualification_batches": (
            "batch_id",
            "provider_set_sha256",
            "state",
        ),
        "qualification_batch_providers": (
            "batch_id",
            "provider_key",
            "state",
        ),
        "qualification_provider_attempts": (
            "batch_id",
            "provider_key",
            "attempt_number",
            "state",
        ),
        "qualification_retry_authorizations": (
            "batch_id",
            "provider_key",
            "retry_number",
        ),
        "qualification_ledger_recoveries": (
            "recovery_id",
            "batch_id",
            "provider_key",
        ),
    },
}
_PROFILE_LEGACY_ANCHORS = {
    "webui": (
        "users",
        "agentic_runtime_runs",
        "model_connections",
        "conversation_raw_turns",
        "delivery_publish_intents",
        "capability_pack_versions",
        "candidate_verification_migrations",
        "runtime_routing_migrations",
        "capability_acquisition_migrations",
        "capability_governance_events",
    ),
    "scheduler": ("scheduled_tasks",),
    "legacy_app": ("collected_items",),
    "qualification_ledger": ("qualification_ledger_metadata",),
}


def _alembic_config(connection: object) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).with_name("alembic")),
    )
    config.attributes["connection"] = connection
    return config


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(_alembic_config(None))


def _revision_manifest() -> dict[str, str]:
    path = Path(__file__).with_name("revision_manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("迁移 revision 冻结清单不可读取") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(revision, str)
        and isinstance(content_sha256, str)
        and len(content_sha256) == 64
        for revision, content_sha256 in payload.items()
    ):
        raise RuntimeError("迁移 revision 冻结清单格式无效")
    return payload


@lru_cache(maxsize=1)
def _schema_manifest() -> dict[str, dict[str, object]]:
    """读取每个当前 revision 拥有的完整 sqlite_master 冻结摘要。"""
    path = Path(__file__).with_name("schema_manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("迁移 Schema 冻结清单不可读取") from exc
    if not isinstance(payload, dict) or set(payload) != set(_PROFILE_HEADS):
        raise RuntimeError("迁移 Schema 冻结清单 Profile 不完整")
    for profile, expected_revision in _PROFILE_HEADS.items():
        entry = payload.get(profile)
        if not isinstance(entry, dict) or entry.get("revision") != expected_revision:
            raise RuntimeError(f"迁移 Schema 冻结清单版本不匹配：{profile}")
        objects = entry.get("objects")
        tables = entry.get("tables")
        if not isinstance(objects, dict) or not isinstance(tables, dict) or not tables:
            raise RuntimeError(f"迁移 Schema 冻结清单对象无效：{profile}")
        if not all(
            isinstance(key, str)
            and isinstance(value, str)
            and len(value) == 64
            for contract in (objects, tables)
            for key, value in contract.items()
        ):
            raise RuntimeError(f"迁移 Schema 冻结清单对象无效：{profile}")
    return payload


def _schema_object_sha256(row: tuple[object, ...]) -> str:
    kind, name, table, sql = row
    normalized_sql = " ".join(str(sql).split()) if sql is not None else None
    payload = {
        "type": str(kind),
        "name": str(name),
        "table": str(table),
        "sql": normalized_sql,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _check_expressions(sql: str) -> tuple[str, ...]:
    """提取规范化 CHECK 表达式；兼容嵌套括号与引号。"""
    expressions: list[str] = []
    upper = sql.upper()
    cursor = 0
    while True:
        match = re.search(r"\bCHECK\s*\(", upper[cursor:])
        if match is None:
            break
        opening = cursor + match.end() - 1
        depth = 1
        index = opening + 1
        quote: str | None = None
        while index < len(sql) and depth:
            character = sql[index]
            if quote is not None:
                if character == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 1
                    else:
                        quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            index += 1
        if depth:
            raise RuntimeError("迁移 Schema 冻结清单含无法解析的 CHECK 约束")
        expressions.append(" ".join(sql[opening + 1:index - 1].split()))
        cursor = index
    return tuple(sorted(expressions))


def _strip_sql_comments(sql: str) -> str:
    """只移除引号外注释，避免格式注释改变 Schema 语义摘要。"""
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(sql):
        character = sql[index]
        if quote is not None:
            result.append(character)
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            result.append(character)
            index += 1
            continue
        if character == "[":
            quote = "]"
            result.append(character)
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline < 0 else newline + 1
            result.append(" ")
            continue
        if sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            index = len(sql) if closing < 0 else closing + 2
            result.append(" ")
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _normalize_sql_fragment(fragment: str) -> str:
    """规范格式和关键字大小写，同时保留引号内字面量。"""
    fragment = _strip_sql_comments(fragment)
    result: list[str] = []
    quoted_tokens: list[str] = []
    index = 0
    while index < len(fragment):
        character = fragment[index]
        if character not in {"'", '"', "`", "["}:
            result.append(character.upper())
            index += 1
            continue
        closing = "]" if character == "[" else character
        start = index
        index += 1
        while index < len(fragment):
            if fragment[index] == closing:
                if index + 1 < len(fragment) and fragment[index + 1] == closing:
                    index += 2
                    continue
                index += 1
                break
            index += 1
        quoted_tokens.append(fragment[start:index])
        result.append(f"\x00Q{len(quoted_tokens) - 1}\x00")
    normalized = " ".join("".join(result).split())
    normalized = re.sub(r"\s*([(),])\s*", r"\1", normalized)
    normalized = re.sub(r"\s*(\|\||<<|>>|<=|>=|<>|!=|[-+*/%=<>|&~])\s*", r"\1", normalized)
    for token_index, token in enumerate(quoted_tokens):
        normalized = normalized.replace(f"\x00Q{token_index}\x00", token)
    return normalized.strip()


def _split_table_definitions(sql: str) -> tuple[list[str], str]:
    """按顶层逗号拆分 CREATE TABLE，保留括号内表达式。"""
    opening = sql.find("(")
    if opening < 0:
        raise RuntimeError("迁移 Schema 表定义缺少左括号")
    definitions: list[str] = []
    start = opening + 1
    depth = 1
    quote: str | None = None
    index = start
    while index < len(sql):
        character = sql[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "[":
            quote = "]"
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                definitions.append(sql[start:index])
                return definitions, sql[index + 1:]
        elif character == "," and depth == 1:
            definitions.append(sql[start:index])
            start = index + 1
        index += 1
    raise RuntimeError("迁移 Schema 表定义括号不完整")


def _leading_definition_name(definition: str) -> str:
    value = definition.lstrip()
    if not value:
        return ""
    if value[0] in {'"', "`", "["}:
        closing = "]" if value[0] == "[" else value[0]
        end = value.find(closing, 1)
        return value[1:end] if end >= 0 else value
    return re.split(r"\s|\(", value, maxsplit=1)[0]


def _table_definition_contract(sql: str) -> dict[str, object]:
    """冻结完整列定义和表约束，但不依赖列声明顺序。"""
    definitions, suffix = _split_table_definitions(_strip_sql_comments(sql))
    table_constraint_prefixes = {
        "CONSTRAINT",
        "PRIMARY",
        "UNIQUE",
        "CHECK",
        "FOREIGN",
    }
    columns: list[tuple[str, str]] = []
    constraints: list[str] = []
    for definition in definitions:
        normalized = _normalize_sql_fragment(definition)
        name = _leading_definition_name(definition)
        if name.upper() in table_constraint_prefixes:
            constraints.append(normalized)
        else:
            columns.append((name.casefold(), normalized))
    return {
        "columns": sorted(columns),
        "table_constraints": sorted(constraints),
        "suffix": _normalize_sql_fragment(suffix),
    }


def _table_contract_sha256(
    connection: sqlite3.Connection,
    table: str,
) -> str:
    """生成与列声明顺序无关、覆盖 PK/UNIQUE/FK/CHECK 的表契约摘要。"""
    quoted = '"' + table.replace('"', '""') + '"'
    schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if schema_row is None:
        return ""
    columns = [
        {
            "name": str(row[1]),
            "type": str(row[2] or "").upper(),
            "not_null": int(row[3]),
            "default": None if row[4] is None else " ".join(str(row[4]).split()),
            "primary_key_position": int(row[5]),
            "hidden": int(row[6]),
        }
        for row in connection.execute(f"PRAGMA table_xinfo({quoted})").fetchall()
    ]
    unique_constraints = []
    for row in connection.execute(f"PRAGMA index_list({quoted})").fetchall():
        origin = str(row[3])
        if origin not in {"u", "pk"}:
            continue
        index_name = str(row[1]).replace('"', '""')
        indexed_columns = [
            None if item[2] is None else str(item[2])
            for item in connection.execute(
                f'PRAGMA index_xinfo("{index_name}")'
            ).fetchall()
            if int(item[5]) == 1
        ]
        unique_constraints.append(
            {
                "origin": origin,
                "partial": int(row[4]),
                "columns": indexed_columns,
            }
        )
    foreign_keys = [
        {
            "id": int(row[0]),
            "sequence": int(row[1]),
            "target_table": str(row[2]),
            "source_column": str(row[3]),
            "target_column": None if row[4] is None else str(row[4]),
            "on_update": str(row[5]),
            "on_delete": str(row[6]),
            "match": str(row[7]),
        }
        for row in connection.execute(f"PRAGMA foreign_key_list({quoted})").fetchall()
    ]
    normalized_sql = " ".join(str(schema_row[0] or "").split()).upper()
    payload = {
        "columns": sorted(columns, key=lambda item: item["name"]),
        "unique_constraints": sorted(
            unique_constraints,
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        "foreign_keys": sorted(
            foreign_keys,
            key=lambda item: (
                item["id"], item["sequence"], item["source_column"]
            ),
        ),
        "checks": _check_expressions(str(schema_row[0] or "")),
        "without_rowid": normalized_sql.endswith(" WITHOUT ROWID"),
        "strict": normalized_sql.endswith(" STRICT"),
        # PRAGMA 不暴露 AUTOINCREMENT、COLLATE 和生成列表达式，必须同时冻结 SQL 定义。
        "definition": _table_definition_contract(str(schema_row[0] or "")),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _profile_schema_gaps(
    profile: str,
    connection: sqlite3.Connection,
    existing_gaps: list[str],
) -> list[str]:
    """校验当前 revision 的完整受管对象集合，同时允许非受管扩展表。"""
    entry = _schema_manifest()[profile]
    expected = dict(entry["objects"])
    expected_tables = dict(entry["tables"])
    owned_tables = set(expected_tables)
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "ORDER BY type, name"
    ).fetchall()
    actual_objects = {
        f"{row[0]}:{row[1]}": _schema_object_sha256(row)
        for row in rows
        if str(row[0]) != "table"
        and str(row[2]) in owned_tables
        and not str(row[1]).startswith("sqlite_")
    }
    missing_table_contracts = {
        gap.removeprefix("table:")
        for gap in existing_gaps
        if gap.startswith("table:")
    }
    missing_column_contracts = {
        gap.removeprefix("column:").split(".", 1)[0]
        for gap in existing_gaps
        if gap.startswith("column:")
    }
    redundant_tables = missing_table_contracts | missing_column_contracts
    gaps: list[str] = []
    for table, expected_sha256 in sorted(expected_tables.items()):
        if table in redundant_tables:
            continue
        if _table_contract_sha256(connection, table) != expected_sha256:
            gaps.append(f"object:{table}")
    for key in sorted(set(expected) | set(actual_objects)):
        if expected.get(key) == actual_objects.get(key):
            continue
        _kind, name = key.split(":", 1)
        gaps.append(f"object:{name}")
    return gaps


def _pending_revisions(
    profile: str,
    current_revision: str | None,
) -> tuple[str, ...] | None:
    """返回从旧到新的确定执行顺序；None 表示当前版本不在目标祖先链。"""
    target_revision = _PROFILE_HEADS[profile]
    if current_revision == target_revision:
        return ()
    lower = current_revision or "base"
    try:
        revisions = _script_directory().iterate_revisions(target_revision, lower)
        return tuple(revision.revision for revision in reversed(tuple(revisions)))
    except Exception:
        # Alembic 对未知 revision 和非祖先 revision 抛不同异常；公开契约统一为 unknown。
        return None


def _revision_content_sha256(revision: str) -> str:
    script = _script_directory().get_revision(revision)
    if script is None:
        raise RuntimeError(f"迁移 revision 不存在：{revision}")
    digest = hashlib.sha256()
    python_path = Path(script.path)
    for path in (python_path, python_path.with_suffix(".sql")):
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    actual = digest.hexdigest()
    expected = _revision_manifest().get(revision)
    if expected is None:
        raise RuntimeError(f"迁移 revision 未进入冻结清单：{revision}")
    if actual != expected:
        raise RuntimeError(f"迁移 revision 内容与冻结 SHA-256 不一致：{revision}")
    return actual


def _revision_operations(revision: str) -> tuple[str, ...]:
    script = _script_directory().get_revision(revision)
    if script is None:
        raise RuntimeError(f"迁移 revision 不存在：{revision}")
    operations = getattr(script.module, "operation_summary", ())
    if not operations:
        raise RuntimeError(f"迁移 revision 缺少脱敏操作清单：{revision}")
    return tuple(str(item) for item in operations)


def _owned_tables(profile: str) -> frozenset[str]:
    """从冻结 SQL 与 Schema 契约推导本 Profile 拥有的表。"""
    tables = set(_PROFILE_REQUIRED_COLUMNS[profile])
    for revision in _script_directory().walk_revisions(
        base="base", head=_PROFILE_HEADS[profile]
    ):
        sql_path = Path(revision.path).with_suffix(".sql")
        if not sql_path.is_file():
            continue
        sql = sql_path.read_text(encoding="utf-8")
        tables.update(
            match.group(1)
            for match in re.finditer(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                r"[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)",
                sql,
                flags=re.IGNORECASE,
            )
        )
    return frozenset(tables)


def _logical_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    if isinstance(value, float):
        return {"float": repr(value)}
    return value


def _non_target_fingerprints(
    connection: sqlite3.Connection,
    profile: str,
) -> dict[str, str]:
    """为非本 revision 所有的表生成与物理页布局无关的逻辑指纹。"""
    owned = _owned_tables(profile) | {"alembic_version"}
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    result: dict[str, str] = {}
    for table_name, schema_sql in rows:
        table = str(table_name)
        if table in owned:
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        logical_rows = [
            [_logical_value(value) for value in row]
            for row in connection.execute(f"SELECT * FROM {quoted}").fetchall()
        ]
        logical_rows.sort(
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        payload = {
            "table": table,
            "schema": str(schema_sql or ""),
            "rows": logical_rows,
        }
        result[table] = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    return result


def _fingerprint_set_sha256(fingerprints: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            fingerprints,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _create_backup(database: Path, backup: Path) -> str:
    if backup.exists():
        raise FileExistsError("迁移备份已存在，拒绝覆盖恢复点")
    backup.parent.mkdir(parents=True, exist_ok=True)
    temporary = backup.with_name(f".{backup.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise FileExistsError("迁移临时备份已存在，拒绝覆盖")
    try:
        # sqlite3.Connection 的上下文只管事务，不会关闭句柄；Windows 原子改名前必须显式关闭。
        with closing(sqlite3.connect(database, timeout=30)) as source:
            with closing(sqlite3.connect(temporary, timeout=30)) as destination:
                source.backup(destination)
        with closing(sqlite3.connect(temporary, timeout=30)) as validation:
            if validation.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("迁移备份完整性检查失败")
        os.replace(temporary, backup)
    finally:
        temporary.unlink(missing_ok=True)
    return _file_sha256(backup)


def _receipt_path(backup: Path) -> Path:
    return backup.with_name(f"{backup.name}.receipt.json")


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    """原子写入不可覆盖的结构化迁移收据。"""
    if path.exists():
        raise FileExistsError("迁移收据已存在，拒绝覆盖历史证据")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise FileExistsError("迁移临时收据已存在，拒绝覆盖")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _receipt_payload(
    *,
    target: DatabaseTarget,
    source_revision: str | None,
    source_database_sha256: str | None,
    target_revision: str,
    applied_revisions: tuple[str, ...],
    backup: Path,
    backup_sha256: str | None,
    revision_content_sha256: dict[str, str],
    non_target_fingerprint_sha256: str,
    integrity_check: str,
    foreign_key_violations: int,
    outcome: Literal["succeeded", "failed"],
    error_type: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_version": 1,
        "outcome": outcome,
        "profile": target.profile,
        # 收据只存逻辑名，避免把维护者本机绝对路径带入可提交证据。
        "database_name": target.path.name,
        "backup_name": backup.name,
        "backup_created": backup_sha256 is not None,
        "source_revision": source_revision,
        "source_database_sha256": source_database_sha256,
        "target_revision": target_revision,
        "applied_revisions": list(applied_revisions),
        "revision_content_sha256": revision_content_sha256,
        "backup_sha256": backup_sha256,
        "non_target_fingerprint_sha256": non_target_fingerprint_sha256,
        "integrity_check": integrity_check,
        "foreign_key_violations": foreign_key_violations,
    }
    if error_type is not None:
        payload["error_type"] = error_type
    return payload


def _profile_evidence_gaps(
    profile: str,
    connection: sqlite3.Connection,
    table_names: set[str],
) -> list[str]:
    """只校验统一 revision 仍需保留的历史恢复点证据，不另造版本权威。"""
    if profile != "webui":
        return []
    gaps: list[str] = []
    if "candidate_verification_migrations" in table_names:
        rows = connection.execute(
            "SELECT migration_id, backup_sha256, ddl_sha256 "
            "FROM candidate_verification_migrations"
        ).fetchall()
        migrations = {str(row[0]): row for row in rows}
        required = {
            "0001_candidate_verification_attempts",
            "0002_delivery_publication_idempotency",
            "0003_historical_reverification_authorities",
            "0004_legacy_candidate_rebaseline",
        }
        if not required.issubset(migrations) or any(
            not isinstance(migrations[item][1], str)
            or len(migrations[item][1]) != 64
            for item in required & migrations.keys()
        ):
            gaps.append("evidence:candidate_verification_migrations")
        elif (
            migrations["0003_historical_reverification_authorities"][2]
            != "2eed49f8d9c13989cac8f6c18f9b7e1183101673cf96fa1c5ba9b431da24606a"
            or migrations["0004_legacy_candidate_rebaseline"][2]
            != "16c93187ba117d7db9fd68d5a6b8e14402b4ede0c8e15b7718508cbed8530e04"
        ):
            gaps.append("evidence:candidate_verification_migrations")
    if "runtime_routing_migrations" in table_names:
        row = connection.execute(
            "SELECT ddl_sha256, backup_sha256 FROM runtime_routing_migrations "
            "WHERE migration_id='0001_runtime_routing'"
        ).fetchone()
        if (
            row is None
            or row[0]
            != "9bfdf8e129fedf8915d7f2cf7e0827a7ea9b97f59a70337eb0843f49ddbdc2df"
            or not isinstance(row[1], str)
            or len(row[1]) != 64
        ):
            gaps.append("evidence:runtime_routing_migrations")
    if "capability_acquisition_migrations" in table_names:
        row = connection.execute(
            "SELECT backup_sha256 FROM capability_acquisition_migrations "
            "WHERE migration_id='0001_acquisition_runs'"
        ).fetchone()
        if row is None or not isinstance(row[0], str) or len(row[0]) != 64:
            gaps.append("evidence:capability_acquisition_migrations")
    return gaps


def _inspect_open_connection(
    target: DatabaseTarget,
    connection: sqlite3.Connection,
) -> DatabaseStatus:
    try:
        target_revision = _PROFILE_HEADS[target.profile]
    except KeyError as exc:
        raise ValueError(f"未知数据库 Profile：{target.profile}") from exc
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    version_table = "alembic_version" in table_names
    version_rows = (
        connection.execute(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        ).fetchall()
        if version_table
        else []
    )
    gaps: list[str] = []
    if table_names:
        for table, columns in _PROFILE_REQUIRED_COLUMNS[target.profile].items():
            if table not in table_names:
                gaps.append(f"table:{table}")
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            actual_columns = {
                str(item[1])
                for item in connection.execute(
                    f"PRAGMA table_info({quoted})"
                ).fetchall()
            }
            gaps.extend(
                f"column:{table}.{column}"
                for column in columns
                if column not in actual_columns
            )
    current_revision = (
        str(version_rows[0][0]) if len(version_rows) == 1 else None
    )
    has_ambiguous_versions = len(version_rows) > 1
    pending = _pending_revisions(target.profile, current_revision)
    is_current = not has_ambiguous_versions and current_revision == target_revision
    if is_current:
        gaps.extend(_profile_schema_gaps(target.profile, connection, gaps))
        gaps.extend(_profile_evidence_gaps(target.profile, connection, table_names))
    has_managed_table = bool(table_names & _owned_tables(target.profile))
    is_known_legacy = (
        pending is not None
        and (
            current_revision is not None
            or not has_managed_table
            or any(
                anchor in table_names
                for anchor in _PROFILE_LEGACY_ANCHORS[target.profile]
            )
        )
    )
    return DatabaseStatus(
        target=target,
        state=(
            "unknown"
            if has_ambiguous_versions
            else "uninitialized"
            if not table_names
            else
            "drift"
            if is_current and gaps
            else "current"
            if is_current
            else "legacy"
            if is_known_legacy
            else "unknown"
        ),
        current_revision=current_revision,
        target_revision=target_revision,
        pending_revisions=pending or (() if is_current else (target_revision,)),
        gaps=tuple(sorted(set(gaps))),
    )


def inspect_database(target: DatabaseTarget) -> DatabaseStatus:
    """只读检查数据库版本；不得创建文件或 Schema。"""

    try:
        target_revision = _PROFILE_HEADS[target.profile]
    except KeyError as exc:
        raise ValueError(f"未知数据库 Profile：{target.profile}") from exc
    if not target.path.is_file():
        pending = _pending_revisions(target.profile, None)
        if pending is None:
            raise RuntimeError(f"无法解析数据库 Profile：{target.profile}")
        return DatabaseStatus(
            target=target,
            state="uninitialized",
            current_revision=None,
            target_revision=target_revision,
            pending_revisions=pending,
        )
    database_uri = f"{target.path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=5)) as connection:
        return _inspect_open_connection(target, connection)


def plan_database(target: DatabaseTarget) -> DatabasePlan:
    """解析并冻结有序 revision 计划；只读且不创建数据库文件。"""

    status = inspect_database(target)
    revisions = tuple(
        PlannedRevision(
            revision=revision,
            content_sha256=_revision_content_sha256(revision),
            # 当前 revisions 包含 Schema/数据探测，必须在恢复点副本上实跑验证。
            requires_copy_validation=True,
            operations=_revision_operations(revision),
        )
        for revision in status.pending_revisions
    )
    return DatabasePlan(
        target=target,
        state=status.state,
        source_revision=status.current_revision,
        target_revision=status.target_revision,
        pending_revisions=status.pending_revisions,
        revisions=revisions,
    )


def apply_migrations(
    target: DatabaseTarget,
    backup_path: str | Path,
    expected_source_sha256: str | None = None,
) -> MigrationReceipt:
    """创建恢复点后，把一个数据库显式迁移到 Profile 当前版本。"""

    database = target.path.expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    receipt_path = _receipt_path(backup)
    if database == backup:
        raise ValueError("迁移备份不能覆盖源数据库")
    # 预检失败也必须留下收据；先建立证据目录，不能让 FileNotFoundError 覆盖原始错误。
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    normalized_target = DatabaseTarget(target.profile, database)
    source_lock_path = Path(f"{database}.migration.lock")
    artifact_lock_path = Path(f"{receipt_path}.migration.lock")
    # 恢复点和收据本身也是唯一目标；不同源库必须先争用同一个跨进程目标锁。
    with FileLock(artifact_lock_path, timeout=0), FileLock(
        source_lock_path,
        timeout=0,
    ):
        if backup.exists():
            raise FileExistsError("迁移备份已存在，拒绝覆盖恢复点")
        if receipt_path.exists():
            raise FileExistsError("迁移收据已存在，拒绝覆盖历史证据")
        existed = database.is_file()
        try:
            before = inspect_database(normalized_target)
            plan = plan_database(normalized_target)
        except sqlite3.DatabaseError as exc:
            _write_receipt(
                receipt_path,
                _receipt_payload(
                    target=normalized_target,
                    source_revision=None,
                    source_database_sha256=(
                        _file_sha256(database) if existed else None
                    ),
                    target_revision=_PROFILE_HEADS[target.profile],
                    applied_revisions=(),
                    backup=backup,
                    backup_sha256=None,
                    revision_content_sha256={},
                    non_target_fingerprint_sha256=_fingerprint_set_sha256({}),
                    integrity_check="not-run",
                    foreign_key_violations=0,
                    outcome="failed",
                    error_type=type(exc).__name__,
                ),
            )
            raise RuntimeError("迁移源数据库不可读取") from exc
        database.parent.mkdir(parents=True, exist_ok=True)
        if not existed:
            sqlite3.connect(database).close()
        engine = create_engine(URL.create("sqlite", database=str(database)))
        backup_sha256: str | None = None
        source_database_sha256: str | None = None
        non_target_fingerprint_sha256 = _fingerprint_set_sha256({})
        integrity_check = "not-run"
        foreign_key_violations = 0
        revision_hashes = {
            item.revision: item.content_sha256 for item in plan.revisions
        }
        try:
            with engine.connect() as connection:
                raw_connection = connection.connection.driver_connection
                raw_connection.execute("PRAGMA foreign_keys=ON")
                raw_connection.execute("PRAGMA busy_timeout=5000")
                # 必须经 SQLAlchemy Connection 开启事务，让 Alembic 识别并加入同一外部事务；
                # 直接调用底层 sqlite3 会让 Alembic 误判为无事务并提前提交，放跑并发写者。
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                locked_before = _inspect_open_connection(
                    normalized_target,
                    raw_connection,
                )
                if locked_before != before:
                    raise RuntimeError("取得写锁后数据库 revision 或 Schema 已变化")
                source_database_sha256 = _file_sha256(database)
                if existed and locked_before.state == "unknown":
                    raise SchemaNotCurrentError(
                        "现有数据库 Schema 未被识别，拒绝猜测迁移"
                    )
                if existed and locked_before.state == "drift":
                    raise SchemaNotCurrentError(
                        "当前 revision 的 Schema 漂移，必须新增编号迁移，拒绝原地修补"
                    )
                if (
                    expected_source_sha256 is not None
                    and source_database_sha256 != expected_source_sha256
                ):
                    raise ValueError("源数据库 SHA-256 与调用方预期不一致")
                quick_check = str(
                    raw_connection.execute("PRAGMA quick_check").fetchone()[0]
                )
                if quick_check != "ok":
                    raise RuntimeError("迁移前数据库快速完整性检查失败")
                integrity_check = str(
                    raw_connection.execute("PRAGMA integrity_check").fetchone()[0]
                )
                if integrity_check != "ok":
                    raise RuntimeError("迁移前数据库完整性检查失败")
                foreign_key_violations = len(
                    raw_connection.execute("PRAGMA foreign_key_check").fetchall()
                )
                if foreign_key_violations:
                    raise RuntimeError("迁移前数据库外键检查失败")
                non_target_before = _non_target_fingerprints(
                    raw_connection,
                    target.profile,
                )
                non_target_fingerprint_sha256 = _fingerprint_set_sha256(
                    non_target_before
                )
                backup_sha256 = _create_backup(database, backup)
                alembic_config = _alembic_config(connection)
                alembic_config.attributes["backup_sha256"] = backup_sha256
                command.upgrade(alembic_config, before.target_revision)
                in_transaction_status = _inspect_open_connection(
                    normalized_target,
                    raw_connection,
                )
                in_transaction_status.require_current()
                integrity_check = str(
                    raw_connection.execute("PRAGMA integrity_check").fetchone()[0]
                )
                if integrity_check != "ok":
                    raise RuntimeError("迁移后数据库完整性检查失败")
                foreign_key_violations = len(
                    raw_connection.execute("PRAGMA foreign_key_check").fetchall()
                )
                if foreign_key_violations:
                    raise RuntimeError("迁移后数据库外键检查失败")
                non_target_after = _non_target_fingerprints(
                    raw_connection,
                    target.profile,
                )
                if non_target_after != non_target_before:
                    raise RuntimeError("迁移意外改写了非目标表，事务已回滚")
                connection.commit()
            after = inspect_database(normalized_target)
            after.require_current()
        except Exception as exc:
            if not receipt_path.exists():
                applied_revisions: tuple[str, ...] = ()
                try:
                    failed_status = inspect_database(normalized_target)
                    failed_pending = _pending_revisions(
                        target.profile,
                        failed_status.current_revision,
                    )
                    if failed_pending is not None:
                        applied_revisions = tuple(
                            revision
                            for revision in before.pending_revisions
                            if revision not in failed_pending
                        )
                except Exception:
                    # 失败数据库本身不可读时，不能把计划执行冒充已成功 revision。
                    applied_revisions = ()
                _write_receipt(
                    receipt_path,
                    _receipt_payload(
                        target=normalized_target,
                        source_revision=before.current_revision,
                        source_database_sha256=source_database_sha256,
                        target_revision=before.target_revision,
                        applied_revisions=applied_revisions,
                        backup=backup,
                        backup_sha256=backup_sha256,
                        revision_content_sha256=revision_hashes,
                        non_target_fingerprint_sha256=(
                            non_target_fingerprint_sha256
                        ),
                        integrity_check=integrity_check,
                        foreign_key_violations=foreign_key_violations,
                        outcome="failed",
                        error_type=type(exc).__name__,
                    ),
                )
            raise
        finally:
            engine.dispose()
        assert backup_sha256 is not None
        assert source_database_sha256 is not None
        _write_receipt(
            receipt_path,
            _receipt_payload(
                target=normalized_target,
                source_revision=before.current_revision,
                source_database_sha256=source_database_sha256,
                target_revision=after.target_revision,
                applied_revisions=before.pending_revisions,
                backup=backup,
                backup_sha256=backup_sha256,
                revision_content_sha256=revision_hashes,
                non_target_fingerprint_sha256=non_target_fingerprint_sha256,
                integrity_check=integrity_check,
                foreign_key_violations=foreign_key_violations,
                outcome="succeeded",
            ),
        )
        return MigrationReceipt(
            target=normalized_target,
            source_revision=before.current_revision,
            source_database_sha256=source_database_sha256,
            target_revision=after.target_revision,
            applied_revisions=before.pending_revisions,
            backup_path=backup,
            backup_sha256=backup_sha256,
            receipt_path=receipt_path,
        )


def verify_restored_copy(
    receipt_path: str | Path,
    restored_path: str | Path,
) -> RestoreVerification:
    """只读验证恢复副本与迁移恢复点一致，不执行覆盖。"""
    migration_receipt_path = Path(receipt_path).expanduser().resolve()
    receipt = json.loads(migration_receipt_path.read_text(encoding="utf-8"))
    restored = Path(restored_path).expanduser().resolve()
    expected_sha256 = str(receipt["backup_sha256"])
    if _file_sha256(restored) != expected_sha256:
        raise ValueError("恢复副本 SHA-256 与迁移恢复点不一致")
    database_uri = f"{restored.as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=5)) as connection:
        integrity_check = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        if integrity_check != "ok":
            raise RuntimeError("恢复副本完整性检查失败")
        foreign_key_violations = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        if foreign_key_violations:
            raise RuntimeError("恢复副本外键检查失败")
    status = inspect_database(
        DatabaseTarget(profile=str(receipt["profile"]), path=restored)
    )
    if status.current_revision != receipt.get("source_revision"):
        raise RuntimeError("恢复副本 revision 与迁移前状态不一致")
    verification_receipt_path = migration_receipt_path.with_name(
        f"{migration_receipt_path.name}.restore-verification.json"
    )
    verification_payload: dict[str, object] = {
        "format_version": 1,
        "outcome": "succeeded",
        "profile": str(receipt["profile"]),
        "restored_name": restored.name,
        "backup_sha256": expected_sha256,
        "source_revision": receipt.get("source_revision"),
        "integrity_check": integrity_check,
        "foreign_key_violations": foreign_key_violations,
        "schema_state": status.state,
    }
    if verification_receipt_path.exists():
        existing = json.loads(
            verification_receipt_path.read_text(encoding="utf-8")
        )
        if existing != verification_payload:
            raise FileExistsError("恢复验证收据已存在且内容不一致，拒绝覆盖")
    else:
        _write_receipt(verification_receipt_path, verification_payload)
    return RestoreVerification(
        restored_path=restored,
        verification_receipt_path=verification_receipt_path,
        backup_sha256=expected_sha256,
        integrity_check=integrity_check,
        foreign_key_violations=foreign_key_violations,
        schema_state=status.state,
    )


def _apply_compatibility_adapter(
    database_path: str | Path,
    backup_path: str | Path,
) -> Path:
    """让历史公开入口委托统一 webui Seam，并保留同路径幂等返回。"""
    database = Path(database_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    receipt_path = _receipt_path(backup)

    def verified_existing_receipt() -> Path | None:
        if not receipt_path.is_file():
            return None
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            payload.get("outcome") != "succeeded"
            or payload.get("profile") != "webui"
            or payload.get("database_name") != database.name
            or payload.get("backup_name") != backup.name
            or not backup.is_file()
            or payload.get("backup_sha256") != _file_sha256(backup)
        ):
            raise RuntimeError("历史迁移 Adapter 的中央收据或恢复点无效")
        inspect_database(DatabaseTarget("webui", database)).require_current()
        return backup

    existing = verified_existing_receipt()
    if existing is not None:
        return existing
    try:
        return apply_migrations(
            DatabaseTarget("webui", database),
            backup,
        ).backup_path
    except Timeout:
        # 兼容旧入口的并发幂等语义：等待中央迁移完成后只核验，不重复执行。
        with FileLock(
            Path(f"{receipt_path}.migration.lock"),
            timeout=30,
        ):
            pass
        existing = verified_existing_receipt()
        if existing is None:
            raise RuntimeError("并发中央迁移结束但未产生成功收据")
        return existing


__all__ = [
    "DatabasePlan",
    "DatabaseStatus",
    "DatabaseTarget",
    "MigrationReceipt",
    "PlannedRevision",
    "RestoreVerification",
    "SchemaNotCurrentError",
    "apply_migrations",
    "inspect_database",
    "plan_database",
    "verify_restored_copy",
]
