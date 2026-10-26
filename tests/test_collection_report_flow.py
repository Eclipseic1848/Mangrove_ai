"""报告必须经过真实节点链，原始采集快照与数量门保持。"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.conductor import graph
from src.conductor.nodes import analyze as analyze_module
from src.conductor.nodes import checker as checker_module
from src.conductor.nodes import output as output_module
from src.conductor.nodes import planner as planner_module
from src.conductor.task_spec import AnalysisType, OutputFormat, TaskSpec
from src.config.settings import settings


def report_state(tmp_path, *, count=2, target=2, raw_only=False, reports=True):
    spec = TaskSpec.from_draft({"intent": "在小红书采集光大银行私行权益并生成分析报告", "platforms": ["小红书"],
        "bank_benefits": {"source_evidence": "小红书", "banks": [{"name": "光大银行", "keyword": "光大银行私行权益"}],
                          "audience": "私行", "target_count": target, "raw_only": raw_only},
        "analysis_type": "summary", "outputs": ["json", "report_md"] if reports else ["json"]})
    image = tmp_path / "report-task/images/frozen.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"frozen-image")
    notes = [{"group": "光大银行", "status": "selected", "reason": None, "title": f"私行权益{i}",
        "url": f"https://www.xiaohongshu.com/explore/n{i}", "content": f"光大银行私行客户可享权益{i}。",
        "metadata": {"note_id": f"n{i}"}, "images": [{"status": "recognized", "image_index": 1,
            "text": "礼宾接送每年2次。", "artifact_path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}],
        "assessment": {"benefits": [{"benefit_name": "礼宾接送", "quota": "每年2次", "evidence_status": "quoted_unreviewed",
            "evidence": {"benefit_name": {"source_id": "image:1", "quote": "礼宾接送"},
                         "quota": {"source_id": "image:1", "quote": "每年2次"}}}]}} for i in range(count)]
    snapshot = {"schema_version": "bank-benefits-v2", "query": spec.intent,
        "scope": spec.bank_benefits.model_dump(mode="json"), "banks": [{"name": "光大银行", "selected_count": count,
            "stop_reason": "target_reached" if count >= target else "candidate_limit"}], "candidates": notes}
    return {"task_spec": spec, "task_id": "report-task", "provider": "bound", "model": "test-model",
            "bank_collection": snapshot, "raw_dataset": notes, "cleaned_dataset": notes,
            "collector_used": "mediacrawler", "collector_notes": []}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(output_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "template_learning_enabled", False)
    monkeypatch.setattr(settings, "lesson_learning_enabled", False)
    monkeypatch.setattr(settings, "checkpoint_enabled", False)
    monkeypatch.setattr(analyze_module, "_select_system", lambda *a, **k: ("根据输入原件生成报告。", "builtin", None))
    monkeypatch.setattr(analyze_module, "lesson_for_analyze", lambda *a, **k: ("", None))
    monkeypatch.setattr(planner_module, "lesson_for_planner", lambda *a, **k: "")
    from src.api import auth
    monkeypatch.setattr(auth, "get_store", lambda: None)


@pytest.mark.parametrize("reports,raw_only", [(True, False), (False, False), (True, True)])
def test_planner_retains_frozen_report_choice(tmp_path, monkeypatch, reports, raw_only):
    original = report_state(tmp_path, reports=reports, raw_only=raw_only)["task_spec"]
    async def plan(*a, **k):
        return original.model_dump(mode="json"), None
    monkeypatch.setattr(planner_module, "_plan", plan)
    result = asyncio.run(planner_module.planner_node({"user_input": original.intent, "understanding": {}}))
    spec = result["task_spec"]
    assert spec.wants_report() is (reports and not raw_only)
    assert spec.analysis_type is (AnalysisType.SUMMARY if reports and not raw_only else AnalysisType.NONE)
    assert OutputFormat.JSON in spec.outputs


@pytest.mark.parametrize("reports,raw_only", [(True, False), (False, False), (True, True)])
def test_real_planner_entry_instructs_explicit_report_and_preserves_source_only_default(tmp_path, monkeypatch, reports, raw_only):
    draft = report_state(tmp_path, target=10, reports=reports, raw_only=raw_only)["task_spec"].model_dump(mode="json")
    prompt = "小红书采集10篇光大银行私人银行权益" + ("并生成分析报告" if reports else "")
    if raw_only:
        prompt += "，只保留原始资料，不分析"
    async def model(messages, **kwargs):
        system = messages[0]["content"]
        assert "明确要求分析报告时" in system
        assert "outputs=[\"report_md\", \"json\"]" in system
        assert "raw_only=true" in system and "默认不生成分析报告" in system
        assert "此场景整理数据和证据，不生成分析报告。" not in system
        assert prompt in messages[-1]["content"]
        return json.dumps(draft, ensure_ascii=False)
    monkeypatch.setattr(planner_module, "achat", model)
    result = asyncio.run(planner_module.planner_node({"user_input": prompt, "understanding": {}, "provider": "bound", "model": "test-model"}))
    assert not result.get("error") and not result.get("needs_clarification")
    assert result["task_spec"].wants_report() is (reports and not raw_only)
    assert result["task_spec"].bank_benefits.target_count == 10


@pytest.mark.parametrize("change,expected", [(None, "analyze"), ("insufficient", "output"), ("raw_only", "output"),
    ("no_report", "output"), ("duplicate", "output"), ("review_required", "output")])
def test_only_complete_independent_notes_enter_analysis(tmp_path, change, expected):
    state = report_state(tmp_path, count=1 if change == "insufficient" else 2,
                         raw_only=change == "raw_only", reports=change != "no_report")
    if change == "duplicate":
        state["bank_collection"]["candidates"][1]["metadata"]["note_id"] = "n0"
    if change == "review_required":
        state["bank_collection"]["candidates"][1]["status"] = "review_required"
    assert graph._route_after_collect(state) == expected


@pytest.mark.parametrize("recovery", [False, True])
def test_full_graph_analyzes_selected_evidence_checks_and_keeps_original_downloads(tmp_path, monkeypatch, recovery):
    state = report_state(tmp_path)
    original = deepcopy(state["bank_collection"])
    excluded = deepcopy(original["candidates"][0])
    excluded.update(status="excluded", reason="audience_mismatch", content="排除内容不可进入报告")
    state["bank_collection"]["candidates"].append(excluded)
    async def intent(value): return {"understanding": {}}
    async def planner(value): return {"task_spec": state["task_spec"]}
    async def collector(value): return {key: item for key, item in state.items() if key not in {"task_spec", "task_id", "provider", "model"}}
    async def report(messages, **kwargs):
        assert "礼宾接送每年2次" in messages[-1]["content"]
        assert "排除内容不可进入报告" not in messages[-1]["content"]
        assert "适用条件、期限和原文链接" in messages[-1]["content"]
        return "## 光大银行私人银行权益分析\n礼宾接送每年2次，条件及期限按原文核对。[1]"
    async def check(messages, **kwargs):
        assert "适用条件、期限和原文链接" in messages[-1]["content"]
        return json.dumps({"score": 90, "passed": True, "issues": [], "summary": "已核对全部原件",
            "source_checks": [{"source_id": i, "usable": True, "reason": "光大银行私行权益有原文依据"} for i in (1, 2)]})
    async def classify(*a, **k): return None
    monkeypatch.setattr(graph, "intent_node", intent)
    monkeypatch.setattr(graph, "planner_node", planner)
    monkeypatch.setattr(graph, "collect_node", collector)
    monkeypatch.setattr(analyze_module, "_classify_route_llm", classify)
    monkeypatch.setattr(analyze_module, "achat", report)
    monkeypatch.setattr(checker_module, "achat", check)
    initial = {"user_input": state["task_spec"].intent + "；请列出适用条件、期限和原文链接。",
        "task_id": state["task_id"], "provider": "bound", "model": "test-model"}
    if recovery:
        initial["task_spec"] = state["task_spec"]
        async def resume():
            async for kind, payload in graph.astream_collection_recovery(initial):
                if kind == "final":
                    return payload
        result = asyncio.run(resume())
    else:
        result = asyncio.run(graph.build_graph().ainvoke(initial))
    nodes = [item["node"] for item in result["trace"]]
    prefix = [] if recovery else ["intent", "planner", "target_resolve", "router"]
    assert nodes == [*prefix, "collect", "analyze", "checker", "output"]
    assert result["quality"]["passed"] and result["quality"]["review_complete"]
    assert len(result["cleaned_dataset"]) == 2
    for key in ("json", "xlsx", "evidence_zip", "report_md"):
        assert Path(result["outputs"][key]).is_file()
    saved = json.loads(Path(result["outputs"]["json"]).read_text(encoding="utf-8"))
    assert saved["candidates"] == state["bank_collection"]["candidates"]
    assert saved["scope"] == original["scope"]
    assert "礼宾接送" in Path(result["outputs"]["report_md"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("usable,enabled", [(True, True), (False, True), (True, False)])
def test_report_checker_requires_all_requested_notes(tmp_path, monkeypatch, usable, enabled):
    state = report_state(tmp_path)
    state["analysis"] = "光大银行私行权益分析。"
    async def check(*a, **k):
        return json.dumps({"score": 100, "passed": True, "summary": "核对", "issues": [],
            "source_checks": [{"source_id": 1, "usable": True, "reason": "原文支持"},
                              {"source_id": 2, "usable": usable, "reason": "原文支持或仍待核对"}]})
    monkeypatch.setattr(checker_module, "achat", check)
    monkeypatch.setattr(settings, "checker_enabled", enabled)
    result = asyncio.run(checker_module.checker_node(state))
    assert result["quality"]["passed"] is (usable and enabled)


@pytest.mark.parametrize("passed", [True, False])
def test_report_output_preserves_snapshot_and_fails_closed(tmp_path, passed):
    state = report_state(tmp_path)
    state.update(analysis="光大银行私行权益分析。", quality={"passed": passed, "score": 90 if passed else 30,
        "review_complete": passed, "source_checks": [{"source_id": i, "usable": passed, "reason": "核对"} for i in (1, 2)]})
    result = asyncio.run(output_module.output_node(state))
    assert Path(result["outputs"]["report_md"]).is_file()
    assert Path(result["outputs"]["json"]).name == "data.json"
    assert bool(result.get("error")) is (not passed)
    report = Path(result["outputs"]["report_md"]).read_text(encoding="utf-8")
    if not passed:
        assert "未通过" in report and "不能视为任务完成" in result["reply"]


def test_insufficient_report_request_keeps_snapshot_without_model_success(tmp_path):
    state = report_state(tmp_path, count=1)
    result = asyncio.run(output_module.output_node(state))
    assert "report_md" not in result["outputs"]
    assert "未生成分析报告" in result["reply"]
    assert result["error"]


def test_known_frozen_cloud_model_analyzes_and_checks_all_ten_long_notes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.llm.provider import _bound_chat_identity
    from src.model_connections import broker
    from src.model_connections.catalog import PRESETS_BY_ID
    from src.memory import _library_scope
    from src.conductor.collection_results import collection_report_dataset
    state = report_state(tmp_path, count=10, target=10)
    state["model"] = "qwen3.8-max"
    state["session_id"] = "conversation-with-separate-artifact-task"
    monkeypatch.setattr(_library_scope, "execution_owner", lambda: "owner")
    for index, note in enumerate(state["bank_collection"]["candidates"]):
        note["content"] = "真实原文权益" * 1000 + f"原文末端_{index}"
    monkeypatch.setattr(settings, "analyze_max_blob_chars", 12000)
    monkeypatch.setattr(settings, "clean_max_item_chars", 500)
    monkeypatch.setattr(settings, "checker_max_report_chars", 6000)
    monkeypatch.setattr(broker, "get_default_broker", lambda: SimpleNamespace(
        verified_model_preset=lambda *a: PRESETS_BY_ID["qwen"].model_preset("qwen3.8-max")))
    async def classify(*a, **k): return None
    monkeypatch.setattr(analyze_module, "_classify_route_llm", classify)
    async def report(messages, **kwargs):
        for index in range(10):
            assert f"原文末端_{index}" in messages[-1]["content"]
        assert "已截断" not in messages[-1]["content"]
        return "分析内容" * 1600 + "报告末端"
    async def check(messages, **kwargs):
        for index in range(10):
            assert f"原文末端_{index}" in messages[-1]["content"]
        assert "报告末端" in messages[-1]["content"]
        return json.dumps({"score": 90, "passed": True, "issues": [], "summary": "逐篇完整核对",
            "source_checks": [{"source_id": i, "usable": True, "reason": "原文支持"} for i in range(1, 11)]})
    monkeypatch.setattr(analyze_module, "achat", report)
    monkeypatch.setattr(checker_module, "achat", check)
    token = _bound_chat_identity.set(("owner", "connection", "version", state["model"], state["session_id"], "run", 1))
    try:
        analyzed = asyncio.run(analyze_module.analyze_node(state))
        assert not analyzed.get("error") and analyzed["analysis"].endswith("报告末端")
        checked = asyncio.run(checker_module.checker_node({**state, **analyzed}))
        assert checked["quality"]["passed"] and checked["quality"]["review_complete"]
        assert analyzed["cleaned_dataset"] == collection_report_dataset(state)
    finally:
        _bound_chat_identity.reset(token)


@pytest.mark.parametrize("mode", ["unbound", "unknown", "local", "wrong_session", "wrong_owner"])
def test_unverified_capacity_never_sends_truncated_sources_as_complete(tmp_path, monkeypatch, mode):
    from types import SimpleNamespace
    from src.llm.provider import _bound_chat_identity
    from src.model_connections import broker
    from src.memory import _library_scope
    from src.conductor.collection_results import collection_report_dataset
    state = report_state(tmp_path)
    state["session_id"] = "conversation"
    monkeypatch.setattr(_library_scope, "execution_owner", lambda: "different-owner" if mode == "wrong_owner" else "owner")
    state["model"] = "unknown-model" if mode == "unknown" else "qwen3.8-max"
    state["bank_collection"]["candidates"][0]["content"] = "原文" * 3000
    monkeypatch.setattr(settings, "clean_max_item_chars", 500)
    monkeypatch.setattr(broker, "get_default_broker", lambda: SimpleNamespace(verified_model_preset=lambda *a: None))
    async def classify(*a, **k): return None
    async def forbidden(*a, **k):
        pytest.fail("不能调用模型并把已截断原文视为完整报告")
    monkeypatch.setattr(analyze_module, "_classify_route_llm", classify)
    monkeypatch.setattr(analyze_module, "achat", forbidden)
    monkeypatch.setattr(checker_module, "achat", forbidden)
    identity = None if mode == "unbound" else ("owner", "connection", "version", state["model"],
        "different-conversation" if mode == "wrong_session" else state["session_id"], "run", 1)
    token = _bound_chat_identity.set(identity)
    try:
        analyzed = asyncio.run(analyze_module.analyze_node(state))
        assert analyzed["analysis"] is None and "上下文" in analyzed["error"]
        state.update(cleaned_dataset=collection_report_dataset(state), analysis="长报告" * 3000)
        checked = asyncio.run(checker_module.checker_node(state))
        assert not checked["quality"]["passed"] and not checked["quality"]["review_complete"]
    finally:
        _bound_chat_identity.reset(token)


def test_ordinary_analysis_preserves_existing_small_context_limits(tmp_path, monkeypatch):
    state = report_state(tmp_path)
    state.pop("bank_collection")
    state["cleaned_dataset"] = [{"content": "普通任务原文" * 1000 + "普通末端"}]
    monkeypatch.setattr(settings, "clean_max_item_chars", 500)
    async def classify(*a, **k): return None
    async def report(messages, **kwargs):
        assert "普通末端" not in messages[-1]["content"] and "已截断" in messages[-1]["content"]
        return "普通任务报告"
    monkeypatch.setattr(analyze_module, "_classify_route_llm", classify)
    monkeypatch.setattr(analyze_module, "achat", report)
    result = asyncio.run(analyze_module.analyze_node(state))
    assert result["analysis"] == "普通任务报告" and not result.get("error")
