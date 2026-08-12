# -*- coding: utf-8 -*-
"""HttpApiConnector 测试（plan.md 第 9 节 / Phase 2 Task 9）。

用 httpx.MockTransport 注入（不真连网络），覆盖：
- 配置解析（GET/POST readonly/不支持方法）
- URL 脱敏 + Retry-After 解析
- probe（可达/SSRF 拒绝/4xx）
- read 单次请求 + 四种分页（page/offset/cursor/link）
- 429 重试 + Retry-After
- SSRF 阻断（event_hook 每跳校验）
- 凭证脱敏（request_snapshot/uri 不泄漏）
- 错误分类（fatal 4xx / retryable 429 用尽）
- checkpoint 传递
"""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from src.connectors.http_api_connector import (
    HttpApiConnector,
    _HttpApiConfig,
    _redact_url,
    _retry_after_seconds,
)
from src.connectors.http_security import HttpSecurityGuard, SsrfError
from src.data_prep.models import SourceSpec, SourceType

URL = "https://api.example.com/items"
# 测试环境 DNS 对公网域名可能劫持（如返回 198.18.x.x），注入 mock resolver
# 让 example.com 解析到真实公网 IP，避免 SSRF 误拒
_PUBLIC_IP = "93.184.216.34"


def _make_conn(handler, *, allow_private: bool = False) -> HttpApiConnector:
    """测试用 connector：mock resolver 绕过本机 DNS 劫持。"""
    guard = HttpSecurityGuard(
        allow_private=allow_private,
        resolver=lambda h: [_PUBLIC_IP],
    )
    return HttpApiConnector(
        transport=httpx.MockTransport(handler),
        security_guard=guard,
    )


def _spec(opts: Dict[str, Any]) -> SourceSpec:
    url = opts.pop("url", URL)
    return SourceSpec(
        source_id="api-1",
        source_type=SourceType.HTTP_API,
        locator=url,
        options=opts,
    )


def _cleanup(task_id: str) -> None:
    p = Path("downloads") / task_id
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


async def _collect(aiter) -> List:
    out = []
    async for item in aiter:
        out.append(item)
    return out


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# 配置解析
# ===========================================================================
class TestConfig:
    def test_get_default(self):
        c = _HttpApiConfig.from_spec(_spec({"task_id": "t1"}))
        assert c.method == "GET"
        assert c.url == URL
        assert c.max_retries == 3

    def test_client_ignores_environment_proxy(self):
        conn = _make_conn(lambda request: httpx.Response(200, json=[]))
        config = _HttpApiConfig(url=URL)
        client = conn._make_client(config)
        try:
            assert client._trust_env is False
        finally:
            _run(client.aclose())

    def test_post_requires_readonly(self):
        with pytest.raises(ValueError, match="readonly"):
            _HttpApiConfig.from_spec(_spec({"task_id": "t1", "method": "POST"}))

    def test_post_readonly_ok(self):
        c = _HttpApiConfig.from_spec(
            _spec({"task_id": "t1", "method": "POST", "readonly_post": True})
        )
        assert c.method == "POST"
        assert c.readonly_post is True

    def test_unsupported_method(self):
        with pytest.raises(ValueError, match="方法"):
            _HttpApiConfig.from_spec(_spec({"task_id": "t1", "method": "DELETE"}))

    def test_body_string_encoded(self):
        c = _HttpApiConfig.from_spec(
            _spec({"method": "POST", "readonly_post": True, "body": '{"q":"x"}'})
        )
        assert c.body == b'{"q":"x"}'

    def test_missing_url(self):
        spec = SourceSpec(
            source_id="api-1", source_type=SourceType.HTTP_API, locator=""
        )
        with pytest.raises(ValueError, match="url"):
            _HttpApiConfig.from_spec(spec)


# ===========================================================================
# 工具函数
# ===========================================================================
class TestRedactUrl:
    def test_redact_token(self):
        r = _redact_url("https://x/p?token=secret&keep=1")
        assert "secret" not in r
        assert "REDACTED" in r
        assert "keep=1" in r

    def test_redact_api_key(self):
        r = _redact_url("https://x/p?api_key=abc123")
        assert "abc123" not in r
        assert "REDACTED" in r

    def test_no_query_unchanged(self):
        assert _redact_url("https://x/p") == "https://x/p"

    def test_no_sensitive_param_unchanged(self):
        assert _redact_url("https://x/p?page=1&size=10") == "https://x/p?page=1&size=10"


class TestRetryAfter:
    def test_seconds(self):
        assert _retry_after_seconds("5") == 5.0

    def test_none(self):
        assert _retry_after_seconds(None) is None
        assert _retry_after_seconds("") is None

    def test_invalid(self):
        assert _retry_after_seconds("garbage") is None


# ===========================================================================
# probe
# ===========================================================================
class TestProbe:
    def test_reachable(self):
        def handler(req):
            return httpx.Response(200, json={"ok": True})

        conn = _make_conn(handler)
        probe = _run(conn.probe(_spec({"task_id": "t1"})))
        assert probe.reachable
        assert probe.sample["status"] == 200
        assert "ok" in probe.sample["body_preview"]

    def test_ssrf_blocked(self):
        def handler(req):
            return httpx.Response(200)

        conn = _make_conn(handler)
        probe = _run(conn.probe(_spec({"task_id": "t1", "url": "http://10.0.0.1/x"})))
        assert not probe.reachable
        assert "SSRF" in probe.message

    def test_4xx_not_reachable(self):
        def handler(req):
            return httpx.Response(404)

        conn = _make_conn(handler)
        probe = _run(conn.probe(_spec({"task_id": "t1"})))
        assert not probe.reachable
        assert "404" in probe.message

    def test_config_error(self):
        conn = _make_conn(lambda r: httpx.Response(200))
        probe = _run(conn.probe(_spec({"method": "DELETE"})))
        assert not probe.reachable


# ===========================================================================
# read：单次请求
# ===========================================================================
class TestReadSingle:
    def test_single_request(self):
        task_id = f"http_{uuid.uuid4().hex[:8]}"
        body = b'[{"id":1},{"id":2}]'
        try:
            def handler(req):
                return httpx.Response(200, content=body,
                                      headers={"content-type": "application/json"})

            conn = _make_conn(handler)
            batches = _run(_collect(conn.read(_spec({"task_id": task_id}))))
            assert len(batches) == 1
            assert len(batches[0].artifacts) == 1
            assert batches[0].artifacts[0].size_bytes == len(body)
            assert batches[0].checkpoint.is_final
        finally:
            _cleanup(task_id)

    def test_missing_task_id(self):
        conn = _make_conn(lambda r: httpx.Response(200))
        with pytest.raises(ValueError, match="task_id"):
            _run(_collect(conn.read(_spec({}))))


# ===========================================================================
# read：四种分页
# ===========================================================================
class TestReadPagination:
    def test_page_number(self):
        task_id = f"pg_{uuid.uuid4().hex[:8]}"
        pages = {1: b'[{"id":1},{"id":2}]', 2: b'[{"id":3},{"id":4}]', 3: b'[]'}
        try:
            def handler(req):
                page = int(req.url.params["page"])
                return httpx.Response(200, content=pages[page])

            conn = _make_conn(handler)
            spec = _spec({
                "task_id": task_id,
                "pagination": {"strategy": "page",
                               "options": {"per_page": 2, "start_page": 1, "max_pages": 10}},
            })
            batches = _run(_collect(conn.read(spec)))
            # page 1, 2, 3（空页也落 artifact，末页 is_final）
            assert len(batches) == 3
            assert not batches[0].checkpoint.is_final
            assert batches[2].checkpoint.is_final
            assert batches[2].artifacts[0].size_bytes == len(b'[]')
        finally:
            _cleanup(task_id)

    def test_offset(self):
        task_id = f"of_{uuid.uuid4().hex[:8]}"
        try:
            data = {0: b'[{"id":1},{"id":2}]', 2: b'[{"id":3}]'}

            def handler(req):
                off = int(req.url.params["offset"])
                return httpx.Response(200, content=data[off])

            conn = _make_conn(handler)
            spec = _spec({
                "task_id": task_id,
                "pagination": {"strategy": "offset",
                               "options": {"limit": 2, "start_offset": 0, "max_pages": 10}},
            })
            batches = _run(_collect(conn.read(spec)))
            # offset 0 (2条) -> offset 2 (1条 < limit 停)
            assert len(batches) == 2
            assert batches[1].checkpoint.is_final
        finally:
            _cleanup(task_id)

    def test_cursor(self):
        task_id = f"cu_{uuid.uuid4().hex[:8]}"
        try:
            data = {
                "": b'{"data":[1],"next_cursor":"a"}',
                "a": b'{"data":[2],"next_cursor":"b"}',
                "b": b'{"data":[3],"next_cursor":null}',
            }

            def handler(req):
                c = req.url.params.get("cursor", "")
                return httpx.Response(200, content=data.get(c, b'{}'))

            conn = _make_conn(handler)
            spec = _spec({
                "task_id": task_id,
                "pagination": {"strategy": "cursor", "options": {"max_pages": 10}},
            })
            batches = _run(_collect(conn.read(spec)))
            assert len(batches) == 3
            assert batches[2].checkpoint.is_final
        finally:
            _cleanup(task_id)

    def test_link_header(self):
        task_id = f"lk_{uuid.uuid4().hex[:8]}"
        try:
            def handler(req):
                url = str(req.url)
                if "page=2" in url:
                    return httpx.Response(200, content=b'[2]')  # 无 next
                return httpx.Response(
                    200, content=b'[1]',
                    headers={"link": f'<{URL}?page=2>; rel="next"'},
                )

            conn = _make_conn(handler)
            spec = _spec({
                "task_id": task_id,
                "pagination": {"strategy": "link", "options": {"max_pages": 10}},
            })
            batches = _run(_collect(conn.read(spec)))
            assert len(batches) == 2
            assert batches[1].checkpoint.is_final
        finally:
            _cleanup(task_id)


# ===========================================================================
# read：重试
# ===========================================================================
class TestRetry:
    def test_retry_on_429_then_success(self):
        task_id = f"rt_{uuid.uuid4().hex[:8]}"
        calls = {"n": 0}
        try:
            def handler(req):
                calls["n"] += 1
                if calls["n"] < 3:
                    return httpx.Response(429, headers={"retry-after": "0"})
                return httpx.Response(200, content=b'{"ok":1}')

            conn = _make_conn(handler)
            batches = _run(_collect(conn.read(
                _spec({"task_id": task_id, "max_retries": 3})
            )))
            assert calls["n"] == 3
            assert len(batches) == 1
            assert batches[0].artifacts[0].size_bytes > 0
        finally:
            _cleanup(task_id)

    def test_retry_exhausted_returns_retryable_error(self):
        task_id = f"re_{uuid.uuid4().hex[:8]}"
        try:
            def handler(req):
                return httpx.Response(429, headers={"retry-after": "0"})

            conn = _make_conn(handler)
            batches = _run(_collect(conn.read(
                _spec({"task_id": task_id, "max_retries": 2})
            )))
            assert len(batches) == 1
            assert batches[0].retryable_error == "HTTP 429"
            assert batches[0].fatal_error is None
            assert batches[0].checkpoint.is_final
        finally:
            _cleanup(task_id)


# ===========================================================================
# read：错误分类
# ===========================================================================
class TestErrorClassification:
    def test_4xx_fatal(self):
        task_id = f"fe_{uuid.uuid4().hex[:8]}"
        try:
            def handler(req):
                return httpx.Response(404)

            conn = _make_conn(handler)
            batches = _run(_collect(conn.read(_spec({"task_id": task_id}))))
            assert len(batches) == 1
            assert batches[0].fatal_error == "HTTP 404"
            assert batches[0].retryable_error is None
            assert batches[0].checkpoint.is_final
        finally:
            _cleanup(task_id)


# ===========================================================================
# read：SSRF 阻断
# ===========================================================================
class TestSsrfBlock:
    def test_read_ssrf_raises(self):
        def handler(req):
            return httpx.Response(200)

        conn = _make_conn(handler)
        with pytest.raises(SsrfError):
            _run(_collect(conn.read(
                _spec({"task_id": "t1", "url": "http://169.254.169.254/x"})
            )))

    def test_loopback_blocked(self):
        def handler(req):
            return httpx.Response(200)

        conn = _make_conn(handler)
        with pytest.raises(SsrfError):
            _run(_collect(conn.read(
                _spec({"task_id": "t1", "url": "http://127.0.0.1/x"})
            )))


# ===========================================================================
# read：凭证脱敏
# ===========================================================================
class TestCredentialRedaction:
    def test_credentials_not_in_artifact(self):
        task_id = f"cr_{uuid.uuid4().hex[:8]}"
        try:
            def handler(req):
                return httpx.Response(200, content=b'{"ok":1}',
                                      headers={"content-type": "application/json"})

            conn = _make_conn(handler)
            spec = _spec({
                "task_id": task_id,
                "headers": {"Authorization": "Bearer secret-token"},
                "params": {"token": "secret", "filter": "active"},
            })
            batches = _run(_collect(conn.read(spec)))
            art = batches[0].artifacts[0]
            snap = json.dumps(art.request_snapshot, ensure_ascii=False)
            # 凭证不进 request_snapshot
            assert "secret" not in snap
            assert "Bearer" not in snap
            assert "Authorization" not in snap
            # uri 脱敏 token
            assert "secret" not in art.uri
            assert "REDACTED" in art.uri
            # 非敏感参数保留
            assert "filter=active" in art.uri or "filter" in art.uri
        finally:
            _cleanup(task_id)


# ===========================================================================
# read：checkpoint 传递
# ===========================================================================
class TestCheckpoint:
    def test_checkpoint_has_cursor_and_page(self):
        task_id = f"ck_{uuid.uuid4().hex[:8]}"
        try:
            pages = {1: b'[{"id":1}]', 2: b'[{"id":2}]', 3: b'[]'}

            def handler(req):
                page = int(req.url.params["page"])
                return httpx.Response(200, content=pages[page])

            conn = _make_conn(handler)
            spec = _spec({
                "task_id": task_id,
                "pagination": {"strategy": "page",
                               "options": {"per_page": 1, "max_pages": 10}},
            })
            batches = _run(_collect(conn.read(spec)))
            # 非末批有 cursor（可恢复）
            assert batches[0].checkpoint.cursor is not None
            state = json.loads(batches[0].checkpoint.cursor)
            assert "current_page" in state
            assert batches[0].checkpoint.page == 1
            assert batches[-1].checkpoint.is_final
        finally:
            _cleanup(task_id)

    def test_capabilities(self):
        from src.data_prep.models import ConnectorCapability
        conn = _make_conn(lambda r: httpx.Response(200))
        caps = conn.capabilities()
        assert ConnectorCapability.READ_ONLY in caps
        assert ConnectorCapability.SUPPORTS_CHECKPOINT in caps
        assert ConnectorCapability.STREAMING in caps
