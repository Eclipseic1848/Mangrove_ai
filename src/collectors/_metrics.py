"""
采集器指标收集（进程内存，重启清零）。

每个采集器的调用次数、成功率、延迟累计，供仪表盘展示（累计口径，不随时间衰减）。
另维护一个最近 N 次调用的滚动窗口，供 registry 路由做"近期健康度"降权——累计口径
会被早期偶发失败长期拖累、好转后也很难回升，滚动窗口能反映"最近是不是真的不行"。
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict

# {collector_name: {"calls": int, "successes": int, "total_ms": float}}（累计，供仪表盘展示）
_metrics: Dict[str, dict] = {}

# {collector_name: deque[bool]}，仅存最近 _WINDOW_SIZE 次成功/失败，供健康度判断
_WINDOW_SIZE = 20
_recent: Dict[str, Deque[bool]] = {}

# 判定"近期不健康"的最小样本数与成功率阈值：样本不足时不判断，避免偶发失败误伤
_MIN_SAMPLES = 5
_UNHEALTHY_THRESHOLD = 0.2


def record(name: str, success: bool, latency_ms: float) -> None:
    """记录一次采集器调用。latency_ms 为耗时（毫秒）。"""
    m = _metrics.setdefault(name, {"calls": 0, "successes": 0, "total_ms": 0.0})
    m["calls"] += 1
    if success:
        m["successes"] += 1
    m["total_ms"] += latency_ms
    _recent.setdefault(name, deque(maxlen=_WINDOW_SIZE)).append(success)


def is_unhealthy(name: str) -> bool:
    """近期（最近 _WINDOW_SIZE 次）成功率是否低到该临时降权。

    只降权、不排除候选：registry 侧仍会把它排到健康采集器之后再试，
    既让它有机会靠新的成功记录自愈，也避免某任务类型仅此一个候选时被完全排除。
    """
    window = _recent.get(name)
    if not window or len(window) < _MIN_SAMPLES:
        return False
    return (sum(window) / len(window)) < _UNHEALTHY_THRESHOLD


def snapshot() -> dict:
    """返回所有采集器的指标快照：{name: {calls, success_rate, avg_ms}}。"""
    out = {}
    for name, m in _metrics.items():
        calls = m["calls"]
        out[name] = {
            "calls": calls,
            "success_rate": round(m["successes"] / calls, 3) if calls > 0 else 0.0,
            "avg_ms": round(m["total_ms"] / calls, 1) if calls > 0 else 0.0,
        }
    return out


def clear() -> None:
    """清空指标（测试用）。"""
    _metrics.clear()
    _recent.clear()
