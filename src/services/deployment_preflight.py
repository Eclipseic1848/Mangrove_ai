"""部署前只读核对持久停止事实；不修复、不取消，也不重放业务。"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from src.database_migrations import DatabaseTarget, inspect_database


@dataclass(frozen=True)
class DrainBlocker:
    check_id: str
    count: int | None
    summary: str


_WEBUI_CHECKS = (
    ("account_cleanup", "SELECT count(*) FROM account_execution_bindings WHERE state='cleanup_failed'"),
    # active 也用于尚未启动的库存；仅接受同 Owner、同代且没有开始证据的队列例外。
    ("account_active", """SELECT count(*) FROM account_execution_bindings b WHERE b.state='active' AND NOT (
        EXISTS (SELECT 1 FROM users u WHERE u.user_id=b.owner_user_id AND u.execution_generation=b.generation AND u.disabled=0 AND u.pending=0)
        AND (
            (b.resource_kind='workspace' AND EXISTS (
                SELECT 1 FROM semantic_workspace_tasks w WHERE w.user_id=b.owner_user_id AND w.task_id=b.resource_id
                AND w.status='queued' AND w.run_id IS NULL AND w.cancel_requested=0
                AND NOT EXISTS (SELECT 1 FROM agentic_runtime_runs r WHERE r.user_id=w.user_id AND r.task_id=w.task_id
                    AND r.revision=w.active_revision AND (r.status!='queued' OR r.run_id IS NOT NULL))))
            OR (b.resource_kind='candidate' AND EXISTS (
                SELECT 1 FROM candidate_verification_attempts c WHERE c.owner_id=b.owner_user_id AND c.attempt_id=b.resource_id
                AND c.status='requested' AND c.started_at IS NULL AND c.provider_attempt_id IS NULL))
            OR (b.resource_kind='validation' AND EXISTS (
                SELECT 1 FROM capability_validation_runs v WHERE v.owner_id=b.owner_user_id AND v.run_id=b.resource_id
                AND v.status='queued' AND NOT EXISTS (SELECT 1 FROM capability_validation_leases l WHERE l.run_id=v.run_id)))
            OR (b.resource_kind='validation' AND EXISTS (
                SELECT 1 FROM capability_platform_validation_runs v WHERE b.resource_id='platform:'||v.run_id
                AND json_extract(v.payload_json,'$.actor_id')=b.owner_user_id AND v.status='queued'
                AND NOT EXISTS (SELECT 1 FROM capability_platform_validation_leases l WHERE l.run_id=v.run_id)))
        ))"""),
    ("account_hold", "SELECT count(*) FROM account_execution_holds WHERE status!='completed' OR reconciliation_complete!=1 OR error_code IS NOT NULL"),
    ("source_reads", "SELECT count(*) FROM source_read_uses WHERE state!='completed'"),
    ("source_deletion_operations", "SELECT count(*) FROM source_deletion_operations WHERE state NOT IN ('completed','needs_confirmation')"),
    ("source_deletions", "SELECT count(*) FROM source_deletions WHERE state!='deleted'"),
    ("publication", "SELECT count(*) FROM delivery_publish_intents WHERE status IN ('staging','committing')"),
    ("workspace", "SELECT count(*) FROM semantic_workspace_tasks WHERE status IN ('running','pausing','cancelling')"),
    ("runtime", "SELECT count(*) FROM agentic_runtime_runs WHERE status IN ('preparing','running')"),
    ("runtime_unknown", "SELECT count(*) FROM agentic_runtime_runs WHERE json_extract(failure_json,'$.error_code')='MODEL_OUTCOME_UNKNOWN'"),
    ("workspace_unknown", "SELECT count(*) FROM semantic_workspace_tasks WHERE json_extract(failure_json,'$.error_code')='MODEL_OUTCOME_UNKNOWN'"),
    ("harness", "SELECT count(*) FROM semantic_harness_runs WHERE status='running'"),
    ("candidate", "SELECT count(*) FROM candidate_verification_attempts WHERE status IN ('running','outcome_unknown')"),
    ("source_acquisition", "SELECT count(*) FROM source_acquisition_attempts WHERE status IN ('acquiring','cancelling')"),
    ("validation", "SELECT count(*) FROM capability_validation_runs WHERE status IN ('running','cancelling')"),
    ("platform_validation", "SELECT count(*) FROM capability_platform_validation_runs WHERE status IN ('running','cancelling')"),
    ("learning_generation", "SELECT count(*) FROM library_learning_receipts WHERE state='generating'"),
    ("source_refresh", "SELECT count(*) FROM source_refresh_intents WHERE status='binding'"),
    ("gap_action", "SELECT count(*) FROM candidate_gap_actions WHERE status='pending'"),
    ("answer_unknown", """SELECT count(*) FROM semantic_workspace_events e
        WHERE substr(e.event_id,1,12)='answer-send:'
        AND NOT EXISTS (SELECT 1 FROM semantic_workspace_events d WHERE d.event_id='answer-done:'||substr(e.event_id,13))
        AND NOT EXISTS (SELECT 1 FROM conversation_steering_results r WHERE r.owner_id=e.user_id AND r.turn_id=substr(e.event_id,13))"""),
)


_SCHEDULER_CHECKS = (
    ("scheduler_runs", "SELECT count(*) FROM scheduled_task_runs WHERE state IN ('running','unknown')"),
)


_WEBUI_STATES = (
    ("account_execution_bindings", "state", ("active", "idle", "paused", "cleanup_failed")),
    ("account_execution_holds", "status", ("pending", "completed", "failed")),
    ("source_read_uses", "state", ("active", "unknown", "completed")),
    ("source_deletion_operations", "state", ("planned", "stopping", "cleaning", "incomplete", "needs_confirmation", "completed")),
    ("source_deletions", "state", ("deleting", "removing", "deleted")),
    ("delivery_publish_intents", "status", ("staging", "committing", "published", "aborted")),
    ("semantic_workspace_tasks", "status", ("queued", "running", "needs_input", "candidate_ready", "completed", "failed", "cancelling", "cancelled", "pausing", "paused")),
    ("agentic_runtime_runs", "status", ("queued", "preparing", "running", "needs_input", "candidate_ready", "failed", "cancelled")),
    ("semantic_harness_runs", "status", ("running", "needs_user", "succeeded", "failed", "cancelled")),
    ("candidate_verification_attempts", "status", ("requested", "running", "passed", "failed", "inconclusive", "outcome_unknown", "cancelled")),
    ("source_acquisition_attempts", "status", ("acquiring", "cancelling", "succeeded", "failed", "canceled")),
    ("capability_validation_runs", "status", ("queued", "running", "succeeded", "failed", "cancelling", "cancelled")),
    ("capability_platform_validation_runs", "status", ("queued", "running", "succeeded", "failed", "cancelling", "cancelled")),
    ("library_learning_receipts", "state", ("queued", "generating", "pending", "applied", "conflict", "skipped")),
    ("source_refresh_intents", "status", ("binding", "completed", "failed")),
    ("candidate_gap_actions", "status", ("pending", "completed", "rejected")),
)


def _check_states(conn: sqlite3.Connection, domains, blockers: list[DrainBlocker]) -> None:
    for table, column, allowed in domains:
        marks = ",".join("?" for _ in allowed)
        if conn.execute(f'SELECT 1 FROM "{table}" WHERE "{column}" IS NULL OR "{column}" NOT IN ({marks}) LIMIT 1', allowed).fetchone():
            blockers.append(DrainBlocker(f"persisted.{table}.state", None, "state_unreadable"))


def _check_failure_metadata(conn: sqlite3.Connection, blockers: list[DrainBlocker]) -> None:
    for table in ("agentic_runtime_runs", "semantic_workspace_tasks"):
        # 只解释固定错误码；损坏、非对象或类型异常的失败元数据不能变成已知结果。
        invalid = conn.execute(f'''SELECT 1 FROM "{table}" WHERE failure_json IS NOT NULL AND CASE
            WHEN json_valid(failure_json)=0 THEN 1
            WHEN json_type(failure_json) NOT IN ('object','null') THEN 1
            WHEN json_type(failure_json,'$.error_code') NOT IN ('text','null') THEN 1
            ELSE 0 END LIMIT 1''').fetchone()
        if invalid:
            blockers.append(DrainBlocker(f"persisted.{table}.failure", None, "state_unreadable"))


def _check_scheduler_shape(conn: sqlite3.Connection, blockers: list[DrainBlocker]) -> None:
    # 旧 add_run 只追加已返回的结果；新运行字段出现后不能借 NULL 状态冒充旧历史。
    invalid = conn.execute("""SELECT count(*) FROM scheduled_task_runs r
        WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks t WHERE t.task_id=r.task_id)
        OR r.success NOT IN (0,1) OR r.run_at IS NULL OR trim(r.run_at)=''
        OR (r.state IS NULL AND (r.started_at IS NOT NULL OR r.ended_at IS NOT NULL))
        OR (r.state IS NOT NULL AND r.state NOT IN ('running','unknown','succeeded','failed','cancelled'))
        OR (r.state IN ('succeeded','failed','cancelled') AND (
            r.started_at IS NULL OR r.ended_at IS NULL OR trim(r.started_at)='' OR trim(r.ended_at)=''))""").fetchone()[0]
    if invalid:
        blockers.append(DrainBlocker("persisted.scheduler_state", None, "state_unreadable"))


def _collect_counts(conn: sqlite3.Connection, checks, blockers: list[DrainBlocker]) -> None:
    for check_id, query in checks:
        try:
            count = conn.execute(query).fetchone()[0]
        except Exception:
            blockers.append(DrainBlocker(f"persisted.{check_id}", None, "state_unreadable"))
        else:
            if count:
                blockers.append(DrainBlocker(f"persisted.{check_id}", count, "persisted_execution_not_quiet"))


def collect_persisted_blockers(webui_db: Path, scheduler_db: Path) -> tuple[DrainBlocker, ...]:
    """空结果只表示两库已明确核对，不代表进程或文件资源已经静默。"""
    blockers = []
    for profile, path in (("webui", webui_db), ("scheduler", scheduler_db)):
        try:
            inspect_database(DatabaseTarget(profile=profile, path=path)).require_current()
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                if profile == "webui":
                    _check_states(conn, _WEBUI_STATES, blockers)
                    _check_failure_metadata(conn, blockers)
                    _collect_counts(conn, _WEBUI_CHECKS, blockers)
                else:
                    _check_states(conn, (("scheduled_tasks", "status", ("active", "paused", "done", "cancelled")),), blockers)
                    _check_scheduler_shape(conn, blockers)
                    _collect_counts(conn, _SCHEDULER_CHECKS, blockers)
        except Exception:
            # 路径和底层异常可能含敏感信息，未知检查仅返回固定低敏代码。
            blockers.append(DrainBlocker(f"persisted.{profile}.unreadable", None, "database_or_schema_unreadable"))
    return tuple(blockers)
