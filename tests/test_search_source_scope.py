"""指定来源是约束，不能在搜索后端降级时悄悄放宽。"""
import pytest
import httpx
import sys

from src.collectors.base import CollectedItem
from src.collectors.search_collector import SearchDiscoveryCollector
from src.conductor.task_spec import TaskSpec


@pytest.mark.asyncio
async def test_empty_site_search_never_falls_back_to_unrequested_sites(monkeypatch):
    collector = SearchDiscoveryCollector()
    calls = []
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    async def discover(backend, query, domain, **kwargs):
        calls.append(domain)
        return []
    monkeypatch.setattr(collector, "_discover_one", discover)
    result = await collector.collect(TaskSpec(intent="指定两个站点的资讯", keywords=["测试主题"], site_domains=["first.example", "second.example"], max_items=2))
    assert not result.success
    assert set(calls) == {"first.example", "second.example"}


@pytest.mark.asyncio
async def test_inline_search_cannot_fill_scoped_results_with_outside_content(monkeypatch):
    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    async def search(*args, **kwargs):
        return [CollectedItem(url="https://outside.example/target.example", content="站外正文" * 100)]
    async def empty(*args, **kwargs):
        return []
    monkeypatch.setattr(collector, "_search_anysearch", search)
    monkeypatch.setattr(collector, "_discover_one", empty)
    result = await collector.collect(TaskSpec(intent="目标站点", keywords=["主题"], site_domains=["target.example"], max_items=1))
    assert not result.success and not result.items


@pytest.mark.parametrize("url, accepted", [
    ("https://target.example/a", True), ("https://news.target.example/a", True),
    ("https://TARGET.EXAMPLE:443/a", True), ("https://eviltarget.example/a", False),
    ("https://outside.example/target.example", False), ("https://target.example@outside.example/a", False),
])
def test_discovery_filters_by_hostname_boundary(url, accepted):
    assert bool(SearchDiscoveryCollector._dedup_filter([url], "target.example", 5)) is accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("final_host, accepted", [("outside.example", False), ("news.target.example", True)])
async def test_redirected_body_uses_final_source_identity(monkeypatch, final_host, accepted):
    from src.collectors import search_collector as module
    collector = SearchDiscoveryCollector()
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    async def discover(backend, *args, **kwargs):
        return ["https://target.example/redirect"] if backend == "searxng" else []
    monkeypatch.setattr(collector, "_discover_one", discover)
    def respond(request):
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": f"https://{final_host}/article"})
        return httpx.Response(200, text="<article>最终来源正文</article>")
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True))
    monkeypatch.setattr(module, "extract_content", lambda html, url: ("合成标题", "最终来源正文", {}))
    monkeypatch.setattr(module, "dedup_seen", lambda url: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    result = await collector.collect(TaskSpec(intent="站内正文", keywords=["主题"], site_domains=["target.example"], max_items=1))
    assert result.success is accepted
    assert [item.url for item in result.items] == ([f"https://{final_host}/article"] if accepted else [])


@pytest.mark.asyncio
async def test_unexpanded_inline_snippets_do_not_block_available_full_bodies(monkeypatch):
    """摘要补采仍为空时，不能占满数量而阻断已有后端的正文补齐。"""
    from unittest.mock import AsyncMock
    from src.collectors import _extract, search_collector as module

    collector = SearchDiscoveryCollector()
    snippets = [
        CollectedItem(url=f"https://target.example/snippet-{i}", content="摘" * size,
                      metadata={"engine": "anysearch", "via": "anysearch"})
        for i, size in enumerate([92, 100, 120])
    ]
    full_urls = [f"https://target.example/article-{i}" for i in range(3)]
    http_calls, discovery_calls = [], []
    limiter = AsyncMock()

    def respond(request):
        http_calls.append(str(request.url))
        body = "" if request.url.path.startswith("/snippet-") else "完整来源的字段与事实。" * 40
        return httpx.Response(200, text=f"<html><body><article>{body}</article></body></html>")

    async def discover(backend, query, domain, **kwargs):
        discovery_calls.append((backend, domain))
        return full_urls if backend == "searxng" else []

    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", discover)
    monkeypatch.setattr(module._anysearch, "search", AsyncMock(return_value=snippets))
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True))
    monkeypatch.setattr(module, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(module, "dedup_seen", lambda url: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    monkeypatch.setattr(_extract, "_TRAFILATURA_OK", False)
    monkeypatch.setattr(_extract, "_READABILITY_OK", False)
    monkeypatch.setitem(sys.modules, "crawl4ai", None)

    result = await collector.collect(TaskSpec(intent="取得来源正文", keywords=["合成主题"],
        site_domains=["target.example"], max_items=3))

    assert http_calls[:3] == [item.url for item in snippets], "必须经过真实摘要补采接缝"
    assert discovery_calls == [("searxng", "target.example")], "摘要没有补全，备用后端仍应补正文"
    assert result.success and [item.url for item in result.items] == full_urls
    assert all(len(item.content) >= 200 for item in result.items)


@pytest.mark.asyncio
async def test_available_body_can_replace_the_same_url_inline_snippet(monkeypatch):
    """同网址的摘要不能触发跨后端去重，阻断后来可取得的完整正文。"""
    from unittest.mock import AsyncMock
    from src.collectors import _extract, search_collector as module

    collector = SearchDiscoveryCollector()
    url = "https://target.example/article"
    snippet = CollectedItem(url=url, content="摘" * 92, metadata={"via": "anysearch"})
    requests = []
    limiter = AsyncMock()

    def respond(request):
        requests.append(str(request.url))
        body = "" if len(requests) == 1 else "完整来源的字段与事实。" * 40
        return httpx.Response(200, text=f"<html><body><article>{body}</article></body></html>")

    async def discover(backend, query, domain, **kwargs):
        return [url] if backend == "searxng" else []

    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", discover)
    monkeypatch.setattr(module._anysearch, "search", AsyncMock(return_value=[snippet]))
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True))
    monkeypatch.setattr(module, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(module, "dedup_seen", lambda url: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    monkeypatch.setattr(_extract, "_TRAFILATURA_OK", False)
    monkeypatch.setattr(_extract, "_READABILITY_OK", False)
    monkeypatch.setitem(sys.modules, "crawl4ai", None)

    result = await collector.collect(TaskSpec(intent="取得来源正文", keywords=["合成主题"],
        site_domains=["target.example"], max_items=1))

    assert result.success and [item.url for item in result.items] == [url]
    assert len(result.items[0].content) >= 200, "后续全文必须替换同网址的未补全摘要"
    assert requests == [url, url], "同网址的正文读取不能被摘要去重跳过"


@pytest.mark.asyncio
async def test_unexpanded_snippet_is_retained_when_other_backends_are_empty(monkeypatch):
    """备用来源耗尽仍保留已取得的短资料，由后续内容核验判断是否够用。"""
    from unittest.mock import AsyncMock
    from src.collectors import search_collector as module

    collector = SearchDiscoveryCollector()
    snippet = CollectedItem(url="https://target.example/brief", content="合成短公告。",
                            metadata={"via": "anysearch"})
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module._anysearch, "search", AsyncMock(return_value=[snippet]))
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=""))))

    result = await collector.collect(TaskSpec(intent="取得来源正文", keywords=["合成主题"],
        site_domains=["target.example"], max_items=3))

    assert result.success and len(result.items) == 1
    assert result.items[0].url == snippet.url and result.items[0].content == snippet.content
    assert result.items[0].metadata["via"] == "anysearch"


@pytest.mark.asyncio
async def test_latest_search_prioritizes_dated_bodies_from_later_backend(monkeypatch):
    """最新资料的无日期摘要和列表不能阻断后续已注明日期的正文。"""
    from unittest.mock import AsyncMock
    from src.collectors import search_collector as module

    collector = SearchDiscoveryCollector()
    early = [CollectedItem(url=f"https://target.example/early-{i}", content="摘" * size)
             for i, size in enumerate([92, 100, 2492])]
    dated = [CollectedItem(url=f"https://target.example/dated-{i}", content="合成完整正文。" * 60,
                           metadata={"date": f"2026-10-0{i + 1}", "via": "tavily"})
             for i in range(3)]
    tavily = AsyncMock(return_value=dated)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_search_anysearch", AsyncMock(return_value=early))
    monkeypatch.setattr(collector, "_search_tavily", tavily)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "search_tavily_first", True)

    result = await collector.collect(TaskSpec(intent="取得最新资料", keywords=["合成主题"],
        site_domains=["target.example"], max_items=3, time_range="最新"))

    assert tavily.await_count == 1, "第一批无日期资料不能占满最新正文的数量"
    assert result.success and [item.url for item in result.items] == [item.url for item in reversed(dated)]
    assert all(item.metadata.get("date") for item in result.items)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anysearch", "tavily"])
async def test_inline_enrichment_stops_at_deadline_and_retains_returned_snippets(monkeypatch, provider):
    """补采超预算时保留本批已返回摘要，不再读取其余页面或搜索后端。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.collectors import search_collector as module

    collector = SearchDiscoveryCollector()
    snippets = [CollectedItem(url=f"https://target.example/brief-{i}", content="摘要" * 40,
                              metadata={"via": provider}) for i in range(3)]
    clock, gets, cancelled = [0.0], [], []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 10.0)
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://api.tavily.example")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: provider == "anysearch")
    monkeypatch.setattr(collector, "_use_tavily", lambda: provider == "tavily")
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(collector, "_discover_one", discover)
    async def search(*args, **kwargs):
        clock[0] = 7.99
        return snippets

    monkeypatch.setattr(module._anysearch, "search", search)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())

    async def single_attempt(operation, **kwargs):
        return await operation()

    async def respond(request):
        if request.method == "POST":
            clock[0] = 7.99
            return httpx.Response(200, json={"results": [item.to_dict() for item in snippets]})
        gets.append(str(request.url))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        finally:
            clock[0] = 9.0

    monkeypatch.setattr(module, "retry_async", single_attempt)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True))

    result = await collector.collect(TaskSpec(intent="保留预算内资料", keywords=["合成主题"], max_items=3))

    assert result.success and [item.url for item in result.items] == [item.url for item in snippets]
    assert [item.content for item in result.items] == [item.content for item in snippets]
    assert gets == [snippets[0].url], "本批 API 资料已保留，补采耗尽后不能再发出读取"
    assert cancelled == [True], "永久等待的补采请求必须被剩余预算取消"
    discover.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anysearch", "tavily", "searxng"])
async def test_html_publication_date_is_preserved_for_recency_filter(monkeypatch, provider):
    """发布日期只存在 HTML meta 时，正文提取后仍须进入原有时效过滤。"""
    from unittest.mock import AsyncMock
    from src.collectors import _extract, search_collector as module

    collector = SearchDiscoveryCollector()
    url = "https://target.example/article"
    snippet = CollectedItem(url=url, content="合成摘要", metadata={"via": provider})
    monkeypatch.setattr(collector, "_use_anysearch", lambda: provider == "anysearch")
    monkeypatch.setattr(collector, "_use_tavily", lambda: provider == "tavily")
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"] if provider == "searxng" else [])
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://api.tavily.example")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module._anysearch, "search", AsyncMock(return_value=[snippet]))
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(module, "dedup_seen", lambda url: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    monkeypatch.setattr(_extract, "_TRAFILATURA_OK", False)
    monkeypatch.setattr(_extract, "_READABILITY_OK", False)

    async def discover(backend, *args, **kwargs):
        return [url] if backend == "searxng" else []

    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"results": [snippet.to_dict()]})
        body = "合成完整正文且不含日期。" * 40
        return httpx.Response(200, text='<meta name="date" content="2001-01-01"><article>' + body + '</article>')

    monkeypatch.setattr(collector, "_discover_one", discover)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True))

    result = await collector.collect(TaskSpec(intent="近一天资料", keywords=["合成主题"],
        site_domains=["target.example"], max_items=1, time_range="近1天"))

    assert not result.success and not result.items, "原网页已明确过期，不能丢日期后保留为未知来源"


@pytest.mark.asyncio
async def test_latest_undated_full_body_is_retained_when_backends_are_exhausted(monkeypatch):
    """最新任务不能因为无法确认日期就丢弃已取得的正文。"""
    from unittest.mock import AsyncMock

    collector = SearchDiscoveryCollector()
    article = CollectedItem(url="https://target.example/article", content="无日期的完整公开正文。" * 40,
                            metadata={"via": "anysearch"})
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_search_anysearch", AsyncMock(return_value=[article]))
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(collector, "_discover_one", discover)

    result = await collector.collect(TaskSpec(intent="取得最新资料", keywords=["合成主题"],
        site_domains=["target.example"], max_items=1, time_range="最新"))

    assert discover.await_count > 0, "日期待核对的正文不能阻断备用来源"
    assert result.success and result.items == [article], "备用来源耗尽后仍保留无日期正文供核对"


@pytest.mark.asyncio
async def test_short_public_body_without_recency_is_not_rejected_by_snippet_threshold(monkeypatch):
    """摘要补采阈值不能成为所有短正文的淘汰门。"""
    from unittest.mock import AsyncMock
    from src.collectors import _extract, search_collector as module

    collector = SearchDiscoveryCollector()
    url = "https://target.example/brief"
    body = "这是一则正常的公开短公告。"
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[url]))
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(_extract, "_TRAFILATURA_OK", False)
    monkeypatch.setattr(_extract, "_READABILITY_OK", False)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module, "dedup_seen", lambda link: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=f"<article>{body}</article>"))))

    result = await collector.collect(TaskSpec(intent="读取公开短公告", keywords=["合成主题"],
        site_domains=["target.example"], max_items=1))

    assert result.success and len(result.items) == 1
    assert result.items[0].url == url and result.items[0].content == body
    assert result.items[0].metadata["via"] == "html_to_text"


@pytest.mark.asyncio
async def test_recency_text_reaches_each_configured_search_backend(monkeypatch):
    """最新偏好无固定时间档位，但原要求仍须传到每个实际发现请求。"""
    import json
    from unittest.mock import AsyncMock
    from src.collectors import search_collector as module

    collector = SearchDiscoveryCollector()
    queries = {}
    client_class = httpx.AsyncClient
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "searxng_base_url", "https://searxng.example")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())

    def respond(request):
        if request.url.host == "api.anysearch.com":
            queries["anysearch"] = json.loads(request.content)["params"]["arguments"]["query"]
            return httpx.Response(200, json={"result": {"content": [
                {"type": "text", "text": "## Search Results (0 results)"}]}})
        if request.url.host == "tavily.example":
            queries["tavily"] = json.loads(request.content)["query"]
            return httpx.Response(200, json={"results": []})
        if request.url.host == "searxng.example":
            queries["searxng"] = request.url.params["q"]
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, text="")

    def client(*args, **kwargs):
        return client_class(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: client(**kwargs))
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: client(**kwargs))

    await collector.collect(TaskSpec(intent="取得最新资料", keywords=["合成主题"],
        site_domains=["target.example"], max_items=3, time_range="最新"))

    assert set(queries) == {"anysearch", "tavily", "searxng"}, "须经过三个真实后端的请求构造"
    assert all("最新" in query for query in queries.values()), queries
    assert all("合成主题" in query for query in queries.values()), queries
    assert queries["searxng"].startswith("site:target.example "), "时间偏好不能改站点范围"


@pytest.mark.asyncio
async def test_browser_html_publication_date_is_preserved_for_recency_filter(monkeypatch):
    """浏览器正文不带日期时，原始 HTML 的明确旧日期仍须参与时效过滤。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.collectors import search_collector as module

    collector = SearchDiscoveryCollector()
    url = "https://target.example/article"
    crawled = []

    class FakeCrawler:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def arun(self, *, url):
            crawled.append(url)
            return SimpleNamespace(url=url, redirected_url=None, success=True,
                markdown="合成公告正文且不含发布时间。" * 40,
                html='<meta name="date" content="2001-01-01"><article>合成公告正文</article>',
                metadata={"title": "合成旧公告"})

    async def discover(backend, *args, **kwargs):
        return [url] if backend == "searxng" else []

    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=FakeCrawler))
    monkeypatch.setattr(module, "crawler_browser_config", lambda: None)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", discover)

    result = await collector.collect(TaskSpec(intent="近一天的公开公告", keywords=["合成主题"],
        site_domains=["target.example"], max_items=1, time_range="近1天"))

    assert crawled == [url], "须经过真实浏览器抓取接缝"
    assert not result.success and not result.items, "HTML 已明确过期，不能丢日期后保留为未知资料"
