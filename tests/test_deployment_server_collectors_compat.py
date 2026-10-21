# -*- coding: utf-8 -*-
"""服务器现役采集兼容增量的纯合成回归，不启动浏览器或外部请求。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.parametrize("payload,expected", [
    ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><link rel="self" href="https://feed.invalid/meta"/>'
     '<link href="https://feed.invalid/article"/><link href="javascript:alert(1)"/></entry></feed>',
     ["https://feed.invalid/article"]),
    ('<rss><channel><item><link>https://feed.invalid/a</link></item><item><link>https://feed.invalid/a</link>'
     '</item></channel></rss>', ["https://feed.invalid/a"]),
    ('<rss><item><link>https://feed.invalid/legacy</link></item>', ["https://feed.invalid/legacy"]),
])
def test_rss_fallback_keeps_atom_namespaces_and_malformed_rss(monkeypatch, payload, expected):
    from src.collectors import rss_collector

    monkeypatch.setattr(rss_collector, "_FEEDPARSER_OK", False)
    assert rss_collector._extract_urls_from_feed(payload) == expected


def _synthetic_client(monkeypatch, module, handler):
    # MockTransport 不建立 socket；限速只在本次合成测试中替身，避免真实等待。
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    ))
    monkeypatch.setattr(module, "get_rate_limiter", lambda: SimpleNamespace(acquire=AsyncMock()))


def test_site_bfs_keeps_body_and_relative_links_after_redirect(monkeypatch):
    from src.collectors import _dedup, site_crawler_collector as module
    from src.conductor.task_spec import TaskSpec

    monkeypatch.setattr(_dedup, "_seen", {})
    requested = []

    def respond(request):
        requested.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/section/index"})
        if request.url.path == "/section/index":
            links = ('<a href="leaf#first">正文页</a><a href="/section/leaf#second">重复页</a>'
                     '<a href="https://other.invalid/no">站外</a><a href="javascript:alert(1)">脚本</a>'
                     '<a href="/image.png">图片</a><a href="http://[broken">坏链接</a>')
            return httpx.Response(200, text="<html><title>首页</title><body><p>" + "首页有效正文。" * 60 + "</p>" + links + "</body></html>")
        assert request.url.path == "/section/leaf"
        return httpx.Response(200, text="<html><title>资料</title><body><p>" + "资料有效正文。" * 60 + "</p></body></html>")

    _synthetic_client(monkeypatch, module, respond)
    result = asyncio.run(module.SiteCrawlerCollector().collect(TaskSpec(
        intent="合成遍历", urls=["https://bfs.invalid/start"], max_items=2,
    )))

    assert result.success and len(result.items) == 2
    assert [item.url for item in result.items] == ["https://bfs.invalid/start", "https://bfs.invalid/section/leaf"]
    assert "首页有效正文" in result.items[0].content and "资料有效正文" in result.items[1].content
    assert requested == ["https://bfs.invalid/start", "https://bfs.invalid/section/index", "https://bfs.invalid/section/leaf"]


def test_site_bfs_stops_at_fifty_visited_pages_even_without_body(monkeypatch):
    from src.collectors import _dedup, site_crawler_collector as module
    from src.conductor.task_spec import TaskSpec

    monkeypatch.setattr(_dedup, "_seen", {})
    monkeypatch.setattr(module, "extract_content", lambda html, url: ("", "", {}))
    requested = []

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(200, text="".join(f'<a href="/page-{index}">页面</a>' for index in range(1, 101)))

    _synthetic_client(monkeypatch, module, respond)
    result = asyncio.run(module.SiteCrawlerCollector().collect(TaskSpec(
        intent="合成上限", urls=["https://limit.invalid/start"], max_items=1,
    )))

    assert not result.success and result.items == []
    assert len(requested) == len(set(requested)) == 50
    assert "遍历 50 个页面" in result.message


@pytest.mark.parametrize("error,reason,action", [
    ("ModuleNotFoundError: fake-module", "collector_dependency_missing", "install_collection_dependencies"),
    ("Host system is missing dependencies", "collector_dependency_missing", "install_collection_dependencies"),
    ("Executable doesn't exist", "collector_browser_missing", "install_collection_browser"),
    ("Chromium distribution 'chrome' is not found", "collector_browser_missing", "install_collection_browser"),
    ("Missing X server", "collector_display_unavailable", "configure_collection_headless"),
    ("without having a XServer running", "collector_display_unavailable", "configure_collection_headless"),
    ("net::ERR_NAME_NOT_RESOLVED", "collector_network_error", "check_collection_network"),
    ("net::ERR_CONNECTION_REFUSED", "collector_network_error", "check_collection_network"),
    ("net::ERR_CONNECTION_TIMED_OUT", "collector_network_error", "check_collection_network"),
    ("net::ERR_PROXY_CONNECTION_FAILED", "collector_network_error", "check_collection_network"),
    ("HTTP 403 unrelated failure", "collection_failed", "inspect_collection_service"),
])
def test_social_environment_diagnostics_remain_fixed_and_do_not_relabel_cookie(error, reason, action):
    from src.collectors.social_media_collector import _diagnose_mc_failure, _operation_evidence
    from src.collectors.base import CollectResult
    from src.conductor.task_spec import TaskSpec
    import json

    diagnostic = _diagnose_mc_failure("xhs", error + " Cookie=fake-secret /private/fake-host.py https://private.invalid/?token=fake-token")
    assert set(diagnostic) == {"reason", "next_action", "message"}
    assert (diagnostic["reason"], diagnostic["next_action"]) == (reason, action)
    result = CollectResult(False, "mediacrawler", coverage={"failure": diagnostic})
    evidence = _operation_evidence(TaskSpec(intent="合成环境诊断"), result)
    assert evidence["search"] == {"status": "unknown", "reason": reason, "next_action": action}
    assert result.authentication == {}
    exposed = json.dumps({"diagnostic": diagnostic, "evidence": evidence}, ensure_ascii=False)
    for private in ("fake-secret", "fake-token", "fake-host.py", "private.invalid", "/private/"):
        assert private not in exposed


def test_jd_comments_keep_personal_cookie_without_product_referer(monkeypatch):
    from src.collectors import ecommerce_collector as module
    from src.config.settings import settings
    from src.config.user_ctx import user_overrides_context
    from src.conductor.task_spec import TaskSpec

    monkeypatch.setattr(settings, "jd_cookie", "synthetic-global")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "club.jd.com"
        assert request.url.path == "/comment/productPageComments.action"
        assert request.url.params["productId"] == "123456"
        return httpx.Response(200, json={"comments": [{"id": 1, "content": "合成评论"}]})

    _synthetic_client(monkeypatch, module, respond)
    with user_overrides_context({"jd_cookie": "synthetic-personal"}):
        result = asyncio.run(module.EcommerceCollector().collect(TaskSpec(
            intent="合成评论", urls=["https://item.jd.com/123456.html"], platforms=["京东"], max_items=1,
        )))

    assert result.success and [item.content for item in result.items] == ["合成评论"]
    assert len(requests) == 1
    assert requests[0].headers["cookie"] == "synthetic-personal"
    assert "referer" not in requests[0].headers
    assert requests[0].headers["user-agent"] and requests[0].headers["accept-language"]


@pytest.fixture
def chrome_module():
    # 遵循现有 Agent 的导入顺序，避免旧模块的反向导入环。
    import src.agent
    from src.tools.mcp import chrome_devtools
    return chrome_devtools


@pytest.mark.parametrize("tool_name,method", [("browser_screenshot", "take_screenshot"), ("browser_snapshot", "take_snapshot")])
def test_chrome_output_wrapper_checks_path_before_creating_directory(monkeypatch, tmp_path, chrome_module, tool_name, method):
    import json

    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    client = Mock()
    getattr(client, method).return_value = {"content": [{"type": "text", "text": "合成快照"}]}
    tool = next(item for item in chrome_module.create_browser_tools(client) if item.name == tool_name)
    forbidden = tmp_path / "source-must-not-be-created"

    result = json.loads(tool.invoke({"file_path": str(forbidden / "output.txt")}))

    assert result["success"] is False
    assert not forbidden.exists()
    getattr(client, method).assert_not_called()


@pytest.mark.parametrize("method,directory", [("take_screenshot", "screenshots"), ("take_snapshot", "snapshots")])
def test_chrome_output_rejects_existing_file_but_allows_new_artifact(monkeypatch, tmp_path, chrome_module, method, directory):
    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    output = tmp_path / directory / "existing.txt"
    output.parent.mkdir()
    output.write_text("原有产物", encoding="utf-8")
    client = chrome_module.ChromeDevToolsMCP.__new__(chrome_module.ChromeDevToolsMCP)
    client.is_connected = lambda: True
    client._send_request = Mock(return_value={"result": {"content": []}})

    with pytest.raises(PermissionError, match="覆盖"):
        client.call_tool(method, {"filePath": str(output)})
    client._send_request.assert_not_called()
    assert output.read_text(encoding="utf-8") == "原有产物"
    assert client.call_tool(method, {"filePath": str(output.parent / "new.txt")}) == {"content": []}
    client._send_request.assert_called_once()


@pytest.mark.parametrize("tool_name,method,directory", [
    ("browser_screenshot", "take_screenshot", "screenshots"),
    ("browser_snapshot", "take_snapshot", "snapshots"),
])
def test_chrome_output_rejects_fresh_artifact_root_as_file(monkeypatch, tmp_path, chrome_module, tool_name, method, directory):
    import json
    from pathlib import Path

    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    client = Mock()

    def write_artifact(**arguments):
        Path(arguments["file_path"]).write_text("合成产物", encoding="utf-8")
        return {"content": []}

    getattr(client, method).side_effect = write_artifact
    tool = next(item for item in chrome_module.create_browser_tools(client) if item.name == tool_name)
    artifact_root = tmp_path / directory

    result = json.loads(tool.invoke({"file_path": str(artifact_root)}))

    assert result["success"] is False
    assert not artifact_root.exists()
    getattr(client, method).assert_not_called()


@pytest.mark.parametrize("platform,display,wayland,expected", [
    ("linux", "", "", True), ("linux", ":99", "", False),
    ("linux", "", "wayland-0", False), ("win32", "", "", False),
])
def test_chrome_headless_default_respects_display_and_explicit_configuration(monkeypatch, chrome_module, platform, display, wayland, expected):
    monkeypatch.setattr(chrome_module.sys, "platform", platform)
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setenv("WAYLAND_DISPLAY", wayland)

    config = chrome_module.ChromeDevToolsConfig()
    assert config.headless is expected
    assert ("--headless" in config.to_args()) is expected
    assert chrome_module.ChromeDevToolsConfig(headless=False).headless is False
    assert chrome_module.ChromeDevToolsConfig(headless=True).headless is True


@pytest.mark.parametrize("local_index", [True, False])
def test_chrome_launch_command_disables_usage_statistics_without_starting_process(monkeypatch, tmp_path, chrome_module, local_index):
    index = tmp_path / "synthetic-index.js"
    if local_index:
        index.write_text("// 合成入口，不执行。\n", encoding="utf-8")
    monkeypatch.setattr(chrome_module, "CHROME_DEVTOOLS_MCP_INDEX_JS", index)
    client = chrome_module.ChromeDevToolsMCP(config=chrome_module.ChromeDevToolsConfig(headless=True))

    command = client._get_command()

    assert "--headless" in command
    assert command.count("--no-usage-statistics") == 1
    assert client._process is None


def _synthetic_rpc_client(module, method, deliver, visibility_barrier=None):
    import io
    import json
    import threading

    client = module.ChromeDevToolsMCP.__new__(module.ChromeDevToolsMCP)
    client._connected = client._initialized = True
    client._request_id = 0
    client._lock = threading.Lock()
    client.verbose = False
    sent = []
    incoming = io.StringIO()

    def list_tools():
        if visibility_barrier is not None:
            visibility_barrier.wait(timeout=5)
        return [{"name": method, "annotations": {"readOnlyHint": True}}]

    def readline():
        request = json.loads(incoming.getvalue().splitlines()[-1])
        sent.append(request)
        return deliver(request)

    client.list_tools = list_tools
    client._process = SimpleNamespace(stdin=incoming, stdout=SimpleNamespace(readline=readline))
    return client, sent


@pytest.mark.parametrize("method,directory", [("take_screenshot", "screenshots"), ("take_snapshot", "snapshots")])
def test_two_chrome_clients_cannot_overwrite_the_same_new_artifact(monkeypatch, tmp_path, chrome_module, method, directory):
    from concurrent.futures import ThreadPoolExecutor
    import json
    from pathlib import Path
    import threading

    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / directory / "shared.txt"
    target.parent.mkdir()
    barrier = threading.Barrier(2)
    written = []

    def deliver(request):
        content = f"合成客户端{threading.get_ident()}".encode("utf-8")
        Path(request["params"]["arguments"]["filePath"]).write_bytes(content)
        written.append(content)
        return json.dumps({"result": {"content": []}}) + "\n"

    clients = [_synthetic_rpc_client(chrome_module, method, deliver, barrier) for _ in range(2)]

    def invoke(entry):
        try:
            entry[0].call_tool(method, {"filePath": str(target)})
            return True
        except (PermissionError, OSError, RuntimeError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, clients))

    assert results.count(True) == 1 and results.count(False) == 1
    assert sum(len(sent) for _, sent in clients) == 1
    assert len(written) == 1 and target.read_bytes() == written[0]


@pytest.mark.parametrize("tool_name,method,directory", [
    ("browser_screenshot", "take_screenshot", "screenshots"),
    ("browser_snapshot", "take_snapshot", "snapshots"),
])
@pytest.mark.parametrize("failure", ["no-response", "empty-success", "tool-error"])
def test_chrome_output_failure_keeps_reservation_and_never_reports_or_retries_success(monkeypatch, tmp_path, chrome_module, tool_name, method, directory, failure):
    import json

    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / directory / "unknown.txt"
    responses = {
        "no-response": "",
        "empty-success": json.dumps({"result": {"content": []}}) + "\n",
        "tool-error": json.dumps({"result": {"isError": True, "content": []}}) + "\n",
    }
    client, sent = _synthetic_rpc_client(chrome_module, method, lambda request: responses[failure])
    tool = next(item for item in chrome_module.create_browser_tools(client) if item.name == tool_name)

    result = json.loads(tool.invoke({"file_path": str(target)}))

    assert result["success"] is False
    assert target.is_file() and target.stat().st_size == 0
    assert len(sent) == 1
    repeated = json.loads(tool.invoke({"file_path": str(target)}))
    assert repeated["success"] is False
    assert target.stat().st_size == 0 and len(sent) == 1


@pytest.mark.parametrize("method,directory", [("take_screenshot", "screenshots"), ("take_snapshot", "snapshots")])
def test_chrome_different_paths_write_their_own_reserved_absolute_targets(monkeypatch, tmp_path, chrome_module, method, directory):
    import json
    from pathlib import Path

    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / directory).mkdir()
    targets = [Path(directory) / "first.txt", Path(directory) / "second.txt"]

    def deliver(request):
        target = Path(request["params"]["arguments"]["filePath"])
        assert target.is_absolute() and target.read_bytes() == b""
        target.write_text(target.name, encoding="utf-8")
        return json.dumps({"result": {"content": []}}) + "\n"

    clients = [_synthetic_rpc_client(chrome_module, method, deliver) for _ in targets]
    for (client, sent), target in zip(clients, targets):
        assert client.call_tool(method, {"filePath": str(target)}) == {"content": []}
        assert target.read_text(encoding="utf-8") == target.name and len(sent) == 1


def test_chrome_unavailable_tool_cannot_even_reserve_output(monkeypatch, tmp_path, chrome_module):
    monkeypatch.setattr(chrome_module, "PROJECT_ROOT", tmp_path)
    target = tmp_path / "snapshots" / "unavailable.txt"
    target.parent.mkdir()
    client, sent = _synthetic_rpc_client(chrome_module, "take_snapshot", lambda request: "")
    client.list_tools = lambda: []

    with pytest.raises(PermissionError, match="元数据冲突"):
        client.call_tool("take_snapshot", {"filePath": str(target)})
    assert not target.exists() and sent == []
