"""项目发现先保留来源范围，再将候选交给业务核验。"""
import json

import httpx
import pytest

from src.collectors import search_collector as module
from src.collectors.search_collector import SearchDiscoveryCollector
from src.conductor.task_spec import TaskSpec


@pytest.fixture(autouse=True)
def isolate_public_page_reads(monkeypatch):
    """所有原页读取都留在内存传输层，测试不得请求外部站点。"""
    from unittest.mock import AsyncMock
    client_class = httpx.AsyncClient
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(lambda request: httpx.Response(200,
            text="<article><h1>正式项目</h1><p>" + "正式项目实施与来源依据。" * 80 + "</p></article>"))))


@pytest.mark.asyncio
async def test_inline_discovery_constrains_site_before_api_result_truncation(monkeypatch):
    """先全网取少量再滤域会漏掉真实存在的站内资料。"""
    seen_queries = []

    def respond(request):
        query = json.loads(request.content)["params"]["arguments"]["query"]
        seen_queries.append(query)
        host = "official.example" if query.startswith("site:official.example ") else "outside.example"
        body = "## Search Results (1 results)\n### 1. 合成项目\n- **URL**: https://" + host + "/project\n- " + "项目实施内容与正式来源依据。" * 30
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": body}]}})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    from unittest.mock import AsyncMock
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))

    result = await collector.collect(TaskSpec(intent="合成项目", keywords=["配网项目"],
        site_domains=["official.example"], max_items=1))

    assert result.success and [item.url for item in result.items] == ["https://official.example/project"]
    assert seen_queries == ["site:official.example 配网项目"]


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_limit, expected_calls", [(32, 16), (8, 8)])
async def test_project_queries_and_domains_share_candidate_pool_without_joining(monkeypatch, candidate_limit, expected_calls):
    """八组词与两家来源都能贡献候选，项目目标不截断待核对网页。"""
    from unittest.mock import AsyncMock
    calls = []

    def respond(request):
        args = json.loads(request.content)["params"]["arguments"]
        query = args["query"]
        calls.append(query)
        site, keyword = query.split(" ", 1)
        host = site.removeprefix("site:")
        results = "## Search Results (2 results)\n"
        for index in range(2):
            results += f"### {index + 1}. 合成项目{keyword}-{index}\n- **URL**: https://{host}/{keyword}/{index}\n- " + "正式项目实施与来源依据。" * 30 + "\n"
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": results}]}})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    spec = TaskSpec(intent="官方项目", keywords=[f"组合{i}" for i in range(8)],
        site_domains=["first.example", "second.example"], max_items=8,
        project_collection={"target_count": 8, "candidate_limit": candidate_limit,
            "queries": [{"name": f"组{i}", "keyword": f"组合{i}"} for i in range(8)]})

    result = await collector.collect(spec)

    assert result.success and len(result.items) == candidate_limit
    assert len(calls) == expected_calls and len(set(calls)) == expected_calls
    assert {query.split(" ", 1)[1] for query in calls} == {f"组合{i}" for i in range(8)}
    assert {httpx.URL(item.url).host for item in result.items} == {"first.example", "second.example"}
    assert result.coverage["discovery"]["stop_reason"] == "candidate_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "api_error", "http_status", "invalid_json", "unrecognized_text"])
async def test_project_unknown_search_preserves_failure_and_continues_independent_queries(monkeypatch, failure):
    """失败请求不重发，但其它独立关键词仍可贡献资料。"""
    from unittest.mock import AsyncMock
    calls = []

    def respond(request):
        query = json.loads(request.content)["params"]["arguments"]["query"]
        calls.append(query)
        if query.endswith("项目二"):
            body = "## Search Results (1 results)\n### 1. 独立项目\n- **URL**: https://official.example/project\n- " + "项目实施与来源依据。" * 30
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": body}]}})
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if failure == "api_error":
            return httpx.Response(200, json={"error": {"code": -1}})
        if failure == "http_status":
            return httpx.Response(429)
        if failure == "invalid_json":
            return httpx.Response(200, text="not-json")
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": "unrecognized response"}]}})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(module.settings, "collector_retry_base_seconds", 0)
    monkeypatch.setattr(module.settings, "collector_retry_max_seconds", 0)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    fallbacks = AsyncMock(return_value=[])
    monkeypatch.setattr(collector, "_discover_one", fallbacks)
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目一", "项目二"],
        site_domains=["official.example"], project_collection={"target_count": 2, "candidate_limit": 4,
            "queries": [{"name": "一", "keyword": "项目一"}, {"name": "二", "keyword": "项目二"}]}))

    assert result.success and len(calls) == 2 and len(set(calls)) == 2
    assert [item.url for item in result.items] == ["https://official.example/project"]
    assert result.coverage["discovery"]["stop_reason"] == "source_unknown"
    attempts = result.coverage["discovery"]["attempts"]
    assert [item["status"] for item in attempts[:2]] == ["unknown", "completed"]
    codes = {"timeout": "SOURCE_TIMEOUT", "api_error": "SOURCE_API_ERROR", "http_status": "SOURCE_HTTP_429",
        "invalid_json": "SOURCE_INVALID_JSON", "unrecognized_text": "SOURCE_FORMAT_ERROR"}
    assert attempts[0]["error_code"] == codes[failure] and attempts[1]["error_code"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["tavily", "searxng"])
async def test_project_other_api_timeout_is_unknown_and_not_retried(monkeypatch, backend):
    from unittest.mock import AsyncMock
    calls = []
    client_class = httpx.AsyncClient

    def respond(request):
        calls.append(str(request.url))
        raise httpx.ReadTimeout("synthetic", request=request)

    def client(*args, **kwargs):
        return client_class(transport=httpx.MockTransport(respond), **kwargs)

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "searxng_base_url", "https://searxng.example")
    monkeypatch.setattr(module.settings, "collector_retry_base_seconds", 0)
    monkeypatch.setattr(module.settings, "collector_retry_max_seconds", 0)
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: backend == "tavily")
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"] if backend == "searxng" else [])
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: client(**kwargs))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"],
        site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 4}))

    assert not result.success and len(calls) == 4 and len(set(calls)) == 4
    assert result.coverage["discovery"]["stop_reason"] == "source_unknown"


@pytest.mark.asyncio
async def test_project_budget_retains_finished_candidates_and_stops_other_queries(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    clock, calls = [0.0], []

    def respond(request):
        calls.append(str(request.url))
        clock[0] = 9.0
        body = "## Search Results (1 results)\n### 1. 合成项目\n- **URL**: https://official.example/project\n- " + "项目实施与明确来源依据。" * 30
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": body}]}})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 10)
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    fallbacks = AsyncMock(return_value=[])
    monkeypatch.setattr(collector, "_discover_one", fallbacks)
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目一", "项目二"],
        site_domains=["official.example"], project_collection={"target_count": 2, "candidate_limit": 4}))

    assert result.success and [item.url for item in result.items] == ["https://official.example/project"]
    assert len(calls) == 1 and result.coverage["discovery"]["stop_reason"] == "time_budget"
    fallbacks.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_project_blocking_request_is_cancelled_without_replay(monkeypatch, cancel):
    import asyncio
    from unittest.mock import AsyncMock
    requests, cancelled = [], []

    async def respond(request):
        requests.append(json.loads(request.content)["params"]["arguments"]["query"])
        try:
            if cancel:
                raise asyncio.CancelledError()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 0.05)
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    operation = collector.collect(TaskSpec(intent="官方项目", keywords=["项目一", "项目二"],
        site_domains=["official.example"], project_collection={"target_count": 2, "candidate_limit": 4}))

    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await operation
    else:
        result = await asyncio.wait_for(operation, timeout=0.5)
        assert not result.success and result.coverage["discovery"]["stop_reason"] == "time_budget"
        assert all(item["error_code"] == "SOURCE_TIMEOUT" for item in result.coverage["discovery"]["attempts"])
    assert len(requests) == (1 if cancel else 2) and len(set(requests)) == len(requests)
    assert cancelled == [True] * len(requests)


@pytest.mark.asyncio
async def test_known_empty_query_continues_but_invalid_or_duplicate_pages_do_not_fill_pool(monkeypatch):
    from unittest.mock import AsyncMock
    calls = []

    def respond(request):
        query = json.loads(request.content)["params"]["arguments"]["query"]
        calls.append(query)
        if query.endswith("项目一"):
            text = "## Search Results (0 results)"
        else:
            text = "## Search Results (4 results)\n"
            for number, host, path, content in [
                (1, "official.example", "project", "正常项目与明确来源依据。" * 30),
                (2, "official.example", "project", "同一网址的重复正文。" * 30),
                (3, "outside.example", "project", "未授权站外资料。" * 30),
                (4, "official.example", "challenge", "安全验证，请完成验证码"),
            ]:
                text += f"### {number}. 合成标题\n- **URL**: https://{host}/{path}\n- {content}\n"
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": text}]}})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module._anysearch, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(collector, "_fetch_inline_page", AsyncMock(side_effect=lambda url, *args, **kwargs:
        httpx.Response(200, text="", request=httpx.Request("GET", url))))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目一", "项目二"],
        site_domains=["official.example"], project_collection={"target_count": 2, "candidate_limit": 8}))

    assert len(calls) == 2 and result.success
    assert [item.url for item in result.items] == ["https://official.example/project"]
    assert result.coverage["discovery"]["stop_reason"] == "exhausted"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anysearch", "tavily"])
async def test_project_reads_long_summaries_and_preserves_unknown_pages_without_replay(monkeypatch, provider):
    """单页未知保留摘要诊断，其他页继续；后续独立搜索不能重复读取未知页。"""
    from unittest.mock import AsyncMock
    searches, pages = [], []
    original = "原页明确记载的独立项目实施内容。" * 30
    long_summary = "搜索摘要不能冒充原页内容。" * 40

    def respond(request):
        if request.method == "POST":
            data = json.loads(request.content)
            query = data["params"]["arguments"]["query"] if provider == "anysearch" else data["query"]
            searches.append(query)
            urls = ["https://official.example/unknown", "https://official.example/complete"] if len(searches) == 1 else ["https://official.example/unknown"]
            if provider == "tavily":
                return httpx.Response(200, json={"results": [{"url": url, "title": "项目", "content": long_summary} for url in urls]})
            text = "## Search Results\n" + "\n".join(f"### {index}. 项目\n- **URL**: {url}\n- {long_summary}" for index, url in enumerate(urls, 1))
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": text}]}})
        pages.append(str(request.url))
        if request.url.path == "/unknown":
            raise httpx.ReadTimeout("synthetic private details must not be saved", request=request)
        return httpx.Response(200, text=f"<article><h1>项目</h1><p>{original}</p></article>")

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(collector, "_use_anysearch", lambda: provider == "anysearch")
    monkeypatch.setattr(collector, "_use_tavily", lambda: provider == "tavily")
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    client = lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module, "smart_client", client)
    monkeypatch.setattr(module._anysearch, "smart_client", client)

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目一", "项目二"],
        site_domains=["official.example"], project_collection={"target_count": 2, "candidate_limit": 6}))

    assert len(searches) == 2 and pages == ["https://official.example/unknown", "https://official.example/complete"]
    assert len(result.items) == 2 and result.items[0].content == long_summary
    assert result.items[0].metadata["body_status"] == "unknown"
    assert result.items[0].metadata["body_error_code"] == "SOURCE_TIMEOUT"
    assert result.items[1].metadata["body_status"] == "complete" and original in result.items[1].content
    attempts = result.coverage["discovery"]["attempts"]
    assert attempts[0]["source_pages"][0] == {"index": 1, "status": "unknown", "error_code": "SOURCE_TIMEOUT"}
    assert "synthetic private details" not in json.dumps(result.coverage)


@pytest.mark.asyncio
async def test_project_tavily_raw_content_is_explicit_without_duplicate_page_read(monkeypatch):
    from unittest.mock import AsyncMock
    methods = []

    def respond(request):
        methods.append(request.method)
        assert request.method == "POST"
        return httpx.Response(200, json={"results": [{"url": "https://official.example/project",
            "title": "项目", "raw_content": "已提供的原页正文。", "content": "搜索摘要"}]})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"],
        site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 1}))

    assert methods == ["POST"] and len(result.items) == 1
    assert result.items[0].content == "已提供的原页正文。"
    assert result.items[0].metadata["body_status"] == "provider_raw"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["denied", "cancelled"])
async def test_project_page_authorization_stop_and_cancel_propagate(monkeypatch, failure):
    import asyncio
    from unittest.mock import AsyncMock
    from src.account_execution import ExecutionDenied
    requests = []

    def respond(request):
        requests.append(request.method)
        if request.method == "POST":
            text = "## Search Results\n### 1. 项目\n- **URL**: https://official.example/project\n- " + "搜索摘要。" * 60
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": text}]}})
        if failure == "denied":
            raise ExecutionDenied("synthetic authorization stopped")
        raise asyncio.CancelledError()

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    client = lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module, "smart_client", client)
    monkeypatch.setattr(module._anysearch, "smart_client", client)

    with pytest.raises(ExecutionDenied if failure == "denied" else asyncio.CancelledError):
        await collector.collect(TaskSpec(intent="官方项目", keywords=["项目一", "项目二", "未派发第三组"],
            site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 4}))
    # 同批两个搜索在正文拒绝前已经完成；拒绝后不能再读第二页或派下一批。
    assert requests == ["POST", "POST", "GET"]


@pytest.mark.asyncio
async def test_project_html_page_failure_preserves_previous_complete_page(monkeypatch):
    import sys
    from unittest.mock import AsyncMock
    pages = []
    client_class = httpx.AsyncClient

    def respond(request):
        if request.url.host == "searxng.example":
            return httpx.Response(200, json={"results": [
                {"url": "https://official.example/complete"}, {"url": "https://official.example/unknown"}]})
        pages.append(str(request.url))
        if request.url.path == "/unknown":
            raise httpx.ReadTimeout("synthetic hidden message", request=request)
        return httpx.Response(200, text="<article><p>" + "正式项目实施的原页依据。" * 40 + "</p></article>")

    collector = SearchDiscoveryCollector()
    discover = collector._discover_one
    async def only_api(backend, *args, **kwargs):
        return await discover(backend, *args, **kwargs) if backend == "searxng" else []

    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(module.settings, "searxng_base_url", "https://searxng.example")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", only_api)
    monkeypatch.setattr(module, "dedup_seen", lambda url: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(respond)))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"],
        site_domains=["official.example"], project_collection={"target_count": 2, "candidate_limit": 4}))

    assert result.success and [item.url for item in result.items] == ["https://official.example/complete", "https://official.example/unknown"]
    assert result.items[1].metadata["body_status"] == "unknown" and not result.items[1].content
    assert pages == ["https://official.example/complete", "https://official.example/unknown"]
    outcomes = result.coverage["discovery"]["attempts"][0]["source_pages"]
    assert outcomes == [{"index": 1, "status": "complete", "error_code": None},
        {"index": 2, "status": "unknown", "error_code": "SOURCE_TIMEOUT"}]


@pytest.mark.asyncio
async def test_slow_first_primary_backend_preserves_time_for_next_primary(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    hosts = []

    async def respond(request):
        hosts.append(request.url.host)
        if request.url.host == "api.anysearch.com":
            await asyncio.Event().wait()
        return httpx.Response(200, json={"results": [{"url": "https://official.example/project",
            "title": "项目", "raw_content": "原页明确记载的项目内容。"}]})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 0.15)
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    client = lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module, "smart_client", client)
    monkeypatch.setattr(module._anysearch, "smart_client", client)

    result = await asyncio.wait_for(collector.collect(TaskSpec(intent="官方项目", keywords=["项目"],
        site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 1})), timeout=0.5)

    assert hosts == ["api.anysearch.com", "tavily.example"]
    assert result.success and result.items[0].metadata["body_status"] == "provider_raw"
    attempts = result.coverage["discovery"]["attempts"]
    assert [attempt["status"] for attempt in attempts] == ["unknown", "completed"]
    assert attempts[0]["error_code"] == "SOURCE_TIMEOUT"


@pytest.mark.asyncio
async def test_complete_inline_body_replaces_unknown_same_url_without_new_candidate_or_page_read(monkeypatch):
    from unittest.mock import AsyncMock
    searches, pages = [], []
    url = "https://official.example/project"
    raw = "后续独立搜索返回的原页项目正文。"

    def respond(request):
        if request.method == "GET":
            pages.append(str(request.url))
            raise httpx.ReadTimeout("synthetic", request=request)
        searches.append(request.url.host)
        if request.url.host == "tavily.example":
            return httpx.Response(200, json={"results": [{"url": url, "title": "原页", "raw_content": raw}]})
        text = "## Search Results\n### 1. 摘要\n- **URL**: " + url + "\n- " + "不能冒充原件的搜索摘要。" * 40
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": text}]}})

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    client = lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module, "smart_client", client)
    monkeypatch.setattr(module._anysearch, "smart_client", client)

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"],
        site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 2}))

    assert searches == ["api.anysearch.com", "tavily.example"] and pages == [url]
    assert len(result.items) == 1 and result.items[0].content == raw
    assert result.items[0].metadata["body_status"] == "provider_raw"
    assert result.coverage["discovery"]["candidate_count"] == 1
    attempts = result.coverage["discovery"]["attempts"]
    assert attempts[0]["source_pages"] == [{"index": 1, "status": "unknown", "error_code": "SOURCE_TIMEOUT"}]
    assert attempts[1]["source_pages"] == [{"index": 1, "status": "provider_raw", "error_code": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["details", "one_slot", "full_pool", "time_budget", "table", "ordinary", "unknown", "nested", "five_limit", "alias"])
async def test_project_directory_follows_only_topic_matched_frozen_site_details_once(monkeypatch, mode):
    import sys
    from unittest.mock import AsyncMock
    pages = []
    directory = "https://official.example/news/index.shtml"
    listing = "\n".join([
        "* [![](thumbnail.jpg)\n\n配网自动化工程](./detail/one.shtml) 2026-10-10 项目实施摘要...",
        "* [重复配网自动化工程](./detail/one.shtml) 2026-10-10",
        "* [建设新闻](../detail/two.shtml) 2026-10-11 配网自动化已投运...",
        "* [配网自动化站外](https://outside.example/detail.shtml)",
        "* [配网自动化自身](index.shtml)",
        "* [招聘公告](./jobs.shtml)",
        "* [配网自动化脚本](javascript:alert(1))",
    ])
    if mode == "table":
        listing += "\n|项目名称|实施内容|\n|---|---|\n|正式项目|已完整披露的项目内容|\n"
    if mode == "five_limit":
        listing = "\n".join(f"* [配网自动化工程{i}](./detail/{i}.shtml) 原文摘要..." for i in range(7))
    if mode == "alias":
        listing = "\n".join(["* [AI重构变电站智能巡视体系](./detail/one.shtml) 云端AI变电智巡系统试运行...",
            "* [配网防外破不再靠人盯](./unrelated.shtml) 配网AI防外破管控系统...",
            "* [另一篇新闻](./other.shtml)"])
    clock = [0.0]

    def respond(request):
        if request.method == "POST":
            if mode == "time_budget":
                clock[0] = 9.0
            return httpx.Response(200, json={"results": [{"url": directory, "title": "新闻目录", "raw_content": listing}]})
        pages.append(str(request.url))
        if mode == "unknown" and request.url.path == "/detail/two.shtml":
            raise httpx.ReadTimeout("synthetic", request=request)
        if mode == "nested":
            return httpx.Response(200, text="<article>" +
                '<p>* <a href="next.shtml">配网自动化继续阅读</a></p><p>* <a href="other.shtml">其他栏目</a></p></article>')
        return httpx.Response(200, text="<article><h1>配网自动化工程</h1><p>" + "独立工程实施的完整事实依据。" * 40 + "</p></article>")

    collector = SearchDiscoveryCollector()
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "dedup_seen", lambda url: False)
    monkeypatch.setattr(module, "dedup_mark", lambda *args: None)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    if mode == "time_budget":
        from types import SimpleNamespace
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        monkeypatch.setattr(module.settings, "collect_timeout_seconds", 10)
    candidate_limit = {"one_slot": 2, "full_pool": 1, "five_limit": 20}.get(mode, 6)
    spec = TaskSpec(intent="官方项目", keywords=["配网自动化", "另一独立词"] if mode == "unknown" else ["配网自动化"],
        site_domains=["official.example"], project_collection=None if mode == "ordinary" else {
            "target_count": 1, "candidate_limit": candidate_limit,
            "topics": ["配电网自动化", "智能巡检"] if mode == "alias" else ["配网自动化"]})
    result = await collector.collect(spec)

    expected = ["https://official.example/news/detail/one.shtml", "https://official.example/detail/two.shtml"]
    if mode in {"full_pool", "time_budget", "table", "ordinary"}:
        expected = []
    elif mode in {"one_slot", "alias"}:
        expected = expected[:1]
    elif mode == "five_limit":
        expected = [f"https://official.example/news/detail/{i}.shtml" for i in range(5)]
    assert pages == expected
    assert result.items[0].url == directory and len(result.items) <= candidate_limit
    if mode == "ordinary":
        assert len(result.items) == 1 and "body_status" not in result.items[0].metadata
    elif mode == "table":
        assert len(result.items) == 1 and result.items[0].metadata["body_status"] == "provider_raw"
    else:
        assert result.items[0].metadata["body_status"] == "list_excerpt"
        if mode == "unknown":
            assert [item.metadata["body_status"] for item in result.items[1:]] == ["complete", "unknown"]
            assert not result.items[2].content
        else:
            assert all(item.metadata["body_status"] == "complete" for item in result.items[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("table", [False, True])
@pytest.mark.parametrize("provider", ["anysearch", "searxng", "tavily_raw"])
async def test_project_html_directory_keeps_bounded_links_but_real_data_table_stays_complete(monkeypatch, table, provider):
    import sys
    from unittest.mock import AsyncMock
    from src.collectors import _extract
    calls = []
    directory = "https://official.example/notices/index.jhtml"
    page = ('<article><p><a href="./one.shtml">配网自动化工程</a> 正式项目摘要</p>'
        '<p><a href="/news/">新闻栏目</a></p><a href="https://outside.example/one">配网自动化站外</a>'
        '<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table></article>')
    if table:
        page += '<table><tr><th>项目名称</th><th>业主</th></tr><tr><td>独立配网项目</td><td>某供电公司</td></tr></table>'

    def respond(request):
        if request.url.host == "searxng.example":
            return httpx.Response(200, json={"results": [{"url": directory}]})
        if request.method == "POST":
            if provider == "tavily_raw":
                return httpx.Response(200, json={"results": [{"url": directory, "raw_content": page}]})
            text = "## Search Results\n### 1. 目录\n- **URL**: " + directory + "\n- " + "搜索摘要。" * 70
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": text}]}})
        calls.append(str(request.url))
        return httpx.Response(200, text=page if str(request.url) == directory else
            "<article><h1>配网自动化工程</h1><p>" + "独立工程实施的完整事实依据。" * 40 + "</p></article>")

    collector = SearchDiscoveryCollector()
    client_class = httpx.AsyncClient
    discover = collector._discover_one
    async def only_api(backend, *args, **kwargs):
        return await discover(backend, *args, **kwargs) if backend == "searxng" else []
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-anysearch")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(module.settings, "searxng_base_url", "https://searxng.example")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: provider == "anysearch")
    monkeypatch.setattr(collector, "_use_tavily", lambda: provider == "tavily_raw")
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"] if provider == "searxng" else [])
    monkeypatch.setattr(collector, "_discover_one", only_api)
    monkeypatch.setattr(_extract, "_TRAFILATURA_OK", False)
    monkeypatch.setattr(_extract, "_READABILITY_OK", False)
    client = lambda *args, **kwargs: client_class(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(module, "smart_client", client)
    monkeypatch.setattr(module._anysearch, "smart_client", client)

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["配网自动化"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 4, "topics": ["配网自动化"]}))

    first = result.items[0]
    assert "](" not in first.content, "实际走会丢弃href的HTML纯文本提取接缝"
    assert first.metadata["project_table_complete"] is table
    assert first.metadata["body_status"] == (("provider_raw" if provider == "tavily_raw" else "complete") if table else "list_excerpt")
    assert len(first.metadata["project_links"]) <= 100
    assert all("outside.example" not in link["url"] for link in first.metadata["project_links"])
    expected = [] if provider == "tavily_raw" else [directory]
    if not table:
        expected.append("https://official.example/notices/one.shtml")
    assert calls == expected


@pytest.mark.asyncio
async def test_project_body_read_reserves_same_job_time_for_static_detail(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    pages, crawlers = [], []
    directory = "https://official.example/news/index.shtml"
    def forbidden_crawler(**kwargs):
        crawlers.append(True)
        raise AssertionError("详情补读不应启动浏览器")
    async def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"results": [
                {"url": "https://official.example/slow.shtml", "content": "无原文的搜索摘要。" * 50},
                {"url": directory, "raw_content": "* [智能巡视体系](./one.shtml) 云端智巡系统...\n* [新闻栏目](./index.shtml)"}]})
        pages.append(str(request.url))
        if request.url.path == "/slow.shtml":
            await asyncio.Event().wait()
        return httpx.Response(200, text="<article><h1>智巡项目</h1><p>" + "云端智能巡视系统的正式项目事实。" * 30 + "</p></article>")

    collector = SearchDiscoveryCollector()
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=forbidden_crawler))
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 0.5)
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond)))

    result = await asyncio.wait_for(collector.collect(TaskSpec(intent="官方项目", keywords=["智能巡检"],
        site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 6,
            "topics": ["智能巡检"]})), timeout=0.7)

    assert pages == ["https://official.example/slow.shtml", "https://official.example/news/one.shtml"]
    assert not crawlers and any(item.url.endswith("/one.shtml") and item.metadata["body_status"] == "complete" for item in result.items)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_project_unhealthy_searxng_skips_remaining_combinations_without_blocking_tavily(monkeypatch, error_type):
    from unittest.mock import AsyncMock
    hosts = []
    client_class = httpx.AsyncClient
    def respond(request):
        hosts.append(request.url.host)
        if request.url.host == "searxng.example":
            raise error_type("synthetic", request=request)
        return httpx.Response(200, json={"results": [{"url": "https://official.example/one.shtml", "raw_content": "原页正文"}]})
    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(module.settings, "searxng_base_url", "https://searxng.example")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-tavily")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    monkeypatch.setattr(module.settings, "search_tavily_first", False)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: client_class(transport=httpx.MockTransport(respond)))
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["组一", "组二"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 1}))
    assert hosts == ["searxng.example", "tavily.example"]
    assert result.success and result.items[0].metadata["body_status"] == "provider_raw"


@pytest.mark.asyncio
async def test_project_slow_original_page_cannot_starve_later_independent_pages(monkeypatch):
    import asyncio
    from src.collectors.base import CollectedItem
    calls, active, peak = [], [0], [0]
    collector = SearchDiscoveryCollector()

    async def fetch(url, deadline, **kwargs):
        calls.append((url, deadline))
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            if url.endswith("/slow.html"):
                await collector._within_budget(asyncio.Event().wait(), deadline)
            await asyncio.sleep(0)
            return httpx.Response(200, request=httpx.Request("GET", url),
                text="<article><h1>智能巡检项目</h1><p>" + "项目建设内容与正式原文。" * 50 + "</p></article>")
        finally:
            active[0] -= 1

    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    urls = ["https://official.example/" + name for name in ("slow.html", "two.html", "three.html")]
    failures = {}
    deadline = module.time.monotonic() + 0.2
    items = await collector._read_project_pages([CollectedItem(url=url, content="搜索线索") for url in urls],
        "official.example", deadline, failures)

    assert [url for url, _ in calls] == urls
    assert [item.metadata["body_status"] for item in items] == ["unknown", "complete", "complete"]
    assert failures == {urls[0]: "SOURCE_TIMEOUT"}
    assert all(value == deadline for _, value in calls)
    assert peak[0] == 2 and active[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "denied"])
async def test_project_concurrent_original_reads_propagate_stop_and_release_workers(monkeypatch, stop):
    import asyncio
    from src.account_execution import ExecutionDenied
    from src.collectors.base import CollectedItem
    collector = SearchDiscoveryCollector()
    started, active = asyncio.Event(), [0]

    async def fetch(url, deadline, **kwargs):
        active[0] += 1
        if active[0] == 2:
            started.set()
        try:
            if url.endswith("/stop") and stop == "denied":
                await started.wait()
                raise ExecutionDenied("合成授权停止")
            await asyncio.Event().wait()
        finally:
            active[0] -= 1

    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    task = asyncio.create_task(collector._read_project_pages(
        [CollectedItem(url="https://official.example/" + name) for name in ("stop", "waiting")],
        "official.example", module.time.monotonic() + 1, {}))
    try:
        await asyncio.wait_for(started.wait(), timeout=0.2)
        if stop == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if stop == "cancel" else ExecutionDenied):
            await task
        assert active[0] == 0
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_project_same_batch_reads_each_url_once_and_preserves_provider_raw(monkeypatch):
    import asyncio
    from src.collectors.base import CollectedItem
    collector, calls = SearchDiscoveryCollector(), []
    async def fetch(url, deadline, **kwargs):
        calls.append(url)
        await asyncio.sleep(0)
        raise httpx.ReadTimeout("合成未知", request=httpx.Request("GET", url))
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    raw_url, unknown_url = "https://official.example/raw", "https://official.example/unknown"
    items = await collector._read_project_pages([
        CollectedItem(url=unknown_url, content="线索1"), CollectedItem(url=unknown_url, content="线索2"),
        CollectedItem(url=raw_url, content="摘要"),
        CollectedItem(url=raw_url, content="原件完整正文", metadata={"body_status": "provider_raw"}),
    ], "official.example", module.time.monotonic() + 1, {})
    assert calls == [unknown_url]
    assert [item.metadata["body_status"] for item in items] == ["unknown", "unknown", "provider_raw", "provider_raw"]
    assert items[2].content == items[3].content == "原件完整正文"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anysearch", "tavily"])
async def test_project_excludes_database_and_recruitment_hits_before_original_read(monkeypatch, provider):
    from unittest.mock import AsyncMock
    pages = []
    hits = [
        ("https://official.example/dump.sql", "外网网站"),
        ("https://official.example/recruit.html", "招聘平台--单位详情"),
        ("https://official.example/project.html", "招聘平台建设采购项目"),
    ]
    client_class = httpx.AsyncClient

    def respond(request):
        if request.method == "POST":
            if provider == "tavily":
                return httpx.Response(200, json={"results": [{"url": url, "title": title, "content": "项目搜索摘要"} for url, title in hits]})
            text = "## Search Results\n" + "\n".join(
                f"### {index}. {title}\n- **URL**: {url}\n- 项目搜索摘要" for index, (url, title) in enumerate(hits, 1))
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": text}]}})
        pages.append(str(request.url))
        return httpx.Response(200, text="<article><h1>招聘平台建设采购项目</h1><p>" + "项目实施的正式公开依据。" * 50 + "</p></article>")

    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_use_anysearch", lambda: provider == "anysearch")
    monkeypatch.setattr(collector, "_use_tavily", lambda: provider == "tavily")
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "anysearch_api_key", "synthetic-key")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-key")
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://tavily.example")
    client = lambda *args, **kwargs: client_class(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module, "smart_client", client)
    monkeypatch.setattr(module._anysearch, "smart_client", client)

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["平台建设"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 4}))

    assert pages == [hits[-1][0]]
    assert [item.url for item in result.items] == [hits[-1][0]]
    statuses = result.coverage["discovery"]["attempts"][0]["source_pages"]
    assert [entry["status"] for entry in statuses] == ["excluded", "excluded", "complete"]
    assert [entry["error_code"] for entry in statuses[:2]] == ["SOURCE_NON_DOCUMENT", "SOURCE_RECRUITMENT"]


@pytest.mark.asyncio
async def test_project_html_discovery_does_not_launch_reader_for_database_dump(monkeypatch):
    import sys
    from types import SimpleNamespace
    def forbidden(**kwargs):
        raise AssertionError("数据库转储不能启动正文读取")
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=forbidden))
    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_fetch_inline_page", forbidden)
    items = await collector._fetch_all(["https://official.example/dump.sql"], strict=True,
        project_domain="official.example")
    assert len(items) == 1 and items[0].metadata == {
        "body_status": "excluded", "body_error_code": "SOURCE_NON_DOCUMENT"}


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://zhaopin.sgcc.com.cn/sgcchr/static/unitInfo.html?obj_id=10672000",
    "https://official.example/careers/positions",
])
async def test_project_bare_recruitment_url_is_excluded_before_any_reader(monkeypatch, url):
    import sys
    from types import SimpleNamespace
    from src.collectors.base import CollectedItem
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("招聘入口不能启动原件读取")
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=forbidden))
    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_fetch_inline_page", forbidden)
    inline = await collector._read_project_pages([CollectedItem(url=url)], "sgcc.com.cn" if "sgcc.com.cn" in url else "official.example", None, {})
    discovered = await collector._fetch_all([url], strict=True, project_domain="official.example")
    assert not calls
    assert [item.metadata["body_status"] for item in inline + discovered] == ["excluded", "excluded"]


@pytest.mark.asyncio
async def test_project_execution_denied_stops_peer_before_third_url_dispatch(monkeypatch):
    import asyncio
    from src.account_execution import ExecutionDenied
    from src.collectors.base import CollectedItem
    collector, calls, denied = SearchDiscoveryCollector(), [], [False]
    peer_started = asyncio.Event()
    async def fetch(url, deadline, **kwargs):
        calls.append((url, denied[0]))
        if url.endswith("/deny"):
            await peer_started.wait()
            denied[0] = True
            raise ExecutionDenied("合成授权拒绝")
        peer_started.set()
        await asyncio.sleep(0)
        return httpx.Response(200, request=httpx.Request("GET", url), text="<article>正式项目原文</article>")
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    with pytest.raises(ExecutionDenied):
        await collector._read_project_pages([
            CollectedItem(url="https://official.example/" + name) for name in ("deny", "peer", "third")
        ], "official.example", module.time.monotonic() + 1, {})
    assert [url.rsplit("/", 1)[-1] for url, _ in calls] == ["deny", "peer"]
    assert not any(was_denied for _, was_denied in calls)


@pytest.fixture
def project_budget_collector(monkeypatch):
    from unittest.mock import AsyncMock
    collector = SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "search_tavily_first", True)
    return collector


@pytest.mark.asyncio
async def test_project_shared_backend_budget_completes_four_slow_queries_in_two_lanes(monkeypatch, project_budget_collector):
    """84秒余量可容纳两路24秒请求，不能硬切成四个21秒而全部丢失。"""
    import asyncio
    from src.collectors.base import CollectedItem
    collector, active, peak, calls = project_budget_collector, [0], [0], []
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 1.05)
    async def search(keyword, domain, **kwargs):
        calls.append((keyword, kwargs["deadline"]))
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            await collector._within_budget(asyncio.sleep(0.24), kwargs["deadline"])
            return [CollectedItem(url=f"https://{domain}/{keyword}", content="项目的完整原件。",
                metadata={"body_status": "provider_raw"})]
        finally:
            active[0] -= 1
    monkeypatch.setattr(collector, "_search_tavily", search)
    started = module.time.monotonic()
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=[f"主题{i}" for i in range(4)],
        site_domains=["official.example"], project_collection={"target_count": 4, "candidate_limit": 8}))
    assert len(result.items) == 4 and peak[0] == 2 and active[0] == 0
    assert len(calls) == 4 and len({keyword for keyword, _ in calls}) == 4
    assert len({deadline for _, deadline in calls}) == 1
    assert module.time.monotonic() - started < 0.84


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["deadline_reached", "httpx", "unknown"])
async def test_project_timeout_observation_is_safe_and_distinguishes_deadline(monkeypatch, project_budget_collector, origin):
    import asyncio
    collector = project_budget_collector
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 0.1)
    async def search(keyword, domain, **kwargs):
        if origin == "httpx":
            raise httpx.ReadTimeout("private-secret-do-not-record", request=httpx.Request("GET", "https://synthetic.example"))
        if origin == "unknown":
            raise TimeoutError("private-secret-do-not-record")
        await collector._within_budget(asyncio.Event().wait(), kwargs["deadline"])
    monkeypatch.setattr(collector, "_search_tavily", search)
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["主题"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 1}))
    attempt = result.coverage["discovery"]["attempts"][0]
    assert attempt["error_code"] == "SOURCE_TIMEOUT" and attempt["timeout_origin"] == origin
    assert attempt["phase"] == "search_api"
    assert attempt["allocated_seconds"] > 0 and attempt["elapsed_seconds"] >= 0
    assert "private-secret" not in json.dumps(result.coverage)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "denied"])
async def test_project_parallel_queries_stop_before_next_batch_and_release_requests(monkeypatch, project_budget_collector, stop):
    import asyncio
    from src.account_execution import ExecutionDenied
    from src.collectors.base import CollectedItem
    collector, calls, active = project_budget_collector, [], [0]
    peer_started = asyncio.Event()
    async def search(keyword, domain, **kwargs):
        calls.append(keyword)
        active[0] += 1
        try:
            if keyword == "拒绝":
                await peer_started.wait()
                if stop == "denied":
                    raise ExecutionDenied("合成拒绝")
                await asyncio.Event().wait()
            peer_started.set()
            if stop == "cancel":
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            return [CollectedItem(url="https://official.example/peer", content="公开原件", metadata={"body_status": "provider_raw"})]
        finally:
            active[0] -= 1
    monkeypatch.setattr(collector, "_search_tavily", search)
    task = asyncio.create_task(collector.collect(TaskSpec(intent="官方项目", keywords=["拒绝", "同伴", "第三组"],
        site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 8})))
    try:
        await asyncio.wait_for(peer_started.wait(), timeout=0.2)
        if stop == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if stop == "cancel" else ExecutionDenied):
            await task
        assert calls == ["拒绝", "同伴"] and active[0] == 0
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_project_parallel_batch_reserves_last_candidate_slot(monkeypatch, project_budget_collector):
    from src.collectors.base import CollectedItem
    collector, calls = project_budget_collector, []
    async def search(keyword, domain, **kwargs):
        calls.append(keyword)
        return [CollectedItem(url="https://official.example/only", content="正式原件", metadata={"body_status": "provider_raw"})]
    monkeypatch.setattr(collector, "_search_tavily", search)
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["第一组", "第二组"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 1}))
    assert calls == ["第一组"] and len(result.items) == 1


@pytest.mark.asyncio
async def test_project_parallel_query_results_reuse_same_original_without_second_read(monkeypatch, project_budget_collector):
    from src.collectors.base import CollectedItem
    collector, reads = project_budget_collector, []
    async def search(keyword, domain, **kwargs):
        return [CollectedItem(url="https://official.example/same", title="项目", content="搜索摘要")]
    async def fetch(url, deadline, **kwargs):
        reads.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url),
            text="<article><h1>正式项目</h1><p>" + "项目原文与逐字依据。" * 60 + "</p></article>")
    monkeypatch.setattr(collector, "_search_tavily", search)
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["第一组", "第二组"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 4}))
    assert reads == ["https://official.example/same"] and len(result.items) == 1
    assert result.items[0].metadata["body_status"] == "complete"


@pytest.mark.asyncio
async def test_project_directory_does_not_consume_inflight_peer_candidate_reservation(monkeypatch, project_budget_collector):
    from src.collectors.base import CollectedItem
    collector, reads = project_budget_collector, []
    async def search(keyword, domain, **kwargs):
        if keyword == "目录":
            return [CollectedItem(url="https://official.example/news/", content="[智能巡检项目](./detail.html)\n[其它新闻](./other.html)",
                metadata={"body_status": "provider_raw"})]
        return [CollectedItem(url="https://official.example/project", content="第二组独立项目原件", metadata={"body_status": "provider_raw"})]
    async def forbidden(*args, **kwargs):
        reads.append(True)
        raise AssertionError("同伴额度不能被目录额外读取占用")
    monkeypatch.setattr(collector, "_search_tavily", search)
    monkeypatch.setattr(collector, "_fetch_inline_page", forbidden)
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["目录", "原件"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 2, "topics": ["智能巡检"]}))
    assert not reads and [item.url for item in result.items] == ["https://official.example/news/", "https://official.example/project"]
    assert [attempt["count"] for attempt in result.coverage["discovery"]["attempts"][:2]] == [1, 1]


@pytest.mark.asyncio
async def test_project_fast_summary_is_read_before_slow_peer_reaches_shared_deadline(monkeypatch, project_budget_collector):
    import asyncio
    from src.collectors.base import CollectedItem
    collector, reads = project_budget_collector, []
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 0.30)
    async def search(keyword, domain, *, deadline, read_originals=True, body_failures=None, **kwargs):
        if keyword == "slow":
            await collector._within_budget(asyncio.Event().wait(), deadline)
        await asyncio.sleep(0.002)
        found = [CollectedItem(url="https://official.example/fast", content="真实搜索线索，需要原页核验")]
        return await collector._read_project_pages(found, domain, module._project_body_deadline(deadline),
            body_failures or {}) if read_originals else found
    async def fetch(url, deadline, **kwargs):
        reads.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url),
            text="<article><h1>项目正文</h1><p>" + "具名建设项目的完整原始依据。" * 60 + "</p></article>")
    monkeypatch.setattr(collector, "_search_tavily", search)
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["slow", "fast"], site_domains=["official.example"],
        project_collection={"target_count": 2, "candidate_limit": 4}))
    assert reads == ["https://official.example/fast"] and len(result.items) == 1
    assert result.items[0].metadata["body_status"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["denied", "cancelled"])
async def test_project_api_stop_cancels_active_original_reader_and_does_not_start_next_batch(monkeypatch, project_budget_collector, stop):
    import asyncio
    from src.account_execution import ExecutionDenied
    from src.collectors.base import CollectedItem
    collector, searches, reads, active, stopped = project_budget_collector, [], [], [0], [False]
    body_started = asyncio.Event()
    async def search(keyword, domain, **kwargs):
        searches.append(keyword)
        if keyword == "slow":
            await body_started.wait()
            stopped[0] = True
            raise ExecutionDenied("合成权限拒绝") if stop == "denied" else asyncio.CancelledError()
        return [CollectedItem(url=f"https://official.example/page{index}", content="待取得原页的搜索摘要") for index in range(3)]
    async def fetch(url, deadline, **kwargs):
        reads.append((url, stopped[0]))
        active[0] += 1
        body_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active[0] -= 1
    monkeypatch.setattr(collector, "_search_tavily", search)
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    with pytest.raises(ExecutionDenied if stop == "denied" else asyncio.CancelledError):
        await asyncio.wait_for(collector.collect(TaskSpec(intent="官方项目", keywords=["slow", "fast", "third"],
            site_domains=["official.example"], project_collection={"target_count": 1, "candidate_limit": 9})), timeout=0.5)
    assert searches == ["slow", "fast"] and 1 <= len(reads) <= 2
    assert not any(after_stop for _, after_stop in reads) and active[0] == 0


@pytest.fixture
def project_searx_collector(monkeypatch):
    collector = SearchDiscoveryCollector()
    discover = collector._discover_one
    async def only_searx(backend, *args, **kwargs):
        return await discover(backend, *args, **kwargs) if backend == "searxng" else []
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: False)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_discover_one", only_searx)
    monkeypatch.setattr(module.settings, "searxng_base_url", "https://searxng.example")
    return collector


@pytest.mark.asyncio
async def test_project_searxng_aggregation_leaves_time_for_http_response(monkeypatch, project_searx_collector):
    """聚合与HTTP同时20秒会丢已有结果，聚合必须先结束并留下返回余量。"""
    from unittest.mock import AsyncMock
    from src.collectors.base import CollectedItem
    collector, calls = project_searx_collector, []
    client_class = httpx.AsyncClient
    def respond(request):
        calls.append(request)
        if float(request.url.params.get("timeout_limit", "20")) >= request.extensions["timeout"]["read"]:
            raise httpx.ReadTimeout("合成20秒边界", request=request)
        return httpx.Response(200, json={"results": [{"url": "https://official.example/project"}]})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(collector, "_fetch_all", AsyncMock(return_value=[CollectedItem(
        url="https://official.example/project", content="完整项目正文", metadata={"body_status": "complete"})]))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 1}))

    assert result.success and [item.url for item in result.items] == ["https://official.example/project"]
    assert len(calls) == 1 and float(calls[0].url.params["timeout_limit"]) == 15.0
    assert calls[0].extensions["timeout"]["read"] == 20.0


@pytest.mark.asyncio
@pytest.mark.parametrize("has_link,failed_engines,status", [(True, 1, "completed"), (False, 1, "unknown"), (False, 0, "completed")])
async def test_project_searxng_keeps_partial_links_and_distinguishes_unknown_empty(
        monkeypatch, project_searx_collector, has_link, failed_engines, status):
    from unittest.mock import AsyncMock
    from src.collectors.base import CollectedItem
    collector, calls = project_searx_collector, []
    client_class = httpx.AsyncClient
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "results": [{"url": "https://official.example/project"}] if has_link else [],
            "unresponsive_engines": [["private-engine", "private-secret-error"]] if failed_engines else []})
    fetch = AsyncMock(return_value=[CollectedItem(url="https://official.example/project", content="完整项目正文",
        metadata={"body_status": "complete"})])
    monkeypatch.setattr(collector, "_fetch_all", fetch)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 1}))

    attempt = result.coverage["discovery"]["attempts"][0]
    assert attempt["status"] == status and attempt["upstream_unresponsive_count"] == failed_engines
    assert attempt["partial"] is bool(has_link and failed_engines)
    assert len(calls) == 1 and result.success is has_link
    assert fetch.await_count == int(has_link) and "private-" not in json.dumps(result.coverage)
    if failed_engines and not has_link:
        assert attempt["error_code"] == "SOURCE_UPSTREAM_INCOMPLETE"
        assert result.coverage["discovery"]["stop_reason"] == "source_unknown"


@pytest.mark.asyncio
async def test_ordinary_searxng_keeps_existing_timeout_and_response_contract(monkeypatch, project_searx_collector):
    from unittest.mock import AsyncMock
    from src.collectors.base import CollectedItem
    collector, calls = project_searx_collector, []
    client_class = httpx.AsyncClient
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [{"url": "https://official.example/page"}],
            "unresponsive_engines": [["synthetic-engine", "timeout"]]})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(collector, "_fetch_all", AsyncMock(return_value=[CollectedItem(
        url="https://official.example/page", content="普通搜索正文")]))

    result = await collector.collect(TaskSpec(intent="普通资料搜索", keywords=["主题"],
        site_domains=["official.example"], max_items=1))

    assert result.success and len(calls) == 1
    assert "timeout_limit" not in calls[0].url.params and calls[0].extensions["timeout"]["read"] == 20.0


@pytest.mark.asyncio
@pytest.mark.parametrize("unresponsive", [None, "private-secret", {"private-engine": "private-secret"}])
async def test_project_searxng_rejects_malformed_failure_metadata(monkeypatch, project_searx_collector, unresponsive):
    collector = project_searx_collector
    client_class = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"results": [], "unresponsive_engines": unresponsive})), **kwargs))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 1}))

    attempt = result.coverage["discovery"]["attempts"][0]
    assert attempt["status"] == "unknown" and attempt["error_code"] == "SOURCE_FORMAT_ERROR"
    assert not result.success and "private-" not in json.dumps(result.coverage)


@pytest.mark.asyncio
async def test_project_browser_exit_timeout_preserves_known_page_and_unfinished_states(monkeypatch):
    import sys
    from types import SimpleNamespace
    clock, calls, closed, failures = [0.0], [], [], {}
    links = ["https://official.example/" + name for name in ("fast", "slow", "unread")]
    class FakeCrawler:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
            raise TimeoutError("private-browser-exit-error")
        async def arun(self, *, url):
            calls.append(url)
            if url.endswith("/slow"):
                clock[0] = 61.0
                raise httpx.ReadTimeout("private-page-timeout", request=httpx.Request("GET", url))
            return SimpleNamespace(success=True, url=url, markdown="动态页面的完整项目正文。" * 30, metadata={}, html="")
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=FakeCrawler))
    monkeypatch.setattr(module, "crawler_browser_config", lambda: None)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    items = await SearchDiscoveryCollector()._fetch_all(links, 60.0, strict=True,
        project_domain="official.example", body_failures=failures)

    assert calls == links[:2] and closed == [True]
    assert [(item.url, item.metadata["body_status"]) for item in items] == list(zip(links, ("complete", "unknown", "unread")))
    assert failures == {links[1]: "SOURCE_TIMEOUT"}
    assert items[2].metadata["body_error_code"] == "SOURCE_BUDGET_EXHAUSTED"
    assert "private-" not in json.dumps([item.metadata for item in items])


@pytest.mark.asyncio
async def test_project_browser_partial_batch_preserves_empty_pending_source_identity(monkeypatch, project_searx_collector):
    from unittest.mock import AsyncMock
    from src.collectors.base import CollectedItem
    collector, calls = project_searx_collector, []
    urls = ["https://official.example/" + name for name in ("complete", "unknown", "unread")]
    client_class = httpx.AsyncClient
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [{"url": url} for url in urls]})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *args, **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(collector, "_fetch_all", AsyncMock(return_value=[
        CollectedItem(url=urls[0], content="已取得的原始项目正文", metadata={"body_status": "complete"}),
        CollectedItem(url=urls[1], metadata={"body_status": "unknown", "body_error_code": "SOURCE_TIMEOUT"}),
        CollectedItem(url=urls[2], metadata={"body_status": "unread", "body_error_code": "SOURCE_BUDGET_EXHAUSTED"})]))

    result = await collector.collect(TaskSpec(intent="官方项目", keywords=["项目"], site_domains=["official.example"],
        project_collection={"target_count": 1, "candidate_limit": 4}))

    assert len(calls) == 1 and [item.url for item in result.items] == urls
    assert [item.metadata["body_status"] for item in result.items] == ["complete", "unknown", "unread"]
    assert not result.items[1].content and not result.items[2].content
    attempt = result.coverage["discovery"]["attempts"][0]
    assert attempt["status"] == "completed" and len(attempt["source_pages"]) == 3
    assert result.coverage["discovery"]["stop_reason"] == "source_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["project", "ordinary", "strict_nonproject"])
async def test_browser_twenty_second_batch_cap_only_applies_to_project_reads(monkeypatch, mode):
    import sys
    from types import SimpleNamespace
    collector, budgets, closed = SearchDiscoveryCollector(), [], []
    within = collector._within_budget
    async def observe(operation, deadline):
        budgets.append(deadline)
        return await within(operation, deadline)
    class FakeCrawler:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        async def arun(self, *, url):
            return SimpleNamespace(success=True, url=url, markdown="只有JS渲染后才有的完整正文", metadata={}, html="")
    def no_static_request(*args, **kwargs):
        raise AssertionError("本轮必须保留原有JS渲染路径")
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=FakeCrawler))
    monkeypatch.setattr(module, "crawler_browser_config", lambda: None)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(module, "smart_client", no_static_request)
    monkeypatch.setattr(collector, "_within_budget", observe)

    items = await collector._fetch_all(["https://official.example/js"], 160.0,
        strict=mode != "ordinary", project_domain="official.example" if mode == "project" else "")

    assert items[0].content == "只有JS渲染后才有的完整正文" and items[0].metadata["via"] == "crawl4ai"
    assert closed == [True] and budgets and set(budgets) == ({120.0} if mode == "project" else {160.0})


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["denied", "cancelled"])
async def test_project_browser_exit_error_cannot_hide_execution_stop(monkeypatch, stop):
    import asyncio
    import sys
    from types import SimpleNamespace
    from src.account_execution import ExecutionDenied
    started, closed, calls = asyncio.Event(), [], []
    class FakeCrawler:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
            raise TimeoutError("private-browser-exit")
        async def arun(self, *, url):
            calls.append(url)
            started.set()
            if stop == "denied":
                raise ExecutionDenied("合成拒绝")
            await asyncio.Event().wait()
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=FakeCrawler))
    monkeypatch.setattr(module, "crawler_browser_config", lambda: None)
    collector = SearchDiscoveryCollector()
    task = asyncio.create_task(collector._fetch_all(["https://official.example/first", "https://official.example/second"],
        module.time.monotonic() + 60, strict=True, project_domain="official.example", body_failures={}))
    try:
        await asyncio.wait_for(started.wait(), timeout=0.2)
        if stop == "cancelled":
            task.cancel()
        with pytest.raises(ExecutionDenied if stop == "denied" else asyncio.CancelledError):
            await task
        assert closed == [True] and calls == ["https://official.example/first"]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
