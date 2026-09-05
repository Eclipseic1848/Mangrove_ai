# -*- coding: utf-8 -*-
"""测试根 conftest：performance / db_live 标记默认跳过（plan Phase 2 Task 12 + Phase 3）。

常规 `pytest` 跳过 @pytest.mark.performance / @pytest.mark.db_live 标记的测试；
`pytest --run-performance` / `pytest --run-db-live` 显式开启。
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_remote_services_and_memory(monkeypatch, tmp_path):
    """单元测试默认隔离局域网服务和生产记忆目录。"""
    from src.config.settings import settings
    from src.memory import lessons, templates

    monkeypatch.setattr(settings, "mineru_enabled", False)
    monkeypatch.setattr(settings, "paddleocr_vl_enabled", False)
    monkeypatch.setattr(settings, "embedding_enabled", False)
    monkeypatch.setattr(settings, "rerank_base_url", "")
    monkeypatch.setattr(settings, "jwt_secret", "isolated-test-signing-key-" + "x" * 32)
    monkeypatch.setattr(settings, "data_prep_db_secret_key", "isolated-test-database-key-" + "x" * 32)
    monkeypatch.setattr(lessons, "LESSONS_DIR", tmp_path / "lessons")
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path / "templates")


def pytest_addoption(parser):
    parser.addoption(
        "--run-performance",
        action="store_true",
        default=False,
        help="运行 @pytest.mark.performance 标记的大规模性能测试",
    )
    parser.addoption(
        "--run-db-live",
        action="store_true",
        default=False,
        help="运行 @pytest.mark.db_live 标记的数据库实连测试（需 DSN 环境变量）",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-performance"):
        _skip_marker(items, "performance", "大规模性能测试，需 --run-performance 显式开启")
    if not config.getoption("--run-db-live"):
        _skip_marker(items, "db_live", "需要真实 MySQL/PG 连接，需 --run-db-live 显式开启")


def _skip_marker(items, marker_name: str, reason: str):
    """按标记名统一跳过，避免重复代码。"""
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if marker_name in item.keywords:
            item.add_marker(skip)
