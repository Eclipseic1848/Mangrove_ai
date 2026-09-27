"""模型配置编辑的公开 API 回归；只使用隔离库与合成 Provider。"""
import httpx
import pytest
import asyncio
import json
from src.account_execution import ExecutionAuthorization, execution_context

from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.test_model_connections_api import _client, migrated_webui_database


def test_test_then_apply_keeps_old_binding_and_does_not_test_twice(tmp_path):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {"total_tokens": 2}})
    vault = FernetCredentialVault.generate()
    repository = ModelConnectionRepository(str(migrated_webui_database(tmp_path / "edit.db")))
    broker = ConnectionBroker(repository=repository, vault=vault, resolver=lambda host: ["8.8.8.8"], transport=httpx.MockTransport(provider))
    old = repository.create_managed(created_by="admin-a", display_name="云端模型", base_url="https://models.example/v1",
        model="sample", api_format="openai_chat_completions", locality="public_external", ciphertext=vault.encrypt("synthetic-old-key"), key_hint="-key", verified_at="2026-09-15T00:00:00")
    _, client = _client(user_id="admin-a", role="admin", broker=broker)
    path = f"/api/model-connections/{old['connection_id']}/configuration"
    config = client.get(path)
    assert config.status_code == 200
    assert "synthetic-old-key" not in config.text
    body = {"operation_id": "test-edit-1", "expected_version": config.json()["version"], "display_name": "新的显示名",
        "base_url": "https://models.example/v1", "model": "sample", "models": ["sample"], "api_key": "synthetic-new-key", "thinking": "default"}
    checked = client.post(path + "/test", json=body)
    assert checked.status_code == 200, checked.text
    assert checked.json()["state"] == "verified"
    restored = client.get(path + "/operations/test-edit-1")
    assert restored.json()["state"] == "verified"
    assert "synthetic-new-key" not in restored.text
    assert len(calls) == 1
    assert client.post(path + "/test", json=body).json()["state"] == "verified"
    assert len(calls) == 1
    old_binding = broker.freeze_connection("user-a", old["connection_id"])
    saved = client.post(path + "/apply", json={"operation_id": "test-edit-1"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["connection_id"] != old["connection_id"]
    assert len(calls) == 1
    assert client.post(path + "/apply", json={"operation_id": "test-edit-1"}).json()["connection_id"] == saved.json()["connection_id"]
    assert client.get(path + "/operations/test-edit-1").json()["state"] == "applied"
    assert broker.freeze_connection("user-a", old["connection_id"]) == old_binding
    items = client.get("/api/model-connections").json()["items"]
    assert next(item for item in items if item["connection_id"] == old["connection_id"])["models"][0]["current_catalog"] is False


@pytest.mark.parametrize("failure", ["credentials", "timeout", "permission", "endpoint"])
def test_failed_edit_never_replaces_original_or_retries_provider(tmp_path, failure):
    calls = []
    def provider(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("合成超时")
        return httpx.Response(401, json={"error": {"message": "synthetic"}})
    vault = FernetCredentialVault.generate()
    repository = ModelConnectionRepository(str(migrated_webui_database(tmp_path / "failed.db")))
    broker = ConnectionBroker(repository=repository, vault=vault, resolver=lambda host: ["8.8.8.8"], transport=httpx.MockTransport(provider))
    old = repository.create_managed(created_by="admin-a", display_name="云端模型", base_url="https://models.example/v1", model="sample", api_format="openai_chat_completions", locality="public_external", ciphertext=vault.encrypt("synthetic-key"), key_hint="-key", verified_at="2026-09-15T00:00:00")
    _, client = _client(user_id="admin-a", role="admin", broker=broker)
    path = f"/api/model-connections/{old['connection_id']}/configuration"
    body = {"operation_id": "failed-1", "expected_version": client.get(path).json()["version"], "display_name": "新名称", "base_url": "https://new.example/v1" if failure == "endpoint" else "https://models.example/v1", "model": "sample", "models": ["sample"]}
    if failure == "permission":
        _, client = _client(user_id="user-b", role="user", broker=broker)
        assert client.get(path).status_code == 404
    checked = client.post(path + "/test", json=body)
    assert checked.status_code == (400 if failure in ("permission", "endpoint") else 200)
    client.post(path + "/test", json=body)
    assert len(calls) == (0 if failure in ("permission", "endpoint") else 1)
    assert client.post(path + "/apply", json={"operation_id": "failed-1"}).status_code == 409
    _, admin = _client(user_id="admin-a", role="admin", broker=broker)
    assert admin.get(path).json()["display_name"] == "云端模型"
    assert len(admin.get("/api/model-connections").json()["items"]) == 1


def test_local_thinking_is_used_by_relay_and_concurrent_edit_cannot_overwrite(tmp_path):
    bodies = []
    def provider(request):
        bodies.append(json.loads(request.read()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    repository = ModelConnectionRepository(str(migrated_webui_database(tmp_path / "local.db")))
    broker = ConnectionBroker(repository=repository, vault=FernetCredentialVault.generate(), resolver=lambda host: ["192.168.1.20"], transport=httpx.MockTransport(provider))
    old = repository.create_managed(created_by="admin-a", display_name="本地模型", base_url="http://local.example/v1", model="Qwen-test", api_format="openai_chat_completions", locality="managed_private", ciphertext=None, key_hint="", verified_at="2026-09-15T00:00:00")
    _, client = _client(user_id="admin-a", role="admin", broker=broker)
    path = f"/api/model-connections/{old['connection_id']}/configuration"
    body = {"operation_id": "local-1", "expected_version": client.get(path).json()["version"], "display_name": "本地模型", "base_url": "http://local.example/v1", "model": "Qwen-test", "models": ["Qwen-test"], "thinking": "off"}
    assert client.post(path + "/test", json=body).json()["state"] == "verified"
    assert bodies[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert client.post(path + "/test", json={**body, "operation_id": "local-2"}).status_code == 200
    saved = client.post(path + "/apply", json={"operation_id": "local-1"}).json()
    assert client.post(path + "/apply", json={"operation_id": "local-2"}).status_code == 409
    async def relay():
        binding = broker.freeze_connection("user-a", saved["connection_id"])
        with execution_context(ExecutionAuthorization("user-a", 0)):
            grant = broker.issue_grant(owner_user_id="user-a", connection_id=saved["connection_id"], connection_version=binding.connection_version, task_id="local-edit", revision=1, run_id="local-edit-run", purpose="agent_inference", ttl_seconds=60)
            response = await broker.relay(grant_token=grant.token, protocol_path="chat/completions", method="POST", headers={}, body=json.dumps({"model": "Qwen-test", "messages": [{"role": "user", "content": "OK"}]}).encode())
            _ = b"".join([chunk async for chunk in response.iter_bytes()])
    asyncio.run(relay())
    assert bodies[-1]["chat_template_kwargs"] == {"enable_thinking": False}
