"""
URL 去重缓存：防止同一 URL 被不同采集器或不同任务反复抓取。

进程内内存实现，重启清零。对单次任务而言直接生效；对定时任务可减少
短期内重复抓取。未来可替换为 Redis/文件持久化。
"""
from __future__ import annotations

import logging
import time
from typing import Set, Tuple

logger = logging.getLogger(__name__)

# url -> (timestamp, source_collector)
_seen: dict[str, Tuple[float, str]] = {}

# 默认缓存窗口（秒）：在此窗口内相同 URL 跳过。0 表示不设窗口（仅本次进程去重）
DEFAULT_TTL = 300  # 5 分钟


def seen(url: str, ttl: float = DEFAULT_TTL) -> bool:
    """URL 是否在缓存窗口内已被抓取过。"""
    if not url:
        return False
    if url not in _seen:
        return False
    ts, src = _seen[url]
    if ttl > 0 and time.time() - ts > ttl:
        # 过期，允许重新抓取
        del _seen[url]
        return False
    logger.debug("URL 去重命中（%s 抓过，%.0fs前）: %s", src, time.time() - ts, url[:80])
    return True


def mark(url: str, source: str = "") -> None:
    """标记 URL 已被抓取。"""
    if url:
        _seen[url] = (time.time(), source or "unknown")


def stats() -> dict:
    """返回去重缓存的统计信息。"""
    now = time.time()
    active = sum(1 for ts, _ in _seen.values() if now - ts <= DEFAULT_TTL)
    return {"total": len(_seen), "active": active, "ttl": DEFAULT_TTL}


def clear() -> None:
    """清空缓存（测试用）。"""
    _seen.clear()
