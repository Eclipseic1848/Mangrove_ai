"""显式同账号恢复复用原进度；账号或环境不符时原文件不变。"""
import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
import hashlib
import json

import pytest

from src.config import cookie_identity, user_ctx
from src.config.cookie_probe_binding import capture_probe_environment
from src.collectors.base import CollectedItem, CollectResult
from src.conductor import evidence_collection as flow
from src.conductor.collection_recovery import load_recovery, progress_location, recovery_request
from src.conductor.task_spec import TaskSpec
from tests.test_evidence_collection_flow import spec_for


@pytest.mark.parametrize("change", ["same", "account", "node", "missing_initial", "missing_current", "query", "source", "legacy_same", "legacy_constraint",
    "unknown_challenge", "missing_rejection_proof", "non_boolean_rejection_proof", "confirmed_challenge", "interrupted_unknown_challenge"])
def test_explicit_identity_recovery_preserves_or_rejects_progress(tmp_path, monkeypatch, change):
    monkeypatch.setattr(flow.settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "mc_cookie_xhs", "new-cookie")
    monkeypatch.setattr(flow, "execution_owner", lambda: "owner")
    spec = spec_for("设备维护", initial_candidates=1, candidate_limit=2 if change == "interrupted_unknown_challenge" else 1)
    state = {"task_id": "task", "task_spec": spec, "user_input": spec.intent}
    if change == "interrupted_unknown_challenge":
        state["session_id"] = "synthetic-conversation"
    environment = capture_probe_environment()
    account = hashlib.sha256(b"account-a").hexdigest()
    if change != "missing_initial":
        cookie_identity.record_identity("mc_cookie_xhs", "old-cookie", environment,
            {"status": "valid", "account_ref_kind": "account_id", "account_ref": account})
    calls = []
    async def collect(self, request):
        calls.append(user_ctx.effective("mc_cookie_xhs"))
        if len(calls) == 1:
            failure = {"reason": "login_required", "rejection_confirmed": True,
                "diagnostic_hint": {"http_status": 401, "platform_code": None}}
            if change in {"unknown_challenge", "non_boolean_rejection_proof", "interrupted_unknown_challenge"}:
                failure = {"reason": "challenge_required", "rejection_confirmed": False if change != "non_boolean_rejection_proof" else 1,
                    "diagnostic_hint": {"exit_code": 1, "http_status": None, "isolated_exception_type": "UnknownError", "isolated_cause_type": None}}
            elif change == "missing_rejection_proof":
                failure = {"reason": "login_required"}
            elif change == "confirmed_challenge":
                failure = {"reason": "challenge_required", "rejection_confirmed": True,
                    "diagnostic_hint": {"http_status": 461, "platform_code": None}}
            return CollectResult(False, "synthetic", coverage={"failure": failure,
                "operations": {"search": {"status": "failed" if failure.get("rejection_confirmed") is True else "unknown", "reason": failure["reason"]}}})
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
    if change == "interrupted_unknown_challenge":
        saved["groups"][0]["done"] = False
        store.write_json(key, "progress.json", saved)
    original = path.read_bytes()
    if change in {"unknown_challenge", "missing_rejection_proof", "non_boolean_rejection_proof", "interrupted_unknown_challenge"}:
        with pytest.raises(ValueError, match="结果尚未确认"):
            load_recovery("owner", "task")
        assert path.read_bytes() == original and calls == ["old-cookie"]
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
            if change not in {"same", "legacy_same", "confirmed_challenge"}:
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


@pytest.mark.parametrize("kind", ["old_bank", "old_bank_v4", "missing_bank", "other_bank", "current_bank",
    "previous_bank", "previous_bank_connection", "previous_bank_spec", "previous_bank_credential", "generic"])
@pytest.mark.parametrize("explicit", [False, True])
def test_changed_bank_rules_reject_old_progress_without_replaying_or_rewriting(monkeypatch, tmp_path, kind, explicit):
    from src.conductor import bank_benefits
    monkeypatch.setattr(flow.settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow, "execution_owner", lambda: "owner")
    bank = kind != "generic"
    spec = TaskSpec(intent="采集光大银行私人银行权益", bank_benefits={
        "source_evidence": "小红书", "banks": [{"name": "光大银行", "keyword": "光大银行私人银行权益"}],
        "audience": "私人银行", "target_count": 2, "frozen_at": "2026-10-13T12:00:00+08:00"}) if bank else spec_for("设备维护", target_count=2)
    scope = spec.bank_benefits if bank else spec.evidence_collection
    text = "光大银行今年生日礼体验" if kind == "old_bank" else "光大银行私人银行客户可免费停车" if bank else "设备维护规则"
    calls = []
    async def collect(self, request):
        calls.append("collect")
        return CollectResult(True, "synthetic", items=[CollectedItem(content=text, metadata={
            "note_id": "one", "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00"})],
            coverage={"pages": [{"has_more": False}]})
    async def assess(*args):
        calls.append("model")
        return {"relevant": True, "tag_audience_mismatch": False, "benefits": [], "records": []}
    async def forbidden(*args):
        raise AssertionError("无图片不能调用OCR")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    state = {"task_id": "version", "task_spec": spec, "user_input": spec.intent}
    def run():
        return asyncio.run(flow.collect_evidence(state, scope=scope, queries=scope.banks if bank else scope.queries,
            assess=assess, exclude=bank_benefits._exclusion if bank else flow.exclude_topic,
            read_images=forbidden, legacy=bank))
    with user_ctx.user_overrides_context({"mc_cookie_xhs": "same-cookie"}):
        cookie_identity.record_identity("mc_cookie_xhs", "same-cookie", capture_probe_environment(),
            {"status": "valid", "account_ref_kind": "account_id", "account_ref": "a" * 64})
        assert run()["collection_outcome"]["selected_count"] == 1
        store, key = progress_location("owner", "version")
        path = store.task_dir(key) / "progress.json"
        saved = json.loads(path.read_bytes())
        saved["groups"][0]["done"] = False
        if kind in {"old_bank", "old_bank_v4"}:
            # 合成旧版生日礼入选：未证明私人银行客群，禁止沿用旧资格或隐式重新判断。
            saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v4" if kind == "old_bank_v4" else "evidence-collection-v3"
            if kind == "old_bank_v4":
                # 旧版无效过期引用已降未知并丢失否定标记，不能沿用原入选状态。
                saved["candidates"][0]["assessment"]["tag_stale"] = None
                saved["candidates"][0]["assessment"].pop("invalid_negative_evidence", None)
            else:
                saved["candidates"][0]["assessment"]["tag_audience_mismatch"] = None
        elif kind == "missing_bank":
            saved["binding_fields"].pop("processing_version")
        elif kind == "other_bank":
            saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v999"
        elif kind.startswith("previous_bank"):
            # 旧任务保留冻结的 v5 策略，升级调度不能改写其绑定摘要。
            saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v5"
        saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        store.write_json(key, "progress.json", saved)
        original = path.read_bytes()
        raw_path = flow.ArtifactStore().resolve_path(saved["candidates"][0]["source_artifact"]["storage_path"])
        raw_bytes = raw_path.read_bytes()
        if kind == "previous_bank_connection":
            monkeypatch.setattr(flow, "_bound_chat_identity", ContextVar("changed_frozen_connection",
                default=("owner", "different-connection", 2, "cloud-model", "conversation", "run", 1)))
        elif kind == "previous_bank_spec":
            scope = scope.model_copy(update={"target_count": 3})
            spec = spec.model_copy(update={"bank_benefits": scope})
            state["task_spec"] = spec
        current_cookie = "new-cookie" if kind == "previous_bank_credential" else "same-cookie"
        if kind == "previous_bank_credential":
            cookie_identity.record_identity("mc_cookie_xhs", current_cookie, capture_probe_environment(),
                {"status": "valid", "account_ref_kind": "account_id", "account_ref": "a" * 64})
        calls.clear()
        with user_ctx.user_overrides_context({"mc_cookie_xhs": current_cookie}), recovery_request(saved["binding"], "b" * 32) if explicit else nullcontext():
            if kind in {"old_bank", "old_bank_v4", "missing_bank", "other_bank"}:
                with pytest.raises(ValueError, match="银行权益采集规则已变化"):
                    run()
                assert path.read_bytes() == original
            elif kind in {"previous_bank_connection", "previous_bank_spec"} or (kind == "previous_bank_credential" and not explicit):
                with pytest.raises(ValueError, match="恢复请求与冻结任务或进度不一致" if explicit else "拒绝复用"):
                    run()
                assert path.read_bytes() == original
            else:
                expected = "bank-benefits-collection-v5" if kind.startswith("previous_bank") else "bank-benefits-collection-v7" if bank else "evidence-collection-v3"
                assert saved["binding_fields"]["processing_version"] == expected
                result = run()
                assert result["collection_outcome"]["selected_count"] == 1
                updated = json.loads(path.read_bytes())
                assert updated["binding_fields"]["processing_version"] == expected
                if kind == "previous_bank_credential":
                    assert updated["binding"] != saved["binding"]
                    attempt, = updated["recovery_attempts"]
                    assert attempt["from_binding"] == saved["binding"] and attempt["to_binding"] == updated["binding"]
                    assert updated["binding_fields"]["credential_binding"] != saved["binding_fields"]["credential_binding"]
                    assert {k: v for k, v in updated["binding_fields"].items() if k != "credential_binding"} == {
                        k: v for k, v in saved["binding_fields"].items() if k != "credential_binding"}
                else:
                    assert updated["binding"] == saved["binding"]
                    assert updated["binding_fields"] == saved["binding_fields"]
        assert calls == []
        assert raw_path.read_bytes() == raw_bytes
