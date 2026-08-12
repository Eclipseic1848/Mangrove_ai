"""
域名级反爬健康度追踪（进程内存，重启清零）。

`platforms.ANTI_SCRAPE_DOMAINS` 是手工维护的已知强反爬站清单，不会随时间自动
增补或淘汰。本模块追踪"非隐身引擎"（article/site_crawler/firecrawl/crawl4ai/
simple_http）在具体域名上的近期成败，一旦某域名近期持续失败（样本足够且成功率
过低），临时把它并入反爬短路判断——让 scrapling(Camoufox) 对这个新出现的
"疑似强反爬站"也提前尝试，避免继续在注定失败的中间引擎上空跑。

只统计"非隐身引擎"：scrapling 自身失败不计入（它已是终极兜底，它失败换个思路
没意义，不该反过来触发"短路去用它"这个逻辑）；mediacrawler/ecommerce 等专属
平台采集器也不计入（域名反爬和平台专属采集是两个不同维度）。

粒度权衡：collect_node 里一次采集器调用对应整个 TaskSpec（可能含多个 URL），
这里按 spec.urls 逐个域名同步记录同一次成败——多域名混合任务会有噪声，但
实际任务多为单域名（分析一篇文章/一批同站招标），这个近似足够用。

误判后的两条自愈路径（这是本模块最容易被忽略的一点：一旦域名被判定短路，
scrapling 会抢先尝试且通常会成功，"非隐身引擎"反而再没机会被重新调用去
证明自己已经恢复——如果只靠"新的成功记录挤掉旧的失败记录"，会陷入永久
短路、无法自愈的死角）：
1. **时间衰减**——记录带时间戳，超过 _TTL_SECONDS 的旧记录在判断时自动忽略；
   即使短路后非隐身引擎很少再被调用，旧的失败记录也会随时间过期，样本不足
   后自动解除短路判定，域名下次任务会被重新按 tier 正常路由测试。
2. **手动重置**——`reset(domain)` 供确认误判时立即释放，见
   `GET/DELETE /api/config/domain-health`。
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# 只统计这些"非隐身"引擎的成败；scrapling/mediacrawler/ecommerce 等不计入
_TRACKED_COLLECTORS = {"article", "site_crawler", "firecrawl", "crawl4ai", "simple_http"}

_WINDOW_SIZE = 10
_MIN_SAMPLES = 3
_UNHEALTHY_THRESHOLD = 0.25
_TTL_SECONDS = 1800  # 30 分钟：超过此时长的记录判断时自动忽略，误判/临时问题不会永久生效

# {domain: deque[(timestamp, success)]}
_recent: Dict[str, Deque[Tuple[float, bool]]] = {}


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def record(collector_name: str, urls: list, success: bool) -> None:
    """记录一次"非隐身引擎"对该任务涉及域名的采集成败。"""
    if collector_name not in _TRACKED_COLLECTORS:
        return
    now = time.time()
    for url in urls:
        domain = _domain(url)
        if not domain:
            continue
        _recent.setdefault(domain, deque(maxlen=_WINDOW_SIZE)).append((now, success))


def _valid_samples(domain: str) -> List[bool]:
    """过滤掉超过 TTL 的旧记录，只保留近期有效样本。"""
    window = _recent.get(domain)
    if not window:
        return []
    cutoff = time.time() - _TTL_SECONDS
    return [success for ts, success in window if ts >= cutoff]


def is_learned_anti_scrape(url: str) -> bool:
    """该 URL 所在域名是否因近期持续失败被临时判定为"疑似强反爬站"。"""
    valid = _valid_samples(_domain(url))
    if len(valid) < _MIN_SAMPLES:
        return False
    return (sum(valid) / len(valid)) < _UNHEALTHY_THRESHOLD


def list_flagged() -> Dict[str, dict]:
    """列出当前被判定为"疑似强反爬"的域名及其近期样本数/成功率（供管理员查看）。"""
    out: Dict[str, dict] = {}
    for domain in list(_recent.keys()):
        valid = _valid_samples(domain)
        if len(valid) >= _MIN_SAMPLES and (sum(valid) / len(valid)) < _UNHEALTHY_THRESHOLD:
            out[domain] = {"samples": len(valid), "success_rate": round(sum(valid) / len(valid), 3)}
    return out


def reset(domain: Optional[str] = None) -> None:
    """手动释放：domain 为空清空全部记录，否则只清该域名（确认误判时立即恢复正常路由，不必等 TTL 过期）。"""
    if domain is None:
        _recent.clear()
    else:
        _recent.pop(domain, None)


def clear() -> None:
    """清空记录（测试用，等价于 reset()）。"""
    _recent.clear()
