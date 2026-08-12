# -*- coding: utf-8 -*-
"""脚本式测试的共享隔离夹具。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_collector_registry():
    """每个测试后恢复采集器注册表，防止假采集器污染后续路由测试。"""
    from src.collectors import registry

    snapshot = dict(registry._REGISTRY)
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(snapshot)
