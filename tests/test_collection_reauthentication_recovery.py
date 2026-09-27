"""显式同账号恢复复用原进度；账号或环境不符时原文件不变。"""
import asyncio
import hashlib
import json

import pytest

from src.config import cookie_identity, user_ctx
from src.config.cookie_probe_binding import capture_probe_environment
from src.collectors.base import CollectedItem, CollectResult
from src.conductor import evidence_collection as flow
from src.conductor.collection_recovery import progress_location, recovery_request
from tests.test_evidence_collection_flow import spec_for


@pytest.mark.parametrize("change", ["same", "account", "node", "missing_initial", "missing_current", "query", "source", "legacy_same", "legacy_constraint"])
def test_explicit_identity_recovery_preserves_or_rejects_progress(tmp_path, monkeypatch, change):
    monkeypatch.setattr(flow.settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "mc_cookie_xhs", "new-cookie")
    monkeypatch.setattr(flow, "execution_owner", lambda: "owner")
    spec = spec_for("设备维护", initial_candidates=1, candidate_limit=1)
    state = {"task_id": "task", "task_spec": spec, "user_input": spec.intent}
    environment = capture_probe_environment()
    account = hashlib.sha256(b"account-a").hexdigest()
    if change != "missing_initial":
        cookie_identity.record_identity("mc_cookie_xhs", "old-cookie", environment,
            {"status": "valid", "account_ref_kind": "account_id", "account_ref": account})
    calls = []
    async def collect(self, request):
        calls.append(user_ctx.effective("mc_cookie_xhs"))
        if len(calls) == 1:
            return CollectResult(False, "synthetic", coverage={"failure": {"reason": "login_required"}})
        return CollectResult(True, "synthetic", items=[CollectedItem(content="设备规则", metadata={"note_id": "one", "note_type": "normal"})])
    async def assess(*args):
        return {"relevant": True, "records": []}
    async def forbidden(*args):
        raise AssertionError("无图片不得调用OCR")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    def run():
        return asyncio.run(flow.collect_evidence(state, scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
            assess=assess, exclude=flow.exclude_topic, read_images=forbidden))
    with user_ctx.user_overrides_context({"mc_cookie_xhs": "old-cookie"}):
        run()
    store, key = progress_location("owner", "task")
    path = store.task_dir(key) / "progress.json"
    saved = json.loads(path.read_bytes())
    if change.startswith("legacy_"):
        saved["binding_fields"]["spec"]["evidence_collection"].pop("include_images")
        saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"],ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()
        store.write_json(key, "progress.json", saved)
    original = path.read_bytes()
    if change == "legacy_constraint":
        state["task_spec"] = spec.model_copy(update={"evidence_collection": spec.evidence_collection.model_copy(update={"include_images": False})})
    if change == "node":
        monkeypatch.setenv("SYNTHETIC_RECOVERY_NODE", "different")
    if change != "missing_current":
        cookie_identity.record_identity("mc_cookie_xhs", "new-cookie", capture_probe_environment(),
            {"status": "valid", "account_ref_kind": "account_id", "account_ref": "b" * 64 if change == "account" else account})
    if change == "query":
        state["user_input"] = "扩大范围"
    with user_ctx.user_overrides_context({} if change == "source" else {"mc_cookie_xhs": "new-cookie"}):
        with pytest.raises(ValueError, match="拒绝复用"):
            run()
        assert path.read_bytes() == original
        with recovery_request(saved["binding"], "a" * 32):
            if change not in {"same", "legacy_same"}:
                with pytest.raises(ValueError):
                    run()
                assert path.read_bytes() == original and calls == ["old-cookie"]
            else:
                result = run()
                assert result["collection_outcome"]["selected_count"] == 1
                assert calls == ["old-cookie", "new-cookie"]
                updated = json.loads(path.read_bytes())
                assert len(updated["recovery_attempts"]) == 1
                assert updated["groups"][0]["candidate_attempted"] == 1
                assert "old-cookie" not in path.read_text(encoding="utf-8")
                assert "new-cookie" not in path.read_text(encoding="utf-8")
                with pytest.raises(ValueError):
                    run()
                assert calls == ["old-cookie", "new-cookie"]
