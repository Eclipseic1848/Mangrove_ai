"""账号证明绑定实际凭证和环境，不持久化原值。"""
import hashlib
import json
import pytest
from src.config import cookie_identity as identity


@pytest.mark.parametrize("kind,status", [("account_id", "valid"), ("handle", "valid"), ("account_id", "invalid"), ("account_id", "unknown")])
def test_private_identity_requires_stable_verified_account(tmp_path, monkeypatch, kind, status):
    monkeypatch.setattr(identity.settings, "webui_db_path", str(tmp_path / "webui.db"))
    account = hashlib.sha256(b"synthetic-account").hexdigest()
    env = {"node": "synthetic-node", "proxy": "private-proxy"}
    identity.record_identity("mc_cookie_xhs", "private-cookie", env,
        {"status": status, "account_ref_kind": kind, "account_ref": account})
    proof = identity.read_identity("mc_cookie_xhs", "private-cookie", env, max_age=600)
    assert bool(proof) is (kind == "account_id" and status == "valid")
    assert identity.read_identity("mc_cookie_xhs", "other-cookie", env) is None
    assert identity.read_identity("mc_cookie_xhs", "private-cookie", {**env, "node": "other"}) is None
    serialized = next(tmp_path.rglob("identity.json")).read_text(encoding="utf-8")
    assert all(value not in serialized for value in ("private-cookie", "private-proxy", "synthetic-account"))
    identity.record_identity("mc_cookie_xhs", "private-cookie", env, {})
    assert identity.read_identity("mc_cookie_xhs", "private-cookie", env) is None


def test_identity_expiry_future_and_corruption_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(identity.settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(identity.time, "time", lambda: 1000)
    identity.record_identity("mc_cookie_xhs", "synthetic", {},
        {"status": "valid", "account_ref_kind": "account_id", "account_ref": "a" * 64})
    monkeypatch.setattr(identity.time, "time", lambda: 1601)
    assert identity.read_identity("mc_cookie_xhs", "synthetic", {}, max_age=600) is None
    assert identity.read_identity("mc_cookie_xhs", "synthetic", {}) is not None
    monkeypatch.setattr(identity.time, "time", lambda: 999)
    assert identity.read_identity("mc_cookie_xhs", "synthetic", {}) is None
    next(tmp_path.rglob("identity.json")).write_text("broken", encoding="utf-8")
    assert identity.read_identity("mc_cookie_xhs", "synthetic", {}) is None


@pytest.mark.parametrize("status", ["invalid", "unknown", "cancelled"])
def test_failed_probe_persistence_never_leaves_old_valid_proof(tmp_path, monkeypatch, status):
    import asyncio
    from src.collectors.social_media_collector import SocialMediaCollector
    from src.collectors.base import CollectResult
    from src.conductor.task_spec import TaskSpec
    from src.config.cookie_probe_binding import capture_probe_environment
    from src.config.user_ctx import user_overrides_context
    monkeypatch.setattr(identity.settings, "webui_db_path", str(tmp_path / "webui.db"))
    env = capture_probe_environment()
    identity.record_identity("mc_cookie_xhs", "synthetic", env,
        {"status": "valid", "account_ref_kind": "account_id", "account_ref": "a" * 64})
    async def probe(*args, **kwargs):
        assert identity.read_identity("mc_cookie_xhs", "synthetic", env) is None
        if status == "cancelled":
            raise asyncio.CancelledError()
        return CollectResult(status == "unknown", "synthetic", authentication={"status": status})
    def cannot_write(*args, **kwargs):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(SocialMediaCollector, "collect", probe)
    monkeypatch.setattr(identity, "record_identity", cannot_write)
    with user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        operation = SocialMediaCollector().verify_cookie(TaskSpec(intent="验证", platforms=["小红书"]))
        if status == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(operation)
        else:
            asyncio.run(operation)
    assert identity.read_identity("mc_cookie_xhs", "synthetic", env) is None
    assert not list(tmp_path.rglob("identity.json"))


def test_revocation_failure_is_persistent_across_processes(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.setattr(identity.settings, "webui_db_path", str(tmp_path / "webui.db"))
    identity.record_identity("mc_cookie_xhs", "synthetic", {},
        {"status": "valid", "account_ref_kind": "account_id", "account_ref": "a" * 64})
    unlink = Path.unlink
    def fail_old_proof(path, *args, **kwargs):
        if path.name == "identity.json":
            raise OSError("synthetic denied delete")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_old_proof)
    with pytest.raises(OSError):
        with identity.identity_verification("mc_cookie_xhs", "synthetic", {}):
            pytest.fail("撤销失败不得运行新探针")
    assert list(tmp_path.rglob("identity.json"))
    code = "from src.config.cookie_identity import settings, read_identity; import sys; settings.webui_db_path=sys.argv[1]; assert read_identity('mc_cookie_xhs','synthetic',{}) is None"
    subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tmp_path / "webui.db")], check=True, capture_output=True, timeout=30)


def test_concurrent_verification_lock_rejects_old_proof(tmp_path, monkeypatch):
    from filelock import FileLock
    monkeypatch.setattr(identity.settings, "webui_db_path", str(tmp_path / "webui.db"))
    identity.record_identity("mc_cookie_xhs", "synthetic", {},
        {"status": "valid", "account_ref_kind": "account_id", "account_ref": "a" * 64})
    binding = identity.cookie_probe_binding("mc_cookie_xhs", "identity-v1", "synthetic", environment={})
    with FileLock(str(identity.identity_store().root / (binding + ".lock"))):
        assert identity.read_identity("mc_cookie_xhs", "synthetic", {}) is None
    assert identity.read_identity("mc_cookie_xhs", "synthetic", {}) is not None
