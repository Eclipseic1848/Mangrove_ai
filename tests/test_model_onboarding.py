"""模型向导的公开 API 行为；仅使用临时库及虚构 Provider。"""
import json
from dataclasses import replace

import httpx
import pytest

from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.test_model_connections_api import _client, migrated_webui_database
from src.account_execution import ExecutionAuthorization, execution_context
from src.connectors.http_security import HttpSecurityGuard, SsrfError
from src.model_connections.catalog import PRESETS_BY_ID


def test_platform_setup_preserves_pending_models_for_explicit_retry(setup_connection):
    calls = []
    def provider(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    client = setup_connection(provider, role="admin")
    result = client.post("/api/model-connections/managed/presets/deepseek", json={
        "display_name": "共享模型", "api_key": "fictional-key", "model": "deepseek-v4-pro",
    })
    assert result.status_code == 201, result.text
    pending = [item["model_id"] for item in result.json()["models"] if item["status"] == "pending_validation"]
    assert "deepseek-v4-flash" in pending
    retry = client.post(f"/api/model-connections/{result.json()['connection_id']}/models/retry", json={"model_ids": ["deepseek-v4-flash"]})
    assert retry.status_code == 200, retry.text
    assert calls == ["deepseek-v4-pro", "deepseek-v4-flash"]


def test_catalog_removal_does_not_block_retry_of_persisted_model(setup_connection, monkeypatch):
    calls = []
    def provider(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    client = setup_connection(provider)
    saved = client.post("/api/model-connections/presets/deepseek", json={
        "display_name": "原有连接", "api_key": "fictional-key", "model": "deepseek-v4-pro",
    }).json()
    preset = PRESETS_BY_ID["deepseek"]
    monkeypatch.setitem(PRESETS_BY_ID, "deepseek", replace(preset, model_catalog=tuple(item for item in preset.model_catalog if item.model_id != "deepseek-v4-flash")))
    retry = client.post(f"/api/model-connections/{saved['connection_id']}/models/retry", json={"model_ids": ["deepseek-v4-flash"]})
    assert retry.status_code == 200, retry.text
    assert calls == ["deepseek-v4-pro", "deepseek-v4-flash"]
    denied = client.post(f"/api/model-connections/{saved['connection_id']}/models/retry", json={"model_ids": ["unknown-model"]})
    assert denied.status_code == 400
    assert len(calls) == 2


@pytest.fixture
def setup_connection(tmp_path):
    def make(provider, role="user", address="8.8.8.8"):
        broker = ConnectionBroker(
            repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "models.db"))),
            vault=FernetCredentialVault.generate(),
            transport=httpx.MockTransport(provider),
            resolver=lambda _host: [address],
        )
        return _client(broker=broker, role=role)[1]
    with execution_context(ExecutionAuthorization("user-a", 0)):
        yield make


@pytest.mark.parametrize("succeeds", [True, False])
def test_personal_setup_calls_only_selected_model_and_never_switches(setup_connection, succeeds):
    calls = []
    def provider(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}) if succeeds else httpx.Response(403)
    client = setup_connection(provider)
    result = client.post("/api/model-connections/presets/deepseek", json={
        "display_name": "我的模型", "api_key": "fictional-key", "model": "deepseek-v4-pro",
    })
    assert calls == ["deepseek-v4-pro"]
    assert result.status_code == (201 if succeeds else 400)
    if succeeds:
        assert result.json()["model"] == "deepseek-v4-pro"
        assert result.json()["available_model_count"] == 1
    else:
        assert client.get("/api/model-connections").json()["items"] == []


def test_discovery_only_lists_models_without_inference_or_granting_qualification(setup_connection):
    calls = []
    def provider(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})
    client = setup_connection(provider, role="admin", address="192.168.1.20")
    result = client.post("/api/model-connections/managed/discover", json={"base_url": "http://192.168.1.20:11434/v1"})
    assert result.status_code == 200
    assert result.json()["models"] == ["local-model"]
    assert result.json()["detected_api_formats"] == []
    assert calls == [("GET", "/v1/models")]
    assert client.get("/api/model-connections").json()["items"] == []


@pytest.mark.parametrize("status,hint", [(401, "鉴权"), (403, "权限"), (429, "频繁"), (503, "不可用"), (302, "重定向")])
def test_discovery_errors_do_not_masquerade_as_unsupported_list(setup_connection, status, hint):
    client = setup_connection(lambda request: httpx.Response(status), role="admin", address="127.0.0.1")
    result = client.post("/api/model-connections/managed/discover", json={"base_url": "http://localhost:11434/v1"})
    assert result.status_code == 400
    assert hint in result.json()["detail"]


def test_discovery_unstarted_service_has_actionable_error(setup_connection):
    def provider(request):
        raise httpx.ConnectError("sensitive-internal-detail", request=request)
    client = setup_connection(provider, role="admin", address="127.0.0.1")
    result = client.post("/api/model-connections/managed/discover", json={"base_url": "http://localhost:11434/v1"})
    assert result.status_code == 400
    assert "启动" in result.text
    assert "sensitive-internal-detail" not in result.text


def test_admin_can_connect_explicit_localhost_model_without_fabricated_key(setup_connection):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    client = setup_connection(provider, role="admin", address="127.0.0.1")
    result = client.post("/api/model-connections/managed", json={
        "display_name": "我的本地模型", "base_url": "http://localhost:11434/v1",
        "api_format": "openai_chat_completions", "model": "local-model",
    })
    assert result.status_code == 200, result.text
    assert len(calls) == 1
    assert calls[0].url.host == "127.0.0.1"
    assert result.json()["locality"] == "managed_private"


@pytest.mark.parametrize("url,address", [
    ("http://169.254.169.254/v1", "169.254.169.254"),
    ("http://127.0.0.2:11434/v1", "127.0.0.2"),
    ("http://evil.example:11434/v1", "127.0.0.1"),
    ("http://localhost:11434/v1", "8.8.8.8"),
])
def test_local_model_exception_does_not_allow_metadata_or_dns_rebinding(setup_connection, url, address):
    calls = []
    client = setup_connection(lambda request: calls.append(request), role="admin", address=address)
    result = client.post("/api/model-connections/managed/discover", json={"base_url": url})
    assert result.status_code == 400
    assert calls == []


def test_ordinary_user_and_general_http_guard_still_reject_loopback(setup_connection):
    calls = []
    client = setup_connection(lambda request: calls.append(request), address="127.0.0.1")
    assert client.post("/api/model-connections/managed/discover", json={"base_url": "http://localhost:11434/v1"}).status_code == 403
    with pytest.raises(SsrfError):
        HttpSecurityGuard(allow_private=True).validate("http://127.0.0.1:11434/v1")
    assert calls == []


def test_region_uses_exact_trusted_workspace_address_and_invalid_input_never_sends(setup_connection):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"object": "response"})
    client = setup_connection(provider)
    body = {"display_name": "百炼", "api_key": "fictional-key", "region": "cn-beijing", "workspace_id": "workspace-test"}
    saved = client.post("/api/model-connections/presets/qwen", json=body)
    assert saved.status_code == 201, saved.text
    assert len(calls) == 1
    assert calls[0].headers["host"] == "workspace-test.cn-beijing.maas.aliyuncs.com"
    assert calls[0].url.path == "/compatible-mode/v1/responses"
    calls.clear()
    for invalid in ("evil.example/path", "../secret", "", "x@evil.example"):
        assert client.post("/api/model-connections/presets/qwen", json={**body, "workspace_id": invalid}).status_code == 400
    assert calls == []


@pytest.mark.parametrize("failure,hint", [(402, "额度不足"), (401, "密钥无效"), (429, "频繁"), ("quota", "额度不足"), ("timeout", "结果未知")])
def test_failure_guidance_never_exposes_provider_body_or_retries(setup_connection, failure, hint):
    calls = []
    def provider(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("SENSITIVE", request=request)
        if failure == "quota":
            return httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "SENSITIVE"}})
        return httpx.Response(failure, json={"error": {"message": "SENSITIVE"}})
    client = setup_connection(provider)
    result = client.post("/api/model-connections/presets/deepseek", json={"display_name": "测试", "api_key": "fictional-key"})
    assert result.status_code == 400
    assert hint in result.json()["detail"]
    assert "SENSITIVE" not in result.text
    assert len(calls) == 1


def test_qwen_catalog_uses_three_distinct_current_generation_models():
    preset = PRESETS_BY_ID["qwen"]
    assert set(preset.models) == {"qwen3.8-max-0902", "qwen3.8-flash", "qwen3.8-27b"}
    assert preset.recommended_model == "qwen3.8-flash"
    assert preset.version == "2026-09-07.2"
