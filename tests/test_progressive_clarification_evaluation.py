"""固定评测准备零请求，真实发送前守住目标与单次预算。"""
import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scripts.evaluate_conversation_steering import check_progressive_request, progressive_rewriter, run_progressive
from src.model_connections.text_protocol import structured_request


@pytest.mark.parametrize("timeout", [0, 601, False])
def test_evaluation_rejects_unbounded_timeout_before_journal(tmp_path, timeout):
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, base_url="http://127.0.0.1:9/v1", model="synthetic-local",
        timeout_seconds=timeout, output=tmp_path / "invalid.json", execute=False,
    )
    with pytest.raises(ValueError):
        asyncio.run(run_progressive(args))
    assert not args.output.exists()


@pytest.mark.parametrize("provider_name,official,model", [
    ("deepseek", "https://api.deepseek.com", "deepseek-v4-pro"),
    ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-max-0902"),
])
@pytest.mark.parametrize("variant", ["exact", "http", "host", "path"])
def test_explicit_cloud_preparation_is_exact_and_never_sends(tmp_path, variant, provider_name, official, model):
    from urllib.parse import urlsplit
    endpoint = {"exact": official, "http": official.replace("https:", "http:"),
                "host": official.replace(urlsplit(official).netloc, urlsplit(official).netloc + ".evil.test"),
                "path": official + "/other"}[variant]
    allowed = variant == "exact"
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, provider=provider_name, base_url=endpoint, model=model,
        output=tmp_path / "cloud.json", execute=False,
    )
    with patch("src.conversation_steering.rewriter.AsyncOpenAI", side_effect=AssertionError("准备不发送")):
        if allowed:
            assert asyncio.run(run_progressive(args)) == 0
            report = json.loads(args.output.read_text(encoding="utf-8"))
            assert report["provider"] == provider_name and report["requests_sent"] == 0
            assert report["endpoint"] == endpoint + "/chat/completions"
        else:
            with pytest.raises(ValueError):
                asyncio.run(run_progressive(args))
            assert not args.output.exists()


def test_cloud_rewriter_requires_explicit_secret_and_preserves_provider(monkeypatch):
    args = argparse.Namespace(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-v4-pro")
    monkeypatch.delenv("MANGROVE_EVAL_API_KEY", raising=False)
    with pytest.raises(ValueError):
        progressive_rewriter(args)
    monkeypatch.setenv("MANGROVE_EVAL_API_KEY", "synthetic-test-key")
    language = progressive_rewriter(args)
    assert language._connection.provider == "deepseek"
    assert language._connection.api_key == "synthetic-test-key"


def test_preparation_never_constructs_model_client_and_preserves_old_evidence(tmp_path):
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, base_url="http://127.0.0.1:9/v1", model="synthetic-local",
        output=tmp_path / "prepared.json", execute=False,
    )
    with patch("src.conversation_steering.rewriter.AsyncOpenAI", side_effect=AssertionError("不得创建网络客户端")):
        assert asyncio.run(run_progressive(args)) == 0
        original = args.output.read_bytes()
        with pytest.raises(FileExistsError):
            asyncio.run(run_progressive(args))
    assert args.output.read_bytes() == original
    report = json.loads(original)
    assert report["requests_sent"] == 0
    assert report["all_passed"] is False
    assert report["status"] == "prepared"


@pytest.mark.parametrize("change", ["endpoint", "method", "model", "output", "input", "count"])
def test_real_send_guard_rejects_drift_and_exhausted_budget(change):
    endpoint = "http://127.0.0.1:9/v1/chat/completions"
    body = {"model": "deepseek-v4-pro", "max_tokens": 384000}
    if change == "model":
        body["model"] = "other"
    if change == "output":
        body["max_tokens"] = 384001
    if change == "input":
        body["messages"] = "x" * 65536
    request = httpx.Request("GET" if change == "method" else "POST",
        endpoint + "/other" if change == "endpoint" else endpoint, json=body)
    with pytest.raises(ValueError):
        check_progressive_request(request, endpoint=endpoint, model="deepseek-v4-pro",
                                  sent=24 if change == "count" else 0, max_calls=24)


def test_real_send_guard_accepts_exact_frozen_request():
    endpoint = "http://127.0.0.1:9/v1/chat/completions"
    request = httpx.Request("POST", endpoint, json={"model": "synthetic-local"})
    check_progressive_request(request, endpoint=endpoint, model="synthetic-local", sent=23, max_calls=24)


def test_explicit_connection_does_not_resolve_global_profiles():
    args = argparse.Namespace(base_url="http://127.0.0.1:9/v1", model="synthetic-local")
    with patch("src.llm.provider.MultiModelProvider.resolve_model", side_effect=AssertionError("不能读取全局连接")):
        language = progressive_rewriter(args)
    assert language._connection.model == args.model
    assert language._connection.base_url == args.base_url
    assert language._connection.trust_env is False


@pytest.mark.parametrize("response_mode", ["complete", "timeout", "unavailable", "invalid_json"])
@pytest.mark.parametrize("provider_name,disable_thinking", [("local", False), ("deepseek", False), ("qwen", False), ("qwen", True)])
def test_real_rewriter_payloads_use_observed_sources_and_actual_turns_without_expected_answers(tmp_path, monkeypatch, response_mode, provider_name, disable_thinking):
    monkeypatch.setenv("MANGROVE_EVAL_API_KEY", "synthetic-test-key")
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, provider=provider_name,
        timeout_seconds=300, disable_thinking=disable_thinking,
        base_url={"deepseek": "https://api.deepseek.com", "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1", "local": "http://127.0.0.1:9/v1"}[provider_name],
        model={"deepseek": "deepseek-v4-pro", "qwen": "qwen3.8-max-0902", "local": "synthetic-local"}[provider_name],
        output=tmp_path / "transport.json", execute=True,
    )
    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert body.get("max_tokens") == {"deepseek": 384000, "qwen": 131072, "local": None}[provider_name]
        assert (body.get("enable_thinking") is False) if disable_thinking else "enable_thinking" not in body
        requests.append(body)
        if response_mode == "timeout":
            raise httpx.ReadTimeout("合成响应未知", request=request)
        if response_mode == "unavailable":
            return httpx.Response(503, json={"error": {"message": "合成服务不可用"}})
        if response_mode == "invalid_json":
            return httpx.Response(200, text="合成无效JSON")
        # 替身只验证产品请求接线，刻意不给正确业务语义，不能获得验收通过。
        draft = {"understanding": {"normalized_text": "仅测试结构接线", "open_questions": []}, "changes": {"intent": "normalization", "confidence": "high"}}
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 0,
            "model": args.model, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    original_client = httpx.AsyncClient

    class IsolatedClient(original_client):
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 300
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    with patch("src.conversation_steering.rewriter.httpx.AsyncClient", IsolatedClient):
        assert asyncio.run(run_progressive(args)) == 1
    assert "synthetic-test-key" not in args.output.read_text(encoding="utf-8")
    report = json.loads(args.output.read_text(encoding="utf-8"))
    assert report["qwen_thinking_policy"] == ("disabled" if disable_thinking else "provider_default")
    assert report["timeout_seconds"] == 300
    if response_mode != "complete":
        assert len(requests) == report["requests_sent"] == 1
        assert report["status"] == "stopped"
        assert report["results"][0]["outcome"] == ("unknown" if response_mode == "timeout" else "unusable_response")
        assert len(report["not_run"]) == 23
        assert report["all_passed"] is False
        return
    assert len(requests) == report["requests_sent"] == 24
    assert report["status"] == "awaiting_semantic_review"
    assert report["all_passed"] is False
    for body in requests:
        payload = body["messages"][-1]["content"]
        decoded = json.loads(payload)
        assert list(decoded)[-1] == "user_turn"
        if decoded["prior_delta"]:
            assert decoded["prior_delta"]["status"] == "unconfirmed_model_draft"
            assert decoded["prior_delta"]["value"]["normalized_text"] == "仅测试结构接线"
        assert "source_findings" in payload and "source_sha256" in payload
        assert "required_semantics" not in payload and "forbidden_assumptions" not in payload
    assert "eval-correction-grain-1" in requests[-1]["messages"][-1]["content"]
    assert "eval-correction-grain-2" in requests[-1]["messages"][-1]["content"]
    assert all(row["semantic_review"] == "pending" for row in report["results"])


def test_structured_protocol_uses_catalog_output_not_small_task_cap():
    _, body, _ = structured_request(api_format="openai_chat_completions", model="deepseek-v4-pro", grant_token="synthetic", system_prompt="JSON", payload={})
    assert body["max_tokens"] == 384000


def test_responses_does_not_misuse_answer_limit_for_thinking_total():
    _, body, _ = structured_request(api_format="openai_responses", model="qwen3.8-flash", grant_token="synthetic", system_prompt="JSON", payload={})
    assert "max_output_tokens" not in body


def test_unknown_compatible_model_leaves_output_to_deployment():
    _, body, _ = structured_request(api_format="openai_chat_completions", model="custom-local", grant_token="synthetic", system_prompt="JSON", payload={})
    assert "max_tokens" not in body


def test_headers_without_body_stop_at_total_deadline_without_retry(tmp_path):
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, base_url="http://127.0.0.1:9/v1", model="synthetic-local",
        timeout_seconds=300, output=tmp_path / "body-timeout.json", execute=True,
    )
    class PendingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield b"{}"
    original_client, original_timeout = httpx.AsyncClient, asyncio.timeout
    deadlines = []
    def deadline(seconds):
        deadlines.append(seconds)
        return original_timeout(0.05)
    class IsolatedClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=PendingBody())))
    from src.conversation_steering import rewriter
    previous_timeout = rewriter.settings.semantic_compiler_timeout_seconds
    with patch.object(rewriter.httpx, "AsyncClient", IsolatedClient), patch("asyncio.timeout", deadline):
        assert asyncio.run(run_progressive(args)) == 1
    assert deadlines == [300]
    assert rewriter.settings.semantic_compiler_timeout_seconds == previous_timeout
    report = json.loads(args.output.read_text(encoding="utf-8"))
    assert report["requests_sent"] == 1 and len(report["not_run"]) == 23
    assert report["error_type"] == "TimeoutError"
    assert report["results"][0]["http_status"] == 200
    assert report["results"][0]["outcome"] == "unknown"
    assert not report["results"][0].get("response_received")


def test_generated_output_formats_match_confirmation_boundary():
    from pydantic import ValidationError
    from src.api.routes.semantic_workspace import _FORMATS
    from src.conversation_steering.rewriter import RewriteDraft

    schema = RewriteDraft.model_json_schema()
    assert set(schema["$defs"]["RewriteChanges"]["properties"]["output_delta"]["items"]["enum"]) == _FORMATS
    draft = RewriteDraft(understanding={"normalized_text": "输出JSON", "open_questions": []}, changes={"intent": "task_refinement", "confidence": "high", "output_delta": ["json"]})
    assert draft.changes.output_delta == ("json",)
    with pytest.raises(ValidationError):
        RewriteDraft(understanding={"normalized_text": "输出JSON", "open_questions": []}, changes={"intent": "task_refinement", "confidence": "high", "output_delta": ["输出为JSON"]})


@pytest.mark.parametrize("questions", [None, ["问题一", "问题二"], "missing"])
def test_generated_questions_require_explicit_single_decision(questions):
    from pydantic import ValidationError
    from src.conversation_steering.rewriter import RewriteDraft
    value = {"changes": {"intent": "normalization", "confidence": "high"}, "understanding": {"normalized_text": "保持当前要求"}}
    if questions != "missing":
        value["understanding"]["open_questions"] = questions
    with pytest.raises(ValidationError):
        RewriteDraft.model_validate(value)
    value["understanding"]["open_questions"] = []
    assert RewriteDraft.model_validate(value).understanding.open_questions == ()


@pytest.mark.parametrize("value", [None, True, 0, "false"])
def test_disabled_thinking_guard_rejects_non_false(value):
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    request = httpx.Request("POST", endpoint, json={"model": "qwen3.8-max-0902", "max_tokens": 131072, "enable_thinking": value})
    with pytest.raises(ValueError):
        check_progressive_request(request, endpoint=endpoint, model="qwen3.8-max-0902", sent=0, max_calls=24, disable_thinking=True)


def test_non_qwen_cannot_disable_thinking_before_journal(tmp_path):
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, provider="deepseek", base_url="https://api.deepseek.com",
        model="deepseek-v4-pro", disable_thinking=True, output=tmp_path / "invalid-mode.json", execute=False,
    )
    with pytest.raises(ValueError):
        asyncio.run(run_progressive(args))
    assert not args.output.exists()


def test_default_thinking_guard_requires_absent_parameter():
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    request = httpx.Request("POST", endpoint, json={"model": "qwen3.8-max-0902", "max_tokens": 131072, "enable_thinking": False})
    with pytest.raises(ValueError):
        check_progressive_request(request, endpoint=endpoint, model="qwen3.8-max-0902", sent=0, max_calls=24)


def test_legacy_mode_rejects_disable_thinking():
    from scripts.evaluate_conversation_steering import run
    with pytest.raises(ValueError):
        asyncio.run(run(argparse.Namespace(mode="legacy", disable_thinking=True)))


def test_understanding_question_survives_empty_material_changes():
    from src.conversation_steering.rewriter import RewriteDraft
    from src.conversation_steering.models import ContextDelta
    from src.conversation_steering.service import SemanticDiffGate
    draft = RewriteDraft.model_validate({
        "understanding": {"normalized_text": "资料可读取，处理目标待定", "open_questions": ["需要怎样处理这些资料？"]},
        "changes": {"intent": "normalization", "confidence": "medium"},
    })
    fields = draft.context_fields()
    assert fields["open_questions"] == ("需要怎样处理这些资料？",)
    assert fields["goal_delta"] is None and fields["output_delta"] == ()
    delta = ContextDelta(delta_id="synthetic-draft", owner_id="owner", task_id="task", inherited_revision=1,
                         source_turn_ids=("turn",), **fields)
    assert SemanticDiffGate.classify(delta).value == "normalized_no_material_change"
    assert "understanding" not in delta.model_dump() and "changes" not in delta.model_dump()
