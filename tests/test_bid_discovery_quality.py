"""用第三轮实采暴露的查询分摊与目录形态验证公告发现质量。"""
import json
import socket
import sys
from collections import Counter
from unittest.mock import AsyncMock

import httpx
import pytest

from src.collectors import search_collector as module
from src.collectors.base import CollectedItem
from src.conductor.task_spec import TaskSpec


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    violations = []
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def connect(sock, address):
        if address[0] not in {"127.0.0.1", "::1", "localhost"}:
            violations.append(address)
            raise AssertionError("公告策略回归禁止外部请求")
        return original_connect(sock, address)

    def resolve(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            violations.append(host)
            raise AssertionError("公告策略回归禁止外部请求")
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    yield
    assert not violations


@pytest.fixture
def searches(monkeypatch):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        rows = [{"url": f"https://hospital.example/{len(requests)}/{kind}", "title": title,
                 "raw_content": title + "\n" + "合成资料完整原文。" * 30}
                for kind, title in (("directory", "医疗设备招标采购目录"), ("news", "医疗设备行业动态"),
                                    ("notice", "医疗设备公开招标公告"))]
        return httpx.Response(200, json={"results": rows[:payload["max_results"]]})

    collector = module.SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://search.example")
    monkeypatch.setattr(module.settings, "tavily_api_key", "synthetic-only")
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    return collector, requests


def actual_query_shape():
    return TaskSpec(intent="采集最新发布的3条医疗设备招标公告", data_type="bid", max_items=15,
        time_range="最新", keywords=["医疗设备 招标公告", "医疗设备 采购公告", "医疗器械 招标公告", "医疗设备 公开招标"])


@pytest.mark.asyncio
async def test_actual_eight_jobs_do_not_reduce_each_query_to_first_directory(searches):
    collector, requests = searches
    spec = actual_query_shape()
    before = spec.model_dump(mode="json")
    result = await collector.collect_bid_candidates(spec)
    assert requests[0]["max_results"] >= 3, "15候选分给8组后，不能再减半成只取首个目录"
    assert any(item.url.endswith("/notice") for item in result.items)
    assert len(result.items) <= 15 and spec.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_bid_query_does_not_include_model_time_range_explanation(searches):
    collector, requests = searches
    explanation = "最新发布（不设近7天或近30天硬性时间窗，按本次检索候选中核实发布日期倒序取前3条）"
    spec = actual_query_shape().model_copy(update={"time_range": explanation})
    before = spec.model_dump(mode="json")
    await collector.collect_bid_candidates(spec)
    queries = [request["query"] for request in requests]
    assert all(not any(text in query for text in ("7天", "30天", "最新发布", "硬性时间窗", "本次检索")) for query in queries)
    assert queries[0] == f"提交投标文件截止时间 医疗设备 招标公告 {module.beijing_now().year}"
    assert "医疗设备 招标公告" in queries and spec.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_preferred_query_locates_formal_body_and_keeps_original_fallback(monkeypatch):
    collector = module.SearchDiscoveryCollector()
    candidates = AsyncMock()
    monkeypatch.setattr(collector, "_collect_candidates", candidates)
    spec = TaskSpec(intent="最新招标公告", data_type="bid", keywords=["配电设备 招标公告"], max_items=15)
    await collector.collect_bid_candidates(spec)
    options = candidates.call_args.kwargs
    year = str(module.beijing_now().year)
    assert options["keywords"] == [f"提交投标文件截止时间 配电设备 招标公告 {year}", "配电设备 招标公告"]
    assert options["topics"] == ["配电设备"] and options["limit"] == 15


@pytest.mark.asyncio
async def test_public_tender_format_is_not_business_topic(monkeypatch):
    collector = module.SearchDiscoveryCollector()
    candidates = AsyncMock()
    monkeypatch.setattr(collector, "_collect_candidates", candidates)
    await collector.collect_bid_candidates(actual_query_shape())
    assert set(candidates.call_args.kwargs["topics"]) == {"医疗设备", "医疗器械"}


@pytest.mark.asyncio
async def test_directory_details_need_subject_and_notice_in_link_title(monkeypatch):
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, text="<article><h1>医疗设备正式公告</h1><p>" + "合成公告完整原文。" * 40 + "</p></article>")

    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    directory = CollectedItem(url="https://hospital.example/index.html", content="医疗设备采购目录", metadata={
        "body_status": "complete", "project_links": [
            {"text": "上一篇：最新53个医疗器械准产批件一览表", "url": "https://hospital.example/news", "context": "医疗器械"},
            {"text": "医药、医疗器械", "url": "https://hospital.example/industry", "context": "医疗设备 招标公告 公开"},
            {"text": "招标采购", "url": "https://hospital.example/category", "context": "医疗设备 招标公告 公开"},
            {"text": "医疗设备招标公告", "url": "https://hospital.example/notice", "context": "2026-10-17 医疗设备招标公告"}]})
    result = await module.SearchDiscoveryCollector()._expand_project_directories([directory], ["医疗设备", "医疗器械"],
        ["hospital.example"], 6, set(), module.time.monotonic() + 30, {}, allow_page_links=True)
    assert requests == ["https://hospital.example/notice"], "不能借整行主题把目录导航和上下篇新闻当公告详情"
    assert result[0] is directory and result[1].metadata["body_status"] == "complete"


def abundant_main_backends(monkeypatch, *, directory_details=False):
    collector = module.SearchDiscoveryCollector()
    calls, pages = [], []

    def items(backend, limit):
        calls.append((backend, limit))
        rows = []
        for index in range(limit):
            prefix = f"https://{backend}.example/{len(calls)}/{index}"
            metadata = {"body_status": "provider_raw"}
            if directory_details and backend == "anysearch":
                metadata["project_links"] = [{"text": "医疗设备招标公告", "url": prefix + f"/notice-{number}",
                    "context": "2026-10-18 医疗设备招标公告"} for number in range(8)]
            rows.append(CollectedItem(url=prefix, title="医疗设备采购目录", content="合成完整候选原文。" * 30, metadata=metadata))
        return rows

    async def anysearch(*args, limit, **kwargs):
        return items("anysearch", limit)

    async def tavily(*args, limit, **kwargs):
        return items("tavily", limit)

    async def discover(backend, *args, limit, **kwargs):
        return [row.url for row in items(backend, limit)] if backend == "searxng" else []

    def respond(request):
        pages.append(str(request.url))
        return httpx.Response(200, text="<article><h1>医疗设备招标公告</h1><p>" + "合成完整公告。" * 40 + "</p></article>")

    monkeypatch.setattr(collector, "_use_anysearch", lambda: True)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: ["searxng"])
    monkeypatch.setattr(collector, "_search_anysearch", anysearch)
    monkeypatch.setattr(collector, "_search_tavily", tavily)
    monkeypatch.setattr(collector, "_discover_one", discover)
    monkeypatch.setattr(module.settings, "search_tavily_first", False)
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    return collector, calls, pages


@pytest.mark.asyncio
async def test_bid_reserves_candidate_share_for_all_three_main_backends(monkeypatch):
    collector, calls, pages = abundant_main_backends(monkeypatch, directory_details=True)
    result = await collector.collect_bid_candidates(actual_query_shape())
    counts = Counter(item.metadata["discovery_backend"] for item in result.items)
    assert counts == {"anysearch": 5, "searxng": 5, "tavily": 5}, "首后端的目录和详情不能耗尽其他主后端份额"
    assert {backend for backend, _ in calls} == {"anysearch", "searxng", "tavily"}
    assert sum("anysearch.example" in url for url in pages) == 2, "详情读取也须受该后端剩余份额约束"
    assert len(result.items) == 15 and result.coverage["discovery"]["stop_reason"] == "candidate_limit"


@pytest.mark.asyncio
async def test_single_main_backend_keeps_full_fifteen_candidates(searches):
    collector, requests = searches
    result = await collector.collect_bid_candidates(actual_query_shape())
    assert len(result.items) == 15
    assert {item.metadata["discovery_backend"] for item in result.items} == {"tavily"}
    assert sum(request["max_results"] for request in requests) == 15


@pytest.mark.asyncio
async def test_project_keeps_existing_first_backend_candidate_budget(monkeypatch):
    collector, calls, _ = abundant_main_backends(monkeypatch)
    result = await collector.collect(TaskSpec(intent="医疗设备项目采集", keywords=["医疗设备"],
        project_collection={"target_count": 3, "candidate_limit": 15}))
    assert len(result.items) == 15 and calls == [("anysearch", 15)]
    assert {item.metadata["discovery_backend"] for item in result.items} == {"anysearch"}
