"""
按用户隔离的凭证覆盖 —— contextvar 透传，任务执行期内生效。

链路：chat 网关 / 调度器在执行任务前 set_user_overrides(该用户的运行时配置)；
流水线内的读取点（LLM 供应商 API Key、平台 Cookie）经 effective(key) 取值：
用户覆盖 → 全局 settings（已含管理员全局覆盖）→ .env 基线。

contextvar 随 asyncio 任务自动继承/隔离，多用户并发任务互不串。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, List, Optional

from src.config.settings import settings

_OVERRIDES: ContextVar[Dict[str, str]] = ContextVar("user_config_overrides", default={})
_MEMORIES: ContextVar[List[str]] = ContextVar("user_memories", default=[])


def set_user_overrides(overrides: Dict[str, str]):
    """任务开始前调用；返回 token 可用于恢复（一般不需要，任务结束上下文即销毁）。"""
    return _OVERRIDES.set(dict(overrides or {}))


@contextmanager
def user_overrides_context(overrides: Dict[str, str]) -> Iterator[None]:
    """在当前请求/任务内临时启用用户配置，结束后恢复原上下文。"""
    token = set_user_overrides(overrides)
    try:
        yield
    finally:
        _OVERRIDES.reset(token)


def get_user_override(key: str) -> Optional[str]:
    v = _OVERRIDES.get().get(key)
    return v if v not in (None, "") else None


def effective(key: str) -> str:
    """取值链：用户覆盖 → settings（全局覆盖/.env）。返回字符串（未配置为空串）。"""
    ov = get_user_override(key)
    if ov is not None:
        return ov
    return str(getattr(settings, key, "") or "")


def set_user_memories(texts: List[str]):
    """任务开始前调用，传入当前用户的个人记忆文本列表（按用户隔离，与凭证覆盖同一套 contextvar 机制）。"""
    return _MEMORIES.set(list(texts or []))


def get_user_memories() -> List[str]:
    return list(_MEMORIES.get())
