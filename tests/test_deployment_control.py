"""维护回执必须属于本次请求与当前进程，旧回执不得激活。"""
from __future__ import annotations

import json
import os
import time

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

import scripts.deployment_control as control_module
from scripts.deployment_control import checked_report, control, require_quiet
from src.services.deployment_drain import DeploymentGate
from src.services.deployment_runtime import collect_resource_blockers, publish_drain_report, write_private_json

_REAL_PROCESS_PROBE = control_module._require_running_process


@pytest.fixture(autouse=True)
def process_probe(monkeypatch):
    # 运维目标是 Linux；文件协议测试不向 Windows 进程发任何信号。
    probe = MagicMock()
    monkeypatch.setattr(control_module, "_require_running_process", probe)
    return probe


@pytest.mark.parametrize("platform,state,cwd_matches,allowed", [
    ("win32", "S", True, False), ("linux", "S", True, True),
    ("linux", "Z", True, False), ("linux", "S", False, False),
])
def test_process_probe_is_read_only_linux_and_matches_live_cwd(tmp_path, monkeypatch, platform, state, cwd_matches, allowed):
    # 调用保存的真实函数，/proc 本身以只读替身提供，不依赖 Windows 链接权限。
    proc = MagicMock()
    process = proc.__truediv__.return_value
    process.__truediv__.return_value.read_text.return_value = f"123 (python) {state} 1 2 3"
    process.__truediv__.return_value.resolve.return_value = tmp_path if cwd_matches else tmp_path / "other"
    monkeypatch.setattr(control_module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(control_module, "Path", lambda value: proc)
    if allowed:
        _REAL_PROCESS_PROBE(123, tmp_path)
    else:
        with pytest.raises(ValueError):
            _REAL_PROCESS_PROBE(123, tmp_path)
    assert proc.__truediv__.call_count == (0 if platform != "linux" else 1)


def report_for(tmp_path, state_dir, request_id, phase="draining"):
    return {
        "version": 1, "request_id": request_id, "phase": phase, "pid": os.getpid(),
        "process_token": "c" * 32, "runtime_root": str(tmp_path), "updated_at": time.time(),
        "quiet": True, "active": {}, "total_active": 0, "blockers": [], "error": None,
    }


def test_two_phase_control_requires_a_new_sealed_report(tmp_path):
    state_dir = tmp_path / "control"
    request_id = control("request", state_dir)["request_id"]
    args = dict(request_id=request_id, pid=os.getpid(), runtime_root=tmp_path)
    report = report_for(tmp_path, state_dir, request_id)
    write_private_json(state_dir / "status.json", report)
    with pytest.raises(ValueError):
        control("request", state_dir)
    assert control("seal", state_dir, **args)["requires_fresh_sealed_report"]
    with pytest.raises(ValueError):
        control("resume", state_dir, **args)
    report.update(phase="sealed", updated_at=time.time())
    write_private_json(state_dir / "status.json", report)
    assert control("resume", state_dir, **args)["phase"] == "open"


@pytest.mark.parametrize("field,value", [
    ("request_id", "d" * 32), ("phase", "sealed"), ("pid", 1),
    ("process_token", "bad"), ("updated_at", 0), ("updated_at", float("nan")),
    ("runtime_root", "/wrong-root"),
])
def test_stale_or_wrong_identity_never_passes(tmp_path, field, value):
    state_dir = tmp_path / "control"
    request_id = control("request", state_dir)["request_id"]
    report = report_for(tmp_path, state_dir, request_id)
    report[field] = value
    write_private_json(state_dir / "status.json", report)
    with pytest.raises(ValueError):
        checked_report(DeploymentGate(state_dir), request_id, os.getpid(), tmp_path)


@pytest.mark.parametrize("change", [
    {"quiet": False}, {"active": {"chat": 1}, "total_active": 1},
    {"blockers": [{"check_id": "unknown", "count": None}]}, {"error": "invalid"},
])
def test_unknown_and_active_block_sealing(change):
    report = {"quiet": True, "active": {}, "total_active": 0, "blockers": [], "error": None}
    report.update(change)
    with pytest.raises(ValueError):
        require_quiet(report)


def test_cancel_only_removes_own_draining_request(tmp_path):
    request_id = control("request", tmp_path)["request_id"]
    with pytest.raises(ValueError):
        control("cancel", tmp_path, request_id="d" * 32)
    assert control("cancel", tmp_path, request_id=request_id)["phase"] == "open"


def test_managed_resource_ledger_blocks_but_uploaded_names_do_not(tmp_path):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / ".creating").touch()
    assert collect_resource_blockers(tmp_path) == ()
    ledger = tmp_path / ".pi-lifecycle" / ("a" * 64)
    ledger.mkdir(parents=True)
    (ledger / "resources.json").write_text("{}", encoding="utf-8")
    assert collect_resource_blockers(tmp_path)[0].count == 1
    (ledger / "resources.json").unlink()
    host = tmp_path / "capability-hosts" / ("b" * 12)
    host.mkdir(parents=True)
    (host / ".creating").touch()
    assert collect_resource_blockers(tmp_path)[0].count == 1


def test_missing_resource_root_is_unknown(tmp_path):
    assert collect_resource_blockers(tmp_path / "missing")[0].count is None


@pytest.mark.asyncio
async def test_reporter_never_calls_database_while_execution_active(tmp_path, monkeypatch):
    import asyncio
    import src.services.deployment_runtime as module

    state_dir = tmp_path / "control"
    control("request", state_dir)
    gate = DeploymentGate(state_dir)
    lease = gate.try_acquire("relay", continuation=True)
    stopping = asyncio.Event()
    written = []

    def write(path, report):
        written.append(report)
        stopping.set()

    monkeypatch.setattr(module, "write_private_json", write)
    monkeypatch.setattr(module, "collect_persisted_blockers", lambda *a: pytest.fail("活动期间不应探查数据库"))
    await publish_drain_report(gate, webui_db=tmp_path / "unused.db", scheduler_db=tmp_path / "unused2.db", execution_root=tmp_path, runtime_root=tmp_path, stopping=stopping)
    assert written[0]["total_active"] == 1 and written[0]["quiet"] is False
    lease.release()


def test_readiness_is_immediately_unavailable_during_maintenance(tmp_path, monkeypatch):
    from src.api import main
    import src.services.deployment_drain as module

    control("request", tmp_path)
    monkeypatch.setattr(module, "_gate", DeploymentGate(tmp_path))
    response = main.readiness()
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert json.loads(response.body)["checks"][0]["summary"] == "deployment_maintenance"
    assert main.health()["ok"] is True
