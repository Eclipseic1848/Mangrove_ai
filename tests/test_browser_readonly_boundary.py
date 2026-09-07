"""浏览器工具的只读选择与直接调用门；不启动浏览器或网络。"""
from unittest.mock import Mock
import io
import json
import threading
from types import SimpleNamespace

import pytest

# 旧 Agent 包需先初始化，沿产品入口顺序避免已有的反向导入环。
import src.agent
from src.tools.mcp.chrome_devtools import ChromeDevToolsMCP, create_browser_tools


@pytest.mark.parametrize("name", ["click", "evaluate_script", "upload_file", "fill_form",
                                    "voc_store_from_json_file", "new_unknown_tool"])
def test_external_or_unknown_operations_never_reach_transport(name):
    client = ChromeDevToolsMCP.__new__(ChromeDevToolsMCP)
    client.is_connected = lambda: True
    client._send_request = Mock(return_value={"result": {}})
    with pytest.raises(PermissionError):
        client.call_tool(name, {"function": "() => fetch('/publish', {method:'POST'})"})
    client._send_request.assert_not_called()


def test_browser_choice_excludes_generic_mutation_and_database_write():
    names = {tool.name for tool in create_browser_tools(Mock())}
    assert {"browser_snapshot", "browser_navigate", "browser_screenshot"} <= names
    assert {"browser_filter_voc", "browser_analyze_voc", "browser_analyze_video"} <= names
    assert not {"browser_click", "browser_evaluate", "browser_fill", "browser_upload_file",
                "browser_voc_store_from_json_file"} & names


def test_missing_tools_never_restore_old_mutating_prompt_catalog():
    from src.agent.prompts.browser_prompt_utils import get_tools_format_from_tools
    text = get_tools_format_from_tools([])
    assert "无获准" in text
    assert "browser_click" not in text


def test_direct_rpc_and_recreated_client_cannot_bypass_operation_gate():
    for _ in range(2):
        client = ChromeDevToolsMCP.__new__(ChromeDevToolsMCP)
        with pytest.raises(PermissionError):
            client._send_request("tools/call", {"name": "evaluate_script", "arguments": {}})


def test_direct_rpc_rechecks_conflicting_metadata_before_sending_call():
    for _ in range(2):
        client = ChromeDevToolsMCP.__new__(ChromeDevToolsMCP)
        client._connected = client._initialized = True
        client._request_id = 0
        client._lock = threading.Lock()
        client.verbose = False
        client._process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(
            json.dumps({"result": {"tools": [{"name": "take_snapshot",
                         "annotations": {"readOnlyHint": False}}]}}) + "\n"))
        with pytest.raises(PermissionError):
            client._send_request("tools/call", {"name": "take_snapshot"})
        sent = [json.loads(line)["method"] for line in client._process.stdin.getvalue().splitlines()]
        assert sent == ["tools/list"]


def test_notification_channel_cannot_carry_tool_calls():
    client = ChromeDevToolsMCP.__new__(ChromeDevToolsMCP)
    client._process = SimpleNamespace(stdin=Mock())
    client._connected = True
    client._lock = threading.Lock()
    client.verbose = False
    with pytest.raises(PermissionError):
        client._send_notification("tools/call", {"name": "evaluate_script"})
    client._process.stdin.write.assert_not_called()


def test_read_operation_survives_and_conflicting_metadata_is_not_advertised():
    client = ChromeDevToolsMCP.__new__(ChromeDevToolsMCP)
    client._connected = client._initialized = True
    client._request_id = 0
    client._lock = threading.Lock()
    client.verbose = False
    responses = [
        {"result": {"tools": [{"name": "take_snapshot"}]}},
        {"result": {"content": []}},
    ]
    client._process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(
        "\n".join(json.dumps(item) for item in responses) + "\n"))
    assert client.call_tool("take_snapshot") == {"content": []}
    conflicting = {"result": {"tools": [
        {"name": "take_snapshot", "annotations": {"readOnlyHint": False}},
        {"name": "click", "annotations": {"readOnlyHint": True}},
        {"name": "list_pages", "annotations": {"readOnlyHint": True}},
    ]}}
    client._process.stdout = io.StringIO((json.dumps(conflicting) + "\n") * 2)
    assert [item["name"] for item in client.list_tools()] == ["list_pages"]
    with pytest.raises(PermissionError):
        client.call_tool("take_snapshot")


@pytest.mark.parametrize("url", ["javascript:fetch('/publish')", "file:///tmp/private", "https://u:p@example.org"])
def test_navigation_rejects_executable_or_credential_urls(url):
    client = ChromeDevToolsMCP.__new__(ChromeDevToolsMCP)
    client.is_connected = lambda: True
    client._send_request = Mock(return_value={"result": {}})
    with pytest.raises((PermissionError, ValueError)):
        client.call_tool("navigate_page", {"url": url})
    client._send_request.assert_not_called()
