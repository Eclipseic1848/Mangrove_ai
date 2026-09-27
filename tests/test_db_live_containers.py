# -*- coding: utf-8 -*-
"""Testcontainers 驱动的 MySQL/PostgreSQL 实库冒烟测试。"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text

from src.connectors.database_connector import DatabaseConnector
from src.connectors.db_dialects import DbCredentials
from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceSpec, SourceType


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
        creds = _credentials(url, dialect)
        monkeypatch.setattr(settings, "data_prep_db_allowed_ports", str(creds.port))
        setup_url = url.replace("mysql://", "mysql+pymysql://", 1)
        engine = create_engine(setup_url)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE orders(id INTEGER PRIMARY KEY, name VARCHAR(50))"))
            conn.execute(text("INSERT INTO orders VALUES (1, 'a'), (2, 'b'), (3, 'c')"))
        engine.dispose()
        spec = SourceSpec(
            source_id="db", source_type=SourceType.DATABASE, locator="dbconn://live",
            options={"mode": "table", "table": "orders", "task_id": f"live-{dialect}",
                     "batch_size": 2},
        )
        connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")), credentials=creds)

        async def run():
            return [batch async for batch in connector.read(spec)]

        batches = asyncio.run(run())
        assert sum(batch.byte_count > 0 for batch in batches) == 2
        assert batches[-1].checkpoint.is_final is True
    finally:
        container.stop()
