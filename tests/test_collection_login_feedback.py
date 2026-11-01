"""采集登录失败向客户说明原因，同时保留机器事实和未知请求门。"""
import asyncio
import json
from pathlib import Path

import pytest

from src.collectors.base import CollectResult
from src.conductor import bank_benefits
from src.conductor import evidence_collection as flow
from src.conductor.bank_benefits_export import export_snapshot
from src.conductor.task_spec import TaskSpec


@pytest.fixture
def isolated_collection(monkeypatch, tmp_path):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow.settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(flow, "execution_owner", lambda: "synthetic-login-owner")
    monkeypatch.setattr(flow, "_xhs_browser_profile", lambda platform: None)
    progress = []
    monkeypatch.setattr(flow, "emit_progress", lambda node, status, summary, **details: progress.append(summary))
    state = {"task_id": "synthetic-login-feedback", "task_spec": TaskSpec(
        intent="采集5条光大银行私行权益", bank_benefits={"source_evidence": "小红书",
            "banks": [{"name": "光大银行", "keyword": "光大银行私行权益"}], "target_count": 5})}
    return state, progress


@pytest.mark.parametrize("source", ["platform", "personal"])
def test_login_failure_reply_and_progress_keep_specific_maintenance_guidance(monkeypatch, isolated_collection, source):
    from src.config import user_ctx
    state, progress = isolated_collection
    monkeypatch.setattr(user_ctx, "get_user_override", lambda name: "synthetic-login" if source == "personal" else None)
    message = ("小红书登录尚未确认，请由超级管理员维护平台采集账号并重新验证" if source == "platform"
               else "小红书登录尚未确认，请在我的采集账号维护本人的登录信息并重新验证")

    async def collect(self, spec):
        return CollectResult(False, "mediacrawler", message=message, coverage={
            "failure": {"reason": "login_interaction_required", "next_action": "reauthenticate_selected_account",
                        "message": message, "rejection_confirmed": False}})
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    result = export_snapshot({**state, **collected})
    assert message in result["reply"]
    assert any(message in summary for summary in progress)
    assert "collection_failed" not in result["reply"]
    assert "0/5" in result["reply"]
    assert result["collection_outcome"]["candidate_counts"]["found"] == 0
    assert result["collection_outcome"]["target_complete"] is False
    snapshot = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
    group = snapshot["banks"][0]
    assert group["stop_reason"] == "collection_failed"
    assert group["batches"][0]["coverage"]["failure"]["reason"] == "login_interaction_required"
    assert group["batches"][0]["coverage"]["failure"]["rejection_confirmed"] is False


@pytest.mark.parametrize("search_started,expected", [
    (False, "未确认登录成功，搜索尚未开始"),
    (True, "搜索已发出，结果尚未确认"),
])
def test_unknown_discovery_reply_uses_recorded_stage_without_authorizing_replay(monkeypatch, isolated_collection, search_started, expected):
    from src.collectors.social_media_collector import _DIAGNOSTIC_SINK
    state, progress = isolated_collection
    calls = []

    async def collect(self, spec):
        calls.append(spec)
        _DIAGNOSTIC_SINK.get()({"browser_started": True, "login_detected": search_started,
            "search_started": search_started, "search_list_received": False,
            "runtime_phase": "search" if search_started else "login_check", "login_mode": "qrcode"})
        raise asyncio.TimeoutError
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    result = export_snapshot({**state, **collected})
    assert expected in result["reply"]
    assert any(expected in summary for summary in progress)
    assert "discovery_outcome_unknown" not in result["reply"]
    assert "Cookie过期" not in result["reply"] and "登录失效" not in result["reply"]
    group = collected["bank_collection"]["banks"][0]
    assert group["stop_reason"] == "discovery_outcome_unknown" and group["discovery_inflight"] is True
    assert group["candidate_attempted"] == 20
    assert result["collection_outcome"]["selected_count"] == 0
    assert result["collection_outcome"]["target_complete"] is False
    repeated = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls) == 1
    assert repeated["bank_collection"]["banks"][0]["discovery_inflight"] is True


def test_discovery_diagnostic_is_not_borrowed_by_another_group(monkeypatch, isolated_collection):
    from src.collectors.social_media_collector import _DIAGNOSTIC_SINK
    from src.conductor.collection_recovery import progress_location
    state, progress = isolated_collection
    scope = state["task_spec"].bank_benefits
    state["task_spec"] = state["task_spec"].model_copy(update={"bank_benefits": scope.model_copy(update={
        "banks": [*scope.banks, scope.banks[0].model_copy(update={"name": "另一银行", "keyword": "另一银行权益"})]})})
    calls = []

    async def collect(self, spec):
        calls.append(spec)
        if len(calls) == 1:
            _DIAGNOSTIC_SINK.get()({"browser_started": True, "login_detected": False,
                "search_started": False, "runtime_phase": "login_check", "login_mode": "qrcode"})
        raise asyncio.TimeoutError
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls) == 2
    assert any("未确认登录成功" in summary for summary in progress)
    assert "未确认登录成功，搜索尚未开始" in collected["collector_notes"][0]
    assert "登录" not in collected["collector_notes"][1]
    assert "候选检索结果尚未确认" in collected["collector_notes"][1]
    store, key = progress_location("synthetic-login-owner", state["task_id"])
    diagnostic = store.read_json_if_exists(key, "progress.json")["last_collection_diagnostic"]
    assert diagnostic == {"phase": "discovery", "group_name": "另一银行"}
    assert not {"browser_started", "login_detected", "search_started", "search_list_received"}.intersection(diagnostic)
    assert all("last_collection_diagnostic" not in group for group in collected["bank_collection"]["banks"])


def test_interactive_login_failure_stops_remaining_groups_without_claiming_rejection(monkeypatch, isolated_collection):
    state, _ = isolated_collection
    scope = state["task_spec"].bank_benefits
    state["task_spec"] = state["task_spec"].model_copy(update={"bank_benefits": scope.model_copy(update={
        "banks": [*scope.banks, scope.banks[0].model_copy(update={"name": "另一银行", "keyword": "另一银行权益"})]})})
    calls = []

    async def collect(self, spec):
        calls.append(spec)
        return CollectResult(False, "mediacrawler", message="请先维护本次实际使用的采集账号", coverage={
            "failure": {"reason": "login_interaction_required", "rejection_confirmed": False}})
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls) == 1
    first, remaining = collected["bank_collection"]["banks"]
    assert first["batches"][0]["coverage"]["failure"]["rejection_confirmed"] is False
    assert first["candidate_attempted"] == 20
    assert remaining["candidate_attempted"] == 0 and remaining["done"] is False
    assert "本组尚未开始采集" in collected["collector_notes"][1]


@pytest.mark.parametrize("reason,message", [
    ("login_interaction_required", "Authorization: Bearer synthetic-placeholder"),
    ("unexpected_failure", "raw_service_error"),
])
def test_failure_reply_does_not_expose_credentials_or_unrecognized_service_output(monkeypatch, isolated_collection, reason, message):
    state, progress = isolated_collection

    async def collect(self, spec):
        return CollectResult(False, "mediacrawler", message=message, coverage={"failure": {"reason": reason}})
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    result = export_snapshot({**state, **collected})
    assert message not in result["reply"]
    assert all(message not in summary for summary in progress)
    assert "来源获取尚未完成" in result["reply"]


def test_unknown_discovery_without_diagnostic_has_no_fabricated_login_cause(monkeypatch, isolated_collection):
    state, _ = isolated_collection

    async def collect(self, spec):
        raise asyncio.TimeoutError
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    result = export_snapshot({**state, **collected})
    assert "候选检索结果尚未确认" in result["reply"]
    assert "discovery_outcome_unknown" not in result["reply"]
    assert "登录" not in result["reply"]


def test_next_page_without_diagnostic_does_not_reuse_previous_search_fact(monkeypatch, isolated_collection):
    from src.collectors.base import CollectedItem
    from src.collectors.social_media_collector import _DIAGNOSTIC_SINK
    state, progress = isolated_collection
    calls = []
    diagnostic = {"browser_started": True, "login_detected": True, "search_started": True,
                  "search_list_received": True, "runtime_phase": "list_received"}

    async def collect(self, spec):
        calls.append(spec.xhs_search_page)
        if len(calls) == 1:
            _DIAGNOSTIC_SINK.get()(diagnostic)
            return CollectResult(True, "mediacrawler", items=[CollectedItem(
                title="本次范围外的视频", metadata={"note_id": "synthetic-video", "note_type": "video"})],
                coverage={"runtime": diagnostic, "pages": [{"has_more": True}]})
        raise asyncio.TimeoutError
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
    result = export_snapshot({**state, **collected})
    assert calls == [1, 2]
    assert "候选检索结果尚未确认" in result["reply"]
    assert "搜索已发出" not in result["reply"] and "搜索已发出" not in progress[-1]
    group = collected["bank_collection"]["banks"][0]
    assert group["stop_reason"] == "discovery_outcome_unknown" and group["discovery_inflight"] is True
    assert group["batches"][0]["coverage"]["runtime"]["search_started"] is True


def test_cancelled_collection_reports_interruption_without_changing_machine_state(monkeypatch, isolated_collection):
    from src.collectors.social_media_collector import _DIAGNOSTIC_SINK
    from src.conductor.collection_recovery import progress_location
    state, progress = isolated_collection

    async def collect(self, spec):
        _DIAGNOSTIC_SINK.get()({"browser_started": True, "login_detected": False,
                              "search_started": False, "runtime_phase": "login_check"})
        raise asyncio.CancelledError
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert "本组处理已中断，结果仍待核对" in progress[-1]
    assert "检索限额" not in progress[-1] and "用户取消" not in progress[-1]
    store, key = progress_location("synthetic-login-owner", state["task_id"])
    group = store.read_json_if_exists(key, "progress.json")["groups"][0]
    assert group["stop_reason"] == "candidate_limit" and group["discovery_inflight"] is True
    assert group["candidate_attempted"] == 20 and group["done"] is False


@pytest.mark.parametrize("personal", [False, True])
def test_real_collector_login_message_survives_collection_and_export(monkeypatch, isolated_collection, personal):
    from contextlib import nullcontext
    from tests import test_xhs_login_diagnostics as login_fixture
    from src.collectors.social_media_collector import _xhs_browser_profile
    from src.config.user_ctx import user_overrides_context
    state, progress = isolated_collection
    fixture = login_fixture.LoginDiagnosticTests()
    fixture.setUp()
    try:
        (fixture.root / "media_platform/xhs/field.py").write_text(
            "from enum import Enum, IntEnum\nclass SearchSortType(str, Enum):\n    GENERAL = 'general'\n"
            "class SearchNoteType(IntEnum):\n    ALL = 0\n    VIDEO = 1\n    IMAGE = 2\n", encoding="utf-8")
        fixture.install_failure()
        monkeypatch.setattr(flow, "_xhs_browser_profile", _xhs_browser_profile)
        with user_overrides_context({"mc_cookie_xhs": "synthetic-personal"}) if personal else nullcontext():
            collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
        result = export_snapshot({**state, **collected})
        group = collected["bank_collection"]["banks"][0]
        assert group["batches"][0]["coverage"]["failure"]["reason"] == "login_interaction_required"
        assert group["message"] in result["reply"]
        assert any(group["message"] in summary for summary in progress)
        assert ("我的采集账号" if personal else "服务器采集浏览器的登录会话") in result["reply"]
        assert "synthetic-private-never-save" not in result["reply"]
        assert result["collection_outcome"]["target_complete"] is False
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("personal", [False, True])
def test_real_collector_timeout_keeps_login_stage_and_account_guidance(monkeypatch, isolated_collection, personal):
    from contextlib import nullcontext
    from unittest.mock import patch
    from tests import test_xhs_login_diagnostics as login_fixture
    from src.collectors.social_media_collector import _xhs_browser_profile
    from src.config.user_ctx import user_overrides_context
    state, progress = isolated_collection
    fixture = login_fixture.LoginDiagnosticTests()
    fixture.setUp()
    try:
        (fixture.root / "media_platform/xhs/field.py").write_text(
            "from enum import Enum, IntEnum\nclass SearchSortType(str, Enum):\n    GENERAL = 'general'\n"
            "class SearchNoteType(IntEnum):\n    ALL = 0\n    VIDEO = 1\n    IMAGE = 2\n", encoding="utf-8")
        fixture.install_failure(wait=True)
        monkeypatch.setattr(flow, "_xhs_browser_profile", _xhs_browser_profile)
        with patch.object(flow.settings, "collect_timeout_mediacrawler_seconds", 2):
            with user_overrides_context({"mc_cookie_xhs": "synthetic-personal"}) if personal else nullcontext():
                collected = asyncio.run(bank_benefits.collect_bank_benefits(state))
        result = export_snapshot({**state, **collected})
        group = collected["bank_collection"]["banks"][0]
        failure = group["batches"][0]["coverage"]["failure"]
        assert failure["reason"] == "collector_timeout" and failure.get("rejection_confirmed") is not True
        assert group["stop_reason"] == "collection_failed" and group["candidate_attempted"] == 20
        assert "未确认登录成功，搜索尚未开始" in result["reply"]
        assert ("我的采集账号" if personal else "平台采集账号") in result["reply"]
        assert any("未确认登录成功，搜索尚未开始" in summary for summary in progress)
        assert "synthetic-private-never-save" not in result["reply"]
        assert result["collection_outcome"]["target_complete"] is False
    finally:
        fixture.doCleanups()
