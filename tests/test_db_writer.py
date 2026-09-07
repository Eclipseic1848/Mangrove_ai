"""MySQL 外部写在连接前拒绝，不再进入历史 Schema/数据库错误分支。"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.config.settings import settings
from src.conductor.db_writer import write_items
from src.external_readonly import ExternalWriteForbidden


@pytest.mark.parametrize("database_error", [1146, 1054, 1213])
def test_mysql_is_rejected_before_schema_or_database_operations(monkeypatch, database_error):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        raise RuntimeError(database_error)

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(connect=connect))
    monkeypatch.setattr(settings, "db_backend", "mysql")
    with pytest.raises(ExternalWriteForbidden, match="外部只读"):
        write_items("task-1", [{"content": "虚构数据"}], source="test")
    assert calls == []
