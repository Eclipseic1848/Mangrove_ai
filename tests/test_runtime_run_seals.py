# -*- coding: utf-8 -*-
"""历史未知 Run 封存仅使用临时迁移库和合成停止证明。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from src import runtime_run_seals as run_seals
from tests.database_migration_helpers import migrated_webui_database


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _write_json(path, value):
    path.write_text(_json(value), encoding="utf-8")
    path.chmod(0o600)


def _history(database):
    with closing(sqlite3.connect(database)) as conn:
        return {
            "runs": conn.execute("SELECT * FROM agentic_runtime_runs ORDER BY user_id,task_id,revision").fetchall(),
            "events": conn.execute("SELECT * FROM agentic_runtime_events ORDER BY sequence").fetchall(),
            "workspace": conn.execute("SELECT * FROM semantic_workspace_tasks ORDER BY task_id").fetchall(),
            "bytes": conn.execute("""SELECT CAST(failure_json AS BLOB),CAST(candidates_json AS BLOB),
                CAST(verification_json AS BLOB),CAST(request_json AS BLOB) FROM agentic_runtime_runs""").fetchall(),
            "event_bytes": conn.execute("SELECT CAST(details_json AS BLOB) FROM agentic_runtime_events ORDER BY sequence").fetchall(),
        }


def _count(database):
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute("SELECT count(*) FROM runtime_run_seals").fetchone()[0]


@pytest.fixture(scope="module")
def run_seal_database_template(tmp_path_factory):
    return migrated_webui_database(tmp_path_factory.mktemp("run-seal-schema") / "webui.db")


@pytest.fixture
def unknown_run(tmp_path, monkeypatch, request, run_seal_database_template):
    """复用时同时导入本 fixture 与 run_seal_database_template；返回真实 writer 的入参。"""
    from src.agentic_runtime.kernel import RuntimeBinding

    database = tmp_path / "webui.db"
    shutil.copyfile(run_seal_database_template, database)
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    frozen = RuntimeBinding(
        adapter_id="pi", adapter_version="synthetic-1", runtime_artifact="synthetic/pi@sha256:" + "a" * 64,
        protocol_version="1", event_schema_version="1", runtime_version="pi",
        capability_digest="b" * 64, external_run_id="pi_run_0123456789abcdef",
        model_connection_id="synthetic-connection", model_connection_version="1", model="synthetic-model",
    ).model_dump(mode="json")
    failure = '{\n  "error_code": "MODEL_OUTCOME_UNKNOWN", "cause_summary": "原始未知事实"\n}'
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executemany("""INSERT INTO users
            (user_id,username,password_hash,created_at,role,execution_generation)
            VALUES (?,?,'synthetic-hash','before',?,2)""", [
                ("owner", "synthetic-owner", "user"), ("operator", "synthetic-operator", "super_admin"),
                ("other-owner", "synthetic-other", "user"),
            ])
        conn.execute("""INSERT INTO account_execution_bindings
            (owner_user_id,resource_kind,resource_id,generation,state,updated_at)
            VALUES ('owner','workspace','task',2,'idle',1)""")
        conn.execute("""INSERT INTO semantic_workspace_tasks
            (task_id,user_id,title,objective_text,status,active_revision,run_id,failure_json,created_at,updated_at)
            VALUES ('task','owner','合成旧任务','保留原未知事实','needs_input',1,?,?, 'before','before')""",
            (frozen["external_run_id"], failure))
        conn.execute("""INSERT INTO agentic_runtime_runs
            (user_id,task_id,revision,runtime_version,permission_profile,status,run_id,
             model_connection_id,model_connection_version,model_connection_model,failure_json,
             candidates_json,verification_json,request_json,created_at,updated_at)
            VALUES ('owner','task',1,'pi','standard',?,?,'synthetic-connection','1','synthetic-model',?,
                '[ {"filename": "原始候选.txt"} ]','{ "verified": false }','{ "original": "原始请求" }','before','before')""",
            (getattr(request, "param", "failed"), frozen["external_run_id"], failure))
        conn.executemany("""INSERT INTO agentic_runtime_events
            (event_id,user_id,task_id,revision,event_type,summary,details_json,created_at)
            VALUES (?,'owner','task',1,?,?,?,'before')""", [
                ("frozen-event", "kernel.binding.frozen", "原始冻结身份", json.dumps({"binding": frozen}, ensure_ascii=False, indent=2)),
                ("unknown-event", "runtime.failed", "原始未知事件", failure),
            ])
        snapshot = run_seals.snapshot_unknown_run(conn, "owner", "task", 1)
    proof_root = tmp_path / "private-proof"
    proof_root.mkdir(mode=0o700)
    observed = datetime.now(timezone.utc).isoformat()
    proof = {
        "version": 1, "owner_id": "owner", "task_id": "task", "revision": 1,
        "run_id": frozen["external_run_id"], "request_id": "c" * 32,
        "runtime_identity_sha256": _sha(snapshot["identity"]), "snapshot_sha256": _sha(snapshot),
        "approval_reference": "synthetic-issue-244/approval/owner-task-revision-1", "observed_at": observed, "checks": {},
    }
    for kind in ("executor", "resources", "relay"):
        source = proof_root / f"{kind}.json"
        _write_json(source, {
            "version": 1, "check": kind, "state": "stopped", "request_id": proof["request_id"],
            "runtime_identity_sha256": proof["runtime_identity_sha256"], "observed_at": observed,
            "observations": [{"synthetic_observation": kind, "active_count": 0}],
        })
        proof["checks"][kind] = {"state": "stopped", "file": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    proof_path = proof_root / "proof.json"
    _write_json(proof_path, proof)
    stopped_calls = []

    def stopped_service(service, root):
        assert service == "synthetic-mangrove.service" and root.resolve() == runtime_root.resolve()
        stopped_calls.append((service, root))

    def forbidden_process(*args, **kwargs):
        raise AssertionError("封存测试禁止真实 systemctl 或任何进程调用")

    monkeypatch.setattr(run_seals, "_require_stopped_service", stopped_service)
    monkeypatch.setattr(run_seals.subprocess, "run", forbidden_process)
    return {
        "db": database, "snapshot": snapshot, "proof": proof, "proof_path": proof_path,
        "runtime_root": runtime_root, "stopped_calls": stopped_calls,
        "kwargs": {"owner_id": "owner", "task_id": "task", "revision": 1, "run_id": frozen["external_run_id"],
                   "actor_id": "operator", "approval_reference": proof["approval_reference"], "idempotency_key": "synthetic-seal-key",
                   "expected_snapshot_sha256": _sha(snapshot), "proof_path": proof_path,
                   "service": "synthetic-mangrove.service", "runtime_root": runtime_root},
    }


def _seal(case, **overrides):
    return run_seals.seal_unknown_run(case["db"], **{**case["kwargs"], **overrides})


def _refresh_proof_snapshot(case):
    # 负向测试也使用当前完整快照，避免仅因旧摘要不符而掩盖状态门回归。
    with closing(sqlite3.connect(case["db"])) as conn:
        snapshot = run_seals.snapshot_unknown_run(conn, "owner", "task", 1)
    case["snapshot"] = snapshot
    case["kwargs"]["expected_snapshot_sha256"] = _sha(snapshot)
    case["proof"]["snapshot_sha256"] = _sha(snapshot)
    case["proof"]["runtime_identity_sha256"] = _sha(snapshot["identity"])
    for check in case["proof"]["checks"].values():
        source = case["proof_path"].parent / check["file"]
        evidence = json.loads(source.read_text(encoding="utf-8"))
        evidence["runtime_identity_sha256"] = case["proof"]["runtime_identity_sha256"]
        _write_json(source, evidence)
        check["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    _write_json(case["proof_path"], case["proof"])


@pytest.mark.parametrize("unknown_run", ["failed", "needs_input"], indirect=True)
def test_exact_approval_seals_once_without_rewriting_original_history(unknown_run, monkeypatch):
    before = _history(unknown_run["db"])
    statements = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(run_seals.sqlite3, "connect", traced_connect)
    result = _seal(unknown_run)

    assert result["sealed"] is True and result["replayed"] is False
    assert len(result["request_sha256"]) == 64 and result["sealed_at"]
    assert _count(unknown_run["db"]) == 1 and _history(unknown_run["db"]) == before
    writes = [statement.strip().upper() for statement in statements
              if statement.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))]
    assert writes and all(statement.startswith("INSERT INTO RUNTIME_RUN_SEALS ") for statement in writes)
    with closing(sqlite3.connect(unknown_run["db"])) as conn:
        assert run_seals.is_valid_run_seal(conn, "owner", "task", 1)
        assert not run_seals.is_valid_run_seal(conn, "other-owner", "task", 1)
    assert len(unknown_run["stopped_calls"]) == 2


def test_snapshot_and_missing_seal_validation_are_read_only(unknown_run):
    before = unknown_run["db"].read_bytes()
    with closing(sqlite3.connect(unknown_run["db"])) as conn:
        assert run_seals.snapshot_unknown_run(conn, "owner", "task", 1) == unknown_run["snapshot"]
        assert not run_seals.is_valid_run_seal(conn, "owner", "task", 1)
    assert unknown_run["db"].read_bytes() == before


def test_proof_expiring_during_final_stop_check_leaves_no_audit(unknown_run, monkeypatch):
    from types import SimpleNamespace

    observed = datetime.fromisoformat(unknown_run["proof"]["observed_at"])
    now = [observed + timedelta(seconds=1)]
    stopped = run_seals._require_stopped_service

    def final_check(service, root):
        stopped(service, root)
        if len(unknown_run["stopped_calls"]) == 2:
            now[0] = observed + timedelta(seconds=601)

    monkeypatch.setattr(run_seals, "datetime", SimpleNamespace(
        now=lambda tz: now[0], fromisoformat=datetime.fromisoformat))
    monkeypatch.setattr(run_seals, "_require_stopped_service", final_check)
    before = _history(unknown_run["db"])

    with pytest.raises(ValueError):
        _seal(unknown_run)

    assert _count(unknown_run["db"]) == 0 and _history(unknown_run["db"]) == before


@pytest.mark.parametrize("unknown_run", ["failed", "needs_input"], indirect=True)
def test_null_workspace_run_projection_keeps_original_history_and_audit(unknown_run):
    with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
        conn.execute("UPDATE semantic_workspace_tasks SET run_id=NULL")
    _refresh_proof_snapshot(unknown_run)
    before = _history(unknown_run["db"])

    assert _seal(unknown_run)["sealed"] is True

    with closing(sqlite3.connect(unknown_run["db"])) as conn:
        audit = json.loads(conn.execute("SELECT snapshot_json FROM runtime_run_seals").fetchone()[0])
        assert audit["workspace"]["run_id"] is None
        assert audit["identity"]["run_id"] == unknown_run["kwargs"]["run_id"]
        assert run_seals.is_valid_run_seal(conn, "owner", "task", 1)
        with pytest.raises(run_seals.RuntimeRunSealedError):
            run_seals.assert_run_not_sealed(conn, "owner", "task", 1)
    assert _history(unknown_run["db"]) == before


@pytest.mark.parametrize("null_projection", [False, True])
@pytest.mark.parametrize("overrides", [
    {"owner_id": "other-owner"}, {"task_id": "other-task"}, {"revision": 2},
    {"run_id": "other-run"}, {"expected_snapshot_sha256": "0" * 64},
    {"approval_reference": "synthetic-different-approval"},
])
def test_seal_rejects_wrong_identity_snapshot_and_approval(unknown_run, overrides, null_projection):
    if null_projection:
        with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
            conn.execute("UPDATE semantic_workspace_tasks SET run_id=NULL")
        _refresh_proof_snapshot(unknown_run)
    before = _history(unknown_run["db"])
    with pytest.raises(ValueError):
        _seal(unknown_run, **overrides)
    assert _count(unknown_run["db"]) == 0 and _history(unknown_run["db"]) == before


@pytest.mark.parametrize("field,value", [
    ("owner_id", "other-owner"), ("revision", 2), ("run_id", "other-run"),
    ("runtime_identity_sha256", "0" * 64), ("snapshot_sha256", "0" * 64),
    ("request_id", "invalid-nonce"), ("request_id", "d" * 32),
    ("approval_reference", "synthetic-different-approval"),
])
def test_stop_proof_must_bind_exact_run_snapshot_nonce_and_approval(unknown_run, field, value):
    unknown_run["proof"][field] = value
    _write_json(unknown_run["proof_path"], unknown_run["proof"])
    with pytest.raises(ValueError):
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0


@pytest.mark.parametrize("kind", ["executor", "resources", "relay"])
@pytest.mark.parametrize("mutation", ["missing", "changed-bytes", "wrong-identity", "wrong-nonce", "wrong-time", "empty-observations"])
def test_raw_stop_evidence_is_required_and_bound_even_when_rehashed(unknown_run, kind, mutation):
    check = unknown_run["proof"]["checks"][kind]
    source = unknown_run["proof_path"].parent / check["file"]
    if mutation == "missing":
        source.unlink()
    elif mutation == "changed-bytes":
        source.write_bytes(source.read_bytes() + b" ")
    else:
        evidence = json.loads(source.read_text(encoding="utf-8"))
        field, value = {
            "wrong-identity": ("runtime_identity_sha256", "0" * 64),
            "wrong-nonce": ("request_id", "d" * 32),
            "wrong-time": ("observed_at", "2020-01-01T00:00:00+00:00"),
            "empty-observations": ("observations", []),
        }[mutation]
        evidence[field] = value
        _write_json(source, evidence)
        check["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        _write_json(unknown_run["proof_path"], unknown_run["proof"])
    before = _history(unknown_run["db"])
    with pytest.raises((ValueError, OSError)):
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0 and _history(unknown_run["db"]) == before


@pytest.mark.parametrize("age", ["expired", "future", "naive"])
def test_stop_proof_rejects_expired_future_or_unzoned_time(unknown_run, age):
    now = datetime.now(timezone.utc)
    observed = {"expired": now - timedelta(seconds=601), "future": now + timedelta(seconds=60), "naive": now.replace(tzinfo=None)}[age]
    unknown_run["proof"]["observed_at"] = observed.isoformat()
    # 证明与原始文件时间仍一致，确保仅时效门能拒绝这组输入。
    for check in unknown_run["proof"]["checks"].values():
        source = unknown_run["proof_path"].parent / check["file"]
        evidence = json.loads(source.read_text(encoding="utf-8"))
        evidence["observed_at"] = observed.isoformat()
        _write_json(source, evidence)
        check["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    _write_json(unknown_run["proof_path"], unknown_run["proof"])
    with pytest.raises(ValueError):
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0


@pytest.mark.parametrize("actor_state", ["user", "admin", "disabled", "pending", "missing"])
def test_seal_requires_enabled_approved_super_admin(unknown_run, actor_state):
    with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
        if actor_state in {"user", "admin"}:
            conn.execute("UPDATE users SET role=? WHERE user_id='operator'", (actor_state,))
        elif actor_state == "missing":
            conn.execute("DELETE FROM users WHERE user_id='operator'")
        else:
            conn.execute(f"UPDATE users SET {actor_state}=1 WHERE user_id='operator'")
    with pytest.raises(PermissionError):
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0


@pytest.mark.parametrize("null_projection", [False, True])
@pytest.mark.parametrize("statement", [
    "UPDATE account_execution_bindings SET state='active'",
    "UPDATE account_execution_bindings SET state='cleanup_failed'",
    "UPDATE users SET execution_generation=3 WHERE user_id='owner'",
    "UPDATE account_execution_bindings SET generation=3",
    "UPDATE semantic_workspace_tasks SET status='running'",
    "UPDATE semantic_workspace_tasks SET active_revision=2",
    "UPDATE semantic_workspace_tasks SET run_id='newer-run'",
    "UPDATE semantic_workspace_tasks SET run_id=''",
    "UPDATE agentic_runtime_runs SET status='running'",
    "UPDATE agentic_runtime_runs SET failure_json='{}'",
])
def test_current_generation_quiescence_revision_and_run_cannot_be_bypassed_with_fresh_proof(unknown_run, statement, null_projection):
    if null_projection:
        with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
            conn.execute("UPDATE semantic_workspace_tasks SET run_id=NULL")
        _refresh_proof_snapshot(unknown_run)
    with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
        conn.execute(statement)
    before = _history(unknown_run["db"])
    with pytest.raises(ValueError):
        _refresh_proof_snapshot(unknown_run)
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0 and _history(unknown_run["db"]) == before


@pytest.mark.parametrize("null_projection", [False, True])
@pytest.mark.parametrize("mutation", ["missing-event", "wrong-run", "missing-model", "wrong-connection", "wrong-version", "wrong-model"])
def test_original_valid_runtime_binding_cannot_be_missing_or_rebuilt(unknown_run, mutation, null_projection):
    if null_projection:
        with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
            conn.execute("UPDATE semantic_workspace_tasks SET run_id=NULL")
        _refresh_proof_snapshot(unknown_run)
    with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
        if mutation == "missing-event":
            conn.execute("DELETE FROM agentic_runtime_events WHERE event_type='kernel.binding.frozen'")
        else:
            row = conn.execute("SELECT details_json FROM agentic_runtime_events WHERE event_type='kernel.binding.frozen'").fetchone()
            details = json.loads(row[0])
            if mutation == "missing-model":
                del details["binding"]["model"]
            else:
                field = {"wrong-run": "external_run_id", "wrong-connection": "model_connection_id",
                         "wrong-version": "model_connection_version", "wrong-model": "model"}[mutation]
                details["binding"][field] = "synthetic-different"
            conn.execute("UPDATE agentic_runtime_events SET details_json=? WHERE event_type='kernel.binding.frozen'", (_json(details),))
    before = _history(unknown_run["db"])
    with pytest.raises(ValueError):
        _refresh_proof_snapshot(unknown_run)
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0 and _history(unknown_run["db"]) == before


def test_same_seal_replays_without_second_insert(unknown_run):
    original = _seal(unknown_run)
    before = _history(unknown_run["db"])

    repeated = _seal(unknown_run)

    assert repeated == {**original, "replayed": True}
    assert _count(unknown_run["db"]) == 1 and _history(unknown_run["db"]) == before


def test_exact_old_seal_replay_remains_valid_after_new_workspace_revision(unknown_run):
    original = _seal(unknown_run)
    with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
        conn.execute("UPDATE semantic_workspace_tasks SET active_revision=2,run_id='new-revision-run',status='running'")
        conn.execute("UPDATE account_execution_bindings SET state='active'")
        assert run_seals.is_valid_run_seal(conn, "owner", "task", 1)
    before = _history(unknown_run["db"])

    repeated = _seal(unknown_run)

    assert repeated == {**original, "replayed": True}
    assert _count(unknown_run["db"]) == 1 and _history(unknown_run["db"]) == before


@pytest.mark.parametrize("overrides", [
    {"idempotency_key": "synthetic-different-key"}, {"approval_reference": "synthetic-different-approval"},
    {"run_id": "synthetic-different-run"}, {"expected_snapshot_sha256": "0" * 64},
])
def test_sealed_identity_rejects_different_idempotency_or_approval(unknown_run, overrides):
    _seal(unknown_run)
    before = _history(unknown_run["db"])
    with pytest.raises(ValueError):
        _seal(unknown_run, **overrides)
    assert _count(unknown_run["db"]) == 1 and _history(unknown_run["db"]) == before


def test_two_concurrent_requests_only_create_one_immutable_seal(unknown_run):
    from concurrent.futures import ThreadPoolExecutor
    from filelock import Timeout
    import threading

    barrier = threading.Barrier(2)

    def invoke(_):
        barrier.wait(timeout=5)
        try:
            return _seal(unknown_run)
        except Timeout as busy:
            return busy

    before = _history(unknown_run["db"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))

    created = [item for item in results if isinstance(item, dict) and item["replayed"] is False]
    assert len(created) == 1
    remaining = [item for item in results if item is not created[0]]
    assert len(remaining) == 1
    if isinstance(remaining[0], dict):
        assert remaining[0] == {**created[0], "replayed": True}
    else:
        assert isinstance(remaining[0], Timeout)
    assert _seal(unknown_run) == {**created[0], "replayed": True}
    assert _count(unknown_run["db"]) == 1 and _history(unknown_run["db"]) == before


def test_second_stopped_service_check_failure_rolls_back_everything(unknown_run, monkeypatch):
    original = run_seals._require_stopped_service
    calls = []

    def fail_second_check(service, root):
        original(service, root)
        calls.append(service)
        if len(calls) == 2:
            raise ValueError("合成服务在事务内复查时已不再停止")

    monkeypatch.setattr(run_seals, "_require_stopped_service", fail_second_check)
    before = _history(unknown_run["db"])
    database_bytes = unknown_run["db"].read_bytes()
    with pytest.raises(ValueError, match="事务内复查"):
        _seal(unknown_run)
    assert calls == ["synthetic-mangrove.service"] * 2
    assert _count(unknown_run["db"]) == 0 and _history(unknown_run["db"]) == before
    assert unknown_run["db"].read_bytes() == database_bytes


@pytest.mark.parametrize("overrides", [
    {"approval_reference": ""}, {"approval_reference": "   "},
    {"idempotency_key": ""}, {"idempotency_key": "   "},
])
def test_missing_specific_approval_or_idempotency_key_cannot_seal(unknown_run, overrides):
    with pytest.raises(ValueError):
        _seal(unknown_run, **overrides)
    assert _count(unknown_run["db"]) == 0 and unknown_run["stopped_calls"] == []


@pytest.mark.parametrize("mutation", ["missing-proof", "invalid-json", "missing-relay", "not-stopped", "evidence-path-escape"])
def test_incomplete_or_redirected_stop_proof_cannot_seal(unknown_run, mutation):
    if mutation == "missing-proof":
        unknown_run["proof_path"].unlink()
    elif mutation == "invalid-json":
        unknown_run["proof_path"].write_text("{", encoding="utf-8")
    else:
        if mutation == "missing-relay":
            del unknown_run["proof"]["checks"]["relay"]
        elif mutation == "not-stopped":
            unknown_run["proof"]["checks"]["executor"]["state"] = "unknown"
        else:
            unknown_run["proof"]["checks"]["resources"]["file"] = "../outside.json"
        _write_json(unknown_run["proof_path"], unknown_run["proof"])
    with pytest.raises((ValueError, OSError)):
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0


def test_old_approval_cannot_follow_new_matching_account_generation(unknown_run):
    with closing(sqlite3.connect(unknown_run["db"])) as conn, conn:
        conn.execute("UPDATE users SET execution_generation=3 WHERE user_id='owner'")
        conn.execute("UPDATE account_execution_bindings SET generation=3")
    with pytest.raises(ValueError):
        _seal(unknown_run)
    assert _count(unknown_run["db"]) == 0


def test_same_key_cannot_replay_a_changed_proof_document(unknown_run):
    original = _seal(unknown_run)
    unknown_run["proof"]["request_id"] = "d" * 32
    _write_json(unknown_run["proof_path"], unknown_run["proof"])
    before = _history(unknown_run["db"])

    with pytest.raises(ValueError, match="冲突"):
        _seal(unknown_run)

    with closing(sqlite3.connect(unknown_run["db"])) as conn:
        assert run_seals.is_valid_run_seal(conn, "owner", "task", 1)
        assert conn.execute("SELECT request_sha256 FROM runtime_run_seals").fetchone()[0] == original["request_sha256"]
    assert _count(unknown_run["db"]) == 1 and _history(unknown_run["db"]) == before
