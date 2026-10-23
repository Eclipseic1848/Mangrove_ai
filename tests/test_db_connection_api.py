"""数据库连接通过真实路由校验，拒绝输入不得落库、探测或泄露密码。"""
from dataclasses import asdict
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.routes import data_sources
from src.api.store import WebUIStore
from src.config import settings
from src.connectors.base import ProbeResult
from src.connectors.database_connector import DatabaseConnector
from tests.database_migration_helpers import migrated_webui_database


BASE = "/api/data-sources/connections"
PASSWORD_CANARY = "db-api-password-canary-252"


@pytest.fixture
def db_api(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    owner = store.create_user("db-api-owner", "synthetic-unused")["user_id"]
    other = store.create_user("db-api-other", "synthetic-unused")["user_id"]
    user = {"user_id": owner, "role": "user"}
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(settings, "data_prep_db_sqlite_root", str(tmp_path))
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    app = FastAPI()
    app.include_router(data_sources.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client, store, user, owner, other


def _watch_probes(monkeypatch):
    calls = []

    async def probe(self, spec):
        calls.append((asdict(await self._resolve_creds(spec)), spec.options["user_id"]))
        return ProbeResult(reachable=True, message="合成探测")

    async def discover(self, spec):
        calls.append("discover")
        return {"tables": []}

    monkeypatch.setattr(DatabaseConnector, "probe", probe)
    monkeypatch.setattr(DatabaseConnector, "discover", discover)
    return calls


INVALID_INPUTS = [
    pytest.param({}, id="empty-object"),
    pytest.param({"dialect": "unknown"}, id="unknown-dialect"),
    pytest.param({"port": -1}, id="negative-port"),
    pytest.param({"port": 65536}, id="overflow-port"),
    pytest.param({"port": "3306"}, id="string-port"),
    pytest.param({"port": 3306.5}, id="fractional-port"),
    pytest.param({"port": True}, id="boolean-port"),
    pytest.param({"port": None}, id="null-port"),
    pytest.param({"name": []}, id="name-type"),
    pytest.param({"dialect": []}, id="dialect-type"),
    pytest.param({"host": 123}, id="host-type"),
    pytest.param({"database_name": {}}, id="database-type"),
    pytest.param({"username": []}, id="username-type"),
    pytest.param({"password": {"canary": PASSWORD_CANARY}}, id="password-type"),
    pytest.param({"sqlite_relpath": False}, id="sqlite-path-type"),
    pytest.param({"dialect": "sqlite", "host": ""}, id="sqlite-missing-path"),
    pytest.param({"dialect": "mysql", "host": ""}, id="mysql-missing-host"),
    pytest.param({"dialect": "postgresql", "host": ""}, id="postgresql-missing-host"),
]


@pytest.mark.parametrize("path", [BASE, BASE + "/test"], ids=["create", "inline"])
@pytest.mark.parametrize("changes", INVALID_INPUTS)
def test_invalid_input_is_rejected_before_store_or_probe(db_api, monkeypatch, path, changes):
    client, store, _, owner, other = db_api
    calls = _watch_probes(monkeypatch)
    payload = {
        "name": "合成连接", "dialect": "mysql", "host": "db.example.invalid",
        "password": PASSWORD_CANARY,
    }
    # 空对象单独保留，防止缺少 dialect 的请求逃逸为服务端异常。
    payload = {**payload, **changes} if changes else {}
    response = client.post(path, json=payload)
    assert store.list_db_connections(owner) == []
    assert store.list_db_connections(other) == []
    assert calls == []
    assert 400 <= response.status_code < 500, response.text
    assert PASSWORD_CANARY not in response.text


@pytest.mark.parametrize("path", [BASE, BASE + "/test"], ids=["create", "inline"])
def test_missing_body_is_rejected_without_side_effects(db_api, monkeypatch, path):
    client, store, _, owner, _ = db_api
    calls = _watch_probes(monkeypatch)
    response = client.post(path)
    assert 400 <= response.status_code < 500, response.text
    assert store.list_db_connections(owner) == []
    assert calls == []


@pytest.mark.parametrize("name", [None, "", "   "], ids=["omitted", "empty", "blank"])
def test_create_requires_nonblank_name(db_api, monkeypatch, name):
    client, store, _, owner, _ = db_api
    calls = _watch_probes(monkeypatch)
    payload = {"dialect": "sqlite", "sqlite_relpath": "source.db", "password": PASSWORD_CANARY}
    if name is not None:
        payload["name"] = name
    response = client.post(BASE, json=payload)
    assert store.list_db_connections(owner) == []
    assert calls == []
    assert 400 <= response.status_code < 500, response.text
    assert PASSWORD_CANARY not in response.text


@pytest.mark.parametrize("path_field", ["sqlite_relpath", "database_name"])
def test_sqlite_inline_and_stored_probe_real_temporary_database(db_api, tmp_path, path_field):
    client, store, _, owner, _ = db_api
    with sqlite3.connect(tmp_path / "source.db") as connection:
        connection.execute("CREATE TABLE measurements (value INTEGER)")
    payload = {"dialect": "sqlite", path_field: "source.db", "password": ""}
    # inline 不需要名称；两条路径均使用真实只读 SQLite 引擎。
    inline = client.post(BASE + "/test", json=payload)
    assert inline.status_code == 200, inline.text
    assert inline.json()["reachable"] is True
    assert inline.json()["sample"]["table_count"] == 1
    assert store.list_db_connections(owner) == []
    created = client.post(BASE, json={**payload, "name": "临时 SQLite"})
    assert created.status_code == 200, created.text
    public = created.json()
    assert public["port"] == 0
    assert "password" not in public
    assert "password_enc" not in public
    stored = client.post(BASE + "/test", json={"connection_id": public["connection_id"]})
    assert stored.status_code == 200, stored.text
    assert stored.json()["reachable"] is True
    assert stored.json()["sample"]["table_count"] == 1
    assert len(store.list_db_connections(owner)) == 1


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize("port", [None, 0], ids=["omitted-port", "zero-port"])
def test_network_credentials_keep_engine_defaults_without_network(db_api, monkeypatch, dialect, port):
    client, store, _, owner, _ = db_api
    calls = _watch_probes(monkeypatch)
    payload = {
        "dialect": dialect, "host": "db.example.invalid", "username": "",
        "database_name": "", "password": "",
    }
    if port is not None:
        payload["port"] = port
    inline = client.post(BASE + "/test", json=payload)
    assert inline.status_code == 200, inline.text
    assert inline.json()["reachable"] is True
    assert store.list_db_connections(owner) == []
    created = client.post(BASE, json={**payload, "name": "合成网络连接"})
    assert created.status_code == 200, created.text
    assert len(calls) == 1
    assert created.json()["port"] == 0
    stored = client.post(BASE + "/test", json={"connection_id": created.json()["connection_id"]})
    assert stored.status_code == 200, stored.text
    expected = {
        "dialect": dialect, "host": "db.example.invalid", "port": 0,
        "database": "", "username": "", "password": "", "sqlite_relpath": "",
    }
    assert calls == [(expected, owner), (expected, owner)]


def test_owner_isolation_precedes_probe_discovery_and_delete(db_api, monkeypatch):
    client, store, user, owner, other = db_api
    calls = _watch_probes(monkeypatch)
    first = client.post(BASE, json={"name": "Owner 一", "dialect": "sqlite", "sqlite_relpath": "a.db"})
    assert first.status_code == 200, first.text
    first_id = first.json()["connection_id"]
    user["user_id"] = other
    second = client.post(BASE, json={"name": "Owner 二", "dialect": "sqlite", "sqlite_relpath": "b.db"})
    assert second.status_code == 200, second.text
    second_id = second.json()["connection_id"]
    for response in (
        client.post(BASE + "/test", json={"connection_id": first_id}),
        client.get(f"{BASE}/{first_id}/schema"),
        client.delete(f"{BASE}/{first_id}"),
    ):
        assert response.status_code == 404, response.text
    assert calls == []
    assert store.get_db_connection(first_id) is not None
    assert [row["connection_id"] for row in client.get(BASE).json()] == [second_id]
    user["user_id"] = owner
    assert [row["connection_id"] for row in client.get(BASE).json()] == [first_id]


@pytest.mark.parametrize("method,path,payload", [
    ("post", BASE, {"name": "匿名连接", "dialect": "sqlite", "sqlite_relpath": "a.db"}),
    ("post", BASE + "/test", {"dialect": "sqlite", "sqlite_relpath": "a.db"}),
    ("get", BASE, None),
    ("get", BASE + "/missing/schema", None),
    ("delete", BASE + "/missing", None),
], ids=["create", "test", "list", "schema", "delete"])
def test_anonymous_requests_require_authentication(db_api, monkeypatch, method, path, payload):
    client, store, _, owner, other = db_api
    calls = _watch_probes(monkeypatch)
    client.app.dependency_overrides.clear()
    # 满足真实来源门，确保这里拒绝的是缺失身份而非 CSRF。
    response = client.request(method, path, json=payload, headers={
        "Origin": "https://testserver", "X-Mangrove-CSRF": "1",
    })
    assert response.status_code == 401, response.text
    assert store.list_db_connections(owner) == []
    assert store.list_db_connections(other) == []
    assert calls == []
