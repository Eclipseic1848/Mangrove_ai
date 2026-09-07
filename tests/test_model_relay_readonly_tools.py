"""模型 Relay 只传本机函数定义，远程工具在凭据解密和出站之前拒绝。"""
import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest

from src.model_connections.broker import ConnectionBroker, GrantError


PATHS = {
    "openai_chat_completions": "chat/completions",
    "openai_responses": "responses",
    "anthropic_messages": "messages",
    "gemini_generate_content": "models/fixture-model:generateContent",
}


def relay(api_format, fields):
    broker = ConnectionBroker.__new__(ConnectionBroker)
    broker._resolve_active_grant = Mock(return_value={
        "api_format": api_format, "model": "fixture-model",
        "base_url": "https://fixture-provider.example/v1", "locality": "external",
        "ciphertext": "fixture-ciphertext",
    })
    broker._resolver = Mock(return_value=["8.8.8.8"])
    broker._provider_timeout_seconds = 2
    broker._repository = Mock()
    broker._vault = Mock()
    broker._vault.decrypt.return_value = "fixture-secret"
    outbound = Mock(return_value=httpx.Response(200, json={"id": "fixture"}))
    broker._transport = httpx.MockTransport(outbound)

    async def send():
        response = await broker.relay(
            grant_token="fixture", protocol_path=PATHS[api_format], method="POST",
            headers={}, body=json.dumps({"model": "fixture-model", **fields}).encode("utf-8"),
        )
        await response.aclose()

    return broker, outbound, send


@pytest.mark.parametrize("api_format,fields", [
    ("openai_responses", {"tools": [{"type": "mcp", "server_url": "https://fixture.example/mcp", "require_approval": "never"}]}),
    ("openai_responses", {"tools": [{"type": "computer_use_preview"}]}),
    ("openai_chat_completions", {"tools": [{"type": "computer"}]}),
    ("openai_responses", {"tools": [{"type": "function", "name": "local", "server_url": "https://fixture.example"}]}),
    ("anthropic_messages", {"tools": [{"type": "computer_20250124", "name": "computer"}]}),
    ("anthropic_messages", {"mcp_servers": [{"type": "url", "url": "https://fixture.example/mcp"}]}),
    ("gemini_generate_content", {"tools": [{"codeExecution": {}}]}),
    ("gemini_generate_content", {"tools": [{"functionDeclarations": [{"name": "local"}], "googleSearch": {}}]}),
    ("openai_responses", {"previous_response_id": "old-remote-tools"}),
    ("openai_responses", {"conversation": "old-remote-tools"}),
    ("openai_responses", {"input": [{"type": "item_reference", "id": "old-remote-tool"}]}),
    ("openai_responses", {"input": [{"type": "mcp_approval_response", "approval_request_id": "old", "approve": True}]}),
    ("anthropic_messages", {"container": {"id": "old-remote-tools"}}),
    ("gemini_generate_content", {"cachedContent": "cachedContents/remote-tools"}),
    ("gemini_generate_content", {"cached_content": "cachedContents/remote-tools"}),
    ("openai_responses", {"tool_choice": {"type": "mcp", "server_label": "fixture"}}),
    ("anthropic_messages", {"tool_choice": {"type": "computer"}}),
    ("gemini_generate_content", {"toolConfig": {"retrievalConfig": {}}}),
])
def test_remote_tool_and_server_context_rejected_before_credentials(api_format, fields):
    broker, outbound, send = relay(api_format, fields)
    with pytest.raises(GrantError):
        asyncio.run(send())
    broker._vault.decrypt.assert_not_called()
    broker._resolver.assert_not_called()
    outbound.assert_not_called()


@pytest.mark.parametrize("api_format", PATHS)
@pytest.mark.parametrize("tools", [None, {}, "function", [None], [{}]])
def test_malformed_tools_fail_closed(api_format, tools):
    broker, outbound, send = relay(api_format, {"tools": tools})
    with pytest.raises(GrantError):
        asyncio.run(send())
    broker._vault.decrypt.assert_not_called()
    outbound.assert_not_called()


# 参数 schema 内的普通属性不属于 Provider 工具元数据，不能递归误拒。
SCHEMA = {"type": "object", "properties": {"server_url": {"type": "string"}, "mcp_servers": {"type": "array"}}}


@pytest.mark.parametrize("api_format,fields", [
    ("openai_chat_completions", {"tools": [{"type": "function", "function": {"name": "local", "parameters": SCHEMA}}], "tool_choice": {"type": "function", "function": {"name": "local"}}}),
    ("openai_responses", {"tools": [{"type": "function", "name": "local", "parameters": SCHEMA, "strict": False}], "tool_choice": {"type": "function", "name": "local"}}),
    ("anthropic_messages", {"tools": [{"name": "local", "input_schema": SCHEMA}], "tool_choice": {"type": "tool", "name": "local"}}),
    ("anthropic_messages", {"tools": [{"type": "custom", "name": "local", "input_schema": SCHEMA}], "tool_choice": {"type": "auto", "disable_parallel_tool_use": True}}),
    ("gemini_generate_content", {"tools": [{"functionDeclarations": [{"name": "local", "parameters": SCHEMA}]}], "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}}}),
    ("gemini_generate_content", {"tools": [{"function_declarations": [{"name": "local", "parameters": SCHEMA}]}], "tool_config": {"function_calling_config": {"mode": "ANY"}}}),
])
def test_local_function_definitions_preserved(api_format, fields):
    broker, outbound, send = relay(api_format, fields)
    asyncio.run(send())
    assert json.loads(outbound.call_args.args[0].content) == {"model": "fixture-model", **fields}
    broker._vault.decrypt.assert_called_once()


@pytest.mark.parametrize("api_format", PATHS)
@pytest.mark.parametrize("fields", [{}, {"tools": []}])
def test_plain_model_requests_and_empty_tools_preserved(api_format, fields):
    _, outbound, send = relay(api_format, fields)
    asyncio.run(send())
    outbound.assert_called_once()
