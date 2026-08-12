# -*- coding: utf-8 -*-
"""LLM 出口"系统事实"注入测试。

锁住修复：所有经 chat/achat 的请求都会被注入"当前真实日期 + 禁止事实核查"前缀，
避免本地/公网模型因训练数据过时而误判年份/事件是否已发生（如把进行中的赛事判成"尚未举办"）。
"""
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import SystemMessage

from src.llm.provider import _SYS_CONTEXT_TMPL, _inject_system_context

_FAILED = []


def _check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        _FAILED.append(name)


def test_date_injected():
    """注入内容里含今天真实日期。"""
    out = _inject_system_context([{"role": "user", "content": "你好"}])
    today = datetime.now().strftime("%Y年%m月%d日")
    _check("test_date_injected", today in out[0]["content"])


def test_merge_into_existing_system():
    """已有 system 时并入其中（不新增第二条 system），且原文保留。"""
    out = _inject_system_context(
        [{"role": "system", "content": "原始提示"}, {"role": "user", "content": "hi"}]
    )
    sys_count = sum(1 for m in out if isinstance(m, dict) and m.get("role") == "system")
    first = out[0]["content"]
    _check(
        "test_merge_into_existing_system",
        sys_count == 1 and first.startswith("【系统事实") and "原始提示" in first,
    )


def test_insert_when_no_system():
    """无 system 时在最前插入一条。"""
    out = _inject_system_context([{"role": "user", "content": "hi"}])
    _check(
        "test_insert_when_no_system",
        out[0].get("role") == "system" and len(out) == 2,
    )


def test_systemmessage_object_handled():
    """首条为 langchain SystemMessage 对象时也能并入。"""
    out = _inject_system_context([SystemMessage(content="原始"), {"role": "user", "content": "hi"}])
    _check(
        "test_systemmessage_object_handled",
        isinstance(out[0], SystemMessage) and "原始" in out[0].content and "【系统事实" in out[0].content,
    )


def test_forbids_fact_check():
    """前缀明确禁止用训练知识否定事件真实性/是否已发生。"""
    txt = _SYS_CONTEXT_TMPL
    _check("test_forbids_fact_check", "严禁" in txt and "尚未发生" in txt)


def main():
    test_date_injected()
    test_merge_into_existing_system()
    test_insert_when_no_system()
    test_systemmessage_object_handled()
    test_forbids_fact_check()
    print("=" * 50)
    total = 5
    print(f"{total - len(_FAILED)}/{total} 通过")
    sys.exit(1 if _FAILED else 0)


if __name__ == "__main__":
    main()
