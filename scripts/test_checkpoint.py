#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""断点续跑 checkpoint 单元测试。

覆盖：① build_graph 能挂/不挂 checkpointer；② 持久化跨"重启"（新连接/新 saver 读到旧检查点）。
运行：python scripts/test_checkpoint.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.conductor.graph import build_graph


def test_build_graph_default_no_checkpointer():
    g = build_graph()
    assert getattr(g, "checkpointer", None) in (None, False), "默认不应带 checkpointer"


def test_build_graph_accepts_checkpointer():
    g = build_graph(checkpointer=MemorySaver())
    assert getattr(g, "checkpointer", None) is not None, "应挂上 checkpointer"


class _S(TypedDict):
    n: int
    log: list


def _mini_graph(saver):
    def a(s):
        return {"n": s["n"] + 1, "log": s.get("log", []) + ["a"]}

    def b(s):
        return {"n": s["n"] + 10, "log": s.get("log", []) + ["b"]}

    g = StateGraph(_S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile(checkpointer=saver)


def test_persist_across_restart():
    """跑完一次后状态落盘；模拟重启（全新连接/saver）仍能读到该 thread 的检查点。"""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async def run():
        path = os.path.join(tempfile.gettempdir(), "mangrove_ckpt_test.sqlite")
        if os.path.exists(path):
            os.remove(path)
        cfg = {"configurable": {"thread_id": "task-x"}}
        # 第一次运行（落盘）
        conn1 = await aiosqlite.connect(path)
        s1 = AsyncSqliteSaver(conn1)
        await s1.setup()
        r = await _mini_graph(s1).ainvoke({"n": 0, "log": []}, config=cfg)
        await conn1.close()
        assert r["n"] == 11 and r["log"] == ["a", "b"], r
        # 模拟重启：全新连接 + saver 读同一文件
        conn2 = await aiosqlite.connect(path)
        s2 = AsyncSqliteSaver(conn2)
        snap = await _mini_graph(s2).aget_state(cfg)
        await conn2.close()
        assert snap.values.get("n") == 11, snap.values
        assert snap.values.get("log") == ["a", "b"], snap.values

    asyncio.run(run())


def main():
    tests = [
        test_build_graph_default_no_checkpointer,
        test_build_graph_accepts_checkpointer,
        test_persist_across_restart,
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
