"""
Firecrawl 全网发现 + 抓取采集器（自托管）。

Firecrawl 自托管（⚠️ AGPL-3.0，内部自用通常可接受）：
- 关键词任务：用 /search 做"全网发现"，找到相关链接后再 scrape 取干净 Markdown；
- URL 任务：直接 scrape。

仅当安装了 firecrawl-py 且配置了 FIRECRAWL_BASE_URL 时可用，否则路由自动跳过。
自托管见 docker/firecrawl/。安装客户端：pip install firecrawl-py
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, List

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._recency import apply_recency, parse_time_range
from .base import BaseCollector, CollectedItem, CollectResult
from .platforms import resolve_domains, url_in_domains
from .registry import register

logger = logging.getLogger(__name__)

# 并发抓取上限（对齐自托管 firecrawl 的 MAX_CONCURRENT_JOBS/BROWSER_POOL_SIZE，避免压垮）
_SCRAPE_CONCURRENCY = 5
# 伪平台词：不是真实站点，不应拼进搜索 query（否则污染全网发现结果）
_PSEUDO_PLATFORMS = {"全网", "全网搜索", "不限平台", "不限", "全平台", "互联网", "网络", "全网搜"}

try:
    from firecrawl import AsyncFirecrawl  # type: ignore

    _FIRECRAWL_OK = True
except Exception:
    AsyncFirecrawl = None  # type: ignore
    _FIRECRAWL_OK = False


def _ensure_localhost_no_proxy() -> None:
    """确保本机 localhost 不经系统代理（向 no_proxy/NO_PROXY 追加 localhost、127.0.0.1）。"""
    hosts = {"localhost", "127.0.0.1"}
    for var in ("no_proxy", "NO_PROXY"):
        existing = {h.strip() for h in os.environ.get(var, "").split(",") if h.strip()}
        if not hosts <= existing:
            os.environ[var] = ",".join(sorted(existing | hosts))


def _get(obj: Any, *keys: str, default: Any = "") -> Any:
    """从 pydantic 对象或 dict 中取首个命中的字段，容错不同版本结构。"""
    for k in keys:
        if isinstance(obj, dict):
            if obj.get(k):
                return obj[k]
        else:
            v = getattr(obj, k, None)
            if v:
                return v
    return default


class FirecrawlCollector(BaseCollector):
    name = "firecrawl"
    tier = 20  # 全网发现优先：关键词任务先于通用引擎，发现链接后再抓取

    def is_available(self) -> bool:
        return _FIRECRAWL_OK and bool(settings.firecrawl_base_url)

    def matches(self, spec: TaskSpec) -> bool:
        # 仅处理显式 URL 抓取：firecrawl 的 /search 发现虽好，但 scrape 在中文反爬站
        # （知乎/汽车之家/搜狐等）几乎全失败，关键词发现统一交给 search 采集器（Tavily 内联正文更可靠）。
        return bool(spec.urls)

    def _client(self):
        # 自托管 Firecrawl 走 localhost；本机若为 Clash fake-ip，httpx(trust_env=True)
        # 会读 Windows 系统代理拦截 localhost 导致 502。firecrawl-py 不支持注入 httpx 客户端，
        # 故用 no_proxy 放行 localhost（仅追加，保留既有配置）。
        _ensure_localhost_no_proxy()
        return AsyncFirecrawl(
            api_key=settings.firecrawl_api_key or "self-hosted",
            api_url=settings.firecrawl_base_url.rstrip("/"),
        )

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not self.is_available():
            return CollectResult(False, self.name, message="firecrawl 未安装或未配置 FIRECRAWL_BASE_URL")

        app = self._client()
        limit = max(1, min(spec.max_items, settings.collector_max_items))

        # 站点限定：用户指定站点时只在该站内检索/抓取（与 search 采集器共用解析逻辑）
        domains = resolve_domains(spec.platforms, spec.site_domains)

        # 1) 确定要抓取的 URL：用户直给则用之，否则用 /search 全网发现
        target_urls: List[str] = list(spec.urls)
        if not target_urls and spec.keywords:
            # 过滤"全网/不限平台"等伪平台词，避免污染搜索 query
            real_platforms = [p for p in (spec.platforms or []) if p.strip() not in _PSEUDO_PLATFORMS]
            # 命中站点域名时用 site: 强制站内检索；多站点用 OR 连接
            if domains:
                site_expr = " OR ".join(f"site:{d}" for d in domains)
                site_expr = f"({site_expr})" if len(domains) > 1 else site_expr
                query = f"{site_expr} {' '.join(spec.keywords)}".strip()
            else:
                query = " ".join(real_platforms + spec.keywords).strip()
            try:
                data = await app.search(query, limit=limit)
            except Exception as e:
                return CollectResult(False, self.name, message=f"firecrawl 搜索失败: {e}")
            web = _get(data, "web", "results", "data", default=[]) or []
            for r in web:
                u = _get(r, "url", "link")
                # 防御性域名过滤：丢弃站点限定外的链接（site: 偶有漏网）
                if u and url_in_domains(u, domains):
                    target_urls.append(u)
        if not target_urls:
            scope = f"（限定 {', '.join(domains)}）" if domains else ""
            return CollectResult(False, self.name, message=f"firecrawl 未发现可抓取链接{scope}")
        target_urls = target_urls[:limit]

        # 2) 并发 scrape 取干净 Markdown（限并发，避免压垮自托管 firecrawl）
        sem = asyncio.Semaphore(_SCRAPE_CONCURRENCY)

        async def _scrape_one(url: str):
            async with sem:
                try:
                    doc = await app.scrape(url, formats=["markdown"])
                except Exception as e:
                    logger.warning("firecrawl 抓取失败 %s: %s", url, e)
                    return None
            content = str(_get(doc, "markdown", "content", default=""))
            meta = _get(doc, "metadata", default={}) or {}
            title = _get(meta, "title", "ogTitle", default="") if meta else ""
            if not content.strip():
                return None
            # 透传发布时间，供时效过滤/排序按发布日期判断
            published = _get(meta, "publishedTime", "articlePublishedTime", "publishedDate", default="") if meta else ""
            item_meta = {"engine": self.name}
            if published:
                item_meta["publishedTime"] = str(published)
            return CollectedItem(url=url, title=str(title), content=content, metadata=item_meta)

        results = await asyncio.gather(*[_scrape_one(u) for u in target_urls])
        items: list[CollectedItem] = [r for r in results if r]
        # 时效：按发布日期过滤过旧条目并倒序（"最新"优先）
        items = apply_recency(items, parse_time_range(spec.time_range))

        if not items:
            return CollectResult(False, self.name, message="firecrawl 未取得内容")
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 个页面")


register(FirecrawlCollector())
