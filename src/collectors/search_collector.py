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
import hashlib
import io
import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import timedelta
from functools import wraps
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from src.account_execution import ExecutionDenied
from src.api.execution import execution_checkpoint, execution_to_thread
from src.config.settings import settings
from src.conductor.task_spec import TaskSpec
from src.timezone import now as beijing_now

from . import _anysearch, _deepseek
from ._common import crawler_browser_config, html_to_text
from ._dedup import mark as dedup_mark, seen as dedup_seen
from ._extract import extract_content
from ._net import decode_html, get_rate_limiter, pick_headers, retry_async, smart_client
from ._recency import apply_recency, extract_date_from_html, extract_publish_date, parse_time_range
from .base import BaseCollector, CollectedItem, CollectResult
from .platforms import resolve_domains, url_in_domains
from .registry import register

# 时效档位 → 各搜索后端的时间过滤参数取值
_DDG_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}

logger = logging.getLogger(__name__)

_SEARCH_SESSION = ContextVar("collector_search_session", default=None)


def _search_provider() -> str:
    session = _SEARCH_SESSION.get()
    return session["provider"] if session is not None else (settings.search_provider or "auto").lower()


def _frozen_search_config(operation):
    @wraps(operation)
    async def run(*args, **kwargs):
        if _SEARCH_SESSION.get() is not None:
            return await operation(*args, **kwargs)
        # 后端与平台凭据在本次采集内固定；热更新只能影响下一次采集。
        provider = (settings.search_provider or "auto").lower()
        session = {"provider": provider, "key": settings.deepseek_api_key if provider == "deepseek" else "",
            "base_url": settings.deepseek_base_url, "clients": []}
        token = _SEARCH_SESSION.set(session)
        try:
            result = await operation(*args, **kwargs)
            if provider == "deepseek":
                calls = [dict(call) for client in session["clients"] for call in client.calls]
                result.coverage["deepseek"] = {"calls": calls,
                    "billable_calls": sum(call["billable_calls"] for call in calls),
                    "billing_unknown": any(call["billing_unknown"] for call in calls)}
            return result
        finally:
            _SEARCH_SESSION.reset(token)
    return run


def _deepseek_client():
    session = _SEARCH_SESSION.get()
    base_url = session["base_url"] if session is not None else settings.deepseek_base_url
    # 自定义兼容网关的 Key 不授权外发到另一个源，即使它使用 DeepSeek 模型名。
    if str(base_url).rstrip("/") not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        raise ValueError("DeepSeek 独立搜索只能复用官方地址的平台凭据")
    client = _deepseek.DeepSeekToolsClient(session["key"] if session is not None else settings.deepseek_api_key)
    if session is not None:
        session["clients"].append(client)
    return client

# DuckDuckGo 结果链接重定向参数：href 形如 .../l/?uddg=<URL编码后的真实链接>
_UDDG_RE = re.compile(r'uddg=([^&"\']+)')
_MARKDOWN_LINK_RE = re.compile(r'(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


async def _response_document(response: httpx.Response, deadline: Optional[float]):
    raw = response.content
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    metadata = {"media_type": media_type, "body_bytes": len(raw)}
    if not (raw.lstrip().startswith(b"%PDF-") or media_type == "application/pdf"):
        binary = any(byte < 9 or 13 < byte < 32 for byte in raw[:4096])
        textual = not media_type or media_type.startswith("text/") or media_type == "application/xhtml+xml"
        if binary or (not textual and not (media_type == "application/octet-stream" and raw.lstrip().startswith(b"<"))):
            return "", "", {**metadata, "body_status": "unknown", "body_error_code": "SOURCE_BINARY_UNSUPPORTED"}
        return None

    def parse():
        try:
            import pdfplumber
            from src.parsers.pdf_render import validate_pdf_source

            validate_pdf_source(raw)
            with pdfplumber.open(io.BytesIO(raw)) as document:
                pages = []
                for page in document.pages:
                    execution_checkpoint()
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError()
                    pages.append((page.extract_text() or "").strip())
                if not pages or any(not text for text in pages):
                    return "", "", {**metadata, "body_status": "unknown", "body_error_code": "SOURCE_PDF_TEXT_INCOMPLETE"}
                title = document.metadata.get("Title", "")
                return title if isinstance(title, str) else "", "\n\n".join(pages), {
                    **metadata, "body_status": "complete", "via": "pdfplumber", "media_type": "application/pdf",
                    "document_pages": len(pages)}
        except ExecutionDenied:
            raise
        except Exception as error:
            code = "SOURCE_TIMEOUT" if isinstance(error, TimeoutError) else "SOURCE_PDF_PARSE_FAILED"
            return "", "", {**metadata, "body_status": "unknown", "body_error_code": code}

    # 只用既有本地提取链；解析失败不能降回 HTML，也不能悄悄外发 OCR。
    return await execution_to_thread(parse)


def _repeated_body(content: str) -> Optional[dict]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    unique = len(set(lines))
    if len(lines) >= 100 and unique * 5 < len(lines):
        return {"code": "SOURCE_PROVIDER_REPEATED_BODY",
            "raw_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "raw_chars": len(content),
            "nonempty_lines": len(lines), "unique_nonempty_lines": unique}
    return None


def _project_directory_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return not path or path.endswith("/") or bool(re.search(r"(?:^|/)index\.(?:s?html?|jhtml|aspx?|php)$", path))


def _project_directory_markdown(item: CollectedItem, domain: str = "") -> Optional[str]:
    """只识别目录线索，不把含完整表格的页面一概降成摘要。"""
    if not _project_directory_url(item.url):
        return None
    text = item.content or ""
    if re.search(r"<(?:a|table)\b", text, re.I):
        _preserve_project_html_links(item, text, domain or urlsplit(item.url).hostname or "")
    if item.metadata.get("project_table_complete") or re.search(r"(?m)^.*\|.*\n\s*\|?\s*:?-{3,}\s*\|", text):
        return None
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    return text if item.metadata.get("project_directory_hint") or len(_MARKDOWN_LINK_RE.findall(text)) >= 2 else None


def _preserve_project_html_links(item: CollectedItem, html: str, domain: str, *, capture_links: bool = False) -> None:
    """正文提取会丢href；项目目录只保存有限的站内线索，不保存整页HTML。"""
    if not capture_links and not _project_directory_url(item.url):
        return
    soup = BeautifulSoup(html, "html.parser")
    complete_table = False
    fields = ("项目名称", "业主", "建设内容", "采购人", "金额", "预算", "实施地点", "地区", "单位")
    for table in soup.find_all("table"):
        rows = [row.find_all(["th", "td"], recursive=False) for row in table.find_all("tr")]
        if len(rows) < 2 or len(rows[0]) < 2:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in rows[0]]
        if sum(any(field in header for field in fields) for header in headers) >= 2 and any(
                len(row) >= len(headers) and sum(bool(re.search(r"[A-Za-z\u4e00-\u9fff]", cell.get_text())) for cell in row) >= 2
                for row in rows[1:]):
            complete_table = True
            break
    links = []
    source = urlsplit(item.url)._replace(fragment="").geturl()
    anchors = soup.find_all("a", href=True, limit=500)
    seen = set()
    for anchor in anchors:
        label = anchor.get_text(" ", strip=True)[:200]
        target = urlsplit(urljoin(item.url, str(anchor.get("href"))))
        url = target._replace(fragment="").geturl()
        if (not label or label.isdecimal() or not domain or target.scheme not in {"http", "https"}
                or target.username is not None or url == source or url in seen or not url_in_domains(url, [domain])):
            continue
        parent = anchor.find_parent(["li", "p", "tr", "dd", "dt"])
        context = (parent.get_text(" ", strip=True) if parent is not None else label + " " + " ".join(
            node.strip() for node in anchor.next_siblings if isinstance(node, str)))[:400]
        links.append({"text": label, "url": url, "context": context})
        seen.add(url)
        if len(links) >= 100:
            break
    item.metadata.update(project_links=links, project_directory_hint=len(anchors) >= 2,
        project_table_complete=complete_table)


def _project_body_deadline(deadline: Optional[float]) -> Optional[float]:
    if deadline is None:
        return None
    # 原页补读只能用原job的一部分时间，给已发现目录的静态详情留有界余量。
    return deadline - min(5.0, max(0, deadline - time.monotonic()) * 0.4)


def _project_source_exclusion(item: CollectedItem) -> Optional[str]:
    """项目发现不读取数据库转储；明确招聘入口也不用于项目正文探测。"""
    parsed = urlsplit(item.url)
    if re.search(r"\.(?:sql|sqlite3?|db|bak|backup)(?:\.(?:gz|zip|bz2|xz))?$", parsed.path, re.I):
        return "SOURCE_NON_DOCUMENT"
    title = item.title or ""
    recruitment_url = (re.search(r"(?:^|\.)(?:zhaopin|recruit(?:ment)?|careers?)(?:\.|$)", parsed.hostname or "", re.I)
        or re.search(r"/(?:recruit(?:ment)?|careers?)(?:/|$)", parsed.path, re.I))
    if ((recruitment_url or re.search(r"招聘(?:平台|公告|信息|简章|启事|岗位)|(?:校园|社会)招聘", title))
            and not re.search(r"项目|工程|采购|建设", title)):
        return "SOURCE_RECRUITMENT"
    return None


def _source_error_code(error: Exception) -> str:
    """只记录固定类别，不把来源异常中的凭据或响应正文带入轨迹。"""
    if isinstance(error, _deepseek.DeepSeekToolsError):
        return error.code
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "SOURCE_TIMEOUT"
    if isinstance(error, httpx.HTTPStatusError):
        return f"SOURCE_HTTP_{error.response.status_code}"
    if isinstance(error, httpx.RequestError):
        return "SOURCE_CONNECTION_ERROR"
    if isinstance(error, json.JSONDecodeError):
        return "SOURCE_INVALID_JSON"
    if isinstance(error, ValueError):
        if str(error) == "AnySearch 来源返回错误":
            return "SOURCE_API_ERROR"
        if str(error) == "SearXNG 上游结果不完整":
            return "SOURCE_UPSTREAM_INCOMPLETE"
        return "SOURCE_FORMAT_ERROR"
    return "SOURCE_ERROR"


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
        prov = _search_provider()
        return bool(settings.tavily_api_key) and prov in ("auto", "tavily")

    def _use_anysearch(self) -> bool:
        """AnySearch 是否可用（配了 Key + provider 允许）。"""
        prov = _search_provider()
        return bool(settings.anysearch_api_key) and prov in ("auto", "anysearch")

    def _use_deepseek(self) -> bool:
        # 只在明确选择时启用，不能因已有模型 Key 而新增默认收费请求。
        return _search_provider() == "deepseek"

    def _discovery_backends(self) -> List[str]:
        """按 SEARCH_PROVIDER 决定"链接发现"后端顺序（纯逻辑，便于测试）。"""
        prov = _search_provider()
        if prov in ("tavily", "deepseek"):
            return []  # 独立 API 返回候选，不走免费链接发现。
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

    @_frozen_search_config
    async def collect(self, spec: TaskSpec) -> CollectResult:
        if spec.project_collection and not spec.urls:
            return await self._collect_projects(spec)
        if self._use_deepseek():
            result = await self._collect_candidates(spec, keywords=list(dict.fromkeys(spec.keywords)),
                limit=max(1, min(spec.max_items, settings.collector_max_items)), topics=[])
            result.message = f"DeepSeek 获取 {len(result.items)} 条资料，是否符合任务仍须业务核验"
            return result
        domains = resolve_domains(spec.platforms, spec.site_domains)
        limit = max(1, min(spec.max_items, settings.collector_max_items))
        query, _ = self._build_query(spec)
        # 时效：解析时间窗，向搜索后端传时间档位，并在抓取后按发布日期过滤+倒序
        window = parse_time_range(spec.time_range)
        label = window.label if window else None

        kw = " ".join([*spec.keywords, spec.time_range or ""]).strip()
        tavily_done = False
        # 换搜索后端不能放宽用户的站点范围；多个站点逐一尝试，无站点约束才搜索全网。
        dom_seq = domains or [""]

        # 跨后端累积补齐：单个后端不足 limit 时继续用后续后端挖（按 URL 去重），
        # 凑够即返回、穷尽有多少交多少——根治"首命中即交差"导致的数量不足。
        # 时间预算：search 是关键词任务唯一通用引擎，若累积挖掘超时被 collect 节点
        # 的 wait_for 掐掉会丢失全部已采数据，故超预算即带着现有结果提前返回。
        deadline = time.monotonic() + settings.collect_timeout_seconds * 0.8
        merged: List[CollectedItem] = []
        pending: dict[str, CollectedItem] = {}
        seen_urls: set = set()
        parts: List[str] = []  # 各后端贡献说明（含站内/全网标注），拼进结果 message

        def _note(dom: str) -> str:
            """结果说明保留当前检索范围。"""
            if dom:
                return f"（限定 {dom}）"
            return "（全网）"

        def _add(items: List[CollectedItem], source: str, dom: str) -> None:
            """合并一批结果：按 URL（无 URL 用标题+正文前缀）去重，总量不超过 limit。"""
            added = 0
            for it in items:
                if dom and not url_in_domains(it.url or "", [dom]):
                    continue
                if it.is_error_page:
                    continue
                key = (it.url or "").strip() or (it.title or "") + (it.content or "")[:200]
                if key in seen_urls:
                    continue
                # 摘要补采未完成、或时效任务尚无日期时继续找正文；短资料保留供最终核对。
                inline = str(it.metadata.get("via", "")).startswith(("anysearch", "tavily"))
                if (inline and len(it.content or "") < 200) or (window and extract_publish_date(it) is None):
                    previous = pending.get(key)
                    if previous is None or len(it.content or "") > len(previous.content or ""):
                        pending[key] = it
                    continue
                pending.pop(key, None)
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
            items = (apply_recency(merged, window) + apply_recency(list(pending.values()), window))[:limit]
            if not items:  # 预算耗尽且一无所获：如实报失败，让路由降级/前端如实提示
                return CollectResult(False, self.name,
                                     message=f"时间预算内未发现与「{query}」相关的内容")
            msg = " + ".join(parts)
            if len(items) > len(merged):
                msg += f"（保留 {len(items) - len(merged)} 条摘要或日期待核对资料）"
            if len(merged) < limit:
                msg += f"（目标 {limit}，已尽力补采）"
            return CollectResult(True, self.name, items=items, message=msg)

        async def _try_anysearch(dom: str) -> None:
            """AnySearch 检索（16 垂直领域 + 内联正文，反爬站最可靠），结果并入累积。"""
            try:
                need = limit - len(merged)
                items = apply_recency(
                    await self._search_anysearch(kw, dom, limit=need, time_label=label, deadline=deadline), window
                )
            except Exception as e:
                logger.warning("AnySearch 搜索失败: %s", e)
                return
            _add(items, "anysearch", dom)

        async def _try_tavily(dom: str) -> None:
            """Tavily 检索（直接返回内联正文，对反爬站最可靠、且快），结果并入累积。"""
            try:
                need = limit - len(merged)
                items = apply_recency(await self._search_tavily(kw, dom, limit=need, time_label=label, deadline=deadline), window)
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

        # 1) Tavily 正文优先（auto + 开关开 + 有 key），逐个授权站点检索。
        if settings.search_tavily_first and self._use_tavily():
            tavily_done = True
            for dom in dom_seq:
                await _try_tavily(dom)
                if _done():
                    return _result()

        # 2) 免费链接发现后端（SearXNG / DDG）→ 抓正文，保持原站点范围。
        for dom in dom_seq:
            # 有站点约束用 site: 限定，无约束用纯关键词。
            q = f"site:{dom} {kw}".strip() if dom else kw
            for backend in self._discovery_backends():
                if _done():
                    return _result()
                try:
                    links = await self._within_budget(self._discover_one(backend, q, dom, limit=limit, time_label=label), deadline)
                except Exception as e:
                    logger.warning("%s 发现失败: %s", backend, e)
                    continue
                # 过滤已采 URL、只抓还缺的数量，避免重复抓取浪费时间
                links = [u for u in links if u not in seen_urls and url_in_domains(u, [dom] if dom else [])][: limit - len(merged)]
                if not links:
                    continue
                items = apply_recency(await self._fetch_all(links, deadline), window)
                _add(items, backend, dom)

        # 3) Tavily 兜底（若上面未优先用过），仍保持原站点范围。
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
                    links = await self._within_budget(self._discover_one(backend, q, dom, limit=limit), deadline)
                except Exception as e:
                    logger.warning("%s HTML 兜底失败: %s", backend, e)
                    continue
                links = [u for u in links if u not in seen_urls and url_in_domains(u, [dom] if dom else [])][: limit - len(merged)]
                if not links:
                    continue
                items = apply_recency(await self._fetch_all(links, deadline), window)
                _add(items, backend + "兜底", dom)

        if merged or pending:
            return _result()

        hint = ""
        if not settings.searxng_base_url and not settings.tavily_api_key:
            hint = "（免密钥搜索易被反爬；建议自托管 SearXNG 或配 TAVILY_API_KEY）"
        return CollectResult(False, self.name, message=f"未发现与「{query}」相关的内容{hint}")

    async def _collect_projects(self, spec: TaskSpec) -> CollectResult:
        """项目数量在业务核验后判断，此处只发现有界且覆盖各组来源的候选。"""
        scope = spec.project_collection
        keywords = list(dict.fromkeys(query.keyword for query in scope.queries)) or list(dict.fromkeys(spec.keywords))
        return await self._collect_candidates(spec, keywords=keywords, limit=scope.candidate_limit, topics=scope.topics)

    @_frozen_search_config
    async def collect_bid_candidates(self, spec: TaskSpec) -> CollectResult:
        """公告候选沿用严格单次检索与原页读取，摘要不能冒充完整公告。"""
        year = str(beijing_now().year)
        keywords = list(dict.fromkeys([*("提交投标文件截止时间 " + (word if year in word else word + " " + year)
            for word in spec.keywords), *spec.keywords]))
        topics = [term.strip() for word in spec.keywords for term in re.split(r"\s+", re.sub(
            r"最新|招投标|招标公告|采购公告|公告|招标|采购|公开", " ", word)) if term.strip()]
        return await self._collect_candidates(spec, keywords=keywords,
                                              limit=spec.max_items, topics=topics or spec.keywords, bid=True)

    async def _collect_candidates(self, spec: TaskSpec, *, keywords: List[str], limit: int,
                                  topics: List[str], bid: bool = False) -> CollectResult:
        domains = resolve_domains(spec.platforms, spec.site_domains) or [""]
        jobs = [(keyword, domains[(index + offset) % len(domains)])
                for offset in range(len(domains)) for index, keyword in enumerate(keywords)]
        deadline = time.monotonic() + settings.collect_timeout_seconds * 0.8
        window = parse_time_range(spec.time_range)
        label = window.label if window else None
        backends = (["anysearch"] if self._use_anysearch() else [])
        tavily_first = settings.search_tavily_first and self._use_tavily()
        if tavily_first:
            backends.append("tavily")
        backends.extend(self._discovery_backends())
        if self._use_tavily() and not tavily_first:
            backends.append("tavily")
        backends.extend(["baidu_html", "bing_html", "google_html"])
        if self._use_deepseek():
            # 显式渠道不混入旧搜索，保证对比与故障事实可归因。
            backends = ["deepseek"]
        primary = {"anysearch", "tavily", "searxng", "deepseek"}
        backends = list(dict.fromkeys(backends))
        backends = [backend for backend in backends if backend in primary] + [backend for backend in backends if backend not in primary]
        merged: List[CollectedItem] = []
        seen: dict[str, int] = {}
        attempts = []
        stop_reason = "exhausted"
        source_unknown = False
        body_failures: dict[str, str] = {}

        for backend_index, backend in enumerate(backends):
            backend_deadline = deadline
            primary_left = sum(name in primary for name in backends[backend_index:])
            # 公告目录也会占候选位；必须给后续已配置主后端保留独立发现份额。
            backend_limit = (len(merged) + (limit - len(merged) + primary_left - 1) // primary_left
                if bid and backend in primary else limit)
            if backend in primary and primary_left > 1:
                # 为其他已配置的主后端留时间，不让首后端的慢请求占完总预算。
                started = time.monotonic()
                backend_deadline = started + max(0, deadline - started) / primary_left
            index, halt_backend = 0, False
            while index < len(jobs) and not halt_backend:
                remaining = backend_deadline - time.monotonic()
                if len(merged) >= backend_limit:
                    if len(merged) >= limit:
                        stop_reason = "candidate_limit"
                    break
                if remaining <= 0:
                    if time.monotonic() >= deadline:
                        stop_reason = "time_budget"
                    break
                batch, available = [], backend_limit - len(merged)
                # 主API共用原后端截止，不再把慢响应切成不可用的小片；Searx首故障仍停止余组。
                width = 2 if not bid and backend in {"anysearch", "tavily", "deepseek"} else 1
                while index < len(jobs) and len(batch) < width and available > 0:
                    keyword, domain = jobs[index]
                    left_jobs = len(jobs) - index
                    # 公告目录常占首项；每组至少查看三项，但不能突破剩余候选额度。
                    share = min(available, max(3 if bid else 1, (available + left_jobs - 1) // left_jobs))
                    available -= share
                    job_deadline = (backend_deadline if backend in primary else
                        time.monotonic() + remaining / left_jobs)
                    started = time.monotonic()
                    attempt = {"backend": backend, "keyword": keyword, "domain": domain,
                        "status": "unknown", "error_code": None, "count": 0,
                        "allocated_seconds": round(max(0, job_deadline - started), 6),
                        "elapsed_seconds": 0.0, "phase": "search_api"}
                    attempts.append(attempt)
                    batch.append((keyword, domain, share, job_deadline, started, attempt, index))
                    index += 1

                def failed(job, error):
                    nonlocal source_unknown, halt_backend
                    keyword, domain, share, job_deadline, started, attempt, job_index = job
                    attempt["error_code"] = _source_error_code(error)
                    if isinstance(error, httpx.TimeoutException):
                        attempt["timeout_origin"] = "httpx"
                    elif isinstance(error, TimeoutError):
                        attempt["timeout_origin"] = "deadline_reached" if time.monotonic() >= job_deadline else "unknown"
                    attempt["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)
                    source_unknown = True
                    if bid or (backend == "searxng" and attempt["error_code"] in {"SOURCE_TIMEOUT", "SOURCE_CONNECTION_ERROR"}):
                        attempt["backend_remaining_skipped"] = len(jobs) - job_index - 1
                        halt_backend = True

                processing_task, fatal_error = None, None

                async def read_sources(job, found, reserved):
                    keyword, domain, share, job_deadline, started, attempt, _ = job
                    attempt["phase"] = "source_pages"
                    try:
                        if backend in {"anysearch", "tavily", "deepseek"}:
                            known_pages = {url: merged[number] for url, number in seen.items()
                                if merged[number].metadata.get("body_status") in {"complete", "provider_raw"}}
                            items = await self._read_project_pages(found, domain, _project_body_deadline(job_deadline),
                                body_failures, known_pages=known_pages, **({"capture_links": True} if bid else {}))
                        else:
                            links = [url for url in found if url not in seen and url not in body_failures
                                and url_in_domains(url, [domain] if domain else [])]
                            items = await self._fetch_all(links[:share], _project_body_deadline(job_deadline), strict=True,
                                body_failures=body_failures, project_domain=domain,
                                **({"capture_links": True} if bid else {})) if links else []
                        remaining_for_job = max(0, backend_limit - len(merged) - reserved)
                        initial_count = len(items)
                        if bid:
                            original_items = list(items)
                            for number, item in enumerate(original_items):
                                slots = max(0, remaining_for_job - len(items)) // (len(original_items) - number)
                                if not slots:
                                    continue
                                same_host = urlsplit(item.url).hostname or ""
                                expanded = await self._expand_project_directories([item], topics, [same_host] if same_host else [],
                                    slots + 1, set(seen) | {row.url for row in items if row is not item}, job_deadline,
                                    body_failures, allow_page_links=True)
                                items.extend(expanded[1:])
                        else:
                            items = await self._expand_project_directories(items, topics,
                                [domain] if domain else [], remaining_for_job, set(seen), job_deadline, body_failures)
                        return items, min(remaining_for_job, share + len(items) - initial_count)
                    except ExecutionDenied:
                        raise
                    except Exception as error:
                        failed(job, error)
                        return None

                async def search(job):
                    nonlocal halt_backend, fatal_error
                    keyword, domain, share, job_deadline, started, attempt, _ = job
                    if halt_backend or time.monotonic() >= job_deadline:
                        attempt.update(status="skipped", phase="not_dispatched", error_code="SOURCE_BUDGET_EXHAUSTED")
                        return None
                    # BID 的时间说明不是检索词；时效档位仍由独立参数传给后端。
                    query = keyword if bid else " ".join(filter(None, (keyword, spec.time_range or "")))
                    try:
                        if backend == "anysearch":
                            return await self._search_anysearch(query, domain, limit=share, time_label=label,
                                deadline=job_deadline, strict=True, read_originals=False)
                        if backend == "tavily":
                            return await self._search_tavily(query, domain, limit=share, time_label=label,
                                deadline=job_deadline, strict=True, read_originals=False)
                        if backend == "deepseek":
                            scope = spec.project_collection
                            return await self._search_deepseek(keyword if scope else query, domain, limit=share,
                                deadline=job_deadline, **({"publication_from": scope.publication_from,
                                    "publication_to": scope.publication_to} if scope else {}))
                        expression = f"site:{domain} {query}" if domain else query
                        options = {"diagnostics": attempt} if backend == "searxng" else {}
                        return await self._within_budget(self._discover_one(backend, expression, domain,
                            limit=share, time_label=label, strict=True, **options), job_deadline)
                    except (ExecutionDenied, asyncio.CancelledError) as error:
                        halt_backend = True
                        if fatal_error is None:
                            fatal_error = error
                        if processing_task is not None:
                            processing_task.cancel()
                        raise
                    except Exception as error:
                        failed(job, error)
                        return None
                    finally:
                        attempt["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)

                workers = [asyncio.create_task(search(job)) for job in batch]
                positions = {task: number for number, task in enumerate(workers)}
                waiting, ready, outstanding = set(workers), {}, set(range(len(batch)))
                try:
                    while waiting or ready:
                        finished = {task for task in waiting if task.done()}
                        if not finished and not ready:
                            finished, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                        waiting.difference_update(finished)
                        # 同时可用的结果按原job顺序处理；未返回的同伴不能挡住快结果的原页读取。
                        for task in sorted(finished, key=positions.__getitem__):
                            ready[positions[task]] = task.result()
                        position = min(ready)
                        found = ready.pop(position)
                        outstanding.remove(position)
                        if found is None:
                            continue
                        job = batch[position]
                        keyword, domain, share, job_deadline, started, attempt, _ = job
                        if attempt.get("upstream_unresponsive_count"):
                            source_unknown = True
                        # 已发但未处理的同伴仍有候选预留；明确空结果或未知请求不占位置。
                        reserved = sum(batch[number][2] for number in outstanding if number not in ready or ready[number])
                        processing_task = asyncio.create_task(read_sources(job, found, reserved))
                        try:
                            prepared = await processing_task
                        except asyncio.CancelledError:
                            if fatal_error is not None:
                                raise fatal_error
                            raise
                        finally:
                            processing_task = None
                        if fatal_error is not None:
                            raise fatal_error
                        if prepared is None:
                            continue
                        items, merge_share = prepared
                        attempt["source_pages"] = [{"index": number, "status": item.metadata.get("body_status", "unread"),
                            "error_code": item.metadata.get("body_error_code")} for number, item in enumerate(items, 1)]
                        if any(item.metadata.get("body_status") in {"unknown", "unread"} for item in items):
                            source_unknown = True
                        for item in apply_recency(items, window):
                            if (item.metadata.get("body_status") == "excluded" or item.is_error_page
                                    or (not item.content.strip() and item.metadata.get("body_status") not in {"unknown", "unread"})
                                    or not url_in_domains(item.url, [domain] if domain else [])):
                                continue
                            key = item.url.strip() or item.title + item.content[:200]
                            item.metadata.update({"discovery_keyword": keyword, "discovery_domain": domain,
                                "discovery_backend": backend})
                            if key in seen:
                                previous = merged[seen[key]]
                                complete = {"complete", "provider_raw"}
                                if item.metadata.get("body_status") in complete and previous.metadata.get("body_status") not in complete:
                                    # 后续独立响应可补全同一来源，不增加候选计数，也不改写旧失败轨迹。
                                    merged[seen[key]] = item
                                continue
                            seen[key] = len(merged)
                            merged.append(item)
                            attempt["count"] += 1
                            if attempt["count"] >= merge_share or len(merged) >= backend_limit:
                                break
                        attempt["status"] = "completed"
                        attempt["phase"] = "completed"
                        attempt["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)
                finally:
                    # 拒绝或取消后不再派下一批，并清理同批尚未结束的请求。
                    cleanup = workers + ([processing_task] if processing_task is not None else [])
                    for task in cleanup:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*cleanup, return_exceptions=True)
            if stop_reason != "exhausted":
                break
        if len(merged) >= limit:
            stop_reason = "candidate_limit"
        elif stop_reason == "exhausted" and source_unknown:
            stop_reason = "source_unknown"
        return CollectResult(bool(merged), self.name, items=merged,
            message=f"发现 {len(merged)} 条待核对项目资料，不代表独立项目数量已达标",
            coverage={"discovery": {"candidate_limit": limit, "candidate_count": len(merged),
                "stop_reason": stop_reason, "attempts": attempts}})

    async def _expand_project_directories(self, items: List[CollectedItem], topics: List[str], domains: List[str],
                                          remaining: int, seen: set[str], deadline: float,
                                          body_failures: dict[str, str], *, allow_page_links: bool = False) -> List[CollectedItem]:
        """目录仅提供一次有界详情发现，不能直接充当项目原件。"""
        links: List[str] = []
        limit = min(5, max(0, remaining - len({item.url for item in items if item.url not in seen})))
        known = seen | {item.url for item in items}
        # 窄别名只帮助发现详情；是否满足任务仍由冻结主题的后续核验决定。
        aliases = {"配电网自动化": ("配网自动化",), "智能巡检": ("智巡", "智能巡视")}
        terms = [term.casefold() for topic in topics for term in (topic, *aliases.get(topic, ()))]
        for item in items:
            if item.metadata.get("body_status") not in {"complete", "provider_raw"}:
                continue
            text = _project_directory_markdown(item, domains[0] if domains else "")
            if text is None and allow_page_links and (item.metadata.get("project_links") or _MARKDOWN_LINK_RE.search(item.content or "")):
                text = item.content or ""
            if text is None:
                continue
            if not allow_page_links:
                item.metadata["body_status"] = "list_excerpt"
            source = urlsplit(item.url)._replace(fragment="").geturl()
            candidates = []
            for match in _MARKDOWN_LINK_RE.finditer(text):
                end = text.find("\n", match.end())
                context = text[text.rfind("\n", 0, match.start()) + 1:end if end >= 0 else len(text)]
                candidates.append((match.group(1), match.group(2), context))
            candidates.extend((link["text"], link["url"], link.get("context", ""))
                for link in item.metadata.get("project_links", [])[:100])
            if allow_page_links:
                candidates.sort(key=lambda row: (str(beijing_now().year) in row[2],
                    str(extract_publish_date(CollectedItem(content=row[2])) or "")), reverse=True)
            for title, href, context in candidates:
                label = title if allow_page_links else title + context
                if (not terms or not any(term in label.casefold() for term in terms)
                        or (allow_page_links and not re.search(r"公告|招标|采购", title))):
                    continue
                target = urljoin(item.url, href.strip("<>"))
                parsed = urlsplit(target)
                target = parsed._replace(fragment="").geturl()
                if (len(links) >= limit or not domains or parsed.scheme not in {"http", "https"}
                        or (allow_page_links and parsed.hostname != urlsplit(item.url).hostname)
                        or parsed.username is not None or target == source or target in known
                        or target in body_failures or not url_in_domains(target, domains)):
                    continue
                known.add(target)
                links.append(target)
        if not links or time.monotonic() >= deadline:
            return items
        try:
            details = await self._read_project_pages([CollectedItem(url=link) for link in links],
                domains[0], deadline, body_failures)
        except ExecutionDenied:
            raise
        except Exception as error:
            code = _source_error_code(error)
            for link in links:
                body_failures[link] = code
            details = [CollectedItem(url=link, metadata={"body_status": "unknown", "body_error_code": code}) for link in links]
        # 新取得的目录仍只是线索，本轮不再沿它继续跟链。
        for detail in details:
            if not allow_page_links and _project_directory_markdown(detail, domains[0] if domains else "") is not None:
                detail.metadata["body_status"] = "list_excerpt"
        return items + details

    async def _discover_one(self, backend: str, query: str, domain: str, *, limit: int,
                            time_label: Optional[str] = None, strict: bool = False,
                            diagnostics: Optional[dict] = None) -> List[str]:
        """按后端名分派链接发现。"""
        options = {"strict": True} if strict else {}
        if backend == "searxng":
            if diagnostics is not None:
                options["diagnostics"] = diagnostics
            return await self._discover_searxng(query, domain, limit=limit, time_label=time_label, **options)
        if backend == "ddg":
            return await self._discover_ddg(query, domain, limit=limit, time_label=time_label, **options)
        if backend == "baidu_html":
            return await self._discover_baidu_html(query, domain, limit=limit, **options)
        if backend == "bing_html":
            return await self._discover_bing_html(query, domain, limit=limit, **options)
        if backend == "google_html":
            return await self._discover_google_html(query, domain, limit=limit, **options)
        return []

    async def _within_budget(self, operation, deadline: Optional[float]):
        """重试、限速及浏览器启动共用总预算，超时后仍交回之前取得的候选。"""
        async with asyncio.timeout(None if deadline is None else max(0, deadline - time.monotonic())):
            return await operation

    async def _fetch_inline_page(self, url: str, deadline: Optional[float], *, strict: bool = False) -> "httpx.Response":
        async def request():
            await get_rate_limiter().acquire(url)
            async def get():
                async with smart_client(url, timeout=30.0) as client:
                    return await client.get(url, headers=pick_headers())
            return await get() if strict else await retry_async(get)
        return await self._within_budget(request(), deadline)

    async def _search_deepseek(self, keywords: str, domain: str, *, limit: int,
                               deadline: Optional[float] = None,
                               publication_from=None, publication_to=None) -> List[CollectedItem]:
        # 使用平台配置，不读取当前用户的模型 Key，也不借用选中的个人连接。
        client = _deepseek_client()
        query = f"site:{domain} {keywords}" if domain else keywords
        # 搜索商边界包含性未承诺，向外留一天；最终日期仍按原件严格核验。
        if publication_from is not None:
            query += f" after:{(publication_from - timedelta(days=1)).isoformat()}"
        if publication_to is not None:
            query += f" before:{(publication_to + timedelta(days=1)).isoformat()}"
        response = await self._within_budget(client.search(query, limit=min(50, max(1, limit)),
            need_content=False), deadline)
        items, seen = [], set()
        for row in response["results"]:
            parsed = urlsplit(row["url"])
            # 页内定位不发送给 Fetch；先统一身份，避免合法公告被误拒或重复收费。
            url = parsed._replace(fragment="").geturl()
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                    or url in seen or not url_in_domains(url, [domain] if domain else [])):
                continue
            seen.add(url)
            items.append(CollectedItem(url=url, title=row.get("title", ""), metadata={
                "engine": "deepseek", "via": "deepseek_search", "body_status": "unread",
                "search_date_hint": row.get("date"), "search_authority_hint": row.get("authority")}))
            if len(items) >= min(50, max(1, limit)):
                break
        return items

    async def _read_project_pages(self, items: List[CollectedItem], domain: str,
                                  deadline: Optional[float], body_failures: dict[str, str], *,
                                  known_pages: Optional[dict[str, CollectedItem]] = None,
                                  capture_links: bool = False) -> List[CollectedItem]:
        """搜索摘要仅作发现；单页未知留诊断，不丢弃同批其它已知来源。"""
        for item in items:
            if item.metadata.get("body_status") != "provider_raw":
                continue
            anomaly = _repeated_body(item.content)
            if anomaly is not None:
                item.metadata["provider_body_anomaly"] = anomaly
                # 异常只是须回原页核对的信号；先移出已核缓存，读取失败不能回填膨胀正文。
                item.content = ""
                item.metadata["body_status"] = "unread"
        originals = {**(known_pages or {}), **{item.url: item for item in items if item.metadata.get("body_status") == "provider_raw"}}
        requests, pending = [], []
        stopped = False
        for item in items:
            reason = _project_source_exclusion(item)
            if reason:
                item.metadata.update(body_status="excluded", body_error_code=reason)
                continue
            requests.append((item, item.url))
            if item.metadata.get("body_status") == "provider_raw":
                continue
            if item.url not in originals:
                originals[item.url] = item
                pending.append(item)

        async def read(item):
            nonlocal stopped
            url = item.url
            item.metadata["body_status"] = "unread"
            if url in body_failures:
                item.metadata.update(body_status="unknown", body_error_code=body_failures[url])
                return
            if deadline is not None and time.monotonic() >= deadline:
                item.metadata["body_error_code"] = "SOURCE_BUDGET_EXHAUSTED"
                return
            try:
                if self._use_deepseek():
                    client = _deepseek_client()
                    page = await self._within_budget(client.fetch(url), deadline)
                    text = page["content"]
                    anomaly = _repeated_body(text)
                    if CollectedItem(content=text).is_error_page or anomaly:
                        code = "SOURCE_REPEATED_BODY" if anomaly else "SOURCE_CHALLENGE"
                        item.content = ""
                        item.metadata.update(body_status="unknown", body_error_code=code)
                        if anomaly:
                            item.metadata["provider_body_anomaly"] = anomaly
                        body_failures[url] = code
                    else:
                        item.title, item.content = page["title"] or item.title, text
                        item.metadata.update(body_status="provider_raw", via="deepseek_fetch",
                            source_representation="provider_markdown")
                    return
                response = await self._fetch_inline_page(url, deadline, strict=True)
                response.raise_for_status()
                item.url = str(response.url)
                if domain and not url_in_domains(item.url, [domain]):
                    return
                document = await _response_document(response, deadline)
                if document is not None:
                    title, item.content, metadata = document
                    item.title = title or item.title
                    item.metadata.update(metadata)
                    if metadata["body_status"] == "unknown":
                        body_failures[url] = metadata["body_error_code"]
                    return
                html = decode_html(response)
                _preserve_project_html_links(item, html,
                    domain or ((urlsplit(item.url).hostname or "") if capture_links else ""), capture_links=capture_links)
                title, text, metadata = extract_content(html, item.url)
                item.metadata.update({key: value for key, value in metadata.items() if key != "via"})
                if CollectedItem(content=text or "").is_error_page:
                    item.metadata.update(body_status="unknown", body_error_code="SOURCE_CHALLENGE")
                    body_failures[url] = "SOURCE_CHALLENGE"
                elif (text or "").strip():
                    item.content = text
                    item.title = title or item.title
                    item.metadata.update(body_status="complete", via=metadata.get("via", "httpx"))
                else:
                    item.metadata["body_status"] = "insufficient"
            except ExecutionDenied:
                stopped = True
                raise
            except Exception as error:
                code = _source_error_code(error)
                body_failures[url] = code
                item.metadata.update(body_status="unknown", body_error_code=code)

        queue = iter(pending)
        async def worker():
            while not stopped:
                item = next(queue, None)
                if item is None:
                    return
                await read(item)

        # 两路共享原截止时间，避免首个慢页堵住同批所有独立原件；未知URL仍只派发一次。
        workers = [asyncio.create_task(worker()) for _ in range(min(2, len(pending)))]
        try:
            await asyncio.gather(*workers)
        finally:
            # 取消或权限拒绝时必须收口另一读取，不能留下后台外联。
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        for item, url in requests:
            original = originals[url]
            if original is not item:
                item.url, item.title, item.content = original.url, original.title, original.content
                item.metadata.update(original.metadata)
            if (item.metadata.get("provider_body_anomaly") and item.metadata.get("body_status") == "complete"
                    and _repeated_body(item.content) is not None):
                # 缓存和本次读取都须核对，不能让调用方继续持有同一份已知异常的 complete 原件。
                item.content = original.content = ""
                item.metadata.update(body_status="unknown", body_error_code="SOURCE_REPEATED_BODY")
                original.metadata.update(item.metadata)
                body_failures[url] = "SOURCE_REPEATED_BODY"
        return [item for item in items if not domain or url_in_domains(item.url, [domain])]

    async def _search_tavily(self, keywords: str, domain: str, *, limit: int,
                             time_label: Optional[str] = None, deadline: Optional[float] = None,
                             strict: bool = False, body_failures: Optional[dict[str, str]] = None,
                             read_originals: bool = True) -> List[CollectedItem]:
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
        resp = await self._within_budget(_post() if strict else retry_async(_post), deadline)
        resp.raise_for_status()
        data = resp.json()
        if strict and not isinstance(data.get("results"), list):
            raise ValueError("Tavily 来源响应格式未识别")
        items: List[CollectedItem] = []
        for r in (data.get("results") or [])[:limit]:
            if domain and not url_in_domains(r.get("url", ""), [domain]):
                continue
            content = (r.get("raw_content") or r.get("content") or "").strip()
            if not content:
                continue
            items.append(CollectedItem(
                url=r.get("url", ""), title=r.get("title", ""), content=content,
                metadata={"engine": self.name, "via": "tavily", **({"body_status": "provider_raw"}
                    if strict and (r.get("raw_content") or "").strip() else {})},
            ))

        if strict:
            if not read_originals:
                return items
            return await self._read_project_pages(items, domain, _project_body_deadline(deadline),
                body_failures if body_failures is not None else {})

        # 补采：Tavily 对部分 URL（如移动版子域名）仅返回简短摘要，
        # 重新抓取这些页面以获取完整正文写入 data.json
        thin_urls = [item.url for item in items if len(item.content or "") < 200]
        if thin_urls:
            logger.info("Tavily 返回 %d 条摘要型结果，补采完整正文…", len(thin_urls))
            for item in items:
                if item.url not in thin_urls:
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    # 补采目标为结果页（多为国内站），按域名智能分流抓取
                    resp2 = await self._fetch_inline_page(item.url, deadline, **({"strict": True} if strict else {}))
                    resp2.raise_for_status()
                    # 来源身份以最终响应为准，不能把重定向后的站外正文标成站内来源。
                    item.url = str(resp2.url)
                    if domain and not url_in_domains(item.url, [domain]):
                        continue
                    title2, text2, meta2 = extract_content(decode_html(resp2), item.url)
                    item.metadata.update({key: value for key, value in meta2.items() if key != "via"})
                    if (text2 or "").strip() and len(text2) > len(item.content or ""):
                        item.content = text2
                        if title2:
                            item.title = title2
                        item.metadata["via"] = f"tavily+{meta2.get('via', 'httpx')}"
                except Exception as e:
                    if strict:
                        raise
                    logger.warning("Tavily 补采失败 %s: %s", item.url, e)

        return [item for item in items if not domain or url_in_domains(item.url or "", [domain])]

    async def _search_anysearch(self, keywords: str, domain: str, *, limit: int,
                                 time_label: Optional[str] = None, deadline: Optional[float] = None,
                                 strict: bool = False, body_failures: Optional[dict[str, str]] = None,
                                 read_originals: bool = True) -> List[CollectedItem]:
        """调用 AnySearch API：直接返回带正文的结果（内联正文，免二次抓取）。

        AnySearch 支持垂直领域搜索，对反爬站比传统搜索引擎更可靠；
        结果已含完整正文时跳过补采（与 Tavily 不同，AnySearch 正文通常已足够）。
        """
        # 先限定检索范围，避免全网结果截断后再滤域而遗漏真正的站内资料。
        query = f"site:{domain} {keywords}" if domain else keywords
        items = await self._within_budget(_anysearch.search(query, max_results=limit,
            **({"strict": True} if strict else {})), deadline)
        if not items:
            return []

        # 即使后端忽略站点参数，也不能把站外结果计为站内样本或继续抓取其正文。
        if domain:
            items = [item for item in items if url_in_domains(item.url or "", [domain])]

        if strict:
            if not read_originals:
                return items
            return await self._read_project_pages(items, domain, _project_body_deadline(deadline),
                body_failures if body_failures is not None else {})

        # 补采：AnySearch 对部分 URL 可能只返回摘要，短内容补抓完整正文
        thin_urls = [item.url for item in items if len(item.content or "") < 200]
        if thin_urls:
            logger.info("AnySearch 返回 %d 条摘要型结果，补采完整正文…", len(thin_urls))
            for item in items:
                if item.url not in thin_urls:
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    resp2 = await self._fetch_inline_page(item.url, deadline, **({"strict": True} if strict else {}))
                    resp2.raise_for_status()
                    item.url = str(resp2.url)
                    if domain and not url_in_domains(item.url, [domain]):
                        continue
                    title2, text2, meta2 = extract_content(decode_html(resp2), item.url)
                    item.metadata.update({key: value for key, value in meta2.items() if key != "via"})
                    if (text2 or "").strip() and len(text2) > len(item.content or ""):
                        item.content = text2
                        if title2:
                            item.title = title2
                        item.metadata["via"] = f"anysearch+{meta2.get('via', 'httpx')}"
                except Exception as e:
                    if strict:
                        raise
                    logger.warning("AnySearch 补采失败 %s: %s", item.url, e)

        return [item for item in items if not domain or url_in_domains(item.url or "", [domain])]

    def _build_query(self, spec: TaskSpec) -> Tuple[str, str]:
        """构造检索式与目标域名。命中平台域名则用 site: 限定。"""
        kw = " ".join([*spec.keywords, spec.time_range or ""]).strip()
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
            if domain and not url_in_domains(link, [domain]):
                continue
            if link in seen:
                continue
            seen.add(link)
            out.append(link)
            if len(out) >= limit:
                break
        return out

    async def _discover_searxng(self, query: str, domain: str, *, limit: int,
                                time_label: Optional[str] = None, strict: bool = False,
                                diagnostics: Optional[dict] = None) -> List[str]:
        """SearXNG JSON API 发现链接（自托管，免费无限）。"""
        base = settings.searxng_base_url.rstrip("/")
        url = f"{base}/search"
        params = {"q": query, "format": "json", "language": "zh-CN"}
        if strict:
            # 聚合先于20秒HTTP上限结束，给已有结果的序列化和返回留余量。
            params["timeout_limit"] = 15.0
        if time_label:  # SearXNG 支持 time_range=day|week|month|year
            params["time_range"] = time_label
        await get_rate_limiter().acquire(base)
        # trust_env=False：SearXNG 是自托管服务，绕过系统代理（否则 localhost 请求会被代理拦成 502）
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, trust_env=False) as client:
            resp = (await client.get(url, params=params, headers=pick_headers()) if strict
                    else await retry_async(lambda: client.get(url, params=params, headers=pick_headers())))
            resp.raise_for_status()
            data = resp.json()
        if strict and not isinstance(data.get("results"), list):
            raise ValueError("SearXNG 来源响应格式未识别")
        urls = [r.get("url", "") for r in (data.get("results") or [])]
        urls = self._dedup_filter(urls, domain, limit)
        if strict:
            unresponsive = data.get("unresponsive_engines", [])
            if not isinstance(unresponsive, list):
                raise ValueError("SearXNG 来源响应格式未识别")
            if diagnostics is not None:
                diagnostics.update(upstream_unresponsive_count=len(unresponsive), partial=bool(urls and unresponsive))
            # 已有链接可继续核对；上游失败时的空结果不能当作可靠的零命中。
            if unresponsive and not urls:
                raise ValueError("SearXNG 上游结果不完整")
        return urls

    async def _discover_ddg(self, query: str, domain: str, *, limit: int,
                            time_label: Optional[str] = None, strict: bool = False) -> List[str]:
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
                if strict:
                    raise
                logger.warning("ddgs 库发现失败，退回正则抓取: %s", e)

        return await self._discover_ddg_html(query, domain, limit=limit, time_label=time_label,
            **({"strict": True} if strict else {}))

    async def _discover_ddg_html(self, query: str, domain: str, *, limit: int,
                                 time_label: Optional[str] = None, strict: bool = False) -> List[str]:
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
                resp = await _get() if strict else await retry_async(_get)
            except Exception as e:
                if strict:
                    raise
                logger.warning("DDG HTML 发现失败 %s: %s", base, e)
                continue
            collected.extend(unquote(raw) for raw in _UDDG_RE.findall(resp.text))
            if len(self._dedup_filter(collected, domain, limit)) >= limit:
                break
        return self._dedup_filter(collected, domain, limit)

    async def _fetch_all(self, links: List[str],
                         deadline: Optional[float] = None, *, strict: bool = False,
                         body_failures: Optional[dict[str, str]] = None, project_domain: str = "",
                         capture_links: bool = False) -> List[CollectedItem]:
        """抓取发现到的链接：优先 crawl4ai（渲染 JS），否则 httpx 静态抓取。

        deadline（time.monotonic 时刻）：逐条抓取前检查预算，超了立即带着已抓结果返回；
        单条抓取也按剩余预算设上限。否则慢站 × 多链接会烧穿 collect 节点的外层
        wait_for（90s），已抓数据全部丢失、只报"search 超时"。
        """
        items: List[CollectedItem] = []
        tracked = strict and (bool(project_domain) or body_failures is not None)
        if tracked:
            # 原页读取不能独占后端余量；保留原有动态渲染，但本批最多占20秒。
            cutoff = time.monotonic() + 20.0
            deadline = min(deadline, cutoff) if deadline is not None else cutoff
            allowed = []
            for url in links:
                item = CollectedItem(url=url)
                reason = _project_source_exclusion(item)
                if reason:
                    item.metadata.update(body_status="excluded", body_error_code=reason)
                    items.append(item)
                else:
                    allowed.append(url)
            links = allowed
            if not links:
                return items
        if strict:
            documents = [url for url in links if urlsplit(url).path.lower().endswith(".pdf")]
            if documents:
                items.extend(await self._read_project_pages([CollectedItem(url=url) for url in documents], project_domain,
                    deadline, body_failures if body_failures is not None else {}))
                links = [url for url in links if url not in documents]
                if not links:
                    return items
        try:
            from crawl4ai import AsyncWebCrawler  # type: ignore
        except Exception:
            AsyncWebCrawler = None  # type: ignore

        def _left() -> float:
            """剩余预算秒数；未传 deadline 时按单条 30s 上限。"""
            return min(30.0, deadline - time.monotonic()) if deadline else 30.0

        dispatched, settled = set(), set()
        denied = None
        task = asyncio.current_task()
        cancellations = task.cancelling() if task is not None else 0

        def failed_page(url: str, code: str) -> None:
            status = "unread" if tracked and url not in dispatched else "unknown"
            if status == "unknown" and body_failures is not None:
                body_failures[url] = code
            if status == "unread" and code in {"SOURCE_TIMEOUT", "SOURCE_BUDGET_EXHAUSTED"}:
                code = "SOURCE_BUDGET_EXHAUSTED"
            items.append(CollectedItem(url=url, metadata={"engine": self.name,
                "body_status": status, "body_error_code": code}))
            settled.add(url)

        def finish_pending(code: str) -> None:
            for url in links:
                if url not in settled:
                    failed_page(url, code)

        if AsyncWebCrawler is not None:
            try:
                async def browse():
                    nonlocal denied
                    async with AsyncWebCrawler(config=crawler_browser_config()) as crawler:
                        for url in links:
                            if _left() <= 1.0:
                                return
                            try:
                                await self._within_budget(get_rate_limiter().acquire(url), deadline)
                                dispatched.add(url)
                                result = await asyncio.wait_for(
                                    crawler.arun(url=url) if strict else retry_async(lambda u=url: crawler.arun(url=u)), timeout=_left())
                            except Exception as e:
                                if strict:
                                    if isinstance(e, ExecutionDenied):
                                        denied = e
                                        raise
                                    failed_page(url, _source_error_code(e))
                                    continue
                                logger.warning("search 抓取失败 %s: %s", url, e)
                                continue
                            content = str(getattr(result, "markdown", "") or "")
                            meta = getattr(result, "metadata", {}) or {}
                            if strict:
                                html = getattr(result, "html", "") or ""
                                document = await _response_document(httpx.Response(200,
                                    headers=getattr(result, "response_headers", None),
                                    content=html if isinstance(html, bytes) else str(html).encode("utf-8")), deadline)
                                if document is not None:
                                    title, content, document_meta = document
                                    if document_meta["body_status"] == "unknown":
                                        failed_page(url, document_meta["body_error_code"])
                                        items[-1].metadata.update(document_meta)
                                        continue
                                    meta = {**meta, **document_meta, "title": title or meta.get("title", "")}
                            if strict and (not getattr(result, "success", False) or CollectedItem(content=content).is_error_page):
                                failed_page(url, "SOURCE_READ_FAILED")
                                continue
                            # 渲染后的 Markdown 不保留 meta 标签，先捕获原页发布日期再判断时效。
                            published = extract_date_from_html(str(getattr(result, "html", "") or ""))
                            if published and not meta.get("date"):
                                meta = {**meta, "date": published.isoformat()}
                            if content.strip():
                                item = CollectedItem(
                                    url=str(getattr(result, "redirected_url", None) or getattr(result, "url", None) or url),
                                    title=meta.get("title", ""), content=content,
                                    metadata={"engine": self.name, "via": "crawl4ai", **meta,
                                        **({"body_status": "complete"} if strict else {})},
                                )
                                if strict:
                                    _preserve_project_html_links(item, str(getattr(result, "html", "") or ""),
                                        project_domain or ((urlsplit(item.url).hostname or "") if capture_links else ""), capture_links=capture_links)
                                items.append(item)
                            elif strict:
                                items.append(CollectedItem(url=url, metadata={"engine": self.name, "body_status": "insufficient"}))
                            settled.add(url)
                await self._within_budget(browse(), deadline)
                if tracked:
                    finish_pending("SOURCE_BUDGET_EXHAUSTED")
                    return items
                if items:
                    return items
            except Exception as e:
                if strict:
                    if isinstance(e, ExecutionDenied) or not tracked:
                        raise
                    # 浏览器退出错误不能掩盖权限拒绝或外部取消；内部预算超时会撤回自身取消计数。
                    if denied is not None:
                        raise denied
                    if task is not None and task.cancelling() > cancellations:
                        raise asyncio.CancelledError()
                    # 已取得原件不能被浏览器退出/超时整批抹掉；未知URL也不能再走静态重发。
                    finish_pending(_source_error_code(e))
                    return items
                logger.warning("crawl4ai 抓取链路异常，回退 httpx: %s", e)

        # 回退：httpx 静态抓取 + 正文提取级联（Trafilatura→readability→html_to_text）
        # 逐 url 用 smart_client 按域名分流：国内结果页强制直连（绕 Clash），境外走代理
        for url in links:
            if _left() <= 1.0:
                if tracked:
                    finish_pending("SOURCE_BUDGET_EXHAUSTED")
                return items  # 预算耗尽，带着已抓的先走
            try:
                async def _get(u=url) -> "httpx.Response":
                    await get_rate_limiter().acquire(u)
                    dispatched.add(u)
                    async with smart_client(u, timeout=min(30.0, _left())) as client:
                        return await client.get(u, headers=pick_headers())
                resp = await self._within_budget(_get() if strict else retry_async(_get), deadline)
                resp.raise_for_status()
            except Exception as e:
                if strict:
                    if isinstance(e, ExecutionDenied):
                        raise
                    failed_page(url, _source_error_code(e))
                    continue
                logger.warning("search httpx 抓取失败 %s: %s", url, e)
                continue
            if not strict and dedup_seen(url):
                continue
            final_url = str(resp.url)
            if strict:
                document = await _response_document(resp, deadline)
                if document is not None:
                    title, text, metadata = document
                    if metadata["body_status"] == "unknown":
                        failed_page(url, metadata["body_error_code"])
                        items[-1].metadata.update(metadata)
                    else:
                        items.append(CollectedItem(url=final_url, title=title, content=text,
                            metadata={"engine": self.name, **metadata}))
                        settled.add(url)
                    continue
            html = decode_html(resp)
            title, text, meta = extract_content(html, final_url)
            if strict and CollectedItem(content=text or "").is_error_page:
                failed_page(url, "SOURCE_CHALLENGE")
                continue
            if (text or "").strip():
                dedup_mark(url, self.name)
                item = CollectedItem(
                    url=final_url, title=title, content=text,
                    metadata={"engine": self.name, **meta, "via": meta.get("via", "httpx"),
                        **({"body_status": "complete"} if strict else {})},
                )
                if strict:
                    _preserve_project_html_links(item, html,
                        project_domain or ((urlsplit(item.url).hostname or "") if capture_links else ""), capture_links=capture_links)
                items.append(item)
            elif strict:
                items.append(CollectedItem(url=final_url, metadata={"engine": self.name, "body_status": "insufficient"}))
            settled.add(url)
        return items

    # ── 终极兜底：直接抓搜索引擎 HTML 页面提取链接（不依赖任何外部服务）──

    async def _discover_bing_html(self, query: str, domain: str, *, limit: int, strict: bool = False) -> List[str]:
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
            resp = await _get() if strict else await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            if strict:
                raise
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

    async def _discover_baidu_html(self, query: str, domain: str, *, limit: int, strict: bool = False) -> List[str]:
        """百度 HTML 兜底：只提取搜索结果的正文链接。
        中文内容优于 Google，不依赖 API Key，但易被反爬/验证码拦截。
        """
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={min(limit * 3, 50)}"
        await get_rate_limiter().acquire(url)
        # 百度为国内站，smart_client 判定后强制直连（绕 Clash 系统代理劫持）
        try:
            async def _get() -> "httpx.Response":
                async with smart_client(url, timeout=15.0) as client:
                    return await client.get(url, headers=pick_headers({"Accept-Language": "zh-CN,zh;q=0.9"}))
            resp = await _get() if strict else await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            if strict:
                raise
            logger.warning("百度 HTML 兜底请求失败: %s", e)
            return []
        # 全页 href 会混入导航和静态资源；未知结果布局留给后续搜索后端处理。
        links: List[str] = []
        soup = BeautifulSoup(resp.text, "html.parser")
        for result in soup.select("#content_left .result, #content_left .result-op, #content_left .c-container"):
            title_link = result.select_one("h3 a[href]")
            raw = result.get("data-mu") or (title_link.get("href") if title_link else None)
            if raw:
                links.append(str(raw))
        return self._dedup_filter(links, domain, limit)

    async def _discover_google_html(self, query: str, domain: str, *, limit: int, strict: bool = False) -> List[str]:
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
            resp = await _get() if strict else await retry_async(_get, retries=1)
            resp.raise_for_status()
        except Exception as e:
            if strict:
                raise
            logger.warning("Google HTML 兜底请求失败: %s", e)
            return []
        # Google 搜索结果链接以 /url?q=<真实URL> 形式出现
        links: List[str] = []
        for raw in re.findall(r'/url\?q=(https?://[^&"\']+)', resp.text):
            links.append(unquote(raw))
        return self._dedup_filter(links, domain, limit)


register(SearchDiscoveryCollector())
