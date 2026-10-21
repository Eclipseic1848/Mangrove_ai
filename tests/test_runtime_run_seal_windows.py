"""维护窗口集成只替换 OS 核验边界，保留真实事务和三份原始证据。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src import runtime_run_seals as seals
from src.service_identity_window import ServiceIdentityWindow, VerifiedServiceWindow
from tests.test_runtime_run_seals import _count, _history, _seal, _write_json, run_seal_database_template, unknown_run


_NATIVE_GATE = seals._require_stopped_service


@pytest.fixture
def masked_window(unknown_run, monkeypatch, tmp_path):
    import time

    case = unknown_run
    window = ServiceIdentityWindow(tmp_path / "root-manifest.json", "a" * 64, "b" * 32)
    ref = {"version": 1, "nonce": window.nonce, "manifest_sha256": window.manifest_sha256,
           "receipt_sha256": "d" * 64, "approval_reference": case["proof"]["approval_reference"],
           "captured_at": datetime.now(timezone.utc).isoformat()}
    current = [VerifiedServiceWindow(ref, time.monotonic_ns() + 600_000_000_000)]
    case["proof"]["window_ref"] = ref
    _write_json(case["proof_path"], case["proof"])
    state = {"MainPID": "0", "LoadState": "masked", "ActiveState": "inactive", "SubState": "dead",
             "UnitFileState": "masked-runtime", "User": "", "WorkingDirectory": ""}
    monkeypatch.setattr(seals, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(seals.os, "geteuid", lambda: case["db"].stat().st_uid, raising=False)
    monkeypatch.setattr(seals, "_service_user_uid", lambda user: -1)
    monkeypatch.setattr(seals, "_require_stopped_service", _NATIVE_GATE)
    monkeypatch.setattr(seals.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="\n".join(f"{k}={v}" for k, v in state.items())))
    attestations = []

    def verify(received, **kwargs):
        assert received.manifest == window.manifest and received.manifest_sha256 == window.manifest_sha256
        assert received.nonce == current[0].window_ref["nonce"]
        assert kwargs["service"] == case["kwargs"]["service"]
        assert kwargs["runtime_root"] == case["runtime_root"] and kwargs["database"] == case["db"]
        assert kwargs["approval_reference"] == case["proof"]["approval_reference"]
        assert kwargs["target"] == {key: case["proof"][key] for key in
            ("owner_id", "task_id", "revision", "run_id", "runtime_identity_sha256", "snapshot_sha256", "request_id")}
        attestations.append(kwargs)
        return current[0]

    monkeypatch.setattr(seals, "verify_service_window", verify, raising=False)
    return SimpleNamespace(case=case, window=window, current=current, state=state, attestations=attestations)


def test_empty_mask_identity_uses_bound_window_and_preserves_history(masked_window):
    case = masked_window.case
    before = _history(case["db"])
    result = _seal(case, service_window=masked_window.window)
    assert result["sealed"] and len(masked_window.attestations) == 2
    assert _count(case["db"]) == 1 and _history(case["db"]) == before
    with closing(sqlite3.connect(case["db"])) as conn:
        assert seals.is_valid_run_seal(conn, "owner", "task", 1)
        stored = json.loads(conn.execute("SELECT stop_proof_json FROM runtime_run_seals").fetchone()[0])
        assert stored == case["proof"]


def test_cli_forwards_complete_window_to_real_writer(masked_window, monkeypatch, capsys):
    import scripts.seal_runtime_run as cli

    case = masked_window.case
    monkeypatch.setattr(cli.sys, "argv", ["seal_runtime_run.py", "apply", "--database", str(case["db"]),
        "--owner-id", "owner", "--task-id", "task", "--revision", "1", "--run-id", case["kwargs"]["run_id"],
        "--actor-id", "operator", "--approval-reference", case["proof"]["approval_reference"],
        "--idempotency-key", case["kwargs"]["idempotency_key"], "--snapshot-sha256", case["kwargs"]["expected_snapshot_sha256"],
        "--proof", str(case["proof_path"]), "--service", case["kwargs"]["service"], "--runtime-root", str(case["runtime_root"]),
        "--service-window-manifest", str(masked_window.window.manifest),
        "--service-window-manifest-sha256", masked_window.window.manifest_sha256,
        "--service-window-nonce", masked_window.window.nonce])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["sealed"] is True
    assert len(masked_window.attestations) == 2 and _count(case["db"]) == 1


@pytest.mark.parametrize("mutation", ["missing-key", "partial-empty", "nonempty-conflict", "loaded"])
def test_window_never_overrides_missing_partial_or_conflicting_live_identity(masked_window, mutation):
    state = masked_window.state
    if mutation == "missing-key":
        state.pop("User")
    elif mutation == "partial-empty":
        state["User"] = "synthetic-wrong-user"
    elif mutation == "nonempty-conflict":
        state.update(User="synthetic-wrong-user", WorkingDirectory="/other")
    else:
        state["LoadState"] = "loaded"
    with pytest.raises((ValueError, KeyError)):
        _seal(masked_window.case, service_window=masked_window.window)
    assert _count(masked_window.case["db"]) == 0 and masked_window.attestations == []


@pytest.mark.parametrize("mutation", ["no-ref", "different-ref", "missing-resource"])
def test_current_window_does_not_replace_bound_run_proof(masked_window, mutation):
    case = masked_window.case
    if mutation == "no-ref":
        case["proof"].pop("window_ref")
    elif mutation == "different-ref":
        case["proof"]["window_ref"] = {**case["proof"]["window_ref"], "nonce": "e" * 32}
    else:
        (case["proof_path"].parent / case["proof"]["checks"]["resources"]["file"]).unlink()
    _write_json(case["proof_path"], case["proof"])
    before = _history(case["db"])
    with pytest.raises((ValueError, FileNotFoundError)):
        _seal(case, service_window=masked_window.window)
    assert _count(case["db"]) == 0 and _history(case["db"]) == before


def test_second_window_check_failure_rolls_back_without_history_changes(masked_window, monkeypatch):
    verify = seals.verify_service_window

    def fail_second(*args, **kwargs):
        if masked_window.attestations:
            raise ValueError("synthetic changed mask")
        return verify(*args, **kwargs)

    monkeypatch.setattr(seals, "verify_service_window", fail_second)
    before = _history(masked_window.case["db"])
    with pytest.raises(ValueError):
        _seal(masked_window.case, service_window=masked_window.window)
    assert _count(masked_window.case["db"]) == 0 and _history(masked_window.case["db"]) == before


def test_window_expiring_during_final_gate_cannot_insert(masked_window, monkeypatch):
    import src.service_identity_window as windows

    clock = [masked_window.current[0].expires_monotonic_ns - 1]
    verify = seals.verify_service_window

    def expire_second(*args, **kwargs):
        result = verify(*args, **kwargs)
        if len(masked_window.attestations) == 2:
            clock[0] += 2
        return result

    monkeypatch.setattr(seals, "verify_service_window", expire_second)
    monkeypatch.setattr(windows.time, "monotonic_ns", lambda: clock[0])
    with pytest.raises(ValueError):
        _seal(masked_window.case, service_window=masked_window.window)
    assert len(masked_window.attestations) == 2 and _count(masked_window.case["db"]) == 0


def test_new_current_window_replays_expired_old_reference_without_rewriting_audit(masked_window, monkeypatch):
    case = masked_window.case
    original = _seal(case, service_window=masked_window.window)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    replacement = {**masked_window.current[0].window_ref, "nonce": "f" * 32, "captured_at": future.isoformat()}
    masked_window.current[0] = VerifiedServiceWindow(replacement, masked_window.current[0].expires_monotonic_ns)
    masked_window.window = ServiceIdentityWindow(masked_window.window.manifest, masked_window.window.manifest_sha256, "f" * 32)
    monkeypatch.setattr(seals, "datetime", SimpleNamespace(now=lambda tz: future, fromisoformat=datetime.fromisoformat))
    before = case["db"].read_bytes()
    result = _seal(case, service_window=masked_window.window)
    assert result == {**original, "replayed": True} and case["db"].read_bytes() == before
    assert len(masked_window.attestations) == 3 and _count(case["db"]) == 1
    with closing(sqlite3.connect(case["db"])) as conn:
        assert seals.is_valid_run_seal(conn, "owner", "task", 1)
