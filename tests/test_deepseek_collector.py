"""独立搜索渠道沿用原有范围与来源核验，不改变默认渠道。"""
from unittest.mock import AsyncMock

import pytest

from src.collectors import search_collector as module
from src.collectors.base import CollectedItem
from src.conductor.task_spec import TaskSpec


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(module.settings, "search_provider", "deepseek")
    monkeypatch.setattr(module.settings, "deepseek_api_key", "platform-synthetic")
    monkeypatch.setattr(module.settings, "deepseek_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(module.settings, "anysearch_api_key", "other-synthetic")
    monkeypatch.setattr(module.settings, "tavily_api_key", "other-synthetic")
    return module.SearchDiscoveryCollector()


@pytest.mark.asyncio
async def test_deepseek_search_uses_platform_key_and_filters_scope(configured, monkeypatch):
    from src.config.user_ctx import user_overrides_context
    calls = []
    class Client:
        def __init__(self, api_key):
            assert api_key == "platform-synthetic"
        async def search(self, query, **kwargs):
            calls.append((query, kwargs))
            return {"results": [
                {"url": "https://www.sgcc.com.cn/detail", "title": "项目", "snippet": "线索", "content": "伪正文"},
                {"url": "https://evilsgcc.com.cn/detail", "title": "越界"}]}
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    with user_overrides_context({"deepseek_api_key": "personal-must-not-be-used"}):
        items = await configured._search_deepseek("配电网 项目", "sgcc.com.cn", limit=80)
    assert calls == [("site:sgcc.com.cn 配电网 项目", {"limit": 50, "need_content": False})]
    assert len(items) == 1 and items[0].content == ""
    assert items[0].metadata["body_status"] == "unread"


@pytest.mark.asyncio
async def test_deepseek_fetch_preserves_supplier_provenance(configured, monkeypatch):
    text = "# 正式公告\n发布日期：2026-10-18\n" + "项目实施内容与原文依据。" * 30
    class Client:
        def __init__(self, api_key):
            assert api_key == "platform-synthetic"
        async def fetch(self, url):
            return {"title": "正式公告", "content": text}
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    monkeypatch.setattr(configured, "_fetch_inline_page", AsyncMock(side_effect=AssertionError("不可混入旧读取")))
    result = await configured._read_project_pages([CollectedItem(url="https://sgcc.com.cn/detail")], "sgcc.com.cn", None, {})
    assert result[0].content == text
    assert result[0].metadata["source_representation"] == "provider_markdown"
    assert result[0].metadata["body_status"] == "provider_raw"


@pytest.mark.asyncio
async def test_explicit_deepseek_does_not_mix_old_backends(configured, monkeypatch):
    monkeypatch.setattr(configured, "_search_deepseek", AsyncMock(return_value=[]))
    monkeypatch.setattr(configured, "_discover_one", AsyncMock(side_effect=AssertionError("不可混入旧搜索")))
    monkeypatch.setattr(configured, "_search_tavily", AsyncMock(side_effect=AssertionError("不可混入旧搜索")))
    result = await configured.collect(TaskSpec(intent="查找项目", keywords=["配电网"],
        site_domains=["sgcc.com.cn"], project_collection={"target_count": 1, "candidate_limit": 5}))
    assert not result.success
    assert {attempt["backend"] for attempt in result.coverage["discovery"]["attempts"]} == {"deepseek"}


def test_default_backend_order_unchanged(configured, monkeypatch):
    monkeypatch.setattr(module.settings, "search_provider", "auto")
    assert configured._use_deepseek() is False
    assert "deepseek" not in configured._discovery_backends()


def test_search_channel_is_admin_configuration():
    from src.config.runtime_config import USER_KEYS, cast_value
    assert "search_provider" not in USER_KEYS
    assert cast_value("search_provider", "deepseek") == "deepseek"
    with pytest.raises(ValueError):
        cast_value("search_provider", "unsupported")


@pytest.mark.parametrize("address", ["https://gateway.example/v1", "http://api.deepseek.com/v1",
    "https://api.deepseek.com.evil.example/v1", "https://api.deepseek.com/v1?token=private"])
@pytest.mark.asyncio
async def test_gateway_key_is_never_sent_to_official_endpoint(configured, monkeypatch, address):
    monkeypatch.setattr(module.settings, "deepseek_base_url", address)
    client = AsyncMock(side_effect=AssertionError("网关凭据不可发往另一个源"))
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", client)
    with pytest.raises(ValueError, match="官方地址"):
        await configured._search_deepseek("项目", "sgcc.com.cn", limit=1)
    client.assert_not_called()


@pytest.mark.asyncio
async def test_search_canonicalizes_fragments_before_dedup_and_limit(configured, monkeypatch):
    class Client:
        def __init__(self, api_key):
            pass
        async def search(self, query, **kwargs):
            return {"results": [{"url": url, "title": "公告"} for url in [
                "https://outside.example/a", "https://bidding.csg.cn/zbgg/1.jhtml#1#1",
                "https://bidding.csg.cn/zbgg/1.jhtml#2", "https://bidding.csg.cn/zbgg/2.jhtml",
                "https://bidding.csg.cn/zbgg/3.jhtml"]]}
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    items = await configured._search_deepseek("智能巡检 项目", "csg.cn", limit=2)
    assert [item.url for item in items] == ["https://bidding.csg.cn/zbgg/1.jhtml", "https://bidding.csg.cn/zbgg/2.jhtml"]


@pytest.mark.asyncio
async def test_search_uses_frozen_project_date_bounds(configured, monkeypatch):
    from datetime import date
    queries = []
    class Client:
        def __init__(self, api_key):
            pass
        async def search(self, query, **kwargs):
            queries.append(query)
            return {"results": []}
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    await configured._search_deepseek("配电网自动化 项目", "sgcc.com.cn", limit=10,
        publication_from=date(2026, 7, 21), publication_to=date(2026, 10, 19))
    assert queries == ["site:sgcc.com.cn 配电网自动化 项目 after:2026-07-20 before:2026-10-20"]


@pytest.mark.asyncio
async def test_deepseek_repeated_fetch_body_is_not_accepted(configured, monkeypatch):
    class Client:
        def __init__(self, api_key):
            pass
        async def fetch(self, url):
            return {"title": "异常来源", "content": "同一行正文\n" * 101}
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    failures = {}
    result = await configured._read_project_pages([CollectedItem(url="https://sgcc.com.cn/detail")], "sgcc.com.cn", None, failures)
    assert result[0].content == ""
    assert result[0].metadata["body_status"] == "unknown"
    assert failures["https://sgcc.com.cn/detail"] == "SOURCE_REPEATED_BODY"


@pytest.mark.asyncio
async def test_channel_and_platform_key_remain_frozen_during_hot_update(configured, monkeypatch):
    keys = []
    class Client:
        def __init__(self, api_key):
            keys.append(api_key)
            self.calls = []
        async def search(self, query, **kwargs):
            monkeypatch.setattr(module.settings, "search_provider", "auto")
            monkeypatch.setattr(module.settings, "deepseek_api_key", "new-platform-key")
            self.calls.append({"operation": "search", "billable_calls": 1, "billing_unknown": False})
            return {"results": [{"url": "https://sgcc.com.cn/detail", "title": "项目"}]}
        async def fetch(self, url):
            self.calls.append({"operation": "fetch", "billable_calls": 1, "billing_unknown": False})
            return {"title": "正式项目", "content": "完整项目原文依据。" * 30}
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    result = await configured.collect(TaskSpec(intent="查找项目", keywords=["配电网"],
        site_domains=["sgcc.com.cn"], project_collection={"target_count": 1, "candidate_limit": 1}))
    assert result.success and keys == ["platform-synthetic", "platform-synthetic"]
    assert result.coverage["deepseek"]["billable_calls"] == 2
    assert len(result.coverage["deepseek"]["calls"]) == 2
    assert module._SEARCH_SESSION.get() is None


@pytest.mark.asyncio
async def test_unknown_fetch_is_not_retried_or_replaced(configured, monkeypatch):
    from src.collectors._deepseek import DeepSeekToolsError
    fetch = AsyncMock(side_effect=DeepSeekToolsError("DEEPSEEK_TIMEOUT_UNKNOWN"))
    class Client:
        def __init__(self, api_key):
            pass
        async def fetch(self, url):
            return await fetch(url)
    monkeypatch.setattr(module._deepseek, "DeepSeekToolsClient", Client)
    monkeypatch.setattr(configured, "_fetch_inline_page", AsyncMock(side_effect=AssertionError("不可重发未知请求")))
    result = await configured._read_project_pages([CollectedItem(url="https://sgcc.com.cn/detail")], "sgcc.com.cn", None, {})
    assert fetch.await_count == 1 and result[0].metadata["body_status"] == "unknown"
    assert result[0].metadata["body_error_code"] == "DEEPSEEK_TIMEOUT_UNKNOWN"
