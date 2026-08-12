"""
电商评论采集器（Tier 10，电商专用，免登录优先）。

现实约束：淘宝/天猫/拼多多的商品评论强制登录 + 风控，免登录拿不到；
**京东商品评论有公开 JSON 接口**（club.jd.com/comment/productPageComments.action），
免登录即可分页获取——这是目前唯一可靠的免登录电商评论源，本采集器据此落地京东路径。

匹配策略（保持诚实，不冒充能力）：
- 平台名命中「京东/jd」或给出京东商品 URL 时本采集器恒接管；
- 淘宝/天猫/拼多多默认不接管（免登录拿不到，浪费一次采集尝试），自动交给 search/firecrawl
  全网兜底；但若用户配置了对应平台 Cookie（`TB_COOKIE`/`PDD_COOKIE`），登录态已具备，
  不再是"冒充能力"，`matches` 允许尝试接管（失败仍会如实报告、交后续引擎兜底）。

取商品两条路径：① 用户直接给京东商品 URL → 抽 SKU；② 关键词 → 搜 search.jd.com 取前几个 SKU。
随后调用评论接口分页拉取，每条评论封装为 CollectedItem（正文 + 评分 + 规格 + 时间）。

复用 `_net` 的按域名限速、UA/Header 轮换、失败指数退避；零额外依赖（httpx + 标准库）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

import httpx

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._net import decode_html, get_rate_limiter, pick_headers, retry_async, smart_client
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)

# 平台别名表：中文/英文别名 → 平台代码
_PLATFORM_ALIASES = {
    "京东": "jd", "jd": "jd", "jd.com": "jd", "jingdong": "jd", "京东商城": "jd",
    "淘宝": "taobao", "taobao": "taobao", "tb": "taobao",
    "天猫": "tmall", "tmall": "tmall", "tm": "tmall",
    "拼多多": "pdd", "pinduoduo": "pdd", "pdd": "pdd",
}

# 各平台商品 URL 正则（提取商品 ID）
_PRODUCT_URL_PATTERNS = {
    "jd": re.compile(r"(?:item\.jd\.com|item\.m\.jd\.com/product)/(\d+)"),
    "taobao": re.compile(r"(?:item\.taobao\.com|detail\.tmall\.com).*?[?&]id=(\d+)"),
    "tmall": re.compile(r"detail\.tmall\.com.*?[?&]id=(\d+)"),
    "pdd": re.compile(r"(?:mobile\.yangkeduo\.com|pinduoduo\.com).*?goods[_-]?id=(\d+)"),
}

# 京东搜索结果页 SKU 正则
_JD_SKU_SEARCH_RE = re.compile(r'data-sku="(\d+)"')
# 淘宝搜索结果页商品 ID 正则
_TB_ID_SEARCH_RE = re.compile(r'data-nid="(\d+)"')

# 平台中文名
_PLATFORM_CN = {"jd": "京东", "taobao": "淘宝", "tmall": "天猫", "pdd": "拼多多"}

# 一次任务最多抓几个商品
_MAX_PRODUCTS = 3
_PAGE_SIZE = 10


def _resolve_platform(spec: TaskSpec) -> str:
    """从 TaskSpec 平台名解析出平台代码（jd/taobao/tmall/pdd）。"""
    for p in spec.platforms:
        code = _PLATFORM_ALIASES.get(p.strip()) or _PLATFORM_ALIASES.get(p.strip().lower())
        if code:
            return code
    return ""


def _extract_product_ids(spec: TaskSpec) -> dict:
    """从 URL 列表中提取各平台的商品 ID，返回 {platform_code: [id, ...]}。"""
    out: dict[str, list[str]] = {}
    for u in spec.urls or []:
        for code, pattern in _PRODUCT_URL_PATTERNS.items():
            m = pattern.search(u)
            if m and m.group(1):
                out.setdefault(code, []).append(m.group(1))
                break
    return out


def _platform_cookie(platform: str) -> str:
    """取平台 Cookie：当前任务用户的自配 Cookie 优先 → 全局配置/.env。"""
    from src.config.user_ctx import effective
    cookies = {
        "jd": (effective("jd_cookie") or "").strip(),
        "taobao": (effective("tb_cookie") or "").strip(),
        "tmall": (effective("tb_cookie") or "").strip(),
        "pdd": (effective("pdd_cookie") or "").strip(),
    }
    return cookies.get(platform, "")


def _jd_headers(extra: Dict[str, str] | None = None) -> Dict[str, str]:
    """生成京东请求头：UA/Header 轮换 + 可选登录 Cookie（用户自配优先，兜底 .env 的 JD_COOKIE）。"""
    from src.config.user_ctx import effective
    headers = pick_headers(extra)
    cookie = (effective("jd_cookie") or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _parse_comments(data: Dict[str, Any], sku: str) -> List[CollectedItem]:
    """把京东评论接口返回的 JSON 解析为 CollectedItem 列表。"""
    items: List[CollectedItem] = []
    for c in (data or {}).get("comments", []) or []:
        content = str(c.get("content") or "").strip()
        if not content:
            continue
        # 规格（颜色/尺码）拼进 metadata，便于后续分析按规格归因
        spec_parts = [str(c.get("productColor") or ""), str(c.get("productSize") or "")]
        product_spec = " ".join(p for p in spec_parts if p).strip()
        items.append(
            CollectedItem(
                url=f"https://item.jd.com/{sku}.html",
                title=str(c.get("referenceName") or "").strip(),
                content=content,
                metadata={
                    "engine": "ecommerce",
                    "platform": "jd",
                    "kind": "comment",
                    "sku": sku,
                    "score": c.get("score"),          # 评分 1-5
                    "spec": product_spec,             # 商品规格
                    "time": c.get("creationTime"),    # 评价时间
                    "nickname": c.get("nickname"),
                },
            )
        )
    return items


class EcommerceCollector(BaseCollector):
    name = "ecommerce"
    tier = 10  # 电商专用：社媒(0) 之后、rss(12) 之前

    def matches(self, spec: TaskSpec) -> bool:
        # 诚实匹配：京东免登录即有公开评论接口，恒接管；淘宝/天猫/拼多多免登录拿不到评论，
        # 默认不接管、交全网兜底，避免白白浪费一次采集尝试——但若用户配了对应平台 Cookie
        # （见 _platform_cookie），登录态已具备，不再是"冒充能力"，允许尝试接管。
        platform = _resolve_platform(spec)
        if platform == "jd":
            return True
        if platform in ("taobao", "tmall", "pdd") and _platform_cookie(platform):
            return True
        ids = _extract_product_ids(spec)
        if "jd" in ids:
            return True
        return any(p in ids and _platform_cookie(p) for p in ("taobao", "tmall", "pdd"))

    async def collect(self, spec: TaskSpec) -> CollectResult:
        platform = _resolve_platform(spec)
        product_ids = _extract_product_ids(spec)

        # 确定目标平台：优先平台名 → URL 推断
        if not platform and product_ids:
            platform = next(iter(product_ids))
        if not platform:
            return CollectResult(False, self.name, message="未识别到电商平台")

        cn = _PLATFORM_CN.get(platform, platform)
        ids = product_ids.get(platform, [])

        # 电商目标（京东/淘系）均为国内站，smart_client 判定后强制直连（绕 Clash 系统代理劫持）
        async with smart_client("https://www.jd.com", timeout=30.0) as client:
            # 1) 解析商品 ID：优先 URL → 关键词搜索
            if not ids and spec.keywords:
                ids = await self._search_product_ids(client, platform, " ".join(spec.keywords))
            if not ids:
                return CollectResult(False, self.name,
                                     message=f"未发现{cn}商品（搜索为空或被风控），交由后续引擎兜底")

            # 2) 京东走评论 API（最快），淘宝/天猫/拼多多尝试页面提取
            if platform == "jd":
                items = await self._fetch_jd_comments_all(client, ids[: _MAX_PRODUCTS], spec.max_items)
            else:
                items = await self._scrape_product_pages(client, platform, ids[: _MAX_PRODUCTS], spec.max_items)

        if not items:
            hint = "建议配 Cookie" if not _platform_cookie(platform) else ""
            return CollectResult(False, self.name,
                                 message=f"{cn}未获取到数据（可能需登录/Cookie）。{hint}交由后续引擎兜底")
        return CollectResult(True, self.name, items=items[: spec.max_items],
                             message=f"采集 {len(items)} 条{cn}商品数据")

    async def _search_product_ids(self, client: httpx.AsyncClient, platform: str,
                                   keyword: str) -> list[str]:
        """按关键词搜索商品 ID。京东走 search.jd.com，淘宝走 s.taobao.com。"""
        if platform == "jd":
            return await self._search_jd_skus(client, keyword)
        if platform in ("taobao", "tmall"):
            return await self._search_tb_ids(client, keyword)
        return []

    async def _search_jd_skus(self, client: httpx.AsyncClient, keyword: str) -> list[str]:
        """搜索京东商品 SKU。"""
        url = "https://search.jd.com/Search"
        await get_rate_limiter().acquire(url)
        try:
            resp = await retry_async(lambda: client.get(
                url, params={"keyword": keyword, "enc": "utf-8"},
                headers=_jd_headers(),
            ))
            resp.raise_for_status()
        except Exception as e:
            logger.warning("京东搜索失败: %s", e)
            return []
        out: list[str] = []
        for sku in _JD_SKU_SEARCH_RE.findall(resp.text):
            if sku not in out:
                out.append(sku)
            if len(out) >= _MAX_PRODUCTS:
                break
        return out

    async def _search_tb_ids(self, client: httpx.AsyncClient, keyword: str) -> list[str]:
        """搜索淘宝/天猫商品 ID。"""
        url = "https://s.taobao.com/search"
        await get_rate_limiter().acquire(url)
        try:
            resp = await retry_async(lambda: client.get(
                url, params={"q": keyword},
                headers=pick_headers({"Accept-Language": "zh-CN,zh;q=0.9"}),
            ))
            resp.raise_for_status()
        except Exception as e:
            logger.warning("淘宝搜索失败: %s", e)
            return []
        out: list[str] = []
        for nid in _TB_ID_SEARCH_RE.findall(resp.text):
            if nid not in out:
                out.append(nid)
            if len(out) >= _MAX_PRODUCTS:
                break
        return out

    async def _fetch_jd_comments_all(self, client: httpx.AsyncClient,
                                      skus: list[str], max_items: int) -> list[CollectedItem]:
        """京东：走公开评论 API 分页拉取。"""
        items: list[CollectedItem] = []
        for sku in skus:
            items.extend(await self._fetch_jd_comments(client, sku, max_items - len(items)))
            if len(items) >= max_items:
                break
        return items

    async def _scrape_product_pages(self, client: httpx.AsyncClient, platform: str,
                                     ids: list[str], max_items: int) -> list[CollectedItem]:
        """淘宝/天猫/拼多多：从商品页提取基本信息（标题、价格等）。评论需登录。"""
        items: list[CollectedItem] = []
        for pid in ids:
            if len(items) >= max_items:
                break
            await get_rate_limiter().acquire(f"https://{platform}.com")
            try:
                item = await self._scrape_one_product(client, platform, pid)
                if item:
                    items.append(item)
            except Exception as e:
                logger.debug("%s 商品页抓取失败 %s: %s", platform, pid, e)
                continue
        return items

    async def _scrape_one_product(self, client: httpx.AsyncClient, platform: str,
                                   pid: str) -> CollectedItem | None:
        """抓取单个商品页，提取标题+价格等信息。"""
        urls_map = {
            "taobao": f"https://item.taobao.com/item.htm?id={pid}",
            "tmall": f"https://detail.tmall.com/item.htm?id={pid}",
            "pdd": f"https://mobile.yangkeduo.com/goods.html?goods_id={pid}",
        }
        url = urls_map.get(platform)
        if not url:
            return None

        headers = pick_headers({"Accept-Language": "zh-CN,zh;q=0.9"})
        cookie = _platform_cookie(platform)
        if cookie:
            headers["Cookie"] = cookie

        resp = await retry_async(lambda: client.get(url, headers=headers), retries=1)
        resp.raise_for_status()

        from ._extract import extract_content
        title, text, _ = extract_content(decode_html(resp), url)

        if not text or not text.strip():
            return None

        return CollectedItem(
            url=url, title=title, content=text,
            metadata={"engine": self.name, "platform": platform, "kind": "product", "pid": pid},
        )

    async def _fetch_jd_comments(self, client: httpx.AsyncClient, sku: str, need: int) -> List[CollectedItem]:
        """对单个 SKU 分页拉取评论，最多 need 条。"""
        if need <= 0:
            return []
        api = "https://club.jd.com/comment/productPageComments.action"
        referer = f"https://item.jd.com/{sku}.html"
        out: List[CollectedItem] = []
        max_pages = (need + _PAGE_SIZE - 1) // _PAGE_SIZE
        for page in range(max_pages):
            await get_rate_limiter().acquire(api)
            params = {
                "productId": sku, "score": 0, "sortType": 5,
                "page": page, "pageSize": _PAGE_SIZE, "isShadowSku": 0, "fold": 1,
            }

            async def _do() -> httpx.Response:
                resp = await client.get(api, params=params, headers=_jd_headers({"Referer": referer}))
                resp.raise_for_status()
                return resp

            try:
                resp = await retry_async(_do)
                data = resp.json()
            except Exception as e:
                logger.warning("京东评论拉取失败 sku=%s page=%s: %s", sku, page, e)
                break
            page_items = _parse_comments(data, sku)
            if not page_items:
                break  # 无更多评论
            out.extend(page_items)
            if len(out) >= need:
                break
        return out[:need]


register(EcommerceCollector())
