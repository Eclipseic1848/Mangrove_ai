#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cookie_health 存储层单测。运行：python scripts/test_cookie_health_store.py"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(str(migrated_webui_database(tmp.name)))


def test_set_and_get():
    store = _tmp_store()
    assert store.cookie_health_all() == {}
    store.cookie_health_set("jd_cookie", "valid", "京东 Cookie 有效", "manual")
    all_health = store.cookie_health_all()
    assert set(all_health.keys()) == {"jd_cookie"}
    row = all_health["jd_cookie"]
    assert row["status"] == "valid"
    assert row["message"] == "京东 Cookie 有效"
    assert row["checked_by"] == "manual"
    assert row["checked_at"]  # 非空时间戳


def test_overwrite():
    store = _tmp_store()
    store.cookie_health_set("mc_cookie_xhs", "valid", "ok", "manual")
    store.cookie_health_set("mc_cookie_xhs", "invalid", "登录过期", "scheduled")
    row = store.cookie_health_all()["mc_cookie_xhs"]
    assert row["status"] == "invalid"
    assert row["message"] == "登录过期"
    assert row["checked_by"] == "scheduled"


def test_multiple_keys_independent():
    store = _tmp_store()
    store.cookie_health_set("jd_cookie", "valid", "a", "manual")
    store.cookie_health_set("tb_cookie", "unknown", "b", "manual")
    all_health = store.cookie_health_all()
    assert set(all_health.keys()) == {"jd_cookie", "tb_cookie"}
    assert all_health["jd_cookie"]["status"] == "valid"
    assert all_health["tb_cookie"]["status"] == "unknown"


def main():
    tests = [test_set_and_get, test_overwrite, test_multiple_keys_independent]
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
