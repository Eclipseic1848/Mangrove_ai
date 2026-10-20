"""部署排空只读取合成库元数据，未知停止状态不得冒充空闲。"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest

from tests.database_migration_helpers import migrated_profile_database


@pytest.fixture(scope="module")
def database_templates(tmp_path_factory):
    root = tmp_path_factory.mktemp("deployment-preflight-schema")
    return tuple(migrated_profile_database(root / f"{profile}.db", profile=profile)
                 for profile in ("webui", "scheduler"))


@pytest.fixture
def databases(tmp_path, database_templates):
    paths = tuple(tmp_path / source.name for source in database_templates)
    for source, target in zip(database_templates, paths):
        shutil.copyfile(source, target)
    return paths


def test_current_empty_databases_are_clear_without_writing(databases):
    from src.services.deployment_preflight import collect_persisted_blockers

    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in databases]
    assert collect_persisted_blockers(*databases) == ()
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in databases] == before
    assert sorted(path.name for path in databases[0].parent.iterdir()) == ["scheduler.db", "webui.db"]


def test_cleanup_failure_and_active_without_start_evidence_block(databases):
    from src.services.deployment_preflight import collect_persisted_blockers

    with sqlite3.connect(databases[0]) as conn:
        conn.executemany("INSERT INTO account_execution_bindings VALUES (?,?,?,?,?,?)", [
            ("owner", "workspace", "failed-task", 0, "cleanup_failed", 1),
            ("owner", "chat", "unknown-chat", 0, "active", 2),
        ])
    blocks = {item.check_id: item.count for item in collect_persisted_blockers(*databases)}
    assert blocks["persisted.account_cleanup"] == 1
    assert blocks["persisted.account_active"] == 1


def _insert(path, table, **values):
    with sqlite3.connect(path) as conn:
        # 只填合成行的必填元数据；正文列也仅使用固定占位，不加载运行时服务。
        row = {name: (1 if "INT" in kind.upper() else "{}" if name.endswith("_json") else "synthetic")
               for _, name, kind, required, default, _ in conn.execute(f'PRAGMA table_info("{table}")')
               if required and default is None and name not in values}
        row.update(values)
        columns = ",".join(f'"{name}"' for name in row)
        conn.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))


@pytest.mark.parametrize("table,values,check_id", [
    ("account_execution_holds", dict(status="pending", reason="pending", generation=1), "account_hold"),
    ("source_read_uses", dict(state="active"), "source_reads"),
    ("source_read_uses", dict(state="unknown"), "source_reads"),
    ("source_deletion_operations", dict(state="incomplete"), "source_deletion_operations"),
    ("source_deletions", dict(state="deleting"), "source_deletions"),
    ("delivery_publish_intents", dict(status="staging"), "publication"),
    ("delivery_publish_intents", dict(status="committing"), "publication"),
    ("semantic_workspace_tasks", dict(status="pausing"), "workspace"),
    ("semantic_workspace_tasks", dict(status="cancelling"), "workspace"),
    ("agentic_runtime_runs", dict(status="running"), "runtime"),
    ("agentic_runtime_runs", dict(status="preparing"), "runtime"),
    ("agentic_runtime_runs", dict(status="failed", failure_json='{"error_code":"MODEL_OUTCOME_UNKNOWN"}'), "runtime_unknown"),
    ("agentic_runtime_runs", dict(status="needs_input", failure_json='{"error_code":"MODEL_OUTCOME_UNKNOWN"}'), "runtime_unknown"),
    ("semantic_harness_runs", dict(status="running"), "harness"),
    ("candidate_verification_attempts", dict(status="outcome_unknown", reason_code="initial", ruleset_identity_status="versioned"), "candidate"),
    ("source_acquisition_attempts", dict(status="acquiring"), "source_acquisition"),
    ("capability_validation_runs", dict(status="cancelling"), "validation"),
    ("capability_platform_validation_runs", dict(status="running"), "platform_validation"),
    ("semantic_workspace_events", dict(event_id="answer-send:turn-1"), "answer_unknown"),
    ("library_learning_receipts", dict(state="generating", kind="template_new"), "learning_generation"),
    ("source_refresh_intents", dict(status="binding"), "source_refresh"),
    ("candidate_gap_actions", dict(status="pending", action="accept_gap"), "gap_action"),
])
def test_persisted_execution_and_unknown_facts_block(databases, table, values, check_id):
    from src.services.deployment_preflight import collect_persisted_blockers

    _insert(databases[0], table, **values)
    blocks = {item.check_id: item.count for item in collect_persisted_blockers(*databases)}
    assert blocks[f"persisted.{check_id}"] == 1


def _unstarted(database, kind, **overrides):
    _insert(database, "users", user_id="owner", username="owner", execution_generation=2)
    resource = "platform:run" if kind == "platform" else "run"
    binding_kind = "validation" if kind == "platform" else kind
    _insert(database, "account_execution_bindings", owner_user_id="owner", resource_kind=binding_kind,
            resource_id=resource, generation=2, state="active")
    if kind == "workspace":
        table, values = "semantic_workspace_tasks", dict(user_id="owner", task_id="run", status="queued")
    elif kind == "candidate":
        table, values = "candidate_verification_attempts", dict(owner_id="owner", attempt_id="run",
                status="requested", reason_code="initial", ruleset_identity_status="versioned")
    elif kind == "validation":
        table, values = "capability_validation_runs", dict(owner_id="owner", run_id="run", status="queued")
    else:
        table, values = "capability_platform_validation_runs", dict(run_id="run", status="queued",
                payload_json='{"actor_id":"owner"}')
    values.update(overrides)
    _insert(database, table, **values)


@pytest.mark.parametrize("kind", ["workspace", "candidate", "validation", "platform"])
def test_explicit_unstarted_queue_keeps_binding_without_blocking(databases, kind):
    from src.services.deployment_preflight import collect_persisted_blockers

    _unstarted(databases[0], kind)
    assert collect_persisted_blockers(*databases) == ()
    with sqlite3.connect(databases[0]) as conn:
        assert conn.execute("SELECT state,generation FROM account_execution_bindings").fetchone() == ("active", 2)


@pytest.mark.parametrize("state,started,ended,success,expected", [
    ("running", "2026-10-20T00:00:00", None, 0, 1),
    ("unknown", "2026-10-20T00:00:00", "2026-10-20T00:01:00", 0, 1),
    (None, None, None, 1, 0),
    (None, None, None, 0, 0),
    (None, "2026-10-20T00:00:00", None, 0, None),
    ("unrecognized", None, None, 0, None),
    (None, None, "2026-10-20T00:01:00", 0, None),
    (None, None, None, 2, None),
    ("failed", "2026-10-20T00:00:00", "", 0, None),
    ("failed", "2026-10-20T00:00:00", "2026-10-20T00:01:00", 0, 0),
    ("succeeded", "2026-10-20T00:00:00", "2026-10-20T00:01:00", 1, 0),
])
def test_scheduler_legacy_completion_is_narrow(databases, state, started, ended, success, expected):
    from src.services.deployment_preflight import collect_persisted_blockers

    _insert(databases[1], "scheduled_tasks", task_id="schedule", owner_user_id="owner", status="active")
    _insert(databases[1], "scheduled_task_runs", task_id="schedule", state=state,
            started_at=started, ended_at=ended, success=success, run_at="2026-10-20T00:00:00")
    blocks = collect_persisted_blockers(*databases)
    if expected == 0:
        assert blocks == ()
    else:
        assert any(item.check_id.startswith("persisted.scheduler") and item.count == expected for item in blocks)


@pytest.mark.parametrize("table,values", [
    ("agentic_runtime_runs", dict(status="unrecognized")),
    ("semantic_workspace_tasks", dict(status="unrecognized")),
    ("source_read_uses", dict(state="unrecognized")),
    ("delivery_publish_intents", dict(status="unrecognized")),
    ("capability_platform_validation_runs", dict(status="unrecognized")),
    ("agentic_runtime_runs", dict(status="failed", failure_json='"unreadable-state"')),
    ("agentic_runtime_runs", dict(status="failed", failure_json='{"error_code":23}')),
])
def test_unrecognized_state_is_unreadable_not_quiet(databases, table, values):
    from src.services.deployment_preflight import collect_persisted_blockers

    _insert(databases[0], table, **values)
    blocks = collect_persisted_blockers(*databases)
    assert any(item.count is None for item in blocks)
    assert "unrecognized" not in repr(blocks)
    assert "unreadable-state" not in repr(blocks)


@pytest.mark.parametrize("kind,values", [
    ("workspace", dict(user_id="another-owner")),
    ("workspace", dict(run_id="already-started")),
    ("workspace", dict(cancel_requested=1)),
    ("candidate", dict(owner_id="another-owner")),
    ("candidate", dict(started_at="2026-10-20T00:00:00")),
    ("candidate", dict(provider_attempt_id="sent-provider-attempt")),
    ("validation", dict(owner_id="another-owner")),
    ("platform", dict(payload_json='{"actor_id":"another-owner"}')),
])
def test_unstarted_exception_rejects_other_owner_and_started_work(databases, kind, values):
    from src.services.deployment_preflight import collect_persisted_blockers

    _unstarted(databases[0], kind, **values)
    assert any(item.check_id == "persisted.account_active" and item.count == 1
               for item in collect_persisted_blockers(*databases))


@pytest.mark.parametrize("kind", ["workspace", "candidate", "validation", "platform"])
def test_unstarted_exception_never_borrows_current_generation(databases, kind):
    from src.services.deployment_preflight import collect_persisted_blockers

    _unstarted(databases[0], kind)
    with sqlite3.connect(databases[0]) as conn:
        conn.execute("UPDATE users SET execution_generation=3")
    assert any(item.check_id == "persisted.account_active" and item.count == 1
               for item in collect_persisted_blockers(*databases))


@pytest.mark.parametrize("kind", ["validation", "platform"])
def test_queued_validation_with_any_lease_is_not_unstarted(databases, kind):
    from src.services.deployment_preflight import collect_persisted_blockers

    _unstarted(databases[0], kind)
    table = "capability_validation_leases" if kind == "validation" else "capability_platform_validation_leases"
    _insert(databases[0], table, run_id="run", expires_at="2000-01-01T00:00:00")
    assert any(item.check_id == "persisted.account_active" and item.count == 1
               for item in collect_persisted_blockers(*databases))


def test_queued_workspace_with_started_runtime_is_not_unstarted(databases):
    from src.services.deployment_preflight import collect_persisted_blockers

    _unstarted(databases[0], "workspace")
    _insert(databases[0], "agentic_runtime_runs", user_id="owner", task_id="run", revision=1,
            status="queued", run_id="already-started")
    assert any(item.check_id == "persisted.account_active" and item.count == 1
               for item in collect_persisted_blockers(*databases))


@pytest.mark.parametrize("fault", ["missing", "corrupt", "missing_table", "wrong_profile"])
def test_database_faults_close_without_creating_or_disclosing(databases, tmp_path, fault):
    from src.services.deployment_preflight import collect_persisted_blockers

    webui, scheduler = databases
    if fault == "missing":
        webui = tmp_path / "private-owner" / "missing.db"
    elif fault == "corrupt":
        webui.write_text("synthetic-private-database", encoding="utf-8")
    elif fault == "missing_table":
        with sqlite3.connect(webui) as conn:
            conn.execute("DROP TABLE source_read_uses")
    else:
        webui, scheduler = scheduler, webui

    blocks = collect_persisted_blockers(webui, scheduler)
    assert any(item.count is None for item in blocks)
    assert str(tmp_path) not in repr(blocks)
    assert "synthetic-private-database" not in repr(blocks)
    assert not (tmp_path / "private-owner").exists()


def test_preflight_uses_readonly_connections_without_business_or_secret_reads(databases, monkeypatch):
    from src.services.deployment_preflight import collect_persisted_blockers

    connect = sqlite3.connect
    opened = []
    forbidden = []
    private_columns = {"password_hash", "objective_text", "spec_json", "content", "text", "text_preview", "summary", "report_json", "source_refs_json", "after_content", "ciphertext", "token_hash", "refresh_digest", "content_blob"}
    private_tables = {"runtime_config", "runtime_config_secrets", "model_connection_secrets", "source_artifacts"}

    def guarded_connect(database, *args, **kwargs):
        if database == ":memory:":
            return connect(database, *args, **kwargs)
        opened.append(str(database))
        assert kwargs.get("uri") is True and "mode=ro" in str(database)
        conn = connect(database, *args, **kwargs)

        def authorize(action, table, column, _database, _trigger):
            if action == sqlite3.SQLITE_READ and (table in private_tables or column in private_columns):
                forbidden.append((table, column))
                return sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                forbidden.append(("write", table))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    assert collect_persisted_blockers(*databases) == ()
    assert opened and not forbidden


@pytest.mark.parametrize("table,values", [
    ("agentic_runtime_runs", dict(status="failed", failure_json='{"error_code":"PI_RUNTIME_FAILED"}')),
    ("semantic_workspace_tasks", dict(status="needs_input")),
    ("library_learning_receipts", dict(state="queued", kind="template_new")),
    ("library_learning_receipts", dict(state="pending", kind="template_new")),
    ("account_execution_holds", dict(status="completed", reconciliation_complete=1, reason="pending", generation=1)),
])
def test_known_quiet_or_unstarted_facts_are_not_rewritten_or_blocked(databases, table, values):
    from src.services.deployment_preflight import collect_persisted_blockers

    _insert(databases[0], table, **values)
    before = hashlib.sha256(databases[0].read_bytes()).hexdigest()
    assert collect_persisted_blockers(*databases) == ()
    assert hashlib.sha256(databases[0].read_bytes()).hexdigest() == before


def test_legacy_completed_history_never_clears_active_schedule(databases):
    from src.services.deployment_preflight import collect_persisted_blockers

    _insert(databases[1], "scheduled_tasks", task_id="schedule", owner_user_id="owner", status="active")
    _insert(databases[1], "scheduled_task_runs", task_id="schedule", state=None, success=1,
            run_at="2026-10-20T00:00:00")
    _insert(databases[0], "account_execution_bindings", owner_user_id="owner", resource_kind="schedule",
            resource_id="schedule", generation=0, state="active")
    assert any(item.check_id == "persisted.account_active" and item.count == 1
               for item in collect_persisted_blockers(*databases))


@pytest.mark.parametrize("proof", ["done", "result", "other_owner_result"])
def test_answer_completion_proof_requires_the_original_owner(databases, proof):
    from src.services.deployment_preflight import collect_persisted_blockers

    _insert(databases[0], "semantic_workspace_events", user_id="owner", event_id="answer-send:turn")
    if proof == "done":
        _insert(databases[0], "semantic_workspace_events", user_id="owner", event_id="answer-done:turn", sequence=2)
    else:
        _insert(databases[0], "conversation_steering_results", owner_id="owner" if proof == "result" else "another-owner",
                turn_id="turn")
    blocks = collect_persisted_blockers(*databases)
    if proof == "other_owner_result":
        assert any(item.check_id == "persisted.answer_unknown" and item.count == 1 for item in blocks)
    else:
        assert blocks == ()
