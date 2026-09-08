"""固定评测准备零请求，真实发送前守住目标与单次预算。"""
import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scripts.evaluate_conversation_steering import check_progressive_request, progressive_rewriter, run_progressive


@pytest.mark.parametrize("endpoint,allowed", [
    ("https://api.deepseek.com", True),
    ("http://api.deepseek.com", False),
    ("https://api.deepseek.com.evil.test", False),
    ("https://api.deepseek.com/other", False),
])
def test_explicit_cloud_preparation_is_exact_and_never_sends(tmp_path, endpoint, allowed):
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, provider="deepseek", base_url=endpoint, model="deepseek-v4-pro",
        output=tmp_path / "cloud.json", execute=False,
    )
    with patch("src.conversation_steering.rewriter.AsyncOpenAI", side_effect=AssertionError("准备不发送")):
        if allowed:
            assert asyncio.run(run_progressive(args)) == 0
            report = json.loads(args.output.read_text(encoding="utf-8"))
            assert report["provider"] == "deepseek" and report["requests_sent"] == 0
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
    body = {"model": "synthetic-local", "max_tokens": 2048}
    if change == "model":
        body["model"] = "other"
    if change == "output":
        body["max_tokens"] = 2049
    if change == "input":
        body["messages"] = "x" * 65536
    request = httpx.Request("GET" if change == "method" else "POST",
        endpoint + "/other" if change == "endpoint" else endpoint, json=body)
    with pytest.raises(ValueError):
        check_progressive_request(request, endpoint=endpoint, model="synthetic-local",
                                  sent=24 if change == "count" else 0, max_calls=24)


def test_real_send_guard_accepts_exact_frozen_request():
    endpoint = "http://127.0.0.1:9/v1/chat/completions"
    request = httpx.Request("POST", endpoint, json={"model": "synthetic-local", "max_tokens": 2048})
    check_progressive_request(request, endpoint=endpoint, model="synthetic-local", sent=23, max_calls=24)


def test_explicit_connection_does_not_resolve_global_profiles():
    args = argparse.Namespace(base_url="http://127.0.0.1:9/v1", model="synthetic-local")
    with patch("src.llm.provider.MultiModelProvider.resolve_model", side_effect=AssertionError("不能读取全局连接")):
        language = progressive_rewriter(args)
    assert language._connection.model == args.model
    assert language._connection.base_url == args.base_url
    assert language._connection.trust_env is False


@pytest.mark.parametrize("response_mode", ["complete", "timeout", "unavailable", "invalid_json"])
@pytest.mark.parametrize("provider_name", ["local", "deepseek"])
def test_real_rewriter_payloads_use_observed_sources_and_actual_turns_without_expected_answers(tmp_path, monkeypatch, response_mode, provider_name):
    monkeypatch.setenv("MANGROVE_EVAL_API_KEY", "synthetic-test-key")
    args = argparse.Namespace(
        fixture=Path("tests/fixtures/conversation_steering/progressive_clarification_cases.json"),
        rounds=1, concurrency=1, provider=provider_name,
        base_url="https://api.deepseek.com" if provider_name == "deepseek" else "http://127.0.0.1:9/v1", model="synthetic-local",
        output=tmp_path / "transport.json", execute=True,
    )
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if response_mode == "timeout":
            raise httpx.ReadTimeout("合成响应未知", request=request)
        if response_mode == "unavailable":
            return httpx.Response(503, json={"error": {"message": "合成服务不可用"}})
        if response_mode == "invalid_json":
            return httpx.Response(200, text="合成无效JSON")
        # 替身只验证产品请求接线，刻意不给正确业务语义，不能获得验收通过。
        draft = {"intent": "normalization", "confidence": "high", "normalized_text": "仅测试结构接线"}
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 0,
            "model": "synthetic-local", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    original_client = httpx.AsyncClient

    class IsolatedClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    with patch("src.conversation_steering.rewriter.httpx.AsyncClient", IsolatedClient):
        assert asyncio.run(run_progressive(args)) == 1
    assert "synthetic-test-key" not in args.output.read_text(encoding="utf-8")
    report = json.loads(args.output.read_text(encoding="utf-8"))
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
        assert "source_findings" in payload and "source_sha256" in payload
        assert "required_semantics" not in payload and "forbidden_assumptions" not in payload
    assert "eval-correction-grain-1" in requests[-1]["messages"][-1]["content"]
    assert "eval-correction-grain-2" in requests[-1]["messages"][-1]["content"]
    assert all(row["semantic_review"] == "pending" for row in report["results"])
