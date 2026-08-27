from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.config.settings import settings
from src.conductor.db_writer import write_items


class _FakeMySqlError(Exception):
    pass


class _FakeCursor:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.execute_calls: list[tuple[object, ...]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *args: object) -> None:
        self.execute_calls.append(args)

    def executemany(self, *_args: object) -> None:
        raise self._error


class _FakeConnection:
    def __init__(self, error: Exception) -> None:
        self.cursor_instance = _FakeCursor(error)
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        raise AssertionError("写入失败后不得提交")

    def close(self) -> None:
        self.closed = True


def _install_fake_pymysql(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> _FakeConnection:
    connection = _FakeConnection(error)
    fake_module = SimpleNamespace(
        MySQLError=_FakeMySqlError,
        connect=lambda **_kwargs: connection,
    )
    monkeypatch.setitem(sys.modules, "pymysql", fake_module)
    monkeypatch.setattr(settings, "db_backend", "mysql")
    return connection


def test_mysql_missing_schema_fails_closed_with_executable_dba_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _install_fake_pymysql(
        monkeypatch,
        _FakeMySqlError(1146, "Table 'mangrove.collected_items' doesn't exist"),
    )

    with pytest.raises(RuntimeError) as raised:
        write_items("task-1", [{"content": "row"}], source="test")

    assert "未执行自动建表" in str(raised.value)
    assert "CREATE TABLE collected_items" in str(raised.value)
    assert raised.value.__cause__.args[0] == 1146
    assert connection.cursor_instance.execute_calls == []
    assert connection.closed is True


def test_mysql_missing_column_fails_closed_with_executable_dba_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _install_fake_pymysql(
        monkeypatch,
        _FakeMySqlError(1054, "Unknown column 'metadata' in 'field list'"),
    )

    with pytest.raises(RuntimeError) as raised:
        write_items("task-1", [{"content": "row"}], source="test")

    assert "未执行自动改表" in str(raised.value)
    assert (
        "ALTER TABLE collected_items ADD COLUMN metadata LONGTEXT;"
        in str(raised.value)
    )
    assert raised.value.__cause__.args[0] == 1054
    assert connection.cursor_instance.execute_calls == []
    assert connection.closed is True


def test_mysql_non_schema_database_error_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _FakeMySqlError(1213, "Deadlock found")
    connection = _install_fake_pymysql(monkeypatch, original)

    with pytest.raises(_FakeMySqlError) as raised:
        write_items("task-1", [{"content": "row"}], source="test")

    assert raised.value is original
    assert connection.cursor_instance.execute_calls == []
    assert connection.closed is True
