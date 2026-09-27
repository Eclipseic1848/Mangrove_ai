"""正常用户恢复入口穿过真实图和 SQLite，外部来源用合成替身。"""
from pathlib import Path
import asyncio
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.routes import chat
from src.api.store import WebUIStore
from src.config import cookie_identity, user_ctx
from src.config.cookie_probe_binding import capture_probe_environment
from src.config.settings import settings
from src.collectors.base import CollectResult, CollectedItem
from src.conductor import evidence_collection as flow
from src.conductor.nodes.collect import collect_node
from src.conductor.nodes.output import output_node
from src.conductor.collection_recovery import progress_location
from tests.database_migration_helpers import migrated_webui_database
from tests.account_execution_helpers import seed_execution_owner
from tests.test_evidence_collection_flow import spec_for


@pytest.mark.parametrize("bound", [False, True])
def test_normal_recovery_endpoint_preserves_history_and_frozen_task(tmp_path, monkeypatch, bound):
    database = migrated_webui_database(tmp_path / "webui.db")
    seed_execution_owner(database, "owner")
    seed_execution_owner(database, "other")
    store = WebUIStore(str(database))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr("src.data_prep.artifact_store._DEFAULT_ROOT", None)
    monkeypatch.setattr(settings, "mc_cookie_xhs", "old-cookie")
    monkeypatch.setattr(flow, "execution_owner", lambda: "owner")
    monkeypatch.setattr(chat, "platform_session_valid", lambda _: True)
    user = {"user_id": "owner", "role": "user", "execution_generation": 0}
    conv = store.create_conversation("owner", "恢复入口合成验证")["conv_id"]
    spec = spec_for("设备说明", raw_only=not bound, initial_candidates=1, candidate_limit=1)
    state = {"task_id": "collection-api", "task_spec": spec, "user_input": spec.intent,
             "session_id": conv, "provider": "local", "model": "synthetic"}
    from contextlib import nullcontext
    import httpx
    import src.model_connections as connections
    from src.model_connections import conductor
    from src.model_connections.storage import ModelConnectionRepository
    from src.model_connections.vault import FernetCredentialVault
    from src.llm.provider import _bound_chat_identity
    seen = []
    def provider(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"relevant": True, "records": []})}}]})
    context = nullcontext()
    if bound:
        broker = connections.ConnectionBroker(repository=ModelConnectionRepository(str(database)),
            vault=FernetCredentialVault.generate(), transport=httpx.MockTransport(provider), resolver=lambda _: ["8.8.8.8"])
        connection = asyncio.run(broker.create_personal(owner_user_id="owner", display_name="合成模型", preset_id="deepseek", api_key="synthetic-only", verify_all=True))
        monkeypatch.setattr(connections, "get_default_broker", lambda: broker)
        monkeypatch.setattr(conductor, "get_default_broker", lambda: broker)
        binding = broker.freeze_connection("owner", connection["connection_id"])
        state.update(provider="bound", model="deepseek-flash")
        context = conductor.conductor_connection(owner_id="owner", connection_id=connection["connection_id"],
            connection_version=binding.connection_version, model=state["model"], task_id=conv, run_id="original-run")
    frozen = []
    env = capture_probe_environment()
    proof = {"status": "valid", "account_ref_kind": "account_id", "account_ref": hashlib.sha256(b"account").hexdigest()}
    cookie_identity.record_identity("mc_cookie_xhs", "old-cookie", env, proof)
    calls = []
    async def collect(self, request):
        calls.append(user_ctx.effective("mc_cookie_xhs"))
        frozen.append(_bound_chat_identity.get())
        if len(calls) == 1:
            return CollectResult(False, "synthetic", coverage={"failure": {"reason": "login_required"}})
        return CollectResult(True, "synthetic", items=[CollectedItem(content="设备说明", metadata={"note_id": "one", "note_type": "normal"})])
    async def review(*args, **kwargs):
        raise ValueError("合成验收不启动外部初稿模型")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(chat, "_create_bank_review", review)
    with context:
        state.update(asyncio.run(collect_node(state)))
    seen.clear()
    state.update(asyncio.run(output_node(state)))
    original = {path: Path(path).read_bytes() for path in state["outputs"].values()}
    monkeypatch.setattr(settings, "mc_cookie_xhs", "new-cookie")
    cookie_identity.record_identity("mc_cookie_xhs", "new-cookie", capture_probe_environment(), proof)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    endpoint = "/api/chat/collections/collection-api"
    with TestClient(app) as client:
        assert client.get(endpoint + "/recovery").status_code == 200
        user["user_id"] = "other"
        assert client.post(endpoint + "/resume", json={"request_id": "a" * 32}).status_code == 409
        user["user_id"] = "owner"
        assert client.post(endpoint + "/resume", json={"request_id": "../bad"}).status_code == 422
        if bound:
            assert client.post(endpoint + "/resume", json={"request_id": "a" * 32}).status_code == 422
        response = client.post(endpoint + "/resume", json={"request_id": "a" * 32, "external_api_confirmed": True})
        assert response.status_code == 200, response.text
        assert "event: error" not in response.text, response.text
        assert "event: result" in response.text and "data-" + "a" * 32 + ".json" in response.text
        assert calls == ["old-cookie", "new-cookie"]
        assert frozen[0] == frozen[1]
        if bound:
            assert len(seen) == 2
            assert all(item["model"] == "deepseek-flash" for item in seen)
            assert frozen[1][5] == "original-run"
        assert all(Path(path).read_bytes() == raw for path, raw in original.items())
        messages = store.list_messages(conv)
        assert messages[0]["task_id"] == "collection-api"
        assert messages[-1]["task_id"] == "collection-api"
        assert client.post(endpoint + "/resume", json={"request_id": "a" * 32}).status_code == 409
        assert calls == ["old-cookie", "new-cookie"]
        progress_store, key = progress_location("owner", "collection-api")
        saved = progress_store.read_json_if_exists(key, "progress.json")
        saved["groups"][0]["done"] = False
        progress_store.write_json(key, "progress.json", saved)
        before = len(store.list_messages(conv))
        assert client.post(endpoint + "/resume", json={"request_id": "a" * 32, "external_api_confirmed": True}).status_code == 409
        assert len(store.list_messages(conv)) == before
