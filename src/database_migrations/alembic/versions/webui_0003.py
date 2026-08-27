"""把四个历史组件迁移收敛进 webui 的统一版本链。"""

from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from alembic import op


revision: str = "webui_0003"
down_revision: str | None = "webui_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "安装 CandidateVerification 冻结 Schema 并保留历史迁移证据",
    "安装 RuntimeRouting 冻结 Schema 并保留首次恢复点证据",
    "安装 CapabilityAcquisition 与 CapabilityGovernance 冻结 Schema",
)


_SRC = Path(__file__).parents[3]
_CANDIDATE_DIR = _SRC / "candidate_verification" / "migrations"
_ACQUISITION_SQL = (
    _SRC / "capability_acquisition" / "migrations" / "0001_acquisition_runs.sql"
)
_GOVERNANCE_DIR = _SRC / "capability_governance" / "migrations"
_CANDIDATE_FILES = {
    "0001_candidate_verification_attempts": (
        _CANDIDATE_DIR / "0001_candidate_verification_attempts.sql",
        "72a6ec05bd581a4b9f97d5b56923af34a03c4d49eb89bc4bbb0b1021989ed6f3",
    ),
    "0003_historical_reverification_authorities": (
        _CANDIDATE_DIR / "0003_historical_reverification_authorities.sql",
        "2eed49f8d9c13989cac8f6c18f9b7e1183101673cf96fa1c5ba9b431da24606a",
    ),
    "0004_legacy_candidate_rebaseline": (
        _CANDIDATE_DIR / "0004_legacy_candidate_rebaseline.sql",
        "16c93187ba117d7db9fd68d5a6b8e14402b4ede0c8e15b7718508cbed8530e04",
    ),
}
_ACQUISITION_SHA256 = (
    "065b59c3b562f6545d76a3f1843e1115356c89764739c9ce00d010e04ba7378b"
)
_GOVERNANCE_FILES = (
    ("0001_capability_governance.sql", "01f658dced755ef817e208cf80ede38feaa4e912038021f9954ebf4bf76b5f79"),
    ("0002_validation_runs.sql", "c078c47df05b00574095fe5d957acae2e99f20fcbc1c3d4d4466da859688575d"),
    ("0003_supply_chain_evidence.sql", "c7c2e9e8ef4c28674829640d1906748590584f0e4827221ed4bcf0ec18e38853"),
    ("0004_promotion_gate.sql", "9deb277bed5bf3eab10f4e64b13bdf1c40f8f7e205c5e87d8640230ab5b64a52"),
    ("0005_platform_publication.sql", "57498194143d81378cf5d5d3e1211417b8a247313694818add147b2a70d3eaaf"),
)


_RUNTIME_DDL = (
    """CREATE TABLE runtime_routing_migrations (
        migration_id TEXT PRIMARY KEY,
        ddl_sha256 TEXT NOT NULL,
        backup_sha256 TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_gate_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        recorded_by TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_rollout_state (
        state_id INTEGER PRIMARY KEY CHECK (state_id = 1),
        mode TEXT NOT NULL,
        p0_blocked INTEGER NOT NULL CHECK (p0_blocked IN (0, 1)),
        active_gate_snapshot_id TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_rollout_approvals (
        approval_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_rollout_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_assignments (
        owner_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        payload_json TEXT NOT NULL,
        runtime_version TEXT NOT NULL,
        rollout_mode TEXT NOT NULL,
        gate_snapshot_id TEXT NOT NULL,
        assigned_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, task_id, revision)
    )""",
    """CREATE TRIGGER runtime_gate_snapshots_no_update
        BEFORE UPDATE ON runtime_gate_snapshots BEGIN
        SELECT RAISE(ABORT, 'GateSnapshot 不可改写'); END""",
    """CREATE TRIGGER runtime_gate_snapshots_no_delete
        BEFORE DELETE ON runtime_gate_snapshots BEGIN
        SELECT RAISE(ABORT, 'GateSnapshot 不可删除'); END""",
    """CREATE TRIGGER runtime_assignments_no_update
        BEFORE UPDATE ON runtime_assignments BEGIN
        SELECT RAISE(ABORT, 'RuntimeAssignment 不可改写'); END""",
    """CREATE TRIGGER runtime_assignments_no_delete
        BEFORE DELETE ON runtime_assignments BEGIN
        SELECT RAISE(ABORT, 'RuntimeAssignment 不可删除'); END""",
    """CREATE TRIGGER runtime_rollout_events_no_update
        BEFORE UPDATE ON runtime_rollout_events BEGIN
        SELECT RAISE(ABORT, 'RolloutEvent 不可改写'); END""",
    """CREATE TRIGGER runtime_rollout_events_no_delete
        BEFORE DELETE ON runtime_rollout_events BEGIN
        SELECT RAISE(ABORT, 'RolloutEvent 不可删除'); END""",
    """CREATE TRIGGER runtime_rollout_approvals_no_update
        BEFORE UPDATE ON runtime_rollout_approvals BEGIN
        SELECT RAISE(ABORT, 'RolloutApproval 不可改写'); END""",
    """CREATE TRIGGER runtime_rollout_approvals_no_delete
        BEFORE DELETE ON runtime_rollout_approvals BEGIN
        SELECT RAISE(ABORT, 'RolloutApproval 不可删除'); END""",
    """CREATE TRIGGER runtime_routing_migrations_no_update
        BEFORE UPDATE ON runtime_routing_migrations BEGIN
        SELECT RAISE(ABORT, 'RuntimeRoutingMigration 不可改写'); END""",
    """CREATE TRIGGER runtime_routing_migrations_no_delete
        BEFORE DELETE ON runtime_routing_migrations BEGIN
        SELECT RAISE(ABORT, 'RuntimeRoutingMigration 不可删除'); END""",
)
_RUNTIME_NAMES = (
    "runtime_routing_migrations",
    "runtime_gate_snapshots",
    "runtime_rollout_state",
    "runtime_rollout_approvals",
    "runtime_rollout_events",
    "runtime_assignments",
    "runtime_gate_snapshots_no_update",
    "runtime_gate_snapshots_no_delete",
    "runtime_assignments_no_update",
    "runtime_assignments_no_delete",
    "runtime_rollout_events_no_update",
    "runtime_rollout_events_no_delete",
    "runtime_rollout_approvals_no_update",
    "runtime_rollout_approvals_no_delete",
    "runtime_routing_migrations_no_update",
    "runtime_routing_migrations_no_delete",
)
_RUNTIME_DDL_SHA256 = hashlib.sha256(
    "\n".join(_RUNTIME_DDL).encode("utf-8")
).hexdigest()
# 与 RuntimeRouting.RolloutMode 和 ADR-0030 同步冻结。历史 revision 不导入
# 产品模型，避免只读迁移命令加载 Agentic Runtime 等无关运行时依赖。
_RUNTIME_ROLLOUT_MODES = frozenset(
    {
        "legacy_default",
        "admin_gray",
        "explicit_opt_in",
        "vnext_default",
        "legacy_rollback",
    }
)


def _normalized_sql(value: str | None) -> str:
    return " ".join((value or "").split())


def _read_frozen_sql(path: Path, expected_sha256: str) -> str:
    script = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"冻结迁移 SQL 摘要不匹配：{path.name}")
    return script


def _execute_script(connection: sqlite3.Connection, script: str, label: str) -> None:
    buffer: list[str] = []
    for line in script.splitlines(keepends=True):
        buffer.append(line)
        statement = "".join(buffer).strip()
        if statement and sqlite3.complete_statement(statement):
            connection.execute(statement)
            buffer.clear()
    if "".join(buffer).strip():
        raise RuntimeError(f"{label} 包含不完整 SQL")


def _object_signature(
    connection: sqlite3.Connection,
    names: set[str],
) -> dict[str, tuple[str, str, str]]:
    placeholders = ", ".join("?" for _ in names)
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        f"WHERE name IN ({placeholders})",
        tuple(sorted(names)),
    ).fetchall()
    return {
        str(row[1]): (str(row[0]), str(row[2]), _normalized_sql(row[3]))
        for row in rows
    }


def _backup_sha256() -> str:
    value = op.get_context().config.attributes.get("backup_sha256")
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise RuntimeError("webui_0003 必须绑定中央恢复点 SHA-256")
    return value


def _legacy_candidate_set_hash(row: sqlite3.Row) -> str:
    frozen = row["verified_candidate_set_hash"]
    if isinstance(frozen, str) and len(frozen) == 64 and all(
        character in "0123456789abcdef" for character in frozen
    ):
        return frozen
    try:
        candidates = json.loads(row["candidates_json"] or "[]")
        payload = [
            {
                "artifact_id": item["artifact_id"],
                "filename": item["filename"],
                "format": item["format"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in sorted(candidates, key=lambda value: value["artifact_id"])
        ]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy CandidateSet 身份不可重建") from exc
    if not payload:
        raise RuntimeError("legacy VerificationReport 缺少 CandidateSet")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _import_legacy_candidate_attempts(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(agentic_runtime_runs)")
    }
    required = {
        "user_id", "task_id", "revision", "run_id", "candidates_json",
        "verification_json", "verified_candidate_set_hash", "model_connection_id",
        "model_connection_version", "model_connection_model", "created_at", "updated_at",
    }
    if not required.issubset(columns):
        raise RuntimeError("legacy Runtime Schema 缺少候选验证迁移字段")
    previous_row_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM agentic_runtime_runs "
            "WHERE verification_json IS NOT NULL "
            "AND TRIM(verification_json) <> '' "
            "ORDER BY user_id, task_id, revision"
        ).fetchall()
        for row in rows:
            report_json = row["verification_json"]
            try:
                status = json.loads(report_json)["status"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("legacy VerificationReport 无效") from exc
            if status not in {"passed", "failed", "inconclusive"}:
                raise RuntimeError("legacy VerificationReport 状态无效")
            if not row["run_id"]:
                raise RuntimeError("legacy VerificationReport 缺少 Run 身份")
            candidate_set_hash = _legacy_candidate_set_hash(row)
            report_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
            identity_payload = json.dumps(
                {
                    "owner_id": row["user_id"],
                    "task_id": row["task_id"],
                    "revision": row["revision"],
                    "run_id": row["run_id"],
                    "candidate_set_hash": candidate_set_hash,
                    "report_hash": report_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            identity_hash = hashlib.sha256(identity_payload).hexdigest()
            connection.execute(
                "INSERT INTO candidate_verification_attempts ("
                "attempt_id, owner_id, task_id, revision, run_id, previous_attempt_id, "
                "reason_code, candidate_set_hash, manifest_hash, goal_contract_hash, "
                "delivery_spec_hash, ruleset_identity_status, verifier_ruleset_hash, "
                "verifier_code_commit, verifier_source_hash, "
                "verifier_execution_identity_hash, verifier_ruleset_manifest_json, "
                "actor_id, connection_id, connection_version, model_id, "
                "egress_confirmed_at, provider_attempt_id, idempotency_key, request_hash, "
                "status, report_json, report_hash, created_at, started_at, finished_at"
                ") VALUES (" + ", ".join("?" for _ in range(31)) + ")",
                (
                    "legacy_" + identity_hash,
                    row["user_id"], row["task_id"], row["revision"], row["run_id"],
                    None, "initial", candidate_set_hash, None, None, None,
                    "legacy_unversioned", None, None, None, None, None,
                    "system:legacy-migration", row["model_connection_id"],
                    row["model_connection_version"], row["model_connection_model"],
                    None, None, "legacy_import_" + identity_hash, identity_hash, status,
                    report_json, report_hash, row["created_at"], None, row["updated_at"],
                ),
            )
    finally:
        connection.row_factory = previous_row_factory


def _candidate_expected_signature(scripts: dict[str, str]) -> dict[str, tuple[str, str, str]]:
    with closing(sqlite3.connect(":memory:")) as expected:
        _execute_script(expected, scripts["0001_candidate_verification_attempts"], "CV-0001")
        _execute_script(expected, scripts["0003_historical_reverification_authorities"], "CV-0003")
        _execute_script(expected, scripts["0004_legacy_candidate_rebaseline"], "CV-0004")
        names = {
            str(row[0])
            for row in expected.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'candidate_%' "
                "OR name LIKE 'idx_candidate_%' OR name LIKE 'uq_candidate_%'"
            )
        }
        return _object_signature(expected, names)


def _upgrade_candidate(connection: sqlite3.Connection, backup_sha256: str) -> None:
    scripts = {
        migration_id: _read_frozen_sql(path, expected_hash)
        for migration_id, (path, expected_hash) in _CANDIDATE_FILES.items()
    }
    evidence_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='candidate_verification_migrations'"
    ).fetchone()
    attempts_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='candidate_verification_attempts'"
    ).fetchone()
    candidate_object_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name LIKE 'candidate_verification_%' "
        "OR name LIKE 'candidate_reverification_%' LIMIT 1"
    ).fetchone()
    if evidence_table is None and attempts_table is None and candidate_object_exists is None:
        _execute_script(connection, scripts["0001_candidate_verification_attempts"], "CV-0001")
        _import_legacy_candidate_attempts(connection)
        connection.execute(
            "INSERT INTO candidate_verification_migrations "
            "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
            ("0001_candidate_verification_attempts", backup_sha256, _now()),
        )
    elif evidence_table is None or attempts_table is None:
        raise RuntimeError("CandidateVerification 检测到未知部分 Schema")

    migration_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT migration_id FROM candidate_verification_migrations"
        )
    }
    if "0001_candidate_verification_attempts" not in migration_ids:
        raise RuntimeError("CandidateVerification 缺少基础历史迁移证据")
    base_sha = connection.execute(
        "SELECT backup_sha256 FROM candidate_verification_migrations "
        "WHERE migration_id='0001_candidate_verification_attempts'"
    ).fetchone()[0]
    if not isinstance(base_sha, str) or len(base_sha) != 64:
        raise RuntimeError("CandidateVerification 基础恢复点证据无效")

    if "0002_delivery_publication_idempotency" not in migration_ids:
        connection.execute(
            "INSERT INTO candidate_verification_migrations "
            "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
            ("0002_delivery_publication_idempotency", backup_sha256, _now()),
        )
    if "0003_historical_reverification_authorities" not in migration_ids:
        _execute_script(connection, scripts["0003_historical_reverification_authorities"], "CV-0003")
        connection.execute(
            "INSERT INTO candidate_verification_migrations "
            "(migration_id, backup_sha256, applied_at, ddl_sha256) VALUES (?, ?, ?, ?)",
            (
                "0003_historical_reverification_authorities",
                backup_sha256,
                _now(),
                _CANDIDATE_FILES["0003_historical_reverification_authorities"][1],
            ),
        )
    if "0004_legacy_candidate_rebaseline" not in migration_ids:
        _execute_script(connection, scripts["0004_legacy_candidate_rebaseline"], "CV-0004")
        connection.execute(
            "INSERT INTO candidate_verification_migrations "
            "(migration_id, backup_sha256, applied_at, ddl_sha256) VALUES (?, ?, ?, ?)",
            (
                "0004_legacy_candidate_rebaseline",
                backup_sha256,
                _now(),
                _CANDIDATE_FILES["0004_legacy_candidate_rebaseline"][1],
            ),
        )

    hashes = dict(
        connection.execute(
            "SELECT migration_id, ddl_sha256 FROM candidate_verification_migrations "
            "WHERE migration_id IN (?, ?)",
            (
                "0003_historical_reverification_authorities",
                "0004_legacy_candidate_rebaseline",
            ),
        ).fetchall()
    )
    for migration_id in hashes:
        if hashes[migration_id] != _CANDIDATE_FILES[migration_id][1]:
            raise RuntimeError(f"CandidateVerification 历史 SQL 摘要漂移：{migration_id}")
    expected = _candidate_expected_signature(scripts)
    if _object_signature(connection, set(expected)) != expected:
        raise RuntimeError("CandidateVerification 最终 Schema 与冻结版本不一致")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upgrade_runtime_routing(connection: sqlite3.Connection, backup_sha256: str) -> None:
    expected = {
        name: _normalized_sql(statement)
        for name, statement in zip(_RUNTIME_NAMES, _RUNTIME_DDL, strict=True)
    }
    existing = {
        str(row[0]): _normalized_sql(row[1])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ("
            + ", ".join("?" for _ in _RUNTIME_NAMES)
            + ")",
            _RUNTIME_NAMES,
        )
    }
    if not existing:
        for statement in _RUNTIME_DDL:
            connection.execute(statement)
        now = _now()
        connection.execute(
            "INSERT INTO runtime_rollout_state VALUES (1, ?, 0, ?, ?, ?)",
            ("admin_gray", "0" * 64, "migration", now),
        )
        connection.execute(
            "INSERT INTO runtime_routing_migrations VALUES (?, ?, ?, ?)",
            ("0001_runtime_routing", _RUNTIME_DDL_SHA256, backup_sha256, now),
        )
    elif existing != expected:
        raise RuntimeError("RuntimeRouting 检测到未知部分或漂移 Schema")
    evidence = connection.execute(
        "SELECT ddl_sha256, backup_sha256 FROM runtime_routing_migrations "
        "WHERE migration_id='0001_runtime_routing'"
    ).fetchone()
    state = connection.execute(
        "SELECT mode, p0_blocked, active_gate_snapshot_id "
        "FROM runtime_rollout_state WHERE state_id=1"
    ).fetchone()
    if (
        evidence is None
        or evidence[0] != _RUNTIME_DDL_SHA256
        or not isinstance(evidence[1], str)
        or len(evidence[1]) != 64
        or state is None
        # RuntimeRouting Repository 会按同一领域枚举重开该行；迁移不能把
        # ADR-0030 已授权的 vnext_default 生产终态误判成历史脏数据。
        or state[0] not in _RUNTIME_ROLLOUT_MODES
        or state[1] not in {0, 1}
        or len(state[2]) != 64
    ):
        raise RuntimeError("RuntimeRouting 历史迁移证据无效")


def _upgrade_acquisition(connection: sqlite3.Connection, backup_sha256: str) -> None:
    script = _read_frozen_sql(_ACQUISITION_SQL, _ACQUISITION_SHA256)
    names = {
        "capability_acquisition_runs",
        "idx_capability_acquisition_owner_status",
        "capability_acquisition_migrations",
    }
    existing = _object_signature(connection, names)
    if not existing:
        _execute_script(connection, script, "CapabilityAcquisition-0001")
        connection.execute(
            "INSERT INTO capability_acquisition_migrations "
            "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
            ("0001_acquisition_runs", backup_sha256, _now()),
        )
    else:
        with closing(sqlite3.connect(":memory:")) as expected_connection:
            _execute_script(expected_connection, script, "CapabilityAcquisition-0001")
            expected = _object_signature(expected_connection, names)
        if existing != expected:
            raise RuntimeError("CapabilityAcquisition 检测到未知部分或漂移 Schema")
    evidence = connection.execute(
        "SELECT backup_sha256 FROM capability_acquisition_migrations "
        "WHERE migration_id='0001_acquisition_runs'"
    ).fetchone()
    if evidence is None or not isinstance(evidence[0], str) or len(evidence[0]) != 64:
        raise RuntimeError("CapabilityAcquisition 历史迁移证据无效")


def _governance_scripts() -> dict[str, str]:
    return {
        name: _read_frozen_sql(_GOVERNANCE_DIR / name, expected_hash)
        for name, expected_hash in _GOVERNANCE_FILES
    }


def _install_governance_schema(
    connection: sqlite3.Connection,
    scripts: dict[str, str],
) -> None:
    for name in (
        "0001_capability_governance.sql",
        "0002_validation_runs.sql",
        "0003_supply_chain_evidence.sql",
        "0005_platform_publication.sql",
    ):
        _execute_script(connection, scripts[name], name)
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(capability_governance_events)")
    }
    if "event_type" not in columns:
        connection.execute(
            "ALTER TABLE capability_governance_events "
            "ADD COLUMN event_type TEXT NOT NULL DEFAULT 'registered'"
        )
    _execute_script(
        connection,
        scripts["0004_promotion_gate.sql"],
        "0004_promotion_gate.sql",
    )


def _upgrade_governance(connection: sqlite3.Connection) -> None:
    scripts = _governance_scripts()
    _install_governance_schema(connection, scripts)
    with closing(sqlite3.connect(":memory:")) as expected_connection:
        _install_governance_schema(expected_connection, scripts)
        names = {
            str(row[0])
            for row in expected_connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name LIKE 'capability_governance_%' "
                "OR name LIKE 'capability_validation_%' "
                "OR name LIKE 'capability_supply_chain_%' "
                "OR name LIKE 'capability_platform_%' "
                "OR name LIKE 'idx_capability_%'"
            )
        }
        expected = _object_signature(expected_connection, names)
    if _object_signature(connection, names) != expected:
        raise RuntimeError("CapabilityGovernance 最终 Schema 与冻结版本不一致")


def upgrade() -> None:
    """只使用中央恢复点证据，在当前 Alembic 事务中安装四个组件。"""

    backup_sha256 = _backup_sha256()
    connection = op.get_bind().connection.driver_connection
    _upgrade_candidate(connection, backup_sha256)
    _upgrade_runtime_routing(connection, backup_sha256)
    _upgrade_acquisition(connection, backup_sha256)
    _upgrade_governance(connection)


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持隐式降级；请验证并显式恢复备份")
