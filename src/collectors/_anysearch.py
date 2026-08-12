"""
AnySearch API 异步封装 —— 统一实时搜索（anysearch.ai），JSON-RPC 2.0 协议。

提供三个核心能力：
- search：通用/垂直领域单次搜索，直接返回带正文的结果
- batch_search：2~5 个 query 并行搜索（一次 HTTP 往返）
- extract：URL → Markdown 正文提取（截断 50000 字符）

响应格式：AnySearch 返回 MCP 风格 JSON-RPC——
  {"result": {"content": [{"type": "text", "text": "<Markdown>"}]}}
本模块解析 Markdown 文本，转换为 CollectedItem 列表，与 Tavily 集成模式一致。

设计要点：
- 所有调用均走 httpx async + smart_client（自动国内直连/境外代理分流）
- 失败不抛异常，返回空列表/空串；由调用方决定降级策略
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from src.config.settings import settings

from ._net import retry_async, smart_client
from .base import CollectedItem

logger = logging.getLogger(__name__)

# AnySearch JSON-RPC 端点
_ANYSEARCH_ENDPOINT = "https://api.anysearch.com/mcp"

# 已知的垂直领域（domain）
_KNOWN_DOMAINS = [
    "general", "resource", "social_media", "finance", "academic",
    "legal", "health", "business", "security", "ip", "code",
    "energy", "environment", "agriculture", "travel", "film", "gaming",
]

# Markdown 搜索结果解析正则
# 每条结果格式：
#   ### N. Title
#   - **URL**: https://...
#   - snippet text...
_RE_SEARCH_HEADER = re.compile(r'^## Search Results.*$', re.MULTILINE)
_RE_RESULT_SPLIT = re.compile(r'^###\s+\d+\.\s+', re.MULTILINE)
_RE_URL_LINE = re.compile(r'-\s*\*\*URL\*\*:\s*(\S+)')


def _is_configured() -> bool:
    """AnySearch API Key 是否已配置。"""
    return bool(settings.anysearch_api_key)


def _headers() -> Dict[str, str]:
    """构建请求头：JSON-RPC + Bearer 鉴权。"""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.anysearch_api_key}",
    }


def _build_payload(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """构造 JSON-RPC 2.0 请求体。"""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _extract_mcp_text(result: Any) -> Optional[str]:
    """从 MCP 风格 result 中提取 Markdown 文本（content[0].text）。"""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = first.get("text") or first.get("content") or ""
                if text:
                    return text
        # 兜底：result 本身可能直接含 text
        text = result.get("text") or result.get("content") or ""
        if text:
            return text
    return None


def _parse_search_markdown(text: str, domain: str = "general") -> List[CollectedItem]:
    """解析 AnySearch 搜索返回的 Markdown 文本，转为 CollectedItem 列表。

    格式示例：
        ## Search Results (3 results, 2136ms)

        ### 1. What are AI agents? | Google Cloud
        - **URL**: https://cloud.google.com/discover/what-are-ai-agents
        - AI agents are software systems that...

        ### 2. What Are AI Agents? | IBM
        - **URL**: https://www.ibm.com/think/topics/ai-agents
        - An artificial intelligence (AI) agent refers to...
    """
    items: List[CollectedItem] = []

    # 去掉统计头行 "## Search Results (...)"
    body = _RE_SEARCH_HEADER.sub('', text).strip()

    # 按 "### N. " 切分各条结果
    parts = _RE_RESULT_SPLIT.split(body)
    for part in parts:
        part = part.strip()
        if not part:
            continue

        lines = part.split('\n')
        title = lines[0].strip() if lines else ""

        url = ""
        snippet_lines: List[str] = []

        for line in lines[1:]:
            line_stripped = line.strip()
            # 提取 URL 行
            url_match = _RE_URL_LINE.match(line_stripped)
            if url_match:
                url = url_match.group(1)
                continue
            # 跳过空行和子链接行（如 sitelinks:）
            if not line_stripped or line_stripped.startswith('sitelinks:'):
                continue
            # 去掉 Markdown 列表前缀 "- "（AnySearch 搜索结果的正文行以列表项呈现）
            if line_stripped.startswith('- '):
                line_stripped = line_stripped[2:]
            snippet_lines.append(line_stripped)

        content = ' '.join(snippet_lines).strip()
        if not url and not content:
            continue

        # 去掉 URL 中的尾部标点
        url = url.rstrip('.,;:')

        items.append(CollectedItem(
            url=url,
            title=title,
            content=content,
            metadata={"engine": "anysearch", "via": "anysearch", "domain": domain},
        ))

    return items


async def _call_api(tool_name: str, arguments: Dict[str, Any], timeout: float = 30.0) -> Optional[str]:
    """调用 AnySearch JSON-RPC API，返回提取后的 Markdown 文本或 None（失败时）。"""
    payload = _build_payload(tool_name, arguments)

    async def _post() -> httpx.Response:
        async with smart_client(_ANYSEARCH_ENDPOINT, timeout=timeout) as client:
            return await client.post(_ANYSEARCH_ENDPOINT, json=payload, headers=_headers())

    try:
        resp = await retry_async(_post)
        resp.raise_for_status()
        data = resp.json()
        # JSON-RPC 错误
        if "error" in data:
            logger.warning("AnySearch %s 返回错误: %s", tool_name, data["error"])
            return None
        return _extract_mcp_text(data.get("result"))
    except Exception as e:
        logger.warning("AnySearch %s 调用失败: %s", tool_name, e)
        return None


# ---- 公共 API ----

async def search(
    query: str,
    *,
    domain: Optional[str] = None,
    sub_domain: Optional[str] = None,
    sub_domain_params: Optional[Dict[str, str]] = None,
    max_results: int = 10,
) -> List[CollectedItem]:
    """单次搜索（通用或垂直领域）。

    参数：
        query: 搜索关键词
        domain: 垂直领域（如 "finance"、"social_media"），None 为通用搜索
        sub_domain: 子领域路由键（如 "finance.quote"），需先 get_sub_domains 查询
        sub_domain_params: 子领域参数（如 {"symbol": "AAPL"}）
        max_results: 1~10，默认 10

    返回：CollectedItem 列表（带 url/title/content，via="anysearch"）；失败返回空列表。
    """
    if not _is_configured():
        return []

    arguments: Dict[str, Any] = {"query": query, "max_results": min(max_results, 10)}
    if domain:
        arguments["domain"] = domain
    if sub_domain:
        arguments["sub_domain"] = sub_domain
    if sub_domain_params:
        arguments["sub_domain_params"] = sub_domain_params

    text = await _call_api("search", arguments)
    if not text:
        return []

    items = _parse_search_markdown(text, domain=domain or "general")
    logger.info("AnySearch search 返回 %d 条（query=%s, domain=%s）", len(items), query, domain or "general")
    return items


async def batch_search(
    queries: List[Dict[str, Any]],
    *,
    domain: Optional[str] = None,
    sub_domain: Optional[str] = None,
    sub_domain_params: Optional[Dict[str, str]] = None,
) -> List[CollectedItem]:
    """批量并行搜索（2~5 个 query，一次 HTTP 往返）。

    参数：
        queries: 查询对象列表，每项 {"query": "..."}，可选附带自己的 domain/sub_domain/max_results
        domain: 共享领域（注入到未指定 domain 的 query）
        sub_domain: 共享子领域
        sub_domain_params: 共享子领域参数

    返回：所有 query 的结果合并（去重由调用方处理）；失败返回空列表。
    """
    if not _is_configured() or not queries:
        return []

    # 共享参数注入到未指定的 query 项
    enriched = []
    for q in queries[:5]:  # 最多 5 个
        if isinstance(q, str):
            q = {"query": q}
        item = dict(q)
        if domain and "domain" not in item:
            item["domain"] = domain
        if sub_domain and "sub_domain" not in item:
            item["sub_domain"] = sub_domain
        if sub_domain_params and "sub_domain_params" not in item:
            item["sub_domain_params"] = sub_domain_params
        enriched.append(item)

    text = await _call_api("batch_search", {"queries": enriched})
    if not text:
        return []

    items = _parse_search_markdown(text, domain=domain or "general")
    logger.info("AnySearch batch_search 返回 %d 条（%d queries）", len(items), len(enriched))
    return items


async def extract(url: str) -> str:
    """提取指定 URL 的全文 Markdown（截断 50000 字符）。

    返回：Markdown 文本（含标题和来源信息）；失败返回空串。
    """
    if not _is_configured() or not url:
        return ""

    text = await _call_api("extract", {"url": url}, timeout=60.0)
    if not text:
        return ""

    logger.info("AnySearch extract 返回 %d 字（url=%s）", len(text), url[:80])
    return text


async def get_sub_domains(domain: str) -> Dict[str, Any]:
    """查询某垂直领域的子领域目录（含参数说明）。

    返回：子领域信息字典；失败返回空字典。调用方可缓存结果避免重复请求。
    """
    if not _is_configured():
        return {}

    text = await _call_api("get_sub_domains", {"domain": domain})
    if not text:
        return {}
    # get_sub_domains 返回的也是 Markdown 文本，原样返回给调用方解析
    return {"markdown": text, "domain": domain}
