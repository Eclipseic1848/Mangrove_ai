"""
搜索发现采集器（Tier 25）—— 关键词任务的正确入口。

替代"把搜索引擎结果页当内容抓"的旧兜底：先用一次真实搜索做"链接发现"
（命中已知平台时用 site: 限定到该站，对应"去懂车帝/汽车之家搜X"），
拿到真实结果链接后再逐个抓取正文解析。

后端（SEARCH_PROVIDER=auto 时优先级：AnySearch → 免费优先 → Tavily 兜底省额度）：
- AnySearch（配了 ANYSEARCH_API_KEY 时）：统一实时搜索，16 垂直领域+批量搜索+内联正文，
  对反爬站最可靠；auto 模式下最先尝试（直接拿正文，免二次抓取）。
- SearXNG（自托管元搜索，开源免费无限）：聚合 Bing/百度/DDG 等，对中文站覆盖好；
  返回结果链接后用 crawl4ai/httpx 抓正文。见 docker/searxng/。
- ddgs（DuckDuckGo 官方库，免密钥免费）：比手搓抓取稳，但有限流；同样发现链接再抓正文。
  未安装 ddgs 库时退回内置正则抓取 DDG HTML。
- Tavily（配了 TAVILY_API_KEY 时）：为 AI 代理设计，直接返回链接+正文，最可靠但有月额度；
  auto 模式下仅作 AnySearch/SearXNG/DDG 之后的兜底。

抓取：crawl4ai（浏览器渲染，适配 JS 站如懂车帝），未装则退回 httpx。
限速/UA 轮换/退避复用 _net；httpx 为硬依赖，故本采集器恒可用。

数量补齐（2026-07-03）：不再"首命中即返回"——单个后端结果不足 max_items 时
继续用后续后端累积（跨后端按 URL 去重），凑够或穷尽/超时间预算才返回，
根治关键词任务"采到几条就交差"且无引擎可补的问题。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, unquote, urlsplit

import httpx

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from . import _anysearch
from ._common import html_to_text
from ._dedup import mark as dedup_mark, seen as dedup_seen
from ._extract import extract_content
from ._net import decode_html, get_rate_limiter, pick_headers, retry_async, smart_client
from ._recency import apply_recency, parse_time_range
from .base import BaseCollector, CollectedItem, CollectResult
from .platforms import resolve_domains
from .registry import register

# 时效档位 → 各搜索后端的时间过滤参数取值
_DDG_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}

logger = logging.getLogger(__name__)

# DuckDuckGo 结果链接重定向参数：href 形如 .../l/?uddg=<URL编码后的真实链接>
_UDDG_RE = re.compile(r'uddg=([^&"\']+)')


def _resolve_domain(spec: TaskSpec) -> str:
    """从平台名/site_domains 解析站点域名（内置表优先，未命中用规划器给的域名），未命中返回空串。

    站点域名解析下沉到 platforms.resolve_domains，与 firecrawl 等采集器共用，确保"指定站点"
    约束在任何采集路径下一致生效。多站点时取第一个作 site: 限定。
    """
    domains = resolve_domains(spec.platforms, spec.site_domains)
    return domains[0] if domains else ""


class SearchDiscoveryCollector(BaseCollector):
    name = "search"
    tier = 25  # firecrawl(20) 之后、crawl4ai(30) 之前；关键词任务的主入口

    def matches(self, spec: TaskSpec) -> bool:
        # 只处理"有关键词、无显式 URL"的发现型任务
        return bool(spec.keywords) and not spec.urls

    def is_available(self) -> bool:
        """恒可用（httpx 为硬依赖），但启动时诊断搜索后端配置并发出可操作警告。"""
        backends = self._discovery_backends()
        has_anysearch = self._use_anysearch()
        has_tavily = self._use_tavily()
        if not backends and not has_tavily and not has_anysearch:
            logger.warning(
                "search 采集器：高级搜索后端均未配置！将使用 Baidu/Google HTML 兜底"
                "（不稳定，易被反爬）。建议 docker compose -f docker/searxng/docker-compose.yml up -d"
                " 部署 SearXNG，或在 .env 配置 ANYSEARCH_API_KEY / TAVILY_API_KEY"
            )
        else:
            available = (["anysearch"] if has_anysearch else []) + backends + (["tavily"] if has_tavily else [])
            logger.info(
                "search 采集器后端就绪：%s（终极兜底: baidu_html + google_html）",
                ", ".join(available),
            )
        return True

    def _use_tavily(self) -> bool:
        prov = (settings.search_provider or "auto").lower()
        return bool(settings.tavily_api_key) and prov in ("auto", "tavily")

    def _use_anysearch(self) -> bool:
        """AnySearch 是否可用（配了 Key + provider 允许）。"""
        prov = (settings.search_provider or "auto").lower()
        return bool(settings.anysearch_api_key) and prov in ("auto", "anysearch")

    def _discovery_backends(self) -> List[str]:
        """按 SEARCH_PROVIDER 决定"链接发现"后端顺序（纯逻辑，便于测试）。"""
        prov = (settings.search_provider or "auto").lower()
        if prov == "tavily":
            return []  # 只用 Tavily，不走链接发现
        if prov == "searxng":
            return ["searxng"] if settings.searxng_base_url else []
        if prov in ("ddgs", "duckduckgo"):
            return ["ddg"]
        # auto：免费优先，SearXNG（配了才用）→ DDG
        backends = []
        if settings.searxng_base_url:
            backends.append("searxng")
        backends.append("ddg")
        return backends

    async def collect(self, spec: TaskSpec) -> CollectResult:
        domain = _resolve_domain(spec)
        limit = max(1, min(spec.max_items, settings.collector_max_items))
        query, _ = self._build_query(spec)
        # 时效：解析时间窗，向搜索后端传时间档位，并在抓取后按发布日期过滤+倒序
        window = parse_time_range(spec.time_range)
        label = window.label if window else None

        kw = " ".join(spec.keywords).strip()
        tavily_done = False
        # 尝试域名序列：指定平台时先"站内限定"，采不到再"全网兜底"（用户所选策略：先试平台，失败全网）
        dom_seq = [domain, ""] if domain else [""]

        # 跨后端累积补齐：单个后端不足 limit 时继续用后续后端挖（按 URL 去重），
        # 凑够即返回、穷尽有多少交多少——根治"首命中即交差"导致的数量不足。
        # 时间预算：search 是关键词任务唯一通用引擎，若累积挖掘超时被 collect 节点
        # 的 wait_for 掐掉会丢失全部已采数据，故超预算即带着现有结果提前返回。
        deadline = time.monotonic() + settings.collect_timeout_seconds * 0.8
        merged: List[CollectedItem] = []
        seen_urls: set = set()
        parts: List[str] = []  # 各后端贡献说明（含站内/全网标注），拼进结果 message

        def _note(dom: str) -> str:
            """结果说明：站内命中标注限定域名；全网兜底时明确告知数据非来自指定平台。"""
            if dom:
                return f"（限定 {dom}）"
            return f"（全网兜底，数据非来自 {domain}）" if domain else "（全网）"

        def _add(items: List[CollectedItem], source: str, dom: str) -> None:
            """合并一批结果：按 URL（无 URL 用标题+正文前缀）去重，总量不超过 limit。"""
            added = 0
            for it in items:
                key = (it.url or "").strip() or (it.title or "") + (it.content or "")[:200]
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                merged.append(it)
                added += 1
                if len(merged) >= limit:
                    break
            if added:
                parts.append(f"{source} {added} 条{_note(dom)}")

        def _done() -> bool:
            """凑够目标或时间预算耗尽即收工——没数据也要按时返回，
            否则被 collect 节点的外层 wait_for 掐死，已采数据全丢且只报"超时"。"""
            return len(merged) >= limit or time.monotonic() > deadline

        def _result() -> CollectResult:
            if not merged:  # 预算耗尽且一无所获：如实报失败，让路由降级/前端如实提示
                return CollectResult(False, self.name,
                                     message=f"时间预算内未发现与「{query}」相关的内容")
            msg = " + ".join(parts)
            if len(merged) < limit:
                msg += f"（目标 {limit}，已尽力补采）"
            return CollectResult(True, self.name, items=merged, message=msg)

        async def _try_anysearch(dom: str) -> None:
            """AnySearch 检索（16 垂直领域 + 内联正文，反爬站最可靠），结果并入累积。"""
            try:
                need = limit - len(merged)
                items = apply_recency(
                    await self._search_anysearch(kw, dom, limit=need, time_label=label), window
                )
            except Exception as e:
                logger.warning("AnySearch 搜索失败: %s", e)
                return
            _add(items, "anysearch", dom)

        async def _try_tavily(dom: str) -> None:
            """Tavily 检索（直接返回内联正文，对反爬站最可靠、且快），结果并入累积。"""
            try:
                need = limit - len(merged)
                items = apply_recency(await self._search_tavily(kw, dom, limit=need, time_label=label), window)
            except Exception as e:
                logger.warning("Tavily 搜索失败: %s", e)
                return
            _add(items, "tavily", dom)

        # 0) AnySearch 优先（配了 Key 时最先尝试，16 垂直领域 + 内联正文，对反爬站最可靠）
        if self._use_anysearch():
            for dom in dom_seq:
                await _try_anysearch(dom)
                if _done():
                    return _result()

        # 1) Tavily 正文优先（auto + 开关开 + 有 key）：站内→全网依次尝试，直接拿内联正文，最快
        if settings.search_tavily_first and self._use_tavily():
            tavily_done = True
            for dom in dom_seq:
                await _try_tavily(dom)
                if _done():
                    return _result()

        # 2) 免费链接发现后端（SearXNG / DDG）→ 抓正文：同样站内→全网兜底
        for dom in dom_seq:
            # 站内用 site: 限定；全网兜底用纯关键词（避免带平台名反而排除掉其它来源）
            q = f"site:{dom} {kw}".strip() if dom else kw
            for backend in self._discovery_backends():
                if _done():
                    return _result()
                try:
                    links = await self._discover_one(backend, q, dom, limit=limit, time_label=label)
                except Exception as e:
                    logger.warning("%s 发现失败: %s", backend, e)
                    continue
                # 过滤已采 URL、只抓还缺的数量，避免重复抓取浪费时间
                links = [u for u in links if u not in seen_urls][: limit - len(merged)]
                if not links:
                    continue
                items = apply_recency(await self._fetch_all(links, deadline), window)
                _add(items, backend, dom)

        # 3) Tavily 兜底（若上面未优先用过；省额度的旧顺序下在此触发）：站内→全网
        if self._use_tavily() and not tavily_done:
            for dom in dom_seq:
                if _done():
                    return _result()
                await _try_tavily(dom)

        # 4) 终极兜底：直接抓搜索引擎 HTML 页面提取链接（不稳定、易被反爬，但不会全面失败）
        for dom in dom_seq:
            q = f"site:{dom} {kw}".strip() if dom else kw
            # 顺序：百度(中文最好) → 必应(国内直连、免VPN、Google 的直连替代) → 谷歌(需VPN)
            for backend in ["baidu_html", "bing_html", "google_html"]:
                if _done():
                    return _result()
                try:
                    links = await self._discover_one(backend, q, dom, limit=limit)
                except Exception as e:
                    logger.warning("%s HTML 兜底失败: %s", backend, e)
                    continue
                links = [u for u in links if u not in seen_urls][: limit - len(merged)]
                if not links:
                    continue
                items = apply_recency(await self._fetch_all(links, deadline), window)
                _add(items, backend + "兜底", dom)

        if merged:
            return _result()

        hint = ""
        if not settings.searxng_base_url and not settings.tavily_api_key:
            hint = "（免密钥搜索易被反爬；建议自托管 SearXNG 或配 TAVILY_API_KEY）"
        return CollectResult(False, self.name, message=f"未发现与「{query}」相关的内容{hint}")

    async def _discover_one(self, backend: str, query: str, domain: str, *, limit: int,
                            time_label: Optional[str] = None) -> List[str]:
        """按后端名分派链接发现。"""
        if backend == "searxng":
            return await self._discover_searxng(query, domain, limit=limit, time_label=time_label)
        if backend == "ddg":
            return await self._discover_ddg(query, domain, limit=limit, time_label=time_label)
        if backend == "baidu_html":
            return await self._discover_baidu_html(query, domain, limit=limit)
        if backend == "bing_html":
            return await self._discover_bing_html(query, domain, limit=limit)
        if backend == "google_html":
            return await self._discover_google_html(query, domain, limit=limit)
        return []

    async def _search_tavily(self, keywords: str, domain: str, *, limit: int,
                             time_label: Optional[str] = None) -> List[CollectedItem]:
        """调用 Tavily 搜索 API：返回带正文的结果（可 include_domains 限定到目标站）。

        Tavily 对部分 URL（如移动版子域名）只返回简短摘要，本方法在拿到结果后
        会对短内容（< 200 字）的条目补采完整正文，确保 data.json 落盘数据丰满。
        """
        payload: dict = {
            "query": keywords,
            "max_results": limit,
            "search_depth": "basic",
            "include_raw_content": True,
        }
        if domain:
            payload["include_domains"] = [domain]
        if time_label:  # Tavily 支持 time_range=day|week|month|year
            payload["time_range"] = time_label
        url = settings.tavily_base_url.rstrip("/") + "/search"
        headers = {"Authorization": f"Bearer {settings.tavily_api_key}", "Content-Type": "application/json"}
        # Tavily 为境外服务，smart_client 判定后走代理（配了 static 境外代理优先，否则读系统代理）
        async def _post() -> "httpx.Response":
            async with smart_client(url, timeout=30.0) as client:
                return await client.post(url, json=payload, headers=headers)
        resp = await retry_async(_post)
        resp.raise_for_status()
        data = resp.json()
        items: List[CollectedItem] = []
        for r in (data.get("results") or [])[:limit]:
            content = (r.get("raw_content") or r.get("content") or "").strip()
            if not content:
                continue
            items.append(CollectedItem(
                url=r.get("url", ""), title=r.get("title", ""), content=content,
                metadata={"engine": self.name, "via": "tavily"},
            ))

        # 补采：Tavily 对部分 URL（如移动版子域名）仅返回简短摘要，
        # 重新抓取这些页面以获取完整正文写入 data.json
        thin_urls = [item.url for item in items if len(item.content or "") < 200]
        if thin_urls:
            logger.info("Tavily 返回 %d 条摘要型结果，补采完整正文…", len(thin_urls))
            for item in items:
                if item.url not in thin_urls:
                    continue
                await get_rate_limiter().acquire(item.url)
                try:
                    # 补采目标为结果页（多为国内站），按域名智能分流抓取
                    async def _get(u=item.url) -> "httpx.Response":
                        async with smart_client(u, timeout=30.0) as client:
                            return await client.get(u, headers=pick_headers())
                    resp2 = await retry_async(_get)
                    resp2.raise_for_status()
                    title2, text2, meta2 = extract_content(decode_html(resp2), item.url)
                    if (text2 or "").strip() and len(text2) > len(item.content or ""):
                        item.content = text2
                        if title2:
                            item.title = title2
                        item.metadata["via"] = f"tavily+{meta2.get('via', 'httpx')}"
                except Exception as e:
                    logger.warning("Tavily 补采失败 %s: %s", item.url, e)

        return items

    async def _search_anysearch(self, keywords: str, domain: str, *, limit: int,
                                 time_label: Optional[str] = None) -> List[CollectedItem]:
        """调用 AnySearch API：直接返回带正文的结果（内联正文，免二次抓取）。

        AnySearch 支持垂直领域搜索，对反爬站比传统搜索引擎更可靠；
        结果已含完整正文时跳过补采（与 Tavily 不同，AnySearch 正文通常已足够）。
        """
        items = await _anysearch.search(keywords, max_results=limit)
        if not items:
            return []

        # AnySearch 不直接支持 site: 限定，但可通过 domain 垂直领域间接限定；
        # 通用搜索时在结果端按域名过滤，精度低于 Tavily 的 include_domains 但够用
        if domain:
            filtered = []
            for item in items:
                if domain in (item.url or ""):
                    filtered.append(item)
            if filtered:
                items = filtered
            # 不全部滤掉——AnySearch 通用搜索可能在其它站找到相关内容

        # 补采：AnySearch 对部分 URL 可能只返回摘要，短内容补抓完整正文
        thin_urls = [item.url for item in items if len(item.content or "") < 200]
        if thin_urls:
            logger.info("AnySearch 返回 %d 条摘要型结果，补采完整正文…", len(thin_urls))
            for item in items:
                if item.url not in thin_urls:
                    continue
                await get_rate_limiter().acquire(item.url)
                try:
                    async def _get(u: str = item.url) -> "httpx.Response":
                        async with smart_client(u, timeout=30.0) as client:
                            return await client.get(u, headers=pick_headers())
                    resp2 = await retry_async(_get)
                    resp2.raise_for_status()
                    title2, text2, meta2 = extract_content(decode_html(resp2), item.url)
                    if (text2 or "").strip() and len(text2) > len(item.content or ""):
                        item.content = text2
                        if title2:
                            item.title = title2
                        item.metadata["via"] = f"anysearch+{meta2.get('via', 'httpx')}"
                except Exception as e:
                    logger.warning("AnySearch 补采失败 %s: %s", item.url, e)

        return items

    def _build_query(self, spec: TaskSpec) -> Tuple[str, str]:
        """构造检索式与目标域名。命中平台域名则用 site: 限定。"""
        kw = " ".join(spec.keywords).strip()
        domain = _resolve_domain(spec)
        if domain:
            return f"site:{domain} {kw}", domain
        # 未知平台：把平台名并入查询提升相关性
        if spec.platforms:
            return f"{' '.join(spec.platforms)} {kw}".strip(), ""
        return kw, ""

    @staticmethod
    def _dedup_filter(urls: List[str], domain: str, limit: int) -> List[str]:
        """按域名过滤 + 去重 + 截断，保序。"""
        seen: set = set()
        out: List[str] = []
        for link in urls:
            if not link or not link.startswith("http"):
                continue
            if domain and not (urlsplit(link).netloc or "").endswith(domain):
                continue
            if link in seen:
                continue
            seen.add(link)
            out.append(link)
            if len(out) >= limit:
                break
        return out

    async def _discover_searxng(self, query: str, domain: str, *, limit: int,
                                time_label: Optional[str] = None) -> List[str]:
        """SearXNG JSON API 发现链接（自托管，免费无限）。"""
        base = settings.searxng_base_url.rstrip("/")
        url = f"{base}/search"
        params = {"q": query, "format": "json", "language": "zh-CN"}
        if time_label:  # SearXNG 支持 time_range=day|week|month|year
            params["time_range"] = time_label
        await get_rate_limiter().acquire(base)
        # trust_env=False：SearXNG 是自托管服务，绕过系统代理（否则 localhost 请求会被代理拦成 502）
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, trust_env=False) as client:
            resp = await retry_async(lambda: client.get(url, params=params, headers=pick_headers()))
            resp.raise_for_status()
            data = resp.json()
        urls = [r.get("url", "") for r in (data.get("results") or [])]
        return self._dedup_filter(urls, domain, limit)

    async def _discover_ddg(self, query: str, domain: str, *, limit: int,
                            time_label: Optional[str] = None) -> List[str]:
        """DuckDuckGo 发现链接：优先官方 ddgs 库（更稳），未装则退回正则抓 HTML。"""
        try:
            from ddgs import DDGS  # type: ignore
        except Exception:
            try:
                from duckduckgo_search import DDGS  # type: ignore  老包名兼容
            except Exception:
                DDGS = None  # type: ignore

        timelimit = _DDG_TIMELIMIT.get(time_label or "")
        if DDGS is not None:
            import asyncio

            def _run() -> List[str]:
                with DDGS() as ddgs:
                    # ddgs.text 支持 timelimit=d|w|m|y
                    return [r.get("href", "") for r in ddgs.text(query, max_results=limit * 2,
                                                                 timelimit=timelimit)]

            try:
                urls = await asyncio.to_thread(_run)
                links = self._dedup_filter(urls, domain, limit)
                if links:
                    return links
            except Exception as e:
                logger.warning("ddgs 库发现失败，退回正则抓取: %s", e)

        return await self._discover_ddg_html(query, domain, limit=limit, time_label=time_label)

    async def _discover_ddg_html(self, query: str, domain: str, *, limit: int,
                                 time_label: Optional[str] = None) -> List[str]:
        """兜底：直接抓 DuckDuckGo HTML/lite 并用正则解析（易被反爬 202）。"""
        endpoints = [
            "https://lite.duckduckgo.com/lite/?q=",
            "https://html.duckduckgo.com/html/?q=",
        ]
        timelimit = _DDG_TIMELIMIT.get(time_label or "")
        suffix = f"&df={timelimit}" if timelimit else ""
        collected: List[str] = []
        for base in endpoints:
            url = base + quote_plus(query) + suffix
            await get_rate_limiter().acquire(url)
            try:
                # DuckDuckGo 为境外服务，smart_client 判定后走代理
                async def _get(u=url) -> "httpx.Response":
                    async with smart_client(u, timeout=20.0) as client:
                        return await client.get(u, headers=pick_headers())
                resp = await retry_async(_get)
            except Exception as e:
                logger.warning("DDG HTML 发现失败 %s: %s", base, e)
                continue
            collected.extend(unquote(raw) for raw in _UDDG_RE.findall(resp.text))
            if len(self._dedup_filter(collected, domain, limit)) >= limit:
                break
        return self._dedup_filter(collected, domain, limit)

    async def _fetch_all(self, links: List[str],
                         deadline: Optional[float] = None) -> List[CollectedItem]:
        """抓取发现到的链接：优先 crawl4ai（渲染 JS），否则 httpx 静态抓取。

        deadline（time.monotonic 时刻）：逐条抓取前检查预算，超了立即带着已抓结果返回；
        单条抓取也按剩余预算设上限。否则慢站 × 多链接会烧穿 collect 节点的外层
        wait_for（90s），已抓数据全部丢失、只报"search 超时"。
        """
        try:
            from crawl4ai import AsyncWebCrawler  # type: ignore
        except Exception:
            AsyncWebCrawler = None  # type: ignore

        def _left() -> float:
            """剩余预算秒数；未传 deadline 时按单条 30s 上限。"""
            return min(30.0, deadline - time.monotonic()) if deadline else 30.0

        items: List[CollectedItem] = []
        if AsyncWebCrawler is not None:
            try:
                async with AsyncWebCrawler() as crawler:
                    for url in links:
                        if _left() <= 1.0:
                            return items  # 预算耗尽，带着已抓的先走
                        await get_rate_limiter().acquire(url)
                        try:
                            result = await asyncio.wait_for(
                                retry_async(lambda u=url: crawler.arun(url=u)), timeout=_left())
                        except Exception as e:
                            logger.warning("search 抓取失败 %s: %s", url, e)
                            continue
                        content = str(getattr(result, "markdown", "") or "")
                        meta = getattr(result, "metadata", {}) or {}
                        if content.strip():
                            items.append(CollectedItem(
                                url=url, title=meta.get("title", ""), content=content,
                                metadata={"engine": self.name, "via": "crawl4ai", **meta},
                            ))
                if items:
                    return items
            except Exception as e:
                logger.warning("crawl4ai 抓取链路异常，回退 httpx: %s", e)

        # 回退：httpx 静态抓取 + 正文提取级联（Trafilatura→readability→html_to_text）
        # 逐 url 用 smart_client 按域名分流：国内结果页强制直连（绕 Clash），境外走代理
        for url in links:
            if _left() <= 1.0:
                return items  # 预算耗尽，带着已抓的先走
            await get_rate_limiter().acquire(url)
            try:
                async def _get(u=url) -> "httpx.Response":
                    async with smart_client(u, timeout=min(30.0, _left())) as client:
                        return await client.get(u, headers=pick_headers())
                resp = await retry_async(_get)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("search httpx 抓取失败 %s: %s", url, e)
                continue
            if dedup_seen(url):
                continue
            title, text, meta = extract_content(decode_html(resp), url)
            if (text or "").strip():
                dedup_mark(url, self.name)
                items.append(CollectedItem(
                    url=url, title=title, content=text,
                    metadata={"engine": self.name, "via": meta.get("via", "httpx")},
                ))
        return items

    # ── 终极兜底：直接抓搜索引擎 HTML 页面提取链接（不依赖任何外部服务）──

    async def _discover_bing_html(self, query: str, domain: str, *, limit: int) -> List[str]:
        """必应 HTML 兜底：抓取 www.bing.com 结果并从 b_algo 结果块提取链接。
        必应国内可直连（免 VPN，是 Google 的直连替代）、免 API Key、中文结果尚可，
        比百度更少验证码拦截。实测：须用 www.bing.com（cn.bing.com 裸请求只回精简页）
        并带 setlang cookie，否则不返回结果块。
        """
        url = f"https://www.bing.com/search?q={quote_plus(query)}&mkt=zh-CN"
        await get_rate_limiter().acquire(url)
        # 必应国内可直连，smart_client 判定后走直连（绕系统代理劫持）；cookie 触发完整结果页
        headers = pick_headers({"Accept-Language": "zh-CN,zh;q=0.9", "Cookie": "SRCHHPGUSR=SRCHLANG=zh-Hans"})
        try:
            async def _get() -> "httpx.Response":
                async with smart_client(url, timeout=15.0) as client:
                    return await client.get(url, headers=headers)
            resp = await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("必应 HTML 兜底请求失败: %s", e)
            return []
        # 按结果块 <li class="b_algo"> 分割，每块取第一个外部真实链接；排除必应/微软自身域名
        links: List[str] = []
        for blk in re.split(r'<li class="b_algo"', resp.text)[1:]:
            m = re.search(r'<a [^>]*href="(https?://[^"]+)"', blk)
            if not m:
                continue
            raw = m.group(1)
            if any(k in raw for k in ("bing.com", "microsofttranslator", "go.microsoft", "microsoft.com")):
                continue
            links.append(raw)
        return self._dedup_filter(links, domain, limit)

    async def _discover_baidu_html(self, query: str, domain: str, *, limit: int) -> List[str]:
        """百度 HTML 兜底：抓取百度搜索结果的 HTML 并用正则提取链接。
        中文内容优于 Google，不依赖 API Key，但易被反爬/验证码拦截。
        """
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={min(limit * 3, 50)}"
        await get_rate_limiter().acquire(url)
        # 百度为国内站，smart_client 判定后强制直连（绕 Clash 系统代理劫持）
        try:
            async def _get() -> "httpx.Response":
                async with smart_client(url, timeout=15.0) as client:
                    return await client.get(url, headers=pick_headers({"Accept-Language": "zh-CN,zh;q=0.9"}))
            resp = await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("百度 HTML 兜底请求失败: %s", e)
            return []
        # 百度搜索结果中真实链接藏在 data-mu 属性或 href 的重定向参数中
        links: List[str] = []
        for raw in re.findall(r'data-mu="([^"]+)"', resp.text):
            links.append(raw)
        # 也尝试提取标准 href（部分结果使用）
        for raw in re.findall(r'href="(https?://[^"]+)"', resp.text):
            if any(k in raw for k in ("baidu.com", "gov.cn", "rec_banner", "javascript:")):
                continue
            links.append(raw)
        return self._dedup_filter(links, domain, limit)

    async def _discover_google_html(self, query: str, domain: str, *, limit: int) -> List[str]:
        """Google HTML 兜底：抓取 Google 搜索结果的 HTML 并用正则提取链接。
        百度优先（中文内容更好），Google 作备选。不依赖 API Key，易被反爬。
        """
        url = f"https://www.google.com/search?q={quote_plus(query)}&num={min(limit * 2, 50)}&hl=zh-CN"
        await get_rate_limiter().acquire(url)
        # Google 为境外服务，smart_client 判定后走代理
        try:
            async def _get() -> "httpx.Response":
                async with smart_client(url, timeout=15.0) as client:
                    return await client.get(url, headers=pick_headers({"Accept-Language": "zh-CN,zh;q=0.9"}))
            resp = await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Google HTML 兜底请求失败: %s", e)
            return []
        # Google 搜索结果链接以 /url?q=<真实URL> 形式出现
        links: List[str] = []
        for raw in re.findall(r'/url\?q=(https?://[^&"\']+)', resp.text):
            links.append(unquote(raw))
        return self._dedup_filter(links, domain, limit)


register(SearchDiscoveryCollector())
