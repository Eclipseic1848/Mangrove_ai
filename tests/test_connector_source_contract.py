import asyncio
import sqlite3

from pymysql.err import OperationalError
import pytest

from src.connectors.database_connector import DatabaseConnector
from src.connectors.db_dialects import DbCredentials
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceSpec, SourceType
from src.source_acquisition.connection_source import (
    ConnectorRequest,
    freeze,
    read_frozen,
)


def test_frozen_selection_is_stable_across_attempt_namespaces():
    def scope(task_id: str):
        spec = SourceSpec(
            source_id="source-a",
            source_type=SourceType.DATABASE,
            locator="dbconn://connection-a",
            options={"table": "records", "task_id": task_id},
        )
        row = {
            "owner": "owner-a",
            "connection_id": "connection-a",
            "version": "1",
            "spec": spec,
        }
        return ConnectorRequest(freeze("owner-a", row), "读取资料").allowed_scope()

    resolved = scope("connector-resolve")
    acquired = scope("connector-attempt")

    assert resolved["artifact_namespace"] != acquired["artifact_namespace"]
    assert resolved["selection_sha256"] == acquired["selection_sha256"]


@pytest.mark.parametrize(
    ("driver_code", "expected"),
    [(1045, "authorization_expired"), (1142, "permission_denied")],
)
def test_database_access_failure_keeps_public_error_code(
    tmp_path, driver_code, expected
):
    class Engine:
        def dispose(self):
            pass

    class AuthenticationFailureConnector(DatabaseConnector):
        def _reflect_table(self, engine, config):
            raise OperationalError(driver_code, "Access denied")

    spec = SourceSpec(
        source_id="source-a",
        source_type=SourceType.DATABASE,
        locator="dbconn://connection-a",
        options={"table": "records", "task_id": "connector-attempt"},
    )
    row = {
        "owner": "owner-a",
        "connection_id": "connection-a",
        "version": "1",
        "spec": spec,
    }
    store = ArtifactStore(str(tmp_path))
    connector = AuthenticationFailureConnector(
        store,
        credentials=DbCredentials(dialect="mysql"),
        engine_factory=lambda _: Engine(),
    )

    result = asyncio.run(
        read_frozen("owner-a", freeze("owner-a", row), lambda: row, connector, store)
    )

    assert result["error_code"] == expected


def test_registered_database_connection_is_owner_isolated_at_public_api(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.auth import get_execution_user
    from src.api.routes.connector_sources import resolve_registered_source, router_for
    from src.api.store import WebUIStore
    from tests.database_migration_helpers import migrated_webui_database

    database = migrated_webui_database(tmp_path / "webui.db")
    store = WebUIStore(str(database))
    connection = store.create_db_connection(
        "owner-a",
        name="只读样例",
        dialect="sqlite",
        sqlite_relpath="sample.db",
    )
    monkeypatch.setattr("src.api.auth.get_store", lambda: store)
    monkeypatch.setattr("src.api.routes.data_tasks.get_store", lambda: store)
    owner = {"value": "owner-a"}

    def no_connector(_):
        raise AssertionError("核对范围不得开始读取")

    app = FastAPI()
    app.include_router(
        router_for(
            None,
            resolve_registered_source,
            no_connector,
            None,
        )
    )
    app.dependency_overrides[get_execution_user] = lambda: {
        "user_id": owner["value"]
    }
    source = {
        "source_type": "database",
        "connection_id": connection["connection_id"],
        "table": "records",
    }

    with TestClient(app) as client:
        assert client.post("/api/connector-sources/resolve", json=source).status_code == 200
        owner["value"] = "owner-b"
        assert client.post("/api/connector-sources/resolve", json=source).status_code == 404

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM source_acquisition_attempts").fetchone()[0] == 0
