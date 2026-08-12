"""
通用 HTTP 兜底采集器（零额外依赖，仅用 httpx + 标准库）。

作用：在 Crawl4AI/Scrapling 等引擎尚未安装或全部失败时，仍能对静态页面拿到内容，
保证"任意网站架构上都能进流程"。质量较粗，是最低优先级的兜底层。
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._common import build_target_urls
from ._extract import extract_content
from ._net import decode_html, get_rate_limiter, pick_headers, retry_async, smart_client
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)


class SimpleHttpCollector(BaseCollector):
    name = "simple_http"
    tier = 90  # 兜底，最低优先级

    def matches(self, spec: TaskSpec) -> bool:
        # 无 URL 的关键词发现由 search 负责，兜底也只兜有 URL 的任务
        return bool(spec.urls)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        urls = build_target_urls(spec)
        if not urls:
            return CollectResult(False, self.name, message="无可采集的 URL 或关键词")

        urls = urls[: max(1, min(spec.max_items, settings.collector_max_items))]
        items: list[CollectedItem] = []
        results = await asyncio.gather(
            *(self._fetch_one(u) for u in urls), return_exceptions=True
        )
        for r in results:
            if isinstance(r, CollectedItem):
                items.append(r)
            else:
                logger.warning("simple_http 抓取失败: %s", r)

        if not items:
            return CollectResult(False, self.name, message="所有 URL 抓取失败")
        return CollectResult(True, self.name, items=items, message=f"抓取 {len(items)} 个页面")

    async def _fetch_one(self, url: str) -> CollectedItem:
        # 按域名限速 + UA/Header 轮换 + 失败指数退避；smart_client 按域名智能分流（国内直连/境外走代理）
        await get_rate_limiter().acquire(url)

        async def _do() -> httpx.Response:
            async with smart_client(url) as client:
                resp = await client.get(url, headers=pick_headers())
                resp.raise_for_status()
                return resp

        resp = await retry_async(_do)
        title, text, meta = extract_content(decode_html(resp), url)
        # 正文为空视为抓取失败（如 JS 渲染站静态抓不到内容），抛出以触发降级
        if not (text or "").strip():
            raise ValueError(f"正文为空（可能为 JS 渲染页）: {url}")
        return CollectedItem(
            url=url,
            title=title,
            content=text,
            metadata={"status_code": resp.status_code, "engine": self.name, **meta},
        )


register(SimpleHttpCollector())
