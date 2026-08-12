"""
RSSHub 采集器（Tier 5，轻量社媒/内容关键词采集）。

RSSHub（开源 MIT）把大量无官方 RSS 的站点转成标准 RSS。对"微博/什么值得买搜关键词"
这类需求，它比 mediacrawler 轻得多（无需 Cookie、无浏览器、反爬弱、对站友好），是
search + mediacrawler 之外的第三条稳路。

定位：mediacrawler(0) 之后、ecommerce(10) 之前——配了 mediacrawler 时社媒仍优先走它
（能搜关键词、拿评论、更深），失败/未配时 RSSHub 作为轻量承接。

仅当配置了 RSSHUB_BASE_URL、且平台命中下方关键词路由表、且是关键词发现型任务时接管。
只收录**已在 RSSHub 源码核实存在**的关键词路由（微博/B站/百度/什么值得买；诚实匹配，表外不冒充）。
新增路由：在 RSSHub 源码确认 `lib/routes/<站>/` 下有对应关键词路由文件后，加一行「平台名→模板」即可。
自托管：docker run -d -p 1200:1200 diygod/rsshub，再在 .env 配 RSSHUB_BASE_URL=http://localhost:1200。
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import quote

import httpx

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._common import html_to_text
from ._net import get_rate_limiter, pick_headers, retry_async, smart_client
from .base import BaseCollector, CollectedItem, CollectResult
from .platforms import normalize_platform
from .registry import register

logger = logging.getLogger(__name__)

# 平台名 → RSSHub 关键词路由模板（{kw} 为 URL 编码后的关键词）。
# 仅收录**已在 RSSHub 源码核实存在**的关键词检索路由；表外平台不接管（诚实匹配）。
# 核实来源：github.com/DIYgod/RSSHub/lib/routes/{weibo/keyword,bilibili/vsearch,baidu/search,smzdm/keyword}
# 路由的 RSSHub 实例侧前置条件差异很大（2026-07-03 本地 diygod/rsshub 实测）：
#   · 百度：✅ 开箱即用（无 cookie、无浏览器）——最省心，且是"全网搜索"覆盖面最广；
#   · 什么值得买：需 RSSHub 侧配 SMZDM_COOKIE（否则 503 ConfigNotFoundError）；
#   · 微博：需访客/登录 cookie（默认会 "Cooling down"，配 WEIBO cookie 更稳）；
#   · B站 vsearch：需自带浏览器的镜像 `diygod/rsshub:chromium-bundled`（默认镜像报 browser 缺失）。
# 采集器对以上失败均正确捕获并自动降级（百度实测端到端跑通 5 条正文）。
_RSSHUB_KEYWORD_ROUTES = {
    # 微博关键词（/weibo/keyword/:keyword）——需 RSSHub 侧微博 cookie
    "微博": "/weibo/keyword/{kw}",
    "weibo": "/weibo/keyword/{kw}",
    # B站视频搜索（/bilibili/vsearch/:kw）——需 chromium-bundled 镜像
    "b站": "/bilibili/vsearch/{kw}",
    "B站": "/bilibili/vsearch/{kw}",
    "哔哩哔哩": "/bilibili/vsearch/{kw}",
    "bilibili": "/bilibili/vsearch/{kw}",
    "bili": "/bilibili/vsearch/{kw}",
    # 百度全网搜索（/baidu/search/:keyword）——✅ 开箱即用，用户点名"去百度搜X"时拿 RSSHub
    # 结构化结果，比抓百度 HTML 更干净、覆盖面最广。
    "百度": "/baidu/search/{kw}",
    "baidu": "/baidu/search/{kw}",
    # 什么值得买关键词好价（/smzdm/keyword/:keyword）——需 RSSHub 侧 SMZDM_COOKIE
    "什么值得买": "/smzdm/keyword/{kw}",
    "smzdm": "/smzdm/keyword/{kw}",
    "值得买": "/smzdm/keyword/{kw}",
}

try:
    import feedparser  # type: ignore

    _FEEDPARSER_OK = True
except Exception:
    feedparser = None  # type: ignore
    _FEEDPARSER_OK = False

# 正则兜底：从 RSS <item> 中提取 title/link/description
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r"<link>\s*(.*?)\s*</link>", re.IGNORECASE | re.DOTALL)
_DESC_RE = re.compile(r"<description>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</description>", re.IGNORECASE | re.DOTALL)


def _resolve_route(spec: TaskSpec) -> Optional[str]:
    """从平台名解析出 RSSHub 关键词路由模板，无法识别返回 None。"""
    for p in spec.platforms:
        canon = normalize_platform(p)
        route = (
            _RSSHUB_KEYWORD_ROUTES.get(p.strip())
            or _RSSHUB_KEYWORD_ROUTES.get(p.strip().lower())
            or _RSSHUB_KEYWORD_ROUTES.get(canon)
        )
        if route:
            return route
    return None


def _parse_feed(xml_text: str) -> List[dict]:
    """解析 RSS，返回 [{title, link, content}]。优先 feedparser，回退正则。"""
    out: List[dict] = []
    if _FEEDPARSER_OK:
        try:
            feed = feedparser.parse(xml_text)
            for e in (feed.entries or []):
                # RSSHub 的正文常在 description/summary，含 HTML → 转纯文本
                raw = e.get("description") or e.get("summary") or ""
                _, text = html_to_text(raw) if raw else ("", "")
                out.append({
                    "title": (e.get("title") or "").strip(),
                    "link": (e.get("link") or "").strip(),
                    "content": (text or raw).strip(),
                })
            if out:
                return out
        except Exception:
            logger.debug("feedparser 解析 RSSHub 失败，回退正则", exc_info=True)
    # 正则兜底
    for block in _ITEM_RE.findall(xml_text):
        title_m = _TITLE_RE.search(block)
        link_m = _LINK_RE.search(block)
        desc_m = _DESC_RE.search(block)
        raw = (desc_m.group(1).strip() if desc_m else "")
        _, text = html_to_text(raw) if raw else ("", "")
        out.append({
            "title": (title_m.group(1).strip() if title_m else ""),
            "link": (link_m.group(1).strip() if link_m else ""),
            "content": (text or raw).strip(),
        })
    return out


class RsshubCollector(BaseCollector):
    name = "rsshub"
    tier = 5  # mediacrawler(0) 之后、ecommerce(10) 之前

    def is_available(self) -> bool:
        return bool(settings.rsshub_base_url)

    def matches(self, spec: TaskSpec) -> bool:
        # 关键词发现型（无显式 URL）+ 平台命中关键词路由表
        if spec.urls or not spec.keywords:
            return False
        return _resolve_route(spec) is not None

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not self.is_available():
            return CollectResult(False, self.name, message="RSSHub 未配置（RSSHUB_BASE_URL）")
        route = _resolve_route(spec)
        if not route:
            return CollectResult(False, self.name, message="平台无对应 RSSHub 关键词路由")

        kw = " ".join(spec.keywords).strip()
        base = settings.rsshub_base_url.rstrip("/")
        url = base + route.format(kw=quote(kw, safe=""))
        limit = max(1, min(spec.max_items, settings.collector_max_items))

        await get_rate_limiter().acquire(url)
        try:
            # RSSHub 多为自托管（localhost），smart_client 判定后直连（绕 Clash 系统代理劫持）
            async def _get() -> "httpx.Response":
                async with smart_client(url, timeout=30.0) as client:
                    return await client.get(url, headers=pick_headers())
            resp = await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            return CollectResult(False, self.name, message=f"RSSHub 拉取失败（检查服务/路由）: {e}")

        entries = _parse_feed(resp.text)
        items: List[CollectedItem] = []
        for e in entries[:limit]:
            content = e["content"]
            if not content:
                continue
            items.append(CollectedItem(
                url=e["link"], title=e["title"], content=content,
                metadata={"engine": self.name, "via": "rsshub", "route": route},
            ))
        if not items:
            return CollectResult(False, self.name, message="RSSHub feed 为空或无正文，交后续引擎兜底")
        return CollectResult(True, self.name, items=items, message=f"RSSHub 采集 {len(items)} 条（{route}）")


register(RsshubCollector())
