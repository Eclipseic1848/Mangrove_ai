"""反爬增补的故障恢复、采集判定及管理员解除回归。"""
import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.agent
from src.api.auth import get_current_user
from src.api.routes.config_routes import router
from src.collectors import _domain_health as health
from src.collectors import scrapling_collector as scrapling
from src.collectors.base import CollectedItem, CollectResult
from src.collectors.registry import _effective_tier
from src.conductor.task_spec import TaskSpec


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(health, "_recent", {})
    monkeypatch.setattr(scrapling, "_STEALTH_OK", True)
    monkeypatch.setattr(scrapling, "_stealth_broken", False)


def page(text="正常正文内容。", status=200):
    return SimpleNamespace(status=status, get_all_text=lambda: text, find=lambda _: None, css=lambda _: [])


def test_temporary_network_error_does_not_disable_next_browser_request(monkeypatch):
    fetch = AsyncMock(side_effect=[TimeoutError("来源暂时超时"), page()])
    monkeypatch.setattr(scrapling, "StealthyFetcher", SimpleNamespace(async_fetch=fetch))
    assert asyncio.run(scrapling._fetch_stealth("https://first.example")) is None
    assert asyncio.run(scrapling._fetch_stealth("https://second.example")) is not None
    assert fetch.await_count == 2


@pytest.mark.parametrize("status,text", [(403, "拒绝访问。" * 200), (200, "Just a moment. Checking your browser")])
def test_error_or_verification_page_uses_browser_instead_of_claiming_success(monkeypatch, status, text):
    monkeypatch.setattr(scrapling, "_SCRAPLING_OK", True)
    monkeypatch.setattr(scrapling.settings, "scrapling_stealth_enabled", True)
    monkeypatch.setattr(scrapling, "_fetch_one", lambda url: CollectedItem(url=url, content=text, metadata={"status": status}))
    fallback = AsyncMock(return_value=CollectedItem(content="实际来源正文。", metadata={"status": 200}))
    monkeypatch.setattr(scrapling, "_fetch_stealth", fallback)
    monkeypatch.setattr(scrapling, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    result = asyncio.run(scrapling.ScraplingCollector().collect(TaskSpec(intent="检查", urls=["https://verification.example"], max_items=1)))
    assert fallback.await_count == 1
    assert result.items[0].content == "实际来源正文。"


def test_browser_error_page_is_not_source_content(monkeypatch):
    fetch = AsyncMock(return_value=page("Access Denied", 403))
    monkeypatch.setattr(scrapling, "StealthyFetcher", SimpleNamespace(async_fetch=fetch))
    assert asyncio.run(scrapling._fetch_stealth("https://verification.example")) is None


def test_missing_browser_still_stops_repeated_launches(monkeypatch):
    fetch = AsyncMock(side_effect=RuntimeError("Executable doesn't exist at /missing/browser"))
    monkeypatch.setattr(scrapling, "StealthyFetcher", SimpleNamespace(async_fetch=fetch))
    assert asyncio.run(scrapling._fetch_stealth("https://first.example")) is None
    assert asyncio.run(scrapling._fetch_stealth("https://second.example")) is None
    assert fetch.await_count == 1


def test_normal_long_article_with_verification_terms_is_preserved(monkeypatch):
    text = "这篇文章说明如何使用验证码保护用户账号。" * 40
    monkeypatch.setattr(scrapling, "_SCRAPLING_OK", True)
    monkeypatch.setattr(scrapling, "_fetch_one", lambda url: CollectedItem(content=text, metadata={"status": 200}))
    fallback = AsyncMock()
    monkeypatch.setattr(scrapling, "_fetch_stealth", fallback)
    monkeypatch.setattr(scrapling, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))
    result = asyncio.run(scrapling.ScraplingCollector().collect(TaskSpec(intent="正常文章", urls=["https://verification.example"], max_items=1)))
    assert result.items[0].content == text
    assert fallback.await_count == 0


def test_linux_browser_uses_installed_chrome(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None)
    fetch = AsyncMock(return_value=page())
    monkeypatch.setattr(scrapling, "StealthyFetcher", SimpleNamespace(async_fetch=fetch))
    assert asyncio.run(scrapling._fetch_stealth("https://verification.example")) is not None
    assert fetch.await_args.kwargs.get("real_chrome") is True


def test_same_domain_urls_count_once_per_collector_call():
    urls = ["https://verification.example/a", "https://www.verification.example/b", "https://verification.example/c"]
    health.record("article", urls, False)
    assert not health.is_learned_anti_scrape(urls[0])
    health.record("firecrawl", urls, False)
    assert not health.is_learned_anti_scrape(urls[0])
    health.record("crawl4ai", urls, False)
    assert health.list_flagged()["verification.example"]["samples"] == 3


def test_routing_expires_and_manual_release_keeps_other_domains(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(health.time, "time", lambda: clock[0])
    for domain in ("first.example", "second.example"):
        for _ in range(3):
            health.record("article", ["https://" + domain], False)
    assert health.is_learned_anti_scrape("https://first.example")
    assert _effective_tier(SimpleNamespace(name="scrapling", tier=50), True) == 16
    health.reset("first.example")
    assert set(health.list_flagged()) == {"second.example"}
    clock[0] += 1801
    assert not health.list_flagged()


@pytest.mark.parametrize("error", [TimeoutError("超时"), RuntimeError("采集异常")])
def test_conductor_failure_records_are_not_lost(monkeypatch, error):
    collect = importlib.import_module("src.conductor.nodes.collect")
    monkeypatch.setattr(collect, "get_registry", lambda: {"article": SimpleNamespace(collect=AsyncMock(side_effect=error))})
    monkeypatch.setattr(collect, "metrics_record", lambda *args: None)
    monkeypatch.setattr(collect, "emit_progress", lambda *args, **kwargs: None)
    spec = TaskSpec(intent="失败记录检查", urls=["https://verification.example"], max_items=1)
    for _ in range(3):
        result = asyncio.run(collect.collect_node({"task_spec": spec, "collector_candidates": ["article"]}))
        assert not result["raw_dataset"]
    assert health.is_learned_anti_scrape(spec.urls[0])


def test_cancelled_task_does_not_count_as_source_failure(monkeypatch):
    collect = importlib.import_module("src.conductor.nodes.collect")
    monkeypatch.setattr(collect, "get_registry", lambda: {"article": SimpleNamespace(collect=AsyncMock(side_effect=asyncio.CancelledError()))})
    monkeypatch.setattr(collect, "emit_progress", lambda *args, **kwargs: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect.collect_node({"task_spec": TaskSpec(intent="取消检查", urls=["https://verification.example"]), "collector_candidates": ["article"]}))
    assert not health._recent


@pytest.mark.parametrize("entry", ["conductor", "web_adapter"])
@pytest.mark.parametrize("metadata,text", [({}, "Just a moment. Checking your browser"), ({"status_code": 403}, "拒绝访问。" * 200)],
                         ids=["challenge_page", "http_error"])
def test_preceding_error_page_does_not_stop_anti_scrape_routing(monkeypatch, entry, metadata, text):
    url = "https://verification.example"
    first = SimpleNamespace(name="article", collect=AsyncMock(return_value=CollectResult(
        True, "article", items=[CollectedItem(url=url, content=text, metadata=metadata)])))
    fallback = SimpleNamespace(name="scrapling", collect=AsyncMock(return_value=CollectResult(
        True, "scrapling", items=[CollectedItem(url=url, content="实际来源正文。")])))
    if entry == "conductor":
        collect = importlib.import_module("src.conductor.nodes.collect")
        monkeypatch.setattr(collect, "get_registry", lambda: {"article": first, "scrapling": fallback})
        monkeypatch.setattr(collect, "metrics_record", lambda *args: None)
        monkeypatch.setattr(collect, "emit_progress", lambda *args, **kwargs: None)
        result = asyncio.run(collect.collect_node({"task_spec": TaskSpec(intent="升级检查", urls=[url]),
                                                 "collector_candidates": ["article", "scrapling"]}))
        assert result["raw_dataset"][0]["content"] == "实际来源正文。"
        assert [attempt["success"] for attempt in result["collector_attempts"]] == [False, True]
    else:
        from src.connectors import web_adapter
        from src.data_prep import artifact_store
        from src.data_prep.models import SourceSpec, SourceType
        saved = []

        def write_raw(**kwargs):
            saved.append(json.loads(kwargs["data"].decode("utf-8")))
            return SimpleNamespace(size_bytes=len(kwargs["data"]))

        monkeypatch.setattr(web_adapter, "select_collectors", lambda spec: [first, fallback])
        monkeypatch.setattr(web_adapter, "metrics_record", lambda *args: None)
        monkeypatch.setattr(artifact_store, "ArtifactStore", lambda: SimpleNamespace(write_raw=write_raw))

        async def read():
            return [batch async for batch in web_adapter.WebCollectorAdapter().read(
                SourceSpec(source_id="check", source_type=SourceType.WEB, locator=url))]

        batches = asyncio.run(read())
        assert len(batches[0].artifacts) == 1
        assert saved[0]["content"] == "实际来源正文。"
        assert "验证页" in batches[0].warnings[0]
    assert fallback.collect.await_count == 1
    assert list(health._recent["verification.example"])[0][1] is False


def test_page_filter_preserves_normal_text_and_empty_video_manifest():
    normal = CollectedItem(content="介绍验证码设计的正常文章。" * 40)
    video = CollectedItem(content="", metadata={"media_url": "https://video.example/example.mp4"})
    result = CollectResult(True, "article", items=[normal, video,
        CollectedItem(content="Checking your browser"), CollectedItem(content="错误响应", metadata={"status": 503})])
    result.discard_error_pages()
    assert result.has_data
    assert result.items == [normal, video]


def test_domain_health_endpoints_require_admin_and_release_only_target():
    app = FastAPI()
    app.include_router(router)
    for domain in ("first.example", "second.example"):
        for _ in range(3):
            health.record("article", ["https://" + domain], False)
    app.dependency_overrides[get_current_user] = lambda: {"role": "user"}
    with TestClient(app) as client:
        assert client.get("/api/config/domain-health").status_code == 403
        assert client.delete("/api/config/domain-health/first.example").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: {"role": "admin"}
        assert set(client.get("/api/config/domain-health").json()["flagged"]) == {"first.example", "second.example"}
        assert client.delete("/api/config/domain-health/first.example").status_code == 200
        assert set(client.get("/api/config/domain-health").json()["flagged"]) == {"second.example"}
