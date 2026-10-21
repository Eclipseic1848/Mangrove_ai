"""本机封存工具与实际部署检查的合成纵切面，不接触真实服务。"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from tests.database_migration_helpers import migrated_profile_database
from tests.test_deployment_preflight import _insert
from tests.test_runtime_run_seals import run_seal_database_template, unknown_run


def test_plan_is_read_only_and_apply_requires_explicit_approval(unknown_run, monkeypatch, capsys):
    import scripts.seal_runtime_run as cli

    case = unknown_run
    before = hashlib.sha256(case["db"].read_bytes()).hexdigest()
    args = ["seal_runtime_run.py", "plan", "--database", str(case["db"]), "--owner-id", "owner", "--task-id", "task", "--revision", "1"]
    monkeypatch.setattr(cli.sys, "argv", args)
    assert cli.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["stop_confirmed"] is False and plan["snapshot_sha256"] == case["kwargs"]["expected_snapshot_sha256"]
    assert hashlib.sha256(case["db"].read_bytes()).hexdigest() == before
    assert case["stopped_calls"] == []
    monkeypatch.setattr(cli.sys, "argv", [args[0], "apply", *args[2:]])
    assert cli.main() == 1
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["ok"] is False
    assert hashlib.sha256(case["db"].read_bytes()).hexdigest() == before
    assert case["stopped_calls"] == []
    full = [args[0], "apply", *args[2:], "--run-id", case["kwargs"]["run_id"], "--actor-id", "operator",
            "--approval-reference", case["kwargs"]["approval_reference"], "--idempotency-key", "synthetic-seal-key",
            "--snapshot-sha256", plan["snapshot_sha256"], "--proof", str(case["proof_path"]),
            "--service", "synthetic-mangrove.service", "--runtime-root", str(case["runtime_root"])]
    monkeypatch.setattr(cli.sys, "argv", full)
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["sealed"] is True
    assert len(case["stopped_calls"]) == 2


@pytest.mark.parametrize("independent_blocker", [None, "account_cleanup", "source_reads"])
def test_real_seal_only_clears_its_own_unknown_projection(unknown_run, tmp_path, independent_blocker):
    from src.runtime_run_seals import seal_unknown_run
    from src.services.deployment_preflight import collect_persisted_blockers

    case = unknown_run
    scheduler = migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")
    before = {item.check_id for item in collect_persisted_blockers(case["db"], scheduler)}
    assert before == {"persisted.runtime_unknown", "persisted.workspace_unknown"}
    seal_unknown_run(case["db"], **case["kwargs"])
    if independent_blocker == "account_cleanup":
        with sqlite3.connect(case["db"]) as conn:
            conn.execute("UPDATE account_execution_bindings SET state='cleanup_failed' WHERE owner_user_id='owner'")
    elif independent_blocker == "source_reads":
        _insert(case["db"], "source_read_uses", state="active")
    after = {item.check_id for item in collect_persisted_blockers(case["db"], scheduler)}
    assert after == ({f"persisted.{independent_blocker}"} if independent_blocker else set())
    with sqlite3.connect(case["db"]) as conn:
        assert conn.execute("SELECT status,json_extract(failure_json,'$.error_code') FROM agentic_runtime_runs").fetchone() == ("failed", "MODEL_OUTCOME_UNKNOWN")
