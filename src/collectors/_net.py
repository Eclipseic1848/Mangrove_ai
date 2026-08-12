"""
采集层网络加固（无代理依赖）：按域名限速、UA/Header 轮换、失败指数退避重试。

均为进程内、零第三方依赖的通用工具，供各 URL 采集器共用，统一控速与重试，
降低被封概率、提升弱网/偶发失败下的成功率。参数来自 settings，可在 .env 调整。
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

import logging

logger = logging.getLogger(__name__)
from urllib.parse import urlsplit

from src.config import settings

T = TypeVar("T")

# 常见真实浏览器 UA（轮换用；过旧会更易被识别，可定期更新）
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
_ACCEPT_LANG = ["zh-CN,zh;q=0.9,en;q=0.8", "zh-CN,zh;q=0.9", "zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3"]
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


def pick_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """生成一组随机化的请求头（开关关闭时用固定首项，保证可复现）。"""
    if settings.collector_rotate_ua:
        ua = random.choice(_UA_POOL)
        lang = random.choice(_ACCEPT_LANG)
    else:
        ua, lang = _UA_POOL[0], _ACCEPT_LANG[0]
    headers = {"User-Agent": ua, "Accept-Language": lang, "Accept": _ACCEPT}
    if extra:
        headers.update(extra)
    return headers


_META_CHARSET_RE = re.compile(rb'charset=[\"\']?\s*([a-zA-Z0-9_-]+)', re.IGNORECASE)


def decode_html(resp: Any) -> str:
    """按页面真实声明的字符集解码 HTML 正文。

    httpx 仅信任 Content-Type 响应头里的 charset；国内不少站（如 autohome.com.cn）
    响应头不带 charset、只在 <meta charset=gbk> 里声明，httpx 猜测容易乱码，
    导致 Trafilatura 提取出的"正文"实为乱码、被误判为反爬/内容质量问题。
    响应头已带 charset 时沿用 httpx 的判断（更权威）；否则从原始字节里探 <meta> 声明补解码，
    探测/解码失败一律回退 resp.text，不影响现有行为。
    """
    if "charset=" in resp.headers.get("content-type", "").lower():
        return resp.text
    m = _META_CHARSET_RE.search(resp.content[:2048])
    if m:
        charset = m.group(1).decode("ascii", "ignore").lower()
        try:
            return resp.content.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    return resp.text


class AsyncRateLimiter:
    """按域名的区间随机间隔限速（进程内，异步安全）。同站点串行控速，跨站点互不影响。

    每次 acquire 在 [min_interval, max_interval] 内随机取一个目标间隔，模拟真人不规律节奏，
    避免被平台按“固定节律”识别。max_interval 省略或不大于 min 时退化为固定间隔（向后兼容）。
    """

    def __init__(self, min_interval: float, max_interval: Optional[float] = None) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.max_interval = max(self.min_interval, float(max_interval)) if max_interval is not None else self.min_interval
        self._last: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(url).netloc or url

    def _next_interval(self) -> float:
        """区间随机：上限大于下限时取 uniform，否则用固定下限。"""
        if self.max_interval > self.min_interval:
            return random.uniform(self.min_interval, self.max_interval)
        return self.min_interval

    async def acquire(self, url: str) -> None:
        """在对 url 所属域名发起请求前调用：必要时 sleep 以满足本次随机间隔。"""
        if self.min_interval <= 0 and self.max_interval <= 0:
            return
        host = self._host(url)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next_interval() - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()


# 全局共享限速器：所有采集器共用一份，对同一站点统一控速
_GLOBAL_LIMITER: Optional[AsyncRateLimiter] = None


def get_rate_limiter() -> AsyncRateLimiter:
    global _GLOBAL_LIMITER
    if _GLOBAL_LIMITER is None:
        _GLOBAL_LIMITER = AsyncRateLimiter(
            settings.collector_min_interval_seconds,
            settings.collector_max_interval_seconds,
        )
    return _GLOBAL_LIMITER


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: Optional[int] = None,
    base: Optional[float] = None,
    max_delay: Optional[float] = None,
    jitter: bool = True,
) -> T:
    """对零参异步调用做指数退避重试。retries 为额外重试次数（总尝试 = retries+1）。"""
    retries = settings.collector_max_retries if retries is None else retries
    base = settings.collector_retry_base_seconds if base is None else base
    max_delay = settings.collector_retry_max_seconds if max_delay is None else max_delay

    attempt = 0
    while True:
        try:
            return await fn()
        except Exception:
            if attempt >= retries:
                raise
            delay = min(base * (2 ** attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random()  # 抖动，避免同步重试
            await asyncio.sleep(delay)
            attempt += 1


def get_http_proxy() -> Optional[str]:
    """从 MC 代理配置读取 HTTP 代理 URL，供 article/search/simple_http 复用。

    仅当 mc_enable_ip_proxy 为 True 且 provider 为 static（隧道代理）时返回代理 URL；
    快代理/豌豆等动态代理池方案需要各自的 SDK 集成，不适合直接作为 httpx proxy 参数。
    未启用或 provider 非 static 时返回 None（直连）。
    """
    if not settings.mc_enable_ip_proxy:
        return None
    if settings.mc_ip_proxy_provider == "static" and settings.mc_static_proxy_url:
        return settings.mc_static_proxy_url
    # kuaidaili/wandouhttp 需要动态获取 IP，不适合直接用 httpx proxy
    return None


# ── 网络智能分流（让采集免疫本机 VPN 开关）──────────────────────────
# 核心矛盾：开 VPN（Clash TUN/全局）会劫持 localhost/LAN 且国内站对境外 IP 更易风控；
# 关 VPN 则境外服务（Google/DDG/Tavily 等）不通。解法：按目标域名分流——
#   · 国内 / 本地 / 未知域名 → 强制直连（trust_env=False，绕开系统代理劫持），VPN 开关都不受影响；
#   · 已知境外服务 → 优先用配置的境外代理，否则读系统代理（trust_env=True，需 VPN）。
# 这样绝大多数采集目标（国内新闻/社媒/电商/招投标）永远直连成功，与 VPN 状态解耦。

# 需要代理才能访问的境外服务域名（搜索引擎 / AI 搜索 API / 海外社媒等）
_OVERSEAS_DOMAINS = {
    "google.com", "googleapis.com", "gstatic.com",
    "duckduckgo.com",
    "tavily.com",
    "slack.com",
    "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "reddit.com", "medium.com",
    "wikipedia.org",
}


def _host_of(url: str) -> str:
    """取 URL 主机名（去端口/用户信息/前导 www），小写。"""
    host = (urlsplit(url if "//" in url else f"//{url}").netloc or "").split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_overseas(url: str) -> bool:
    """目标是否为「需代理访问」的境外服务（据此决定直连还是走系统代理）。"""
    host = _host_of(url)
    return any(host == d or host.endswith("." + d) for d in _OVERSEAS_DOMAINS)


def smart_client(url: str, **kwargs: Any) -> "httpx.AsyncClient":
    """按目标域名智能分流的 httpx.AsyncClient 工厂（让采集与 VPN 状态解耦）。

    · 境外服务：优先用配置的 static 境外代理；否则 trust_env=True 读系统代理（需 VPN 开）。
    · 国内 / 本地 / 未知：强制直连（trust_env=False + 无代理），绕开 Clash 系统代理劫持，
      VPN 开或关都能直连成功——这是绝大多数采集目标（国内站）稳定采到数据的关键。

    kwargs 透传给 httpx.AsyncClient（默认 follow_redirects=True、timeout=30）。
    """
    import httpx  # 局部导入，避免模块级硬依赖顺序问题
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("timeout", 30.0)
    if is_overseas(url):
        proxy = get_http_proxy()
        if proxy:
            return httpx.AsyncClient(proxy=proxy, trust_env=False, **kwargs)
        return httpx.AsyncClient(trust_env=True, **kwargs)  # 读系统代理（VPN）
    # 国内 / 本地 / 未知：强制直连，绕系统代理劫持
    return httpx.AsyncClient(trust_env=False, **kwargs)


async def probe_network() -> Dict[str, bool]:
    """轻量网络探测：分别试直连一个国内站与一个境外站，返回 {'domestic':bool,'overseas':bool}。

    仅用于启动诊断/日志，容错静默；不影响采集流程。境外走 smart_client（会尝试系统代理）。
    """
    import httpx
    result = {"domestic": False, "overseas": False}
    # 国内：直连百度（绕系统代理）
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=6.0) as c:
            r = await c.get("https://www.baidu.com", headers=pick_headers())
            result["domestic"] = r.status_code < 500
    except Exception:
        pass
    # 境外：经 smart_client（含代理分流）试 google
    try:
        async with smart_client("https://www.google.com", timeout=6.0) as c:
            r = await c.get("https://www.google.com", headers=pick_headers())
            result["overseas"] = r.status_code < 500
    except Exception:
        pass
    logger.info("网络探测：国内直连=%s，境外可达=%s", result["domestic"], result["overseas"])
    return result
