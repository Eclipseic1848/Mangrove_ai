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
