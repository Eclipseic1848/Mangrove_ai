"""采集结果的业务状态与模型预算不依赖银行或站点。"""
import pytest


@pytest.mark.parametrize("count,stop,strict,expected,ready", [
    (0, "collection_failed", False, "failed", False),
    (0, "source_exhausted", False, "empty", False),
    (0, "candidate_limit", False, "incomplete", False),
    (2, "collection_failed", False, "partial", True),
    (2, "candidate_limit", True, "incomplete", False),
    (5, "target_reached", False, "complete", True),
])
def test_outcome_preserves_failure_and_coverage(count, stop, strict, expected, ready):
    from src.conductor.collection_results import collection_outcome
    snapshot = {"scope": {"target_count": 5, "strictness": "strict" if strict else "best_effort"},
                "groups": [{"selected_count": count, "stop_reason": stop, "batches": []}],
                "candidates": [{"status": "selected"} for _ in range(count)]}
    result = collection_outcome(snapshot)
    assert result["status"] == expected
    assert result["ready_for_review"] is ready


def test_failed_snapshot_cannot_create_workspace(tmp_path, monkeypatch):
    import asyncio
    from src.api.routes.chat import _create_bank_review
    from src.api.schemas import ChatIn
    from src.config.settings import settings
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path))
    state = {"task_id": "failed", "bank_collection": {"scope": {}, "candidates": [],
        "banks": [{"selected_count": 0, "stop_reason": "collection_failed", "batches": []}]}}
    with pytest.raises(ValueError, match="初稿"):
        asyncio.run(_create_bank_review(state, ChatIn(content="采集资料"), {"user_id": "owner"}, "local", "model"))


@pytest.mark.parametrize("api_format,key", [
    ("openai_chat_completions", "max_tokens"),
    ("openai_responses", "max_output_tokens"),
    ("anthropic_messages", "max_tokens"),
    ("gemini_generate_content", "maxOutputTokens"),
])
def test_operation_budget_reaches_provider_protocol(api_format, key):
    from src.model_connections.text_protocol import structured_request
    _, body, _ = structured_request(api_format=api_format, model="deepseek-flash",
        grant_token="test", system_prompt="只输出结构", payload={}, max_tokens=1536)
    assert (body["generationConfig"] if api_format == "gemini_generate_content" else body)[key] == 1536


def test_bound_chat_preserves_output_budget():
    import asyncio
    from src.llm.provider import _bound_chat
    from src.llm import achat
    seen = []
    async def generate(messages, *, max_tokens=None):
        seen.append(max_tokens)
        return "{}"
    token = _bound_chat.set(generate)
    try:
        asyncio.run(achat([{"role": "user", "content": "筛选"}], max_tokens=1536))
    finally:
        _bound_chat.reset(token)
    assert seen == [1536]


def test_completion_uses_actual_selected_rows_and_failed_downloads_stay_diagnostic():
    from src.conductor.collection_results import collection_outcome
    from src.api.routes.chat import _build_result
    snapshot = {"scope": {"target_count": 5, "strictness": "strict"},
        "groups": [{"name": "主题", "selected_count": 5, "stop_reason": "target_reached"}],
        "candidates": [{"group": "主题", "status": "selected"}]}
    outcome = collection_outcome(snapshot)
    assert not outcome["target_complete"] and not outcome["ready_for_review"]
    result = _build_result("owner", "conversation", {"task_id": "task", "error": outcome["message"],
        "outputs": {"json": "data.json"}, "collection_outcome": outcome}, outcome["message"], None, None, "query")
    assert result["kind"] == "error" and len(result["files"]) == 1


@pytest.mark.parametrize("api_format,payload", [
    ("openai_chat_completions", {"choices": [{"finish_reason": "length", "message": {"content": '{"relevant": true}'}}]}),
    ("openai_responses", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output_text": '{"relevant": true}'}),
    ("anthropic_messages", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"relevant": true}'}]}),
    ("gemini_generate_content", {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"relevant": true}'}]}}]}),
])
def test_truncated_provider_output_is_not_a_complete_assessment(api_format, payload):
    import json
    from src.model_connections.text_protocol import response_text
    with pytest.raises(ValueError, match="截断"):
        response_text(api_format, json.dumps(payload).encode("utf-8"))


def test_unresolved_candidates_are_not_reported_as_no_matches():
    from src.conductor.collection_results import collection_outcome
    result = collection_outcome({"scope": {"target_count": 1},
        "groups": [{"name": "任意主题", "stop_reason": "source_exhausted"}],
        "candidates": [{"group": "任意主题", "status": "review_required", "reason": "model_output_truncated"}]})
    assert result["status"] == "incomplete"
    assert not result["ready_for_review"]


@pytest.mark.parametrize("domain", ["bank", "topic"])
@pytest.mark.parametrize("response", ['{"relevant":', '{}', '{"relevant": true}'])
def test_invalid_extraction_is_not_converted_to_unrelated(domain, response, monkeypatch):
    import asyncio
    from src.conductor import bank_benefits, evidence_collection
    from src.conductor.task_spec import TaskSpec
    async def model(*args, **kwargs):
        return response
    module = bank_benefits if domain == "bank" else evidence_collection
    monkeypatch.setattr(module, "achat", model)
    spec = TaskSpec(intent="测试", **({"bank_benefits": {"source_evidence": "小红书", "banks": [{"name": "银行", "keyword": "权益"}]}} if domain == "bank" else {"evidence_collection": {"source_evidence": "小红书", "queries": [{"name": "主题", "keyword": "主题"}], "fields": ["name"]}}))
    assess = bank_benefits.assess_note if domain == "bank" else evidence_collection.assess_topic
    scope = spec.bank_benefits if domain == "bank" else spec.evidence_collection
    with pytest.raises(ValueError, match="结构"):
        asyncio.run(assess({"title": "权益", "metadata": {}}, scope, {"task_spec": spec, "_assessment_stage": "extract"}))


@pytest.mark.parametrize("node_name", ["planner", "checker", "analyze"])
def test_truncated_control_decisions_cannot_fall_back(node_name, monkeypatch):
    import asyncio
    from src.conductor.nodes import planner, checker, analyze
    from src.conductor.task_spec import TaskSpec
    from src.model_connections.text_protocol import ModelOutputTruncatedError
    async def truncated(*args, **kwargs):
        raise ModelOutputTruncatedError("模型输出截断")
    if node_name == "planner":
        monkeypatch.setattr(planner, "_plan", truncated)
        monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
        operation = planner.planner_node({"user_input": "仅限指定来源和5篇候选"})
    elif node_name == "checker":
        monkeypatch.setattr(checker, "achat", truncated)
        monkeypatch.setattr(checker.settings, "checker_enabled", True)
        operation = checker.checker_node({"task_spec": TaskSpec(intent="核对原文"), "analysis": "已有报告"})
    else:
        monkeypatch.setattr(analyze, "achat", truncated)
        monkeypatch.setattr(analyze, "skill_for_analysis", lambda *args: "")
        monkeypatch.setattr(analyze, "lesson_for_analyze", lambda *args, **kwargs: ("", None))
        operation = analyze.analyze_node({"task_spec": TaskSpec(intent="整理文章", data_type="article"),
            "cleaned_dataset": [{"content": "原始材料"}]})
    with pytest.raises(ModelOutputTruncatedError):
        asyncio.run(operation)


@pytest.mark.parametrize("budget", [65536, 131072])
def test_bank_extraction_uses_shared_initial_and_retry_budget(budget, monkeypatch):
    import asyncio
    from src.conductor import bank_benefits
    from src.conductor.task_spec import TaskSpec
    seen = []
    async def model(*args, **kwargs):
        seen.append(kwargs["max_tokens"])
        return '{"relevant": true, "benefits": []}'
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = TaskSpec(intent="权益", bank_benefits={"source_evidence": "小红书", "banks": [{"name": "银行", "keyword": "权益"}]})
    state = {"task_spec": spec, "_assessment_stage": "extract"}
    if budget == 131072:
        state["_assessment_max_tokens"] = budget
    asyncio.run(bank_benefits.assess_note({"title": "权益", "metadata": {}}, spec.bank_benefits, state))
    assert seen == [budget]
