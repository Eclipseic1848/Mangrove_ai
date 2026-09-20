from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.routes import config_routes
from src.api.store import WebUIStore
from src.config import runtime_config as rc
from tests.database_migration_helpers import migrated_webui_database


def test_notification_config_saves_and_updates_runtime_without_sending(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / 'notification.db')))
    monkeypatch.setattr(config_routes, 'get_store', lambda: store)
    monkeypatch.setattr(rc, '_BASELINE', {})
    values = {'slack_enabled': 'true', 'slack_bot_token': 'synthetic-token', 'slack_channel_id': 'C_FIXTURE',
              'smtp_enabled': 'true', 'smtp_host': 'smtp.example.invalid', 'smtp_user': 'sender@example.invalid'}
    for key in values:
        monkeypatch.setattr(rc.settings, key, getattr(rc.settings, key))
    app = FastAPI()
    app.include_router(config_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {'user_id': 'test-admin', 'role': 'admin'}
    client = TestClient(app)
    assert client.put('/api/config/batch', json={'values': values}).status_code == 200
    assert rc.settings.slack_bot_token == 'synthetic-token'
    assert rc.settings.slack_channel_id == 'C_FIXTURE'
    assert rc.settings.smtp_enabled is True
    assert rc.settings.smtp_host == 'smtp.example.invalid'
    assert 'synthetic-token' not in client.get('/api/config').text
    with store._conn() as connection:
        row = connection.execute("SELECT value FROM runtime_config WHERE scope='global' AND key='slack_bot_token'").fetchone()
    assert row and row[0] != 'synthetic-token'


def test_service_save_validates_whole_batch_and_preserves_secret(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    monkeypatch.setattr(config_routes, "get_store", lambda: store)
    monkeypatch.setattr(rc, "_BASELINE", {})
    for key in rc.REGISTRY:
        monkeypatch.setattr(rc.settings, key, getattr(rc.settings, key))
    app = FastAPI()
    app.include_router(config_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-admin", "role": "admin"}
    client = TestClient(app)
    response = client.put("/api/config/batch", json={"values": {
        "mysql_host": "db.example.test", "mysql_port": "70000",
    }})
    assert response.status_code == 400
    assert store.config_all("global") == {}
    response = client.put("/api/config/batch", json={"values": {
        "mysql_host": "db.example.test", "mysql_port": "3307", "mysql_password": "synthetic-secret",
    }})
    assert response.status_code == 200
    assert store.config_all("global")["mysql_port"] == "3307"
    items = {i["key"]: i for g in client.get("/api/config").json()["groups"] for i in g["items"]}
    assert items["mysql_password"]["value"] == "····cret"
    assert "synthetic-secret" not in client.get("/api/config").text
    assert "default_value" in items["mysql_host"]
    response = client.put("/api/config/batch", json={"values": {"mysql_host": "other.example.test"}})
    assert response.status_code == 200
    assert store.config_all("global")["mysql_password"] == "synthetic-secret"


def test_service_config_permissions_and_restore(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    monkeypatch.setattr(config_routes, "get_store", lambda: store)
    monkeypatch.setattr(rc, "_BASELINE", {})
    monkeypatch.setattr(rc.settings, "mysql_host", "default.example.test")
    monkeypatch.setattr(rc.settings, "library_stale_draft_days", 0)
    app = FastAPI()
    app.include_router(config_routes.router)
    role = {"user_id": "test-user", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: role
    client = TestClient(app)
    assert client.put("/api/config/batch", json={"values": {"mysql_host": "new.example.test"}}).status_code == 403
    role["role"] = "super_admin"
    assert client.put("/api/config/batch", json={"values": {"mysql_host": "new.example.test"}}).status_code == 200
    for values in ({"mysql_host": ""}, {"db_backend": "bogus"}, {"unknown": "value"}, {}):
        assert client.put("/api/config/batch", json={"values": values}).status_code == 400
        assert store.config_all("global")["mysql_host"] == "new.example.test"
    assert client.delete("/api/config/mysql_host").status_code == 200
    item = next(i for g in client.get("/api/config").json()["groups"] for i in g["items"] if i["key"] == "mysql_host")
    assert item["source"] == "env"
    assert item["value"] == "default.example.test"
    item = next(i for g in client.get("/api/config").json()["groups"] for i in g["items"] if i["key"] == "library_stale_draft_days")
    assert item["default_value"] == "0"


def test_semantic_check_does_not_pass_when_configured_rerank_fails(monkeypatch):
    from src.memory import embeddings
    # 模型调用边界使用合成返回，禁止连接真实端点。
    monkeypatch.setattr(embeddings, "embed_texts_with_model", lambda _: ("synthetic", [[1.0]]))
    monkeypatch.setattr(embeddings, "is_rerank_configured", lambda: True)
    monkeypatch.setattr(embeddings, "rerank_scores", lambda *_: [])
    app = FastAPI()
    app.include_router(config_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-admin", "role": "admin"}
    result = TestClient(app).post("/api/config/verify", json={"target": "semantic"})
    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert "rerank" in result.json()["detail"]


def test_concurrent_saves_keep_connection_value_equal_to_saved_value(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import pymysql

    first_written, second_done = Event(), Event()

    class DelayedStore(WebUIStore):
        def config_set_many(self, scope, values, updated_by=""):
            super().config_set_many(scope, values, updated_by)
            if values.get("mysql_host") == "first.example.test":
                first_written.set()
                second_done.wait(0.5)

    store = DelayedStore(str(migrated_webui_database(tmp_path / "webui.db")))
    monkeypatch.setattr(config_routes, "get_store", lambda: store)
    monkeypatch.setattr(rc, "_BASELINE", {})
    monkeypatch.setattr(rc.settings, "mysql_host", "default.example.test")
    connected = []

    class Connection:
        def close(self):
            pass

    def connect(**kwargs):
        connected.append(kwargs["host"])
        return Connection()

    monkeypatch.setattr(pymysql, "connect", connect)
    app = FastAPI()
    app.include_router(config_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-admin", "role": "admin"}

    def save(host):
        response = TestClient(app).put("/api/config/batch", json={"values": {"mysql_host": host}})
        if host == "second.example.test":
            second_done.set()
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, "first.example.test")
        assert first_written.wait(3)
        second = pool.submit(save, "second.example.test")
        assert first.result() == second.result() == 200
    assert TestClient(app).post("/api/config/verify", json={"target": "mysql"}).json()["ok"] is True
    assert connected == [store.config_all("global")["mysql_host"]]
