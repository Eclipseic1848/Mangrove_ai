"""
V2EX 专用采集器（Tier 13）。

用 V2EX 遗留公开 JSON API（免密钥、无需登录）拉热门/最新话题或指定节点话题，
命中"V2EX"平台名或 v2ex.com 域名 URL 时启用。

接口（均为 GET，返回话题数组）：
- https://www.v2ex.com/api/topics/hot.json         最近热门话题
- https://www.v2ex.com/api/topics/latest.json       最新话题
- https://www.v2ex.com/api/topics/show.json?node_name=<节点>  指定节点话题（如 python/qna）

注：V2EX 新版 v2 API 需要 Personal Access Token 才能搜索/翻页，这里只用免鉴权的
遗留接口做"热门/最新/指定节点"发现，够用且零配置。
"""
from __future__ import annotations

import logging
import re
from typing import List

import httpx

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._net import get_rate_limiter, retry_async, smart_client
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)

_HOT_URL = "https://www.v2ex.com/api/topics/hot.json"
_LATEST_URL = "https://www.v2ex.com/api/topics/latest.json"
_NODE_URL = "https://www.v2ex.com/api/topics/show.json"

# 关键词里若出现常见节点名，直接按该节点取话题（比全量热门更贴合诉求）
_KNOWN_NODES = {
    "python", "go", "java", "javascript", "rust", "android", "ios", "macos",
    "linux", "windows", "docker", "kubernetes", "aws", "programmer", "qna",
    "share", "create", "jobs", "deals", "apple", "google", "agent",
}


def _is_v2ex(spec: TaskSpec) -> bool:
    for p in spec.platforms:
        if p.strip().lower() in ("v2ex",):
            return True
    return any("v2ex.com" in u for u in spec.urls)


def _pick_node(keywords: List[str]) -> str:
    """从关键词里识别已知节点名（如"python 招聘"->python）；未命中返回空串走热门。"""
    for kw in keywords:
        low = kw.strip().lower()
        if low in _KNOWN_NODES:
            return low
    return ""


class V2exCollector(BaseCollector):
    """V2EX 论坛话题采集器——httpx 直调公开 JSON API，免密钥。"""

    name = "v2ex"
    tier = 13  # rsshub(5)/ecommerce(10) 之后、rss(12) 之后、article(15) 之前

    def is_available(self) -> bool:
        return True  # httpx 硬依赖，恒可用

    def matches(self, spec: TaskSpec) -> bool:
        return _is_v2ex(spec)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        limit = max(1, min(spec.max_items, settings.collector_max_items))
        node = _pick_node(spec.keywords)
        url = f"{_NODE_URL}?node_name={node}" if node else (_HOT_URL if not spec.keywords else _LATEST_URL)

        await get_rate_limiter().acquire(url)

        async def _get() -> httpx.Response:
            async with smart_client(url, timeout=15.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp

        try:
            resp = await retry_async(_get)
            data = resp.json()
        except Exception as e:
            return CollectResult(False, self.name, message=f"V2EX 接口请求失败: {e}")

        if not isinstance(data, list) or not data:
            return CollectResult(False, self.name, message="V2EX 未返回话题数据")

        # 关键词过滤（标题/内容命中任一关键词）；无关键词（纯"看V2EX热门"）则不过滤
        kw_list = [k.strip().lower() for k in spec.keywords if k.strip().lower() not in _KNOWN_NODES]
        items: List[CollectedItem] = []
        for topic in data:
            if not isinstance(topic, dict):
                continue
            title = topic.get("title", "") or ""
            content = topic.get("content", "") or ""
            if kw_list and not any(kw in title.lower() or kw in content.lower() for kw in kw_list):
                continue
            node_info = topic.get("node") or {}
            member = topic.get("member") or {}
            items.append(CollectedItem(
                url=topic.get("url", ""),
                title=title,
                content=content or title,
                metadata={
                    "engine": self.name,
                    "node": node_info.get("title", ""),
                    "author": member.get("username", ""),
                    "replies": topic.get("replies", 0),
                    "created": topic.get("created", ""),
                },
            ))
            if len(items) >= limit:
                break

        if not items:
            return CollectResult(False, self.name, message="V2EX 未发现与关键词相关的话题")

        source = f"节点「{node}」" if node else ("最新" if spec.keywords else "热门")
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 条 V2EX {source}话题")


register(V2exCollector())
