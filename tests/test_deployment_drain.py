"""部署排空门使用临时文件，不接触在用数据库或运行资源。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.services.deployment_drain import DeploymentGate


def request(gate, phase="draining"):
    gate.state_dir.mkdir(exist_ok=True)
    gate.request_path.write_text(
        json.dumps({"version": 1, "request_id": "a" * 32, "phase": phase}), encoding="utf-8"
    )


def test_open_does_not_create_control_directory(tmp_path):
    gate = DeploymentGate(tmp_path / "control")
    with gate.admission("workspace") as admitted:
        assert admitted
        assert gate.snapshot()["active"] == {"workspace": 1}
    assert gate.snapshot()["total_active"] == 0
    assert not gate.state_dir.exists()


def test_draining_keeps_existing_lease_and_allows_only_continuation(tmp_path):
    gate = DeploymentGate(tmp_path / "control")
    original = gate.try_acquire("workspace")
    request(gate)
    assert gate.try_acquire("workspace") is None
    relay = gate.try_acquire("relay", continuation=True)
    assert gate.snapshot()["total_active"] == 2
    request(gate, "sealed")
    assert gate.try_acquire("relay", continuation=True) is None
    relay.release()
    original.release()
    original.release()
    assert gate.snapshot()["total_active"] == 0
    gate.request_path.unlink()
    assert not gate.is_draining()


@pytest.mark.parametrize("content", [
    b"", b"{", b"[]", b"x" * 4097,
    b'{"version":true,"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","phase":"draining"}',
    b'{"version":1,"request_id":"bad","phase":"draining"}',
    b'{"version":1,"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","phase":"open"}',
    b'{"version":1,"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","phase":"draining","extra":1}',
    b'\xff',
])
def test_bad_marker_fails_closed_without_breaking_existing_cleanup(tmp_path, content):
    gate = DeploymentGate(tmp_path)
    gate.request_path.write_bytes(content)
    assert gate.try_acquire("workspace") is None
    assert gate.snapshot()["phase"] == "invalid"
    with gate.admission("cleanup", continuation=True) as admitted:
        assert admitted
    assert gate.snapshot()["total_active"] == 0


def test_unreadable_marker_is_not_open(tmp_path, monkeypatch):
    gate = DeploymentGate(tmp_path)
    monkeypatch.setattr(type(gate.request_path), "open", lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
    assert gate.is_draining()


def test_exception_and_concurrent_release_do_not_lose_activity(tmp_path):
    gate = DeploymentGate(tmp_path)
    with pytest.raises(RuntimeError), gate.admission("model") as admitted:
        assert admitted
        raise RuntimeError("synthetic")
    assert gate.snapshot()["total_active"] == 0
    leases = [gate.try_acquire("worker") for _ in range(100)]
    request(gate, "sealed")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda lease: (lease.release(), lease.release()), leases))
    assert gate.snapshot()["total_active"] == 0


def test_kind_is_an_internal_bounded_label(tmp_path):
    with pytest.raises(ValueError), DeploymentGate(tmp_path).admission("owner/private/path"):
        pass
