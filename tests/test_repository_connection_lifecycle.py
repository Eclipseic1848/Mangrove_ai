"""仓库正常返回或抛错后都应主动归还 SQLite 连接，不能依赖 GC。"""
import sqlite3

import pytest

from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.candidate_verification.repository import SqliteCandidateVerificationRepository
from src.delivery_publishing.repository import DeliveryPublishingRepository
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize("kind", ["runtime", "candidate", "delivery"])
@pytest.mark.parametrize("fail_query", [False, True])
def test_repository_closes_connections_on_return_and_error(tmp_path, monkeypatch, kind, fail_query):
    database = migrated_webui_database(tmp_path / "connections.db")
    repository = {
        "runtime": AgenticRuntimeRepository,
        "candidate": SqliteCandidateVerificationRepository,
        "delivery": DeliveryPublishingRepository,
    }[kind](database)
    read = {
        "runtime": lambda: repository.get("owner", "missing", 1),
        "candidate": lambda: repository.get("owner", "missing"),
        "delivery": lambda: repository.get_intent("missing"),
    }[kind]
    real_connect = sqlite3.connect
    connections = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if fail_query and sql.lstrip().startswith("SELECT"):
                raise sqlite3.OperationalError("合成查询失败")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=Connection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        for _ in range(3):
            if fail_query:
                with pytest.raises(sqlite3.OperationalError, match="合成查询失败"):
                    read()
            else:
                assert read() is None
        assert len(connections) == 3
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("PRAGMA user_version")
    finally:
        for connection in connections:
            connection.close()
