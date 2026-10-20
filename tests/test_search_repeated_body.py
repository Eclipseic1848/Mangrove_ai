"""搜索商重复膨胀的原文须回原页核对，不能去重后冒充完整正文。"""
import asyncio
import copy
import hashlib
import socket
from unittest.mock import AsyncMock

import httpx
import pytest

from src.account_execution import ExecutionDenied
from src.collectors import search_collector as module
from src.collectors.base import CollectedItem


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    violations = []
    connect, resolve = socket.socket.connect, socket.getaddrinfo

    def check(host):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            violations.append(host)
            raise AssertionError("重复正文回归禁止真实网络请求")

    def connect_local(sock, address):
        check(address[0])
        return connect(sock, address)

    def resolve_local(host, *args, **kwargs):
        check(host)
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect_local)
    monkeypatch.setattr(socket, "getaddrinfo", resolve_local)
    yield
    assert not violations, "重复正文回归曾尝试外部请求，已拦截"


def repeated_body():
    return "\n\n".join(f"  法规段落{number % 233}：" + "原有法规文字。" * 7 + "  " for number in range(18576))


def provider_item(content):
    return CollectedItem(url="https://source.example/regulation", title="合成来源",
                         content=content, metadata={"via": "tavily", "body_status": "provider_raw"})


async def read(collector, items, failures=None):
    return await collector._read_project_pages(items, "", module.time.monotonic() + 30,
                                                {} if failures is None else failures)


@pytest.mark.asyncio
async def test_inflated_provider_body_reads_original_without_trimming(monkeypatch):
    raw = repeated_body()
    item = provider_item(raw)
    html = "<article><h1>原页完整法规</h1><p>" + "正常原页条文。" * 100 + "原件末端。</p></article>"
    fetch = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", item.url), text=html))
    collector = module.SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    result = await read(collector, [item])
    fetch.assert_awaited_once()
    assert result[0].metadata["body_status"] == "complete"
    assert "原件末端" in result[0].content and raw not in result[0].content
    assert result[0].metadata["provider_body_anomaly"] == {
        "code": "SOURCE_PROVIDER_REPEATED_BODY", "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "raw_chars": len(raw), "nonempty_lines": 18576, "unique_nonempty_lines": 233}
    assert raw not in str(result[0].metadata), "诊断不能把膨胀正文再次塞进后续模型上下文"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "\n".join(f"|项目{number}|设备{number}|100万元|" for number in range(200)),
    "\n".join("重复但短的业务条目" for _ in range(99)),
    "\n".join(f"第{number % 20}项业务" for number in range(100)),
], ids=["distinct_table", "small", "threshold"])
async def test_normal_provider_tables_and_small_bodies_remain_unchanged(monkeypatch, content):
    item = provider_item(content)
    before = copy.deepcopy(item.to_dict())
    fetch = AsyncMock(side_effect=AssertionError("正常provider原文不能新增原页请求"))
    collector = module.SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    assert (await read(collector, [item]))[0].to_dict() == before
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_original_cannot_fall_back_to_bad_raw_or_repeat(monkeypatch):
    collector = module.SearchDiscoveryCollector()
    fetch = AsyncMock(side_effect=httpx.ReadTimeout("合成未知结果"))
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    failures = {}
    first = (await read(collector, [provider_item(repeated_body())], failures))[0]
    assert first.content == "" and first.metadata["body_status"] == "unknown"
    assert first.metadata["body_error_code"] == failures[first.url] == "SOURCE_TIMEOUT"
    second = (await read(collector, [provider_item(repeated_body())], failures))[0]
    assert second.content == "" and second.metadata["body_status"] == "unknown"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_original_page_stays_unknown_without_second_get(monkeypatch):
    collector = module.SearchDiscoveryCollector()
    item = provider_item(repeated_body())
    fetch = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", item.url), text="<article>原页</article>"))
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    monkeypatch.setattr(module, "extract_content", lambda *args: ("合成法规", repeated_body(), {}))
    failures = {}
    result = (await read(collector, [item], failures))[0]
    assert result.content == "" and result.metadata["body_status"] == "unknown"
    assert result.metadata["body_error_code"] == failures[item.url] == "SOURCE_REPEATED_BODY"
    await read(collector, [provider_item(repeated_body())], failures)
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_known_page_cannot_refill_anomalous_provider_body(monkeypatch):
    collector = module.SearchDiscoveryCollector()
    item = provider_item(repeated_body())
    cached = CollectedItem(url=item.url, content=repeated_body(), metadata={"body_status": "complete"})
    fetch = AsyncMock(side_effect=AssertionError("已知缓存分支不应发起新请求"))
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    failures = {}
    result = await collector._read_project_pages([item], "", module.time.monotonic() + 30, failures,
                                                known_pages={item.url: cached})
    assert result[0].content == "" and result[0].metadata["body_status"] == "unknown"
    assert failures[item.url] == result[0].metadata["body_error_code"] == "SOURCE_REPEATED_BODY"
    assert cached.content == "" and cached.metadata["body_status"] == "unknown"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_original_read_closes_without_bad_raw_fallback(monkeypatch):
    entered, closed = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    collector = module.SearchDiscoveryCollector()
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    item = provider_item(repeated_body())
    task = asyncio.create_task(read(collector, [item]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set() and item.content == "" and item.metadata["body_status"] != "provider_raw"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_execution_denial_remains_denial_after_anomaly(monkeypatch):
    collector = module.SearchDiscoveryCollector()
    fetch = AsyncMock(side_effect=ExecutionDenied("合成撤权"))
    monkeypatch.setattr(collector, "_fetch_inline_page", fetch)
    item = provider_item(repeated_body())
    with pytest.raises(ExecutionDenied):
        await read(collector, [item])
    assert item.content == "" and item.metadata["body_status"] != "provider_raw"
