#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""个人记忆（按用户隔离）单元测试：store CRUD + 跨用户隔离 + personal_context() 注入格式。

运行：python scripts/test_user_memory.py
用临时 store 隔离，不碰真实 data/webui.db。
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.config.user_ctx import get_user_memories, set_user_memories
from src.memory.loader import personal_context
from tests.database_migration_helpers import migrated_webui_database


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(str(migrated_webui_database(tmp.name)))


def test_memory_add_and_list():
    store = _tmp_store()
    store.memory_add("u_a", "报告优先用表格呈现")
    store.memory_add("u_a", "默认采集汽车之家口碑")
    items = store.memory_list("u_a")
    assert len(items) == 2
    # 按创建时间倒序（最新的在前）
    assert items[0]["text"] == "默认采集汽车之家口碑"
    assert items[1]["text"] == "报告优先用表格呈现"


def test_memory_isolated_between_users():
    store = _tmp_store()
    store.memory_add("u_a", "A 的偏好")
    assert store.memory_list("u_b") == []
    assert len(store.memory_list("u_a")) == 1


def test_memory_delete_only_own():
    store = _tmp_store()
    item = store.memory_add("u_a", "A 的偏好")
    # B 不能删 A 的记忆
    assert store.memory_delete("u_b", item["id"]) is False
    assert len(store.memory_list("u_a")) == 1
    # A 能删自己的
    assert store.memory_delete("u_a", item["id"]) is True
    assert store.memory_list("u_a") == []


def test_memory_delete_nonexistent_returns_false():
    store = _tmp_store()
    assert store.memory_delete("u_a", 999) is False


def test_delete_user_cleans_up_memory():
    store = _tmp_store()
    user = store.create_user("tmp_user", "hash", role="user")
    store.memory_add(user["user_id"], "会被清理的记忆")
    assert len(store.memory_list(user["user_id"])) == 1
    store.delete_user(user["user_id"])
    assert store.memory_list(user["user_id"]) == []


def test_personal_context_empty_by_default():
    set_user_memories([])
    assert personal_context() == ""


def test_personal_context_format():
    set_user_memories(["报告优先用表格呈现", "默认采集汽车之家口碑"])
    ctx = personal_context()
    assert "# 我的偏好（当前用户的个人记忆）" in ctx
    assert "- 报告优先用表格呈现" in ctx
    assert "- 默认采集汽车之家口碑" in ctx
    set_user_memories([])  # 还原，避免影响其他测试


def test_user_ctx_get_set_roundtrip():
    set_user_memories(["x", "y"])
    assert get_user_memories() == ["x", "y"]
    set_user_memories([])


def test_personal_context_caps_at_20_items():
    """personal_context 最多注入 20 条记忆（超出部分不注入，防止挤占 intent prompt）。"""
    items = [f"记忆第{i}条" for i in range(1, 26)]  # 25 条
    set_user_memories(items)
    ctx = personal_context()
    for i in range(1, 21):  # 前 20 条应在
        assert f"记忆第{i}条" in ctx
    assert "记忆第21条" not in ctx  # 第 21 条不应出现
    assert "记忆第25条" not in ctx
    set_user_memories([])


def main():
    tests = [
        test_memory_add_and_list,
        test_memory_isolated_between_users,
        test_memory_delete_only_own,
        test_memory_delete_nonexistent_returns_false,
        test_delete_user_cleans_up_memory,
        test_personal_context_empty_by_default,
        test_personal_context_format,
        test_personal_context_caps_at_20_items,
        test_user_ctx_get_set_roundtrip,
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
