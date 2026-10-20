"""独立 Search/Fetch 合约、失败关闭、计费条件与无重试回归；不访问真实 API。"""
import asyncio
import json

import httpx
import pytest

from src.collectors import _deepseek as module


def _result(**changes):
    return {"title": "电网项目", "url": "https://example.com/notice", "snippet": "摘要",
            "summary": "搜索总结", "content": "", "site_name": "官网", "site_icon": None,
            "date": "2026-10-18T00:00:00Z", "authority": 3, "rerank_score": 2.5, **changes}


def _client(handler, **kwargs):
    return module.DeepSeekToolsClient(
        "test-authorized-key", transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"], **kwargs,
    )


@pytest.mark.asyncio
async def test_search_fixed_endpoint_parameters_and_safe_metrics():
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.headers["Authorization"] == "Bearer test-authorized-key"
        assert json.loads(request.content) == {"query": "电网 site:sgcc.com.cn", "count": 50,
                                               "need_content": True, "ori_question": "寻找电网项目"}
        return httpx.Response(200, json={"results": [_result()], "unexpected_secret": "discard"})

    client = _client(handle)
    response = await client.search("电网 site:sgcc.com.cn", limit=50, need_content=True, ori_question="寻找电网项目")
    assert response == {"results": [_result()]}
    assert len(requests) == 1 and len(client.calls) == 1
    assert client.calls[0]["result_count"] == 1 and client.calls[0]["billable_calls"] == 1
    assert client.calls[0]["outcome"] == "success" and not client.calls[0]["billing_unknown"]
    assert client.calls[0]["duration_ms"] >= 0
    assert all(value not in json.dumps(client.calls) for value in ("test-authorized-key", "sgcc", "寻找", "discard"))
    assert not hasattr(client, "estimated_cost_cny")


@pytest.mark.asyncio
async def test_fetch_preserves_provider_markdown_and_does_not_send_site_credentials():
    def handle(request):
        assert str(request.url) == "https://api.deepseek.com/v1/tools/fetch"
        assert json.loads(request.content) == {"url": "https://example.com/notice?id=42"}
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"title": "公告", "content": "# 公告\n正文", "raw_html": "discard"})

    client = _client(handle)
    assert await client.fetch("https://example.com/notice?id=42") == {"title": "公告", "content": "# 公告\n正文"}
    assert client.calls[0]["billable_calls"] == 1


@pytest.mark.asyncio
async def test_empty_search_is_known_nonbillable_but_empty_fetch_is_not_success():
    client = _client(lambda request: httpx.Response(200, json={"results": []}))
    assert await client.search("不存在") == {"results": []}
    assert client.calls[0]["outcome"] == "empty" and client.calls[0]["billable_calls"] == 0
    assert not client.calls[0]["billing_unknown"]
    client = _client(lambda request: httpx.Response(200, json={"title": "", "content": "  "}))
    with pytest.raises(module.DeepSeekToolsError, match="DEEPSEEK_CONTENT_EMPTY"):
        await client.fetch("https://example.com/")
    assert client.calls[0]["outcome"] == "empty" and client.calls[0]["billable_calls"] == 0
    assert not client.calls[0]["billing_unknown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "AUTH_FAILED"), (402, "INSUFFICIENT_BALANCE"),
    (403, "FETCH_BLOCKED"), (404, "CONTENT_UNAVAILABLE"), (429, "RATE_LIMITED"),
    (500, "HTTP_ERROR"), (504, "HTTP_ERROR"), (302, "REDIRECT_REJECTED")])
async def test_known_http_error_has_one_attempt_and_never_exposes_body_or_redirects(status, code):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="test-authorized-key private body", headers={"Location": "https://evil.example/"})

    client = _client(handle)
    with pytest.raises(module.DeepSeekToolsError) as error:
        await client.search("电网")
    assert error.value.code == "DEEPSEEK_" + code
    assert error.value.status_code == status
    assert "test-authorized-key" not in str(error.value) and "private body" not in str(error.value)
    assert len(calls) == len(client.calls) == 1
    assert client.calls[0]["outcome"] == "error" and client.calls[0]["billable_calls"] == 0
    assert not client.calls[0]["billing_unknown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "TIMEOUT_UNKNOWN"), (httpx.ConnectError, "NETWORK_UNKNOWN")])
async def test_transport_unknown_is_not_retried_or_falsely_marked_nonbillable(exception, code):
    calls = []

    def handle(request):
        calls.append(request)
        raise exception("test-authorized-key private diagnostic", request=request)

    client = _client(handle)
    with pytest.raises(module.DeepSeekToolsError, match="DEEPSEEK_" + code) as error:
        await client.search("电网")
    assert "private" not in str(error.value) and len(calls) == len(client.calls) == 1
    assert client.calls[0]["outcome"] == "unknown" and client.calls[0]["billing_unknown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, [], {}, {"error": {"message": "secret"}}, {"results": {}},
    {"results": [None]}, {"results": [_result(content=None)]}, {"results": [_result(authority=True)]},
    {"results": [_result(rerank_score=float("inf"))]}])
async def test_unrecognized_response_does_not_become_empty_or_success(data):
    # 无穷数不能经过 JSON 编码；按原始正文模拟异常上游。
    client = _client(lambda request: httpx.Response(200, text=json.dumps(data)))
    with pytest.raises(module.DeepSeekToolsError, match="DEEPSEEK_RESPONSE_INVALID"):
        await client.search("电网")
    assert client.calls[0]["billing_unknown"] and client.calls[0]["billable_calls"] == 0


@pytest.mark.asyncio
async def test_malformed_json_is_unknown_and_cancel_remains_cancelled():
    client = _client(lambda request: httpx.Response(200, text="invalid-json secret"))
    with pytest.raises(module.DeepSeekToolsError, match="DEEPSEEK_RESPONSE_INVALID"):
        await client.search("电网")
    assert client.calls[0]["billing_unknown"]

    def cancel(request):
        raise asyncio.CancelledError()

    client = _client(cancel)
    with pytest.raises(asyncio.CancelledError):
        await client.search("电网")
    assert client.calls[0]["outcome"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("query,kwargs", [("", {}), ("  ", {}), ("字" * 129, {}),
    (None, {}), ("词", {"limit": 0}), ("词", {"limit": 51}), ("词", {"limit": True}),
    ("词", {"need_content": 1}), ("词", {"ori_question": ""})])
async def test_invalid_search_input_does_not_issue_request(query, kwargs):
    client = _client(lambda request: pytest.fail("不应发请求"))
    with pytest.raises(ValueError):
        await client.search(query, **kwargs)
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:password@example.com/", "http://@example.com/",
    "http://127.0.0.1/", "http://169.254.169.254/", "http://192.168.1.1/", "http://[::1]/",
    "https://example.com/?access_token=secret", "https://example.com/?x-amz-credential=secret",
    "https://example.com/?sessionId=secret", "https://example.com/?%61pi_key=secret",
    "https://example.com/#secret", "https://example.com/\nsecret", "https://example.com/" + "a" * 8192])
async def test_fetch_rejects_nonpublic_or_credential_urls_without_api_call(url):
    client = _client(lambda request: pytest.fail("不应发请求"))
    with pytest.raises(ValueError) as error:
        await client.fetch(url)
    assert "secret" not in str(error.value) and not client.calls


@pytest.mark.asyncio
async def test_dns_all_addresses_must_be_public_and_authorization_checked_before_after(monkeypatch):
    client = module.DeepSeekToolsClient("key", transport=httpx.MockTransport(lambda request: pytest.fail("不应发请求")),
                                      resolver=lambda host: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(ValueError, match="公开网络地址校验"):
        await client.fetch("https://mixed.example/")
    assert not client.calls

    checkpoints = []
    monkeypatch.setattr(module, "execution_checkpoint", lambda **kwargs: checkpoints.append(kwargs))
    client = _client(lambda request: httpx.Response(200, json={"results": []}), required_authorization=True)
    await client.search("电网")
    assert checkpoints == [{"required": True}, {"required": True}]
    assert not _client(lambda request: None).calls


@pytest.mark.asyncio
async def test_authorization_revoked_after_send_is_propagated_and_attempt_retained(monkeypatch):
    class Denied(RuntimeError):
        pass

    count = 0

    def checkpoint(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise Denied()

    monkeypatch.setattr(module, "execution_checkpoint", checkpoint)
    client = _client(lambda request: httpx.Response(200, json={"results": [_result()]}))
    with pytest.raises(Denied):
        await client.search("电网")
    assert client.calls[0]["outcome"] == "success" and client.calls[0]["billable_calls"] == 1


@pytest.mark.parametrize("kwargs", [{"api_key": ""}, {"api_key": "key\nsecret"}, {"api_key": "key", "timeout": 31},
    {"api_key": "key", "timeout": 0}, {"api_key": "key", "timeout": float("nan")}])
def test_client_rejects_invalid_credentials_and_timeout(kwargs):
    with pytest.raises(ValueError):
        module.DeepSeekToolsClient(**kwargs)
