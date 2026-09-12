# -*- coding: utf-8 -*-
"""Testcontainers 驱动的 MySQL/PostgreSQL 实库产品链路测试。"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

import src.api.auth as auth_mod
from src.api.auth import get_current_user
from src.api.routes import connector_sources, data_sources
from src.connectors.db_dialects import DbCredentials, make_engine
from src.config.settings import settings
from src.source_acquisition.service import SourceAcquisitionRepository
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


pytestmark = [pytest.mark.db_live, pytest.mark.timeout(180)]


def _credentials(url: str, dialect: str) -> DbCredentials:
    parsed = urlsplit(url.replace("+pymysql", "").replace("+psycopg2", ""))
    return DbCredentials(
        dialect=dialect, host=parsed.hostname or "127.0.0.1", port=parsed.port or 0,
        database=parsed.path.lstrip("/"), username=parsed.username or "",
        password=parsed.password or "",
    )


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_live_database_readonly_extraction(dialect, tmp_path, monkeypatch):
    engine = None
    if dialect == "mysql":
        from testcontainers.mysql import MySqlContainer
        container = MySqlContainer(os.getenv("PHASE3_MYSQL_TEST_IMAGE", "mysql:8.0"))
    else:
        from testcontainers.postgres import PostgresContainer
        container = PostgresContainer(os.getenv("PHASE3_POSTGRES_TEST_IMAGE", "postgres:16"))
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker/Testcontainers 不可用: {exc}")
    try:
        url = container.get_connection_url()
        admin = _credentials(url, dialect)
        monkeypatch.setattr(settings, "data_prep_db_allowed_ports", str(admin.port))
        setup_url = (
            URL.create(
                "mysql+pymysql",
                username="root",
                password=container.root_password,
                host=admin.host,
                port=admin.port,
                database=admin.database,
            )
            if dialect == "mysql"
            else url
        )
        engine = create_engine(setup_url)
        readers = {}
        with engine.begin() as conn:
            for owner, suffix in (("owner-a", "a"), ("owner-b", "b")):
                table = f"orders_{suffix}"
                username = f"mangrove_reader_{suffix}"
                password = f"mangrove-{dialect}-{suffix}-reader"
                conn.execute(text(
                    f"CREATE TABLE {table}("
                    "id INTEGER PRIMARY KEY, water INTEGER, name VARCHAR(50))"
                ))
                conn.execute(
                    text(
                        f"INSERT INTO {table}(id, water, name) "
                        "VALUES (:id, :water, :name)"
                    ),
                    [
                        {"id": index, "water": (index - 1) // 2,
                         "name": f"{suffix}-{index}"}
                        for index in range(1, 13)
                    ],
                )
                if dialect == "mysql":
                    conn.execute(text(
                        f"CREATE USER '{username}'@'%' IDENTIFIED BY '{password}'"
                    ))
                    conn.execute(text(
                        f"GRANT SELECT ON `{admin.database}`.`{table}` "
                        f"TO '{username}'@'%'"
                    ))
                else:
                    conn.execute(text(
                        f"CREATE ROLE {username} LOGIN PASSWORD '{password}'"
                    ))
                    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {username}"))
                    conn.execute(text(f"GRANT SELECT ON TABLE {table} TO {username}"))
                readers[owner] = (
                    DbCredentials(
                        dialect=dialect,
                        host=admin.host,
                        port=admin.port,
                        database=admin.database,
                        username=username,
                        password=password,
                    ),
                    table,
                    [f"{suffix}-{index}" for index in range(1, 13)],
                )
        webui = migrated_webui_database(tmp_path / "webui.db")
        monkeypatch.setattr(settings, "webui_db_path", str(webui))
        monkeypatch.setattr(settings, "data_prep_db_batch_size", 1)
        monkeypatch.setattr(
            settings,
            "data_prep_artifact_root",
            str(tmp_path / "artifacts"),
        )
        for owner in ("owner-a", "owner-b"):
            seed_execution_owner(webui, owner)
        monkeypatch.setattr(auth_mod, "_store", None)
        active_owner = {"id": "owner-a"}
        app = FastAPI()
        app.include_router(data_sources.router)
        app.include_router(connector_sources.router)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": active_owner["id"],
            "execution_generation": 0,
        }
        source_ids = {}
        responses = []
        with TestClient(app) as client:
            for owner in ("owner-a", "owner-b"):
                active_owner["id"] = owner
                reader, table, expected_names = readers[owner]
                created = client.post("/api/data-sources/connections", json={
                    "name": f"{dialect}-readonly",
                    "dialect": dialect,
                    "host": reader.host,
                    "port": reader.port,
                    "database_name": reader.database,
                    "username": reader.username,
                    "password": reader.password,
                })
                assert created.status_code == 200, created.text
                responses.append(created)
                connection_id = created.json()["connection_id"]
                tested = client.post(
                    "/api/data-sources/connections/test",
                    json={"connection_id": connection_id},
                )
                assert tested.status_code == 200 and tested.json()["reachable"] is True
                responses.append(tested)
                schema = client.get(
                    f"/api/data-sources/connections/{connection_id}/schema"
                )
                assert schema.status_code == 200
                assert [item["name"] for item in schema.json()["tables"]] == [table]
                responses.append(schema)
                source = {
                    "source_type": "database",
                    "connection_id": connection_id,
                    "table": table,
                    "incremental": {
                        "strategy": "watermark",
                        "cursor_field": "water",
                    },
                }
                resolved = client.post("/api/connector-sources/resolve", json=source)
                assert resolved.status_code == 200
                responses.append(resolved)
                acquired = client.post(
                    "/api/connector-sources/acquisitions",
                    headers={"Idempotency-Key": f"live-{dialect}"},
                    json={
                        "source": source,
                        "purpose": "验证真实只读数据库",
                        "expected_connection_version": resolved.json()[
                            "connection_version"
                        ],
                    },
                )
                assert acquired.status_code == 202, acquired.text
                assert acquired.json()["status"] == "acquiring"
                assert acquired.json()["error_code"] == "connector_checkpoint_ready"
                responses.append(acquired)
                resumed = client.post(
                    "/api/connector-sources/acquisitions",
                    headers={"Idempotency-Key": f"live-{dialect}"},
                    json={
                        "source": source,
                        "purpose": "验证真实只读数据库",
                        "expected_connection_version": resolved.json()[
                            "connection_version"
                        ],
                        "resume_checkpoint": True,
                    },
                )
                assert resumed.status_code == 202, resumed.text
                assert resumed.json()["status"] == "succeeded"
                responses.append(resumed)
                repository = SourceAcquisitionRepository(webui)
                artifacts = [
                    repository.get_artifact(
                        owner,
                        item["artifact_id"],
                        include_content=True,
                    )
                    for item in resumed.json()["snapshot"]["artifacts"]
                ]
                assert [
                    item["name"]
                    for artifact in artifacts
                    for item in map(json.loads, artifact["content_blob"].splitlines())
                ] == expected_names
                replayed = client.post(
                    "/api/connector-sources/acquisitions",
                    headers={"Idempotency-Key": f"live-{dialect}"},
                    json={"source": source, "purpose": "验证真实只读数据库"},
                )
                assert replayed.status_code == 202
                assert replayed.json()["attempt_id"] == resumed.json()["attempt_id"]
                responses.append(replayed)
                rejected_sql = client.post(
                    "/api/connector-sources/resolve",
                    json={**source, "sql": "CALL side_effect()"},
                )
                assert rejected_sql.status_code == 422
                responses.append(rejected_sql)
                source_ids[owner] = (
                    connection_id,
                    resumed.json()["snapshot_id"],
                )

            active_owner["id"] = "owner-b"
            owner_a_connection, owner_a_snapshot = source_ids["owner-a"]
            denied = client.post("/api/connector-sources/resolve", json={
                "source_type": "database",
                "connection_id": owner_a_connection,
                "table": readers["owner-a"][1],
            })
            assert denied.status_code == 404
            responses.append(denied)
            repository = SourceAcquisitionRepository(webui)
            assert repository.get_snapshot("owner-b", owner_a_snapshot) is None
            for reader, _, _ in readers.values():
                secret = reader.password.encode()
                assert all(secret not in response.content for response in responses)
                assert all(
                    secret not in path.read_bytes()
                    for path in tmp_path.rglob("*")
                    if path.is_file()
                )

        for owner, (reader, table, _) in readers.items():
            other_table = readers[
                "owner-b" if owner == "owner-a" else "owner-a"
            ][1]
            reader_engine = make_engine(reader)
            try:
                with pytest.raises(DBAPIError), reader_engine.begin() as conn:
                    conn.execute(text(
                        f"INSERT INTO {table} VALUES (13, 6, 'denied')"
                    ))
                with pytest.raises(DBAPIError), reader_engine.begin() as conn:
                    conn.execute(text(f"CREATE TABLE denied_{owner[-1]}(id INTEGER)"))
                with pytest.raises(DBAPIError), reader_engine.connect() as conn:
                    conn.execute(text(f"SELECT * FROM {other_table}"))
            finally:
                reader_engine.dispose()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM orders_a")).scalar_one() == 12
            assert conn.execute(text("SELECT COUNT(*) FROM orders_b")).scalar_one() == 12
    finally:
        if engine is not None:
            engine.dispose()
        container.stop()
