"""非特权调用方只信任固定 root 核验器；不读取真实服务或回执。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from src import service_identity_window as windows


def test_installed_helper_digest_matches_exact_lf_source():
    source = Path(__file__).resolve().parents[1] / "scripts" / "runtime_service_identity.py"
    raw = source.read_bytes()
    assert b"\r" not in raw and hashlib.sha256(raw).hexdigest() == windows.HELPER_SHA256


@pytest.fixture
def window_case(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    database = runtime / "webui.db"
    database.write_bytes(b"synthetic database identity only")
    monkeypatch.chdir(candidate)
    monkeypatch.setattr(windows, "sys", SimpleNamespace(platform="linux"))
    info = database.stat()
    monkeypatch.setattr(windows.os, "geteuid", lambda: info.st_uid, raising=False)
    now = datetime.now(timezone.utc).isoformat()
    ref = {"version": 1, "nonce": "a" * 32, "receipt_sha256": "b" * 64,
           "manifest_sha256": "c" * 64, "approval_reference": "synthetic-issue-244", "captured_at": now}
    response = {"version": 1, "ok": True, "window_ref": ref, "service": "mangrove.service",
                "runtime_root": str(runtime.resolve()), "candidate_root": str(candidate.resolve()),
                "service_uid": info.st_uid, "database_identity": {"canonical_path": str(database.resolve()),
                    "dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid, "mode": info.st_mode},
                "verified_at": now, "boot_id": "synthetic-boot", "current_state": "stopped_masked",
                "expires_monotonic_ns": windows.time.monotonic_ns() + 600_000_000_000}
    calls = []
    trust = []
    monkeypatch.setattr(windows, "_trusted_helper", lambda: trust.append(True))

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(response), returncode=0)

    monkeypatch.setattr(windows.subprocess, "run", run)
    window = windows.ServiceIdentityWindow(tmp_path / "root-manifest.json", "c" * 64, "a" * 32)
    target = {"owner_id": "owner", "task_id": "task", "revision": 1, "run_id": "synthetic-run",
              "runtime_identity_sha256": "d" * 64, "snapshot_sha256": "e" * 64, "request_id": "f" * 32}
    kwargs = {"service": "mangrove.service", "runtime_root": runtime, "database": database,
              "approval_reference": "synthetic-issue-244", "target": target}
    return SimpleNamespace(window=window, kwargs=kwargs, response=response, calls=calls, trust=trust)


def test_fixed_privileged_verify_binds_local_database_candidate_and_target(window_case):
    case = window_case
    result = windows.verify_service_window(case.window, **case.kwargs)
    assert result.window_ref == case.response["window_ref"]
    result.require_fresh(datetime.now(timezone.utc))
    assert case.trust == [True] and len(case.calls) == 1
    command, options = case.calls[0]
    assert command[:7] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-B", str(windows.HELPER_PATH)]
    assert command[7] == "verify" and "--request-id" in command and "--approval-reference" in command
    assert "--database" not in command and "--runtime-root" not in command
    assert options["timeout"] == 10 and options["check"] is True and options["encoding"] == "utf-8"


@pytest.mark.parametrize("field,value", [
    ("ok", False), ("current_state", "identity_captured"), ("service", "other.service"),
    ("runtime_root", "/other"), ("candidate_root", "/other"), ("service_uid", -1),
    ("expires_monotonic_ns", 0), ("verified_at", "2020-01-01T00:00:00+00:00"),
])
def test_mismatched_or_stale_privileged_result_is_rejected(window_case, field, value):
    window_case.response[field] = value
    with pytest.raises(ValueError):
        windows.verify_service_window(window_case.window, **window_case.kwargs)
    assert len(window_case.calls) == 1


def test_replaced_database_inode_is_rejected(window_case):
    case = window_case
    case.response["database_identity"]["ino"] += 1
    with pytest.raises(ValueError):
        windows.verify_service_window(case.window, **case.kwargs)
    assert len(case.calls) == 1


@pytest.mark.parametrize("field", ["nonce", "manifest_sha256", "approval_reference"])
def test_reference_cannot_be_swapped(window_case, field):
    window_case.response["window_ref"][field] = "0" * 64
    with pytest.raises(ValueError):
        windows.verify_service_window(window_case.window, **window_case.kwargs)
    assert len(window_case.calls) == 1


def test_untrusted_helper_is_rejected_before_sudo(window_case, monkeypatch):
    def reject():
        raise ValueError("synthetic untrusted helper")

    monkeypatch.setattr(windows, "_trusted_helper", reject)
    with pytest.raises(ValueError):
        windows.verify_service_window(window_case.window, **window_case.kwargs)
    assert window_case.calls == []


@pytest.mark.parametrize("uid,mode", [(1000, stat.S_IFREG | 0o755), (0, stat.S_IFREG | 0o775)])
def test_non_root_or_writable_helper_path_is_untrusted(uid, mode):
    path = SimpleNamespace(parents=(), lstat=lambda: SimpleNamespace(st_uid=uid, st_mode=mode), is_file=lambda: True)
    with pytest.raises(ValueError):
        windows._root_owned_path(path)


def test_root_owned_python_symlink_is_allowed_only_with_protected_target():
    target = SimpleNamespace(parents=(), lstat=lambda: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o755), is_file=lambda: True)
    link = SimpleNamespace(parents=(), lstat=lambda: SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777),
                           resolve=lambda strict: target, readlink=lambda: target, is_file=lambda: True)
    target.resolve = lambda strict: target
    target.is_absolute = lambda: True
    windows._root_owned_path(link, allow_symlinks=True)
    with pytest.raises(ValueError):
        windows._root_owned_path(link)
    target.lstat = lambda: SimpleNamespace(st_uid=1000, st_mode=stat.S_IFREG | 0o755)
    with pytest.raises(ValueError):
        windows._root_owned_path(link, allow_symlinks=True)


def test_safe_final_python_does_not_hide_untrusted_intermediate_link():
    target = SimpleNamespace(parents=(), lstat=lambda: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o755), is_file=lambda: True)
    target.resolve = lambda strict: target
    target.is_absolute = lambda: True
    middle = SimpleNamespace(parents=(), lstat=lambda: SimpleNamespace(st_uid=1000, st_mode=stat.S_IFLNK | 0o777),
                             is_absolute=lambda: True, resolve=lambda strict: target, readlink=lambda: target)
    entry = SimpleNamespace(parents=(), lstat=lambda: SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777),
                            resolve=lambda strict: target, readlink=lambda: middle, is_file=lambda: True)
    with pytest.raises(ValueError):
        windows._root_owned_path(entry, allow_symlinks=True)


def test_root_owned_helper_still_requires_exact_reviewed_bytes(tmp_path, monkeypatch):
    source = tmp_path / "runtime_service_identity.py"
    source.write_text("# 未审核的不同原件\n", encoding="utf-8")
    monkeypatch.setattr(windows, "HELPER_PATH", source)
    monkeypatch.setattr(windows, "HELPER_SHA256", hashlib.sha256(b"reviewed exact source").hexdigest())
    monkeypatch.setattr(windows, "_root_owned_path", lambda *args, **kwargs: None)
    with pytest.raises(ValueError):
        windows._trusted_helper()
