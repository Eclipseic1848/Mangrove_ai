"""采集快照复用既有初稿创建与 Owner 边界。"""
import asyncio
import hashlib
import json

import pytest

from src.api.routes import chat, semantic_workspace
from src.api.schemas import ChatIn
from src.config.settings import settings
from src.conductor.bank_benefits_export import export_snapshot
from src.services.upload_store import UploadStore


def test_review_import_reuses_owner_inputs_and_existing_pi_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "data_prep_upload_root", str(tmp_path / "uploads"))
    snapshot = {"query": "小红书银行权益", "scope": {"target_count": 1},
                "banks": [{"bank": "示例", "selected_count": 1, "stop_reason": "target_reached"}],
                "candidates": [{"status": "selected", "metadata": {"note_id": "one"}, "content": "合成来源"}]}
    state = {"task_id": "collection-one", "bank_collection": snapshot}
    state.update(export_snapshot(state))
    calls = []

    async def create(payload, idempotency_key=None, user=None, request=None):
        calls.append((payload, idempotency_key, user))
        return {"task_id": "workspace-same"}

    monkeypatch.setattr(semantic_workspace, "create_task", create)
    body = ChatIn(content="小红书银行权益", external_api_confirmed=True)
    first = asyncio.run(chat._create_bank_review(state, body, {"user_id": "owner-a"}, "local", "test"))
    second = asyncio.run(chat._create_bank_review(state, body, {"user_id": "owner-a"}, "local", "test"))
    assert first == second
    assert calls[0][0].runtime_version.value == "pi"
    assert calls[0][0].output_formats == ("json", "xlsx")
    assert calls[0][0].upload_ids == calls[1][0].upload_ids
    assert calls[0][1] == calls[1][1]
    asyncio.run(chat._create_bank_review(state, body, {"user_id": "owner-a"}, "local", "test", connection_version="frozen-version"))
    assert calls[-1][0].model_connection_version == "frozen-version"
    uploads = UploadStore(settings.data_prep_upload_root, max_bytes=settings.data_prep_max_upload_bytes)
    source = uploads.resolve("owner-a", calls[0][0].upload_ids[0])
    assert source.sha256 == hashlib.sha256((tmp_path / "artifacts/collection-one/data.json").read_bytes()).hexdigest()
    with pytest.raises(PermissionError):
        asyncio.run(chat._create_bank_review(state, body, {"user_id": "owner-b"}, "local", "test"))


def test_draft_cannot_silently_rewrite_frozen_bank_json(tmp_path):
    from src.agentic_runtime.draft_snapshot import freeze_draft

    output = tmp_path / "output"
    output.mkdir()
    data = b'{"status":"selected"}'
    (output / "data.json").write_bytes(data)
    expected = {"json": hashlib.sha256(data).hexdigest()}
    draft = freeze_draft(tmp_path, owner_id="owner", task_id="task", revision=1,
                         run_id="run", formats=("json",), expected_sha256_by_format=expected)
    assert draft["files"][0]["sha256"] == expected["json"]
    (output / "data.json").write_text(json.dumps({"status": "rewritten"}), encoding="utf-8")
    with pytest.raises(ValueError, match="冻结"):
        freeze_draft(tmp_path, owner_id="owner", task_id="task", revision=1,
                     run_id="run", formats=("json",), expected_sha256_by_format=expected)


@pytest.mark.parametrize("changed", [False, True])
def test_workspace_freezes_source_hash_and_rejects_changed_connection(tmp_path, monkeypatch, changed):
    import httpx
    import src.model_connections.broker as broker_mod
    from src.model_connections import ConnectionBroker
    from src.model_connections.storage import ModelConnectionRepository
    from src.model_connections.vault import FernetCredentialVault
    from tests.test_pi_runtime_workspace_api import _client, FakePiRuntime, RolloutMode

    runtime = FakePiRuntime()
    client = _client(tmp_path, monkeypatch, role="user", pi_runtime=runtime, routing_mode=RolloutMode.VNEXT_DEFAULT)
    broker = ConnectionBroker(repository=ModelConnectionRepository(settings.webui_db_path),
        vault=FernetCredentialVault.generate(), resolver=lambda _host: ["8.8.8.8"],
        transport=httpx.MockTransport(lambda _request: httpx.Response(200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})))
    connection = asyncio.run(broker.configure_personal(owner_user_id="user-a", preset_id="deepseek", api_key="synthetic-only-secret"))
    binding = broker.freeze_connection("user-a", connection["connection_id"])
    monkeypatch.setattr(broker_mod, "_default_broker", broker)
    source = UploadStore(settings.data_prep_upload_root, max_bytes=10000).save_bytes(
        "user-a", "snapshot.json", b'{"frozen":true}', media_type="application/json")
    with client:
        response = client.post("/api/semantic-workspace/tasks", json={
            "objective_text": "原样复制冻结快照为初稿", "runtime_version": "pi", "output_formats": ["json"],
            "upload_ids": [source.upload_id], "source_copy_upload_ids": [source.upload_id],
            "model_connection_id": connection["connection_id"], "external_api_confirmed": True,
            "model_connection_version": "obsolete-version" if changed else binding.connection_version,
        })
        assert response.status_code == (409 if changed else 202), response.text
        if changed:
            assert "版本已变化" in response.json()["detail"]
            assert not runtime.requests
        else:
            import time
            for _ in range(100):
                if runtime.requests:
                    break
                time.sleep(0.05)
            assert runtime.requests
            assert runtime.requests[0].expected_sha256_by_format == {"json": source.sha256}
            assert runtime.requests[0].model_connection_version == binding.connection_version
