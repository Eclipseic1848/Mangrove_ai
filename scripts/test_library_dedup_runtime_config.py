#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""巡检开关接入配置中心 REGISTRY 单测：分组可见性 + 热更新 + 坏值拒绝。

运行：python scripts/test_library_dedup_runtime_config.py
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database
from src.config import runtime_config as rc
from src.config.settings import settings


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(str(migrated_webui_database(tmp.name)))


def test_describe_includes_library_dedup_group():
    store = _tmp_store()
    groups = rc.describe(store)
    group = next((g for g in groups if g["key"] == "library_dedup"), None)
    assert group is not None, "library_dedup 分组未出现在 describe() 结果里"
    keys = {it["key"] for it in group["items"]}
    assert keys == {
        "library_dedup_scan_enabled",
        "library_dedup_scan_interval_hours",
        "library_stale_draft_days",
        "library_dedup_scan_max_merges_per_run",
    }, keys


def test_describe_scan_enabled_is_select_type():
    store = _tmp_store()
    groups = rc.describe(store)
    group = next(g for g in groups if g["key"] == "library_dedup")
    item = next(it for it in group["items"] if it["key"] == "library_dedup_scan_enabled")
    assert item["type"] == "select"
    assert item["choices"] == ["True", "False"]


def test_set_global_hot_updates_scan_enabled():
    store = _tmp_store()
    original = settings.library_dedup_scan_enabled
    try:
        rc.set_global(store, "library_dedup_scan_enabled", "False", updated_by="u_admin")
        assert settings.library_dedup_scan_enabled is False
        rc.set_global(store, "library_dedup_scan_enabled", "True", updated_by="u_admin")
        assert settings.library_dedup_scan_enabled is True
    finally:
        settings.library_dedup_scan_enabled = original


def test_set_global_hot_updates_interval_hours():
    store = _tmp_store()
    original = settings.library_dedup_scan_interval_hours
    try:
        rc.set_global(store, "library_dedup_scan_interval_hours", "6", updated_by="u_admin")
        assert settings.library_dedup_scan_interval_hours == 6
    finally:
        settings.library_dedup_scan_interval_hours = original


def test_set_global_rejects_bad_bool():
    store = _tmp_store()
    try:
        rc.set_global(store, "library_dedup_scan_enabled", "not-a-bool", updated_by="u_admin")
        assert False, "应该抛 ValueError"
    except ValueError:
        pass


def main():
    tests = [
        test_describe_includes_library_dedup_group,
        test_describe_scan_enabled_is_select_type,
        test_set_global_hot_updates_scan_enabled,
        test_set_global_hot_updates_interval_hours,
        test_set_global_rejects_bad_bool,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
