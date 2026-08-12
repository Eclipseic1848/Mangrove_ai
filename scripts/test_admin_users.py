#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户管理搜索/分页测试（无 pytest，纯断言；失败抛异常并以非零退出）。

运行：python scripts/test_admin_users.py
覆盖：WebUIStore.list_users 的关键词/角色/状态过滤 + 分页边界，
以及 admin_routes.list_users 路由返回结构（直接调用路由函数，绕开 FastAPI 依赖注入）。
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore


def _seed(store: WebUIStore):
    store.create_user("alice", "hash", "Alice Zhang", role="admin")
    store.create_user("bob", "hash", "Bob Li", role="user")
    store.create_user("carol", "hash", "Carol Wang", role="user", pending=True)
    store.create_user("dave", "hash", "Dave Chen", role="user")
    store.update_user(store.get_user_by_name("dave")["user_id"], disabled=True)


def test_search_by_username_or_display_name():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        users, total = store.list_users(q="ali")
        assert total == 1 and users[0]["username"] == "alice", (total, users)
        users2, total2 = store.list_users(q="Wang")
        assert total2 == 1 and users2[0]["username"] == "carol", (total2, users2)


def test_filter_by_role():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        users, total = store.list_users(role="admin")
        assert total == 1 and users[0]["username"] == "alice", (total, users)


def test_filter_by_status():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        _, pending_total = store.list_users(status="pending")
        assert pending_total == 1, pending_total
        _, disabled_total = store.list_users(status="disabled")
        assert disabled_total == 1, disabled_total
        _, normal_total = store.list_users(status="normal")
        assert normal_total == 2, normal_total  # alice + bob（dave 已禁用，carol 待审批）


def test_pagination_boundaries():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        for i in range(25):
            store.create_user(f"user{i:02d}", "hash", f"用户{i:02d}")
        page1, total = store.list_users(page=1, page_size=20)
        assert total == 25 and len(page1) == 20, (total, len(page1))
        page2, total2 = store.list_users(page=2, page_size=20)
        assert total2 == 25 and len(page2) == 5, (total2, len(page2))
        page3, total3 = store.list_users(page=3, page_size=20)
        assert total3 == 25 and len(page3) == 0, (total3, len(page3))  # 超出范围返回空列表而非报错


def test_route_response_shape():
    from src.api.routes import admin_routes
    import src.api.auth as auth_mod

    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        original = auth_mod._store
        auth_mod._store = store
        try:
            out = admin_routes.list_users(
                q="", role="", status="", page=1, page_size=20,
                admin={"role": "super_admin"},
            )
        finally:
            auth_mod._store = original
        assert out["total"] == 4, out["total"]
        assert out["pending_total"] == 1, out["pending_total"]
        assert out["page"] == 1 and out["page_size"] == 20, out
        assert len(out["users"]) == 4, out["users"]


def main():
    tests = [
        test_search_by_username_or_display_name,
        test_filter_by_role,
        test_filter_by_status,
        test_pagination_boundaries,
        test_route_response_shape,
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
