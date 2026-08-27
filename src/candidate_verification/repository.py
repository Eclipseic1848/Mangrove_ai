# -*- coding: utf-8 -*-
"""CandidateVerification 的 SQLite Adapter。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from .models import (
    AttemptReason,
    AttemptStatus,
    HistoricalReverificationAuthority,
    HistoricalReverificationEvidence,
    HistoricalReverificationPurpose,
    RebaselineAuthorizationEvidence,
    RulesetIdentityStatus,
    VerificationAttempt,
)
from .runtime_request import (
    frozen_request_contract_hashes,
    parse_frozen_runtime_request,
)


def _canonical_payload_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rebaseline_authorization_matches_attempt(
    attempt: VerificationAttempt,
    authorization: RebaselineAuthorizationEvidence,
) -> bool:
    """逐字段证明 Owner 授权只绑定当前再基线 Attempt。"""

    return (
        authorization.owner_id,
        authorization.task_id,
        authorization.revision,
        authorization.run_id,
        authorization.previous_attempt_id,
        authorization.candidate_set_hash,
        authorization.target_ruleset_hash,
        authorization.actor_id,
        authorization.external_api_confirmed,
        authorization.authorized_at,
    ) == (
        attempt.owner_id,
        attempt.task_id,
        attempt.revision,
        attempt.run_id,
        attempt.previous_attempt_id,
        attempt.candidate_set_hash,
        attempt.verifier_ruleset_hash,
        attempt.actor_id,
        attempt.egress_confirmed_at is not None,
        attempt.created_at,
    )


def _historical_database_evidence_matches(
    connection: sqlite3.Connection,
    evidence: HistoricalReverificationEvidence,
    *,
    allowed_current_attempt_id: str | None = None,
) -> bool:
    """在同一 SQLite 写锁内重建全部数据库身份并比对证据。"""

    try:
        revision = connection.execute(
            "SELECT r.run_id, r.objective_text, r.output_formats_json, "
            "r.table_output_contracts_json, t.upload_ids_json, "
            "t.active_revision, t.cancel_requested "
            "FROM semantic_workspace_revisions AS r "
            "JOIN semantic_workspace_tasks AS t ON t.task_id=r.task_id "
            "AND t.user_id=r.user_id "
            "WHERE r.user_id=? AND r.task_id=? AND r.revision=?",
            (evidence.owner_id, evidence.task_id, evidence.revision),
        ).fetchone()
        runtime = connection.execute(
            "SELECT run_id, runtime_version, status, request_json, created_at, "
            "external_api_confirmed, workspace_root, "
            "verified_candidate_set_hash FROM agentic_runtime_runs "
            "WHERE user_id=? AND task_id=? AND revision=?",
            (evidence.owner_id, evidence.task_id, evidence.revision),
        ).fetchone()
        migration = connection.execute(
            "SELECT backup_sha256, applied_at FROM runtime_routing_migrations "
            "WHERE migration_id=?",
            (evidence.runtime_routing_migration_id,),
        ).fetchone()
        assignment = connection.execute(
            "SELECT 1 FROM runtime_assignments WHERE owner_id=? "
            "AND task_id=? AND revision=?",
            (evidence.owner_id, evidence.task_id, evidence.revision),
        ).fetchone()
        events = connection.execute(
            "SELECT sequence, event_id, event_type, created_at "
            "FROM agentic_runtime_events WHERE user_id=? AND task_id=? "
            "AND revision=? ORDER BY sequence",
            (evidence.owner_id, evidence.task_id, evidence.revision),
        ).fetchall()
        previous = connection.execute(
            "SELECT * FROM candidate_verification_attempts "
            "WHERE owner_id=? AND attempt_id=?",
            (evidence.owner_id, evidence.previous_attempt_id),
        ).fetchone()
        latest = connection.execute(
            "SELECT attempt_id FROM candidate_verification_attempts "
            "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
            "AND candidate_set_hash=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (
                evidence.owner_id,
                evidence.task_id,
                evidence.revision,
                evidence.run_id,
                evidence.candidate_set_hash,
            ),
        ).fetchone()
        allowed_current = (
            connection.execute(
                "SELECT attempt_id, previous_attempt_id, status "
                "FROM candidate_verification_attempts "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                "AND candidate_set_hash=? AND attempt_id=?",
                (
                    evidence.owner_id,
                    evidence.task_id,
                    evidence.revision,
                    evidence.run_id,
                    evidence.candidate_set_hash,
                    allowed_current_attempt_id,
                ),
            ).fetchone()
            if allowed_current_attempt_id is not None
            else None
        )
        active_or_unknown = connection.execute(
            "SELECT 1 FROM candidate_verification_attempts "
            "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
            "AND candidate_set_hash=? AND attempt_id NOT IN (?, ?) "
            "AND status IN ('requested', 'running', 'outcome_unknown') LIMIT 1",
            (
                evidence.owner_id,
                evidence.task_id,
                evidence.revision,
                evidence.run_id,
                evidence.candidate_set_hash,
                evidence.previous_attempt_id,
                allowed_current_attempt_id or evidence.previous_attempt_id,
            ),
        ).fetchone()
        rollout = connection.execute(
            "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
        ).fetchone()
        delivery = connection.execute(
            "SELECT 1 FROM formal_delivery_runs WHERE owner_id=? AND run_id=? "
            "AND status='succeeded' LIMIT 1",
            (evidence.owner_id, evidence.run_id),
        ).fetchone()
        if delivery is None:
            delivery = connection.execute(
                "SELECT 1 FROM semantic_delivery_runs "
                "WHERE user_id=? AND run_id=? LIMIT 1",
                (evidence.owner_id, evidence.run_id),
            ).fetchone()
    except sqlite3.DatabaseError:
        return False
    if (
        revision is None
        or runtime is None
        or migration is None
        or previous is None
        or latest is None
        or latest["attempt_id"]
        != (allowed_current_attempt_id or evidence.previous_attempt_id)
        or (
            allowed_current_attempt_id is not None
            and (
                allowed_current is None
                or allowed_current["previous_attempt_id"]
                != evidence.previous_attempt_id
                or allowed_current["status"] != AttemptStatus.REQUESTED.value
            )
        )
        or active_or_unknown is not None
        or rollout is None
        or bool(rollout["p0_blocked"])
        or assignment is not None
        or delivery is not None
        or bool(revision["cancel_requested"])
        or revision["active_revision"] != evidence.revision
        or revision["run_id"] != evidence.run_id
        or runtime["run_id"] != evidence.run_id
        or runtime["runtime_version"] != "pi"
        or runtime["status"] != "candidate_ready"
    ):
        return False
    try:
        frozen_request, _used_legacy_confirmation = (
            parse_frozen_runtime_request(
                request_json=runtime["request_json"],
                external_api_confirmed=runtime["external_api_confirmed"],
            )
        )
        request_payload = frozen_request.model_dump(
            mode="json",
            exclude={"api_key"},
        )
        output_formats = tuple(json.loads(revision["output_formats_json"]))
        table_contracts = tuple(
            json.loads(revision["table_output_contracts_json"])
        )
        upload_ids = tuple(json.loads(revision["upload_ids_json"]))
        sources = request_payload["sources"]
        if not isinstance(sources, list):
            return False
        source_binding = [
            {
                "upload_id": source["upload_id"],
                "original_name": source["original_name"],
                "sha256": source["sha256"],
                "media_type": source["media_type"],
            }
            for source in sources
        ]
        runtime_created_at = datetime.fromisoformat(runtime["created_at"])
        migration_applied_at = datetime.fromisoformat(migration["applied_at"])
        manifest_bytes = (
            Path(str(runtime["workspace_root"]))
            / "output"
            / "candidate-manifest.json"
        ).read_bytes()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        goal_contract_hash, delivery_spec_hash = (
            frozen_request_contract_hashes(frozen_request)
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if (
        runtime_created_at.tzinfo is None
        or migration_applied_at.tzinfo is None
        or runtime_created_at >= migration_applied_at
        or request_payload.get("user_id") != evidence.owner_id
        or request_payload.get("task_id") != evidence.task_id
        or request_payload.get("revision") != evidence.revision
        or request_payload.get("objective_text") != revision["objective_text"]
        or tuple(request_payload.get("requested_output_formats", ()))
        != output_formats
        or tuple(request_payload.get("table_output_contracts", ()))
        != table_contracts
        or tuple(source["upload_id"] for source in source_binding) != upload_ids
        or request_payload.get("model_connection_id") != evidence.connection_id
        or request_payload.get("model_connection_version")
        != evidence.connection_version
        or request_payload.get("model_connection_model") != evidence.model_id
        or runtime["verified_candidate_set_hash"] != evidence.candidate_set_hash
        or previous["task_id"] != evidence.task_id
        or previous["revision"] != evidence.revision
        or previous["run_id"] != evidence.run_id
        or previous["status"] != AttemptStatus.INCONCLUSIVE.value
        or previous["candidate_set_hash"] != evidence.candidate_set_hash
        or (
            previous["manifest_hash"] is not None
            and previous["manifest_hash"] != evidence.candidate_manifest_hash
        )
        or (
            previous["goal_contract_hash"] is not None
            and previous["goal_contract_hash"] != evidence.goal_contract_hash
        )
        or (
            previous["delivery_spec_hash"] is not None
            and previous["delivery_spec_hash"] != evidence.delivery_spec_hash
        )
        or previous["report_hash"] != evidence.previous_report_hash
        or not isinstance(previous["report_json"], str)
        or hashlib.sha256(previous["report_json"].encode("utf-8")).hexdigest()
        != evidence.previous_report_hash
    ):
        return False
    required_events = {
        "runtime.preparing",
        "agent.started",
        "verification.completed",
        "candidate.ready",
    }
    if not required_events.issubset({row["event_type"] for row in events}):
        return False
    try:
        model_connection = connection.execute(
            "SELECT connection_id, model, secret_id, owner_scope, status, "
            "owner_user_id FROM model_connections WHERE connection_id=?",
            (evidence.connection_id,),
        ).fetchone()
        model_available = connection.execute(
            "SELECT 1 FROM model_connection_models WHERE connection_id=? "
            "AND model_id=? AND status='available' AND enabled=1",
            (evidence.connection_id, evidence.model_id),
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    if (
        model_connection is None
        or model_available is None
        or model_connection["status"] != "verified"
        or (
            model_connection["owner_scope"] == "user_personal"
            and model_connection["owner_user_id"] != evidence.owner_id
        )
    ):
        return False
    secret_version = str(
        model_connection["secret_id"] or model_connection["connection_id"]
    )
    connection_version = hashlib.sha256(
        f"{secret_version}\0{model_connection['model']}".encode("utf-8")
    ).hexdigest()
    if connection_version != evidence.connection_version:
        return False
    rebuilt = evidence.model_copy(
        update={
            "legacy_runtime_created_at": runtime_created_at,
            "runtime_routing_applied_at": migration_applied_at,
            "runtime_routing_backup_sha256": migration["backup_sha256"],
            "runtime_request_hash": _canonical_payload_hash(request_payload),
            "task_revision_hash": _canonical_payload_hash(
                {
                    "owner_id": evidence.owner_id,
                    "task_id": evidence.task_id,
                    "revision": evidence.revision,
                    "run_id": evidence.run_id,
                    "objective_text": revision["objective_text"],
                    "output_formats": output_formats,
                    "table_output_contracts": table_contracts,
                }
            ),
            "source_binding_hash": _canonical_payload_hash(source_binding),
            "candidate_manifest_hash": manifest_hash,
            "goal_contract_hash": goal_contract_hash,
            "delivery_spec_hash": delivery_spec_hash,
            "runtime_event_chain_hash": _canonical_payload_hash(
                [
                    {
                        "sequence": row["sequence"],
                        "event_id": row["event_id"],
                        "event_type": row["event_type"],
                        "created_at": row["created_at"],
                    }
                    for row in events
                ]
            ),
        }
    )
    return rebuilt == evidence


def migrate_candidate_verification(
    db_path: str | Path,
    backup_path: str | Path,
) -> Path:
    """兼容旧调用方；所有写入统一委托中央 webui 迁移 Seam。"""

    from src.database_migrations import _apply_compatibility_adapter

    return _apply_compatibility_adapter(db_path, backup_path)


class SqliteCandidateVerificationRepository:
    """只接受已经执行显式迁移的数据库。"""

    def __init__(self, db_path: str | Path) -> None:
        database = Path(db_path).expanduser().resolve()
        from src.database_migrations import DatabaseTarget, inspect_database

        inspect_database(DatabaseTarget("webui", database)).require_current()
        self._db_path = str(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        # INSERT Trigger 依赖连接内函数，原始 SQL 连接无法伪造 Owner 写命令。
        connection.create_function(
            "historical_authority_write_allowed",
            0,
            lambda: 1,
            deterministic=True,
        )
        return connection

    def claim_runtime_binding(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> None:
        """初验原子认领尚未落库的 Run ID，并拒绝覆盖既有身份。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE agentic_runtime_runs SET run_id=? "
                "WHERE user_id=? AND task_id=? AND revision=? "
                "AND runtime_version='pi' AND run_id IS NULL",
                (run_id, owner_id, task_id, revision),
            )
            row = connection.execute(
                "SELECT 1 FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=? "
                "AND run_id=? AND runtime_version='pi'",
                (owner_id, task_id, revision, run_id),
            ).fetchone()
            connection.commit()
        if row is None:
            raise PermissionError("Runtime 不存在或冻结身份不一致")

    def assert_runtime_binding(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> None:
        """验证精确 Runtime 身份，不泄露其他 Owner 的记录是否存在。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=? "
                "AND run_id=? AND runtime_version='pi'",
                (owner_id, task_id, revision, run_id),
            ).fetchone()
        if row is None:
            raise PermissionError("Runtime 不存在或冻结身份不一致")

    def get_runtime_context(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> dict[str, object] | None:
        """读取语义重试所需的冻结 Runtime 字段。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT runtime_version, status, run_id, workspace_root, "
                "external_api_confirmed, "
                "request_json, candidates_json, verification_json, "
                "verified_candidate_set_hash "
                "FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (owner_id, task_id, revision),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_historical_authority(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        candidate_set_hash: str,
        purpose: HistoricalReverificationPurpose,
    ) -> HistoricalReverificationAuthority | None:
        """只在精确 Owner 与冻结候选身份下返回窄重验权威。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_reverification_authorities "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                "AND candidate_set_hash=? AND purpose=?",
                (
                    owner_id,
                    task_id,
                    revision,
                    run_id,
                    candidate_set_hash,
                    purpose.value,
                ),
            ).fetchone()
        return (
            HistoricalReverificationAuthority.model_validate(dict(row))
            if row is not None
            else None
        )

    def get_historical_authority_by_idempotency(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
    ) -> HistoricalReverificationAuthority | None:
        """恢复权威已落库但 Attempt 尚未创建时，按 Owner 幂等键继续原请求。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_reverification_authorities "
                "WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
        return (
            HistoricalReverificationAuthority.model_validate(dict(row))
            if row is not None
            else None
        )

    def create_historical_authority(
        self,
        authority: HistoricalReverificationAuthority,
    ) -> HistoricalReverificationAuthority:
        """幂等追加 Owner 的历史重验权威，绝不回填 RuntimeAssignment。"""

        values = authority.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            by_idempotency = connection.execute(
                "SELECT * FROM candidate_reverification_authorities "
                "WHERE owner_id=? AND idempotency_key=?",
                (authority.owner_id, authority.idempotency_key),
            ).fetchone()
            if by_idempotency is not None:
                existing = HistoricalReverificationAuthority.model_validate(
                    dict(by_idempotency)
                )
                if existing.authority_id != authority.authority_id:
                    raise ValueError("幂等键已绑定其他历史重验权威请求")
                connection.commit()
                return existing
            existing_row = connection.execute(
                "SELECT * FROM candidate_reverification_authorities "
                "WHERE authority_id=?",
                (authority.authority_id,),
            ).fetchone()
            if existing_row is not None:
                existing = HistoricalReverificationAuthority.model_validate(
                    dict(existing_row)
                )
                connection.commit()
                return existing
            evidence = HistoricalReverificationEvidence.model_validate_json(
                authority.evidence_manifest_json
            )
            if not _historical_database_evidence_matches(connection, evidence):
                # 写锁内重建全部数据库身份，避免并发漂移留下不可删除的伪审计事实。
                raise ValueError("历史重验权威恢复资格已变化")
            columns = tuple(values)
            connection.execute(
                "INSERT INTO candidate_reverification_authorities ("
                + ", ".join(columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")",
                tuple(values[column] for column in columns),
            )
            connection.commit()
        return authority

    def has_succeeded_delivery(self, owner_id: str, run_id: str) -> bool:
        """只读检查新旧正式交付；任一路径命中都必须阻断重复重验。"""

        with self._connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('formal_delivery_runs', 'semantic_delivery_runs')"
                ).fetchall()
            }
            if "formal_delivery_runs" in tables:
                row = connection.execute(
                    "SELECT 1 FROM formal_delivery_runs "
                    "WHERE owner_id=? AND run_id=? AND status='succeeded' LIMIT 1",
                    (owner_id, run_id),
                ).fetchone()
                if row is not None:
                    return True
            if "semantic_delivery_runs" in tables:
                row = connection.execute(
                    "SELECT 1 FROM semantic_delivery_runs "
                    "WHERE user_id=? AND run_id=? LIMIT 1",
                    (owner_id, run_id),
                ).fetchone()
                if row is not None:
                    return True
        return False

    def create(self, attempt: VerificationAttempt) -> VerificationAttempt:
        result, _created = self.create_with_result(attempt)
        return result

    def create_with_result(
        self,
        attempt: VerificationAttempt,
    ) -> tuple[VerificationAttempt, bool]:
        self._validate_requested_attempt(attempt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM candidate_verification_attempts "
                "WHERE owner_id=? AND idempotency_key=?",
                (attempt.owner_id, attempt.idempotency_key),
            ).fetchone()
            result = self._create_with_connection(connection, attempt)
            connection.commit()
        return result, existing is None

    @staticmethod
    def _validate_requested_attempt(attempt: VerificationAttempt) -> None:
        if attempt.status is not AttemptStatus.REQUESTED:
            raise ValueError("公开 create 只能创建 requested Attempt")
        if any(
            value is not None
            for value in (
                attempt.started_at,
                attempt.finished_at,
                attempt.report_json,
                attempt.report_hash,
            )
        ):
            raise ValueError("requested Attempt 状态字段不一致")

    @staticmethod
    def _create_with_connection(
        connection: sqlite3.Connection,
        attempt: VerificationAttempt,
    ) -> VerificationAttempt:
        values = attempt.model_dump(mode="json")
        columns = tuple(values)
        existing_row = connection.execute(
            "SELECT * FROM candidate_verification_attempts "
            "WHERE owner_id=? AND idempotency_key=?",
            (attempt.owner_id, attempt.idempotency_key),
        ).fetchone()
        if existing_row is not None:
            existing = VerificationAttempt.model_validate(dict(existing_row))
            if existing.request_hash != attempt.request_hash:
                raise ValueError("幂等键已绑定其他候选验证请求")
            return existing
        if attempt.previous_attempt_id is not None:
            # 前序链同时是权限与审计边界，绝不能只依赖全局外键串接其他 Owner。
            previous = connection.execute(
                "SELECT task_id, revision, run_id, candidate_set_hash, status, "
                "ruleset_identity_status "
                "FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (attempt.owner_id, attempt.previous_attempt_id),
            ).fetchone()
            if previous is None:
                raise PermissionError("前序 Attempt 不存在或 Owner 不匹配")
            if (
                previous["task_id"] != attempt.task_id
                or previous["revision"] != attempt.revision
                or previous["run_id"] != attempt.run_id
                or previous["candidate_set_hash"] != attempt.candidate_set_hash
            ):
                raise ValueError("前序 Attempt 与冻结 Candidate 身份不一致")
            if previous["status"] not in {
                "passed",
                "failed",
                "inconclusive",
                "outcome_unknown",
                "cancelled",
            }:
                raise RuntimeError("前序 Attempt 尚未进入不可变终态")
            if attempt.reason_code is AttemptReason.LEGACY_REBASELINE and (
                previous["status"] != AttemptStatus.FAILED.value
                or previous["ruleset_identity_status"]
                != RulesetIdentityStatus.LEGACY_UNVERSIONED.value
            ):
                raise ValueError(
                    "Legacy 再基线前序必须是 failed + legacy_unversioned"
                )
            if attempt.reason_code is AttemptReason.LEGACY_REBASELINE:
                try:
                    authorization = RebaselineAuthorizationEvidence.model_validate_json(
                        str(attempt.rebaseline_authorization_json)
                    )
                except ValueError as exc:
                    raise ValueError("Legacy 再基线授权证据无效") from exc
                if not _rebaseline_authorization_matches_attempt(
                    attempt,
                    authorization,
                ):
                    raise ValueError("Legacy 再基线授权证据与冻结 Attempt 身份不一致")
                latest = connection.execute(
                    "SELECT attempt_id FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                    "AND candidate_set_hash=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                versioned = connection.execute(
                    "SELECT 1 FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                    "AND candidate_set_hash=? AND ruleset_identity_status='versioned' "
                    "LIMIT 1",
                    (
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                if latest is None or latest["attempt_id"] != attempt.previous_attempt_id:
                    raise ValueError("Legacy 再基线前序已不是 Candidate 链最新 Attempt")
                if versioned is not None:
                    raise ValueError("Candidate 链已经建立 versioned 验证基线")
            if attempt.reason_code is AttemptReason.PROVIDER_OUTCOME_RECOVERY:
                if previous["status"] != AttemptStatus.OUTCOME_UNKNOWN.value:
                    raise ValueError("Provider 未知结果恢复前序必须是 outcome_unknown")
                latest = connection.execute(
                    "SELECT attempt_id FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                    "AND candidate_set_hash=? ORDER BY rowid DESC LIMIT 1",
                    (
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                if latest is None or latest["attempt_id"] != attempt.previous_attempt_id:
                    raise ValueError("Provider 未知结果恢复前序已不是最新 Attempt")
        active = connection.execute(
            "SELECT 1 FROM candidate_verification_attempts "
            "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
            "AND candidate_set_hash=? AND status IN ('requested', 'running')",
            (
                attempt.owner_id,
                attempt.task_id,
                attempt.revision,
                attempt.run_id,
                attempt.candidate_set_hash,
            ),
        ).fetchone()
        if active is not None:
            raise RuntimeError("该 CandidateSet 已有活动 Attempt")
        connection.execute(
            "INSERT INTO candidate_verification_attempts ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )
        return attempt

    def create_and_start_if_p0_allowed(
        self,
        attempt: VerificationAttempt,
        *,
        started_at: datetime,
    ) -> VerificationAttempt:
        """在同一写事务内重查 P0，并创建、启动一个新 Attempt。"""

        self._validate_requested_attempt(attempt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            routing_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='runtime_rollout_state'"
            ).fetchone()
            if routing_table is not None:
                state = connection.execute(
                    "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
                ).fetchone()
                if state is None or bool(state["p0_blocked"]):
                    raise PermissionError("P0/Gate 当前阻断新的候选验证 Attempt")
            current = self._create_with_connection(connection, attempt)
            if current.status is not AttemptStatus.REQUESTED:
                connection.commit()
                return current
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (started_at.isoformat(), attempt.owner_id, current.attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (attempt.owner_id, current.attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))

    def start_requested_if_current(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        started_at: datetime,
        expected_workspace_root: str,
        expected_request_json: str,
        expected_candidates_json: str,
        expected_verification_json: str,
        expected_historical_authority_id: str | None = None,
    ) -> tuple[VerificationAttempt, bool, bool]:
        """原子重查运行身份、正式交付与 P0，并认领 requested Attempt。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            if row is None:
                raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
            attempt = VerificationAttempt.model_validate(dict(row))
            if attempt.status is not AttemptStatus.REQUESTED:
                connection.commit()
                return attempt, False, False

            def cancel_requested() -> tuple[VerificationAttempt, bool, bool]:
                # Schema 只允许 requested→running→终态；同一事务内跨过 running，
                # 但不释放事务锁，也不执行任何候选读取、Verifier 或外发。
                connection.execute(
                    "UPDATE candidate_verification_attempts "
                    "SET status='running', started_at=? "
                    "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                    (started_at.isoformat(), owner_id, attempt_id),
                )
                connection.execute(
                    "UPDATE candidate_verification_attempts "
                    "SET status='cancelled', finished_at=? "
                    "WHERE owner_id=? AND attempt_id=? AND status='running'",
                    (started_at.isoformat(), owner_id, attempt_id),
                )
                cancelled_row = connection.execute(
                    "SELECT * FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=?",
                    (owner_id, attempt_id),
                ).fetchone()
                connection.commit()
                assert cancelled_row is not None
                return (
                    VerificationAttempt.model_validate(dict(cancelled_row)),
                    False,
                    True,
                )

            state = connection.execute(
                "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
            ).fetchone()
            if state is None or bool(state["p0_blocked"]):
                return cancel_requested()
            runtime = connection.execute(
                "SELECT 1 FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=? AND run_id=? "
                "AND runtime_version='pi' AND status='candidate_ready' "
                "AND verified_candidate_set_hash=? AND workspace_root=? "
                "AND request_json=? AND candidates_json=? AND verification_json=? "
                "AND EXISTS (SELECT 1 FROM semantic_workspace_tasks AS task "
                "WHERE task.user_id=agentic_runtime_runs.user_id "
                "AND task.task_id=agentic_runtime_runs.task_id "
                "AND task.active_revision=agentic_runtime_runs.revision "
                "AND task.cancel_requested=0)",
                (
                    attempt.owner_id,
                    attempt.task_id,
                    attempt.revision,
                    attempt.run_id,
                    attempt.candidate_set_hash,
                    expected_workspace_root,
                    expected_request_json,
                    expected_candidates_json,
                    expected_verification_json,
                ),
            ).fetchone()
            if runtime is None:
                return cancel_requested()
            if attempt.reason_code is AttemptReason.LEGACY_REBASELINE:
                try:
                    authorization = RebaselineAuthorizationEvidence.model_validate_json(
                        str(attempt.rebaseline_authorization_json)
                    )
                except ValueError:
                    authorization = None
                previous = connection.execute(
                    "SELECT status, ruleset_identity_status "
                    "FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=? AND task_id=? "
                    "AND revision=? AND run_id=? AND candidate_set_hash=?",
                    (
                        attempt.owner_id,
                        attempt.previous_attempt_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                latest = connection.execute(
                    "SELECT attempt_id FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                    "AND candidate_set_hash=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                other_versioned = connection.execute(
                    "SELECT 1 FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                    "AND candidate_set_hash=? AND ruleset_identity_status='versioned' "
                    "AND attempt_id<>? LIMIT 1",
                    (
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                        attempt.attempt_id,
                    ),
                ).fetchone()
                authorization_valid = (
                    authorization is not None
                    and _rebaseline_authorization_matches_attempt(
                        attempt,
                        authorization,
                    )
                    and previous is not None
                    and previous["status"] == AttemptStatus.FAILED.value
                    and previous["ruleset_identity_status"]
                    == RulesetIdentityStatus.LEGACY_UNVERSIONED.value
                    and latest is not None
                    and latest["attempt_id"] == attempt.attempt_id
                    and other_versioned is None
                )
                if not authorization_valid:
                    # 授权证据和单次链门在 Worker 认领写锁内再次失败关闭。
                    return cancel_requested()
            if attempt.reason_code is AttemptReason.PROVIDER_OUTCOME_RECOVERY:
                previous = connection.execute(
                    "SELECT status FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=? AND task_id=? "
                    "AND revision=? AND run_id=? AND candidate_set_hash=?",
                    (
                        attempt.owner_id,
                        attempt.previous_attempt_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                latest = connection.execute(
                    "SELECT attempt_id FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                    "AND candidate_set_hash=? ORDER BY rowid DESC LIMIT 1",
                    (
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                    ),
                ).fetchone()
                if (
                    previous is None
                    or previous["status"] != AttemptStatus.OUTCOME_UNKNOWN.value
                    or latest is None
                    or latest["attempt_id"] != attempt.attempt_id
                ):
                    # 未知结果恢复同样只能有一个最新继承者，失败时禁止再次外发。
                    return cancel_requested()
            if expected_historical_authority_id is not None:
                authority_row = connection.execute(
                    "SELECT * FROM candidate_reverification_authorities "
                    "WHERE authority_id=? AND owner_id=? AND task_id=? "
                    "AND revision=? AND run_id=? AND candidate_set_hash=? "
                    "AND purpose=?",
                    (
                        expected_historical_authority_id,
                        attempt.owner_id,
                        attempt.task_id,
                        attempt.revision,
                        attempt.run_id,
                        attempt.candidate_set_hash,
                        HistoricalReverificationPurpose
                        .SEMANTIC_INCONCLUSIVE_REVERIFICATION.value,
                    ),
                ).fetchone()
                try:
                    historical_evidence = (
                        HistoricalReverificationEvidence.model_validate_json(
                            authority_row["evidence_manifest_json"]
                        )
                        if authority_row is not None
                        else None
                    )
                    authority_valid = (
                        authority_row is not None
                        and HistoricalReverificationAuthority.model_validate(
                            dict(authority_row)
                        ).authority_id
                        == expected_historical_authority_id
                        and historical_evidence is not None
                        and _historical_database_evidence_matches(
                            connection,
                            historical_evidence,
                            allowed_current_attempt_id=attempt.attempt_id,
                        )
                    )
                except ValueError:
                    authority_valid = False
                if not authority_valid:
                    # 认领与权威复核共用写锁；失败时绝不进入 Provider。
                    return cancel_requested()
            delivery = connection.execute(
                "SELECT 1 FROM formal_delivery_runs "
                "WHERE owner_id=? AND run_id=? AND status='succeeded' LIMIT 1",
                (attempt.owner_id, attempt.run_id),
            ).fetchone()
            if delivery is None:
                delivery = connection.execute(
                    "SELECT 1 FROM semantic_delivery_runs "
                    "WHERE user_id=? AND run_id=? LIMIT 1",
                    (attempt.owner_id, attempt.run_id),
                ).fetchone()
            if delivery is not None:
                return cancel_requested()
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (started_at.isoformat(), owner_id, attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row)), True, False

    def get(self, owner_id: str, attempt_id: str) -> VerificationAttempt | None:
        with self._connect() as connection:
            # Owner 条件是数据隔离边界，不回退全局查询以免泄露 Attempt 是否存在。
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
        return VerificationAttempt.model_validate(dict(row)) if row else None

    def get_by_idempotency(
        self,
        owner_id: str,
        idempotency_key: str,
    ) -> VerificationAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
        return VerificationAttempt.model_validate(dict(row)) if row else None

    def cancel_requested(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        finished_at: datetime,
    ) -> tuple[VerificationAttempt, bool]:
        """只收口尚未开始的 Attempt；已认领或终态记录保持原样。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            started = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (finished_at.isoformat(), owner_id, attempt_id),
            )
            if started.rowcount == 1:
                connection.execute(
                    "UPDATE candidate_verification_attempts "
                    "SET status='cancelled', finished_at=? "
                    "WHERE owner_id=? AND attempt_id=? AND status='running'",
                    (finished_at.isoformat(), owner_id, attempt_id),
                )
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        if row is None:
            raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
        return VerificationAttempt.model_validate(dict(row)), started.rowcount == 1

    def list_requested_local(self) -> tuple[VerificationAttempt, ...]:
        """恢复尚未认领的 requested；Provider 尚未运行时允许安全接管。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE status='requested' "
                "ORDER BY created_at, rowid"
            ).fetchall()
        return tuple(
            VerificationAttempt.model_validate(dict(row)) for row in rows
        )

    def list_running_local(self) -> tuple[VerificationAttempt, ...]:
        """进程启动时收口上一个 Worker 遗留的 running Attempt。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE status='running' "
                "ORDER BY started_at, rowid"
            ).fetchall()
        return tuple(
            VerificationAttempt.model_validate(dict(row)) for row in rows
        )

    def list_for_candidate(
        self,
        owner_id: str,
        *,
        task_id: str,
        revision: int,
        run_id: str,
        candidate_set_hash: str,
    ) -> tuple[VerificationAttempt, ...]:
        with self._connect() as connection:
            # 历史列表也必须在 Owner 边界内过滤，管理员诊断另走审计接口。
            rows = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                "AND candidate_set_hash=? ORDER BY rowid",
                (owner_id, task_id, revision, run_id, candidate_set_hash),
            ).fetchall()
        return tuple(
            VerificationAttempt.model_validate(dict(row)) for row in rows
        )

    def start(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        started_at: datetime,
    ) -> VerificationAttempt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (started_at.isoformat(), owner_id, attempt_id),
            )
            if updated.rowcount != 1:
                visible = connection.execute(
                    "SELECT 1 FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=?",
                    (owner_id, attempt_id),
                ).fetchone()
                if visible is None:
                    raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
                raise RuntimeError("候选验证 Attempt 已不处于 requested")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))

    def finish(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        status: AttemptStatus,
        report_json: str | None,
        report_hash: str | None,
        finished_at: datetime,
    ) -> VerificationAttempt:
        terminal_statuses = {
            AttemptStatus.PASSED,
            AttemptStatus.FAILED,
            AttemptStatus.INCONCLUSIVE,
            AttemptStatus.OUTCOME_UNKNOWN,
            AttemptStatus.CANCELLED,
        }
        if status not in terminal_statuses:
            raise ValueError("finish 只能写入候选验证终态")
        has_report = report_json is not None or report_hash is not None
        if status in {
            AttemptStatus.PASSED,
            AttemptStatus.FAILED,
            AttemptStatus.INCONCLUSIVE,
        }:
            if report_json is None or report_hash is None:
                raise ValueError("确定性候选验证终态必须冻结报告")
            actual_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
            if actual_hash != report_hash:
                raise ValueError("候选验证报告哈希不匹配")
            try:
                report = json.loads(report_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("候选验证报告不是有效 JSON") from exc
            if not isinstance(report, dict) or report.get("status") != status.value:
                raise ValueError("候选验证报告状态与 Attempt 终态不一致")
        elif has_report:
            raise ValueError("未知或取消终态不得伪造确定性报告")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status=?, report_json=?, report_hash=?, finished_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='running'",
                (
                    status.value,
                    report_json,
                    report_hash,
                    finished_at.isoformat(),
                    owner_id,
                    attempt_id,
                ),
            )
            if updated.rowcount != 1:
                visible = connection.execute(
                    "SELECT 1 FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=?",
                    (owner_id, attempt_id),
                ).fetchone()
                if visible is None:
                    raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
                raise RuntimeError("候选验证 Attempt 已不处于 running")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))

    def finish_with_runtime_projection(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        status: AttemptStatus,
        report_json: str,
        report_hash: str,
        finished_at: datetime,
        candidates_json: str,
        candidate_set_hash: str,
        require_reverification_current: bool = False,
    ) -> VerificationAttempt:
        """原子冻结 Attempt 终态并维护旧 Runtime 读取投影。"""

        if status not in {
            AttemptStatus.PASSED,
            AttemptStatus.FAILED,
            AttemptStatus.INCONCLUSIVE,
        }:
            raise ValueError("兼容投影只接受确定性候选验证终态")
        actual_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        if actual_hash != report_hash:
            raise ValueError("候选验证报告哈希不匹配")
        try:
            report = json.loads(report_json)
        except json.JSONDecodeError as exc:
            raise ValueError("候选验证报告不是有效 JSON") from exc
        if not isinstance(report, dict) or report.get("status") != status.value:
            raise ValueError("候选验证报告状态与 Attempt 终态不一致")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt_row = connection.execute(
                "SELECT task_id, revision, run_id, candidate_set_hash, reason_code "
                "FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=? AND status='running'",
                (owner_id, attempt_id),
            ).fetchone()
            if attempt_row is None:
                raise PermissionError("候选验证 Attempt 不存在或状态不可完成")
            if attempt_row["candidate_set_hash"] != candidate_set_hash:
                raise ValueError("Attempt 与兼容投影的 CandidateSet 不一致")

            if require_reverification_current:
                state = connection.execute(
                    "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
                ).fetchone()
                if state is None or bool(state["p0_blocked"]):
                    raise PermissionError("P0/Gate 已阻断候选重验结论提交")
                current = connection.execute(
                    "SELECT 1 FROM agentic_runtime_runs AS runtime "
                    "WHERE runtime.user_id=? AND runtime.task_id=? "
                    "AND runtime.revision=? AND runtime.run_id=? "
                    "AND runtime.runtime_version='pi' "
                    "AND runtime.status='candidate_ready' "
                    "AND runtime.verified_candidate_set_hash=? "
                    "AND EXISTS (SELECT 1 FROM semantic_workspace_tasks AS task "
                    "WHERE task.user_id=runtime.user_id "
                    "AND task.task_id=runtime.task_id "
                    "AND task.active_revision=runtime.revision "
                    "AND task.cancel_requested=0)",
                    (
                        owner_id,
                        attempt_row["task_id"],
                        attempt_row["revision"],
                        attempt_row["run_id"],
                        candidate_set_hash,
                    ),
                ).fetchone()
                if current is None:
                    raise PermissionError("运行期任务权威身份已漂移或取消")
                delivery = connection.execute(
                    "SELECT 1 FROM formal_delivery_runs "
                    "WHERE owner_id=? AND run_id=? AND status='succeeded' LIMIT 1",
                    (owner_id, attempt_row["run_id"]),
                ).fetchone()
                if delivery is None:
                    delivery = connection.execute(
                        "SELECT 1 FROM semantic_delivery_runs "
                        "WHERE user_id=? AND run_id=? LIMIT 1",
                        (owner_id, attempt_row["run_id"]),
                    ).fetchone()
                if delivery is not None:
                    raise PermissionError("候选已存在正式 Delivery，拒绝提交重验结论")

            # 两张表必须共享一个提交点；任一触发器或身份门拒绝时整体回滚。
            updated_projection = connection.execute(
                "UPDATE agentic_runtime_runs SET candidates_json=?, "
                "verification_json=?, verified_candidate_set_hash=?, updated_at=? "
                "WHERE user_id=? AND task_id=? AND revision=? AND run_id=?",
                (
                    candidates_json,
                    report_json,
                    candidate_set_hash,
                    finished_at.isoformat(),
                    owner_id,
                    attempt_row["task_id"],
                    attempt_row["revision"],
                    attempt_row["run_id"],
                ),
            )
            if updated_projection.rowcount != 1:
                raise PermissionError("Runtime 投影不存在或冻结身份不一致")
            updated_attempt = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status=?, report_json=?, report_hash=?, finished_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='running'",
                (
                    status.value,
                    report_json,
                    report_hash,
                    finished_at.isoformat(),
                    owner_id,
                    attempt_id,
                ),
            )
            if updated_attempt.rowcount != 1:
                raise RuntimeError("候选验证 Attempt 终态提交发生并发冲突")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))
