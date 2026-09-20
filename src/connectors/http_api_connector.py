# -*- coding: utf-8 -*-
"""通用只读 HTTP API Connector（plan.md 第 9 节 / Phase 2 Task 9）。

用 httpx（成熟异步 HTTP 客户端）+ Task 8 的 SSRF 防护与分页状态机。
不手搓 HTTP/重定向核心逻辑，只在 httpx 之上做薄层组装：
- SSRF：httpx event_hooks 每跳（含重定向）校验目标 URL
- 重试：429/502/503/504 + Retry-After，transport 异常指数退避
- 分页：四种策略（Task 8）逐页推进，每页立即落 RawArtifact
- 凭证：只接受服务端注入的 headers/params；request_snapshot 脱敏不泄漏
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.models import ConnectorCapability, RawArtifact, SourceSpec

from .base import ProbeResult, RecordBatch, SourceConnector
from .http_security import HttpSecurityGuard, SsrfError
from .pagination import (
    CursorPager,
    LinkHeaderPager,
    OffsetPager,
    PageNumberPager,
    PageRequest,
    PageResponse,
    PaginationStrategy,
)

logger = logging.getLogger(__name__)

# URL query 中需脱敏的参数名（凭证泄漏防护）
_REDACT_PARAM_NAMES = frozenset({
    "token", "access_token", "api_key", "apikey", "key", "secret",
    "password", "passwd", "auth", "signature", "sign",
})

# 重试状态码（429 限流 / 502/503/504 网关异常）
_RETRY_STATUS = frozenset({429, 502, 503, 504})

# probe 样本上限
_PROBE_SAMPLE_BYTES = 4096


def _redact_url(url: str) -> str:
    """脱敏 URL query 中的凭证参数名，值替换为 REDACTED。"""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted = [
        (k, "REDACTED") if k.lower() in _REDACT_PARAM_NAMES else (k, v)
        for k, v in pairs
    ]
    return urlunsplit(parts._replace(query=urlencode(redacted)))


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """解析 Retry-After 头：整数秒或 HTTP 日期。None 表示无值/无法解析。"""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        import datetime as _dt
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        delta = (dt - _dt.datetime.now(_dt.timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


@dataclass
class _HttpApiConfig:
    """从 SourceSpec.options 解析的 HTTP API 配置。"""
    url: str
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    body: Optional[bytes] = None
    media_type: str = "application/json"
    pagination: Optional[Dict[str, Any]] = None
    timeout: float = 30.0
    max_retries: int = 3
    readonly_post: bool = False

    @classmethod
    def from_spec(cls, spec: SourceSpec) -> "_HttpApiConfig":
        opts = spec.options or {}
        url = opts.get("url") or spec.locator
        if not url:
            raise ValueError("HTTP API 源缺少 url（locator 或 options.url）")
        method = str(opts.get("method", "GET")).upper()
        readonly_post = bool(opts.get("readonly_post", False))
        if method not in ("GET", "POST"):
            raise ValueError(f"不支持的方法: {method}（仅 GET / 只读 POST）")
        if method == "POST" and not readonly_post:
            raise ValueError("POST 请求必须显式标记 readonly_post=True")
        body = opts.get("body")
        if isinstance(body, str):
            body = body.encode("utf-8")
        return cls(
            url=url,
            method=method,
            headers=dict(opts.get("headers") or {}),
            params=dict(opts.get("params") or {}),
            body=body,
            media_type=opts.get("media_type", "application/json"),
            pagination=opts.get("pagination"),
            timeout=float(opts.get("timeout", 30.0)),
            max_retries=int(opts.get("max_retries", 3)),
            readonly_post=readonly_post,
        )


def _build_pager(config: _HttpApiConfig) -> Optional[PaginationStrategy]:
    """从配置构建分页策略，无配置返回 None（单次请求）。"""
    p = config.pagination
    if not p:
        return None
    strategy = p.get("strategy")
    options = dict(p.get("options") or {})
    if strategy == "page":
        return PageNumberPager(config.url, **options)
    if strategy == "offset":
        return OffsetPager(config.url, **options)
    if strategy == "cursor":
        return CursorPager(config.url, **options)
    if strategy == "link":
        return LinkHeaderPager(config.url, **options)
    raise ValueError(f"不支持的分页策略: {strategy}")


class HttpApiConnector(SourceConnector):
    """通用只读 HTTP API 连接器。"""

    name = "http_api"
    source_type = "http_api"

    def __init__(
        self,
        *,
        artifact_store: Optional[ArtifactStore] = None,
        security_guard: Optional[HttpSecurityGuard] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._artifact_store = artifact_store
        allowlist = tuple(
            item.strip()
            for item in settings.data_prep_http_private_host_allowlist.split(",")
            if item.strip()
        )
        self._guard = security_guard or HttpSecurityGuard(
            private_host_allowlist=allowlist
        )
        # transport 注入：生产 None（默认网络），测试注入 MockTransport
        self._transport = transport

    def capabilities(self):
        return {
            ConnectorCapability.READ_ONLY,
            ConnectorCapability.SUPPORTS_CHECKPOINT,
            ConnectorCapability.STREAMING,
        }

    def _make_client(self, config: _HttpApiConfig) -> httpx.AsyncClient:
        guard = self._guard

        async def _request_hook(request: httpx.Request) -> None:
            # 每次请求和每次重定向都重新校验（httpx event_hooks 每跳自动触发）
            try:
                guard.validate(str(request.url))
            except SsrfError as e:
                raise SsrfError(f"请求被 SSRF 防护拒绝: {e}") from e

        kwargs: Dict[str, Any] = {
            "timeout": config.timeout,
            "follow_redirects": True,
            "event_hooks": {"request": [_request_hook]},
            "headers": dict(config.headers),
            "trust_env": False,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _send(
        self, client: httpx.AsyncClient, page_request: PageRequest
    ) -> httpx.Response:
        """单次发送（page_request 的 params/headers/body 合并到 client 默认）。"""
        send_kwargs: Dict[str, Any] = {
            "params": page_request.params or None,
            "headers": page_request.headers or None,
        }
        if page_request.method == "POST" and page_request.body is not None:
            send_kwargs["content"] = page_request.body
        return await client.request(
            page_request.method, page_request.url, **send_kwargs
        )

    async def _fetch_with_retry(
        self,
        client: httpx.AsyncClient,
        page_request: PageRequest,
        max_retries: int,
    ) -> httpx.Response:
        """发请求并对 429/502/503/504 + transport 异常重试。"""
        for attempt in range(max_retries + 1):
            try:
                response = await self._send(client, page_request)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt < max_retries:
                    await asyncio.sleep(min(2 ** attempt, 30.0))
                    continue
                raise
            if response.status_code in _RETRY_STATUS and attempt < max_retries:
                wait = _retry_after_seconds(response.headers.get("retry-after"))
                if wait is None:
                    wait = min(2 ** attempt, 30.0)
                await asyncio.sleep(wait)
                continue
            return response
        raise RuntimeError("重试循环异常退出（不可达）")

    def _page_response(self, response: httpx.Response) -> PageResponse:
        return PageResponse(
            status=response.status_code,
            body=response.content,
            headers=dict(response.headers),
            url=str(response.url),
        )

    def _write_artifact(
        self,
        task_id: str,
        source_id: str,
        response: httpx.Response,
        fallback_media_type: str,
    ) -> RawArtifact:
        """把响应落为不可变 RawArtifact（凭证脱敏）。"""
        store = self._artifact_store or ArtifactStore()
        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";")[0].strip() or fallback_media_type
        redacted_url = _redact_url(str(response.url))
        ext = media_type.split("/")[-1].split(";")[0] or "bin"
        # request_snapshot 不含 headers（Authorization/Cookie 不入）
        snapshot = {
            "method": response.request.method,
            "url": redacted_url,
            "status": response.status_code,
        }
        return store.write_raw(
            task_id=task_id,
            source_id=source_id,
            data=response.content,
            uri=redacted_url,
            media_type=media_type,
            request_snapshot=snapshot,
            response_metadata={"status": response.status_code},
            ext=ext,
        )

    def _pager_checkpoint(
        self, pager: PaginationStrategy, *, is_final: bool
    ) -> Checkpoint:
        state = pager.checkpoint()
        return Checkpoint(
            cursor=json.dumps(state, ensure_ascii=False),
            page=state.get("page_no", 0),
            is_final=is_final,
        )

    def _error_batch(
        self,
        response: httpx.Response,
        *,
        warnings: List[str],
        checkpoint: Checkpoint,
    ) -> RecordBatch:
        """构造错误批次：429/502/503/504 为 retryable，其他 4xx/5xx 为 fatal。"""
        is_retryable = response.status_code in _RETRY_STATUS
        return RecordBatch(
            checkpoint=checkpoint,
            byte_count=len(response.content),
            warnings=warnings,
            retryable_error=(
                f"HTTP {response.status_code}" if is_retryable else None
            ),
            fatal_error=(
                None if is_retryable else f"HTTP {response.status_code}"
            ),
        )

    async def probe(self, spec: SourceSpec) -> ProbeResult:
        """轻量探测：校验 URL + SSRF + 发单次请求取状态和小样本。"""
        try:
            config = _HttpApiConfig.from_spec(spec)
        except ValueError as e:
            return ProbeResult(reachable=False, message=str(e))
        try:
            self._guard.validate(config.url)
        except SsrfError as e:
            return ProbeResult(reachable=False, message=f"SSRF 拒绝: {e}")
        try:
            async with self._make_client(config) as client:
                page_request = PageRequest(
                    method=config.method,
                    url=config.url,
                    params=config.params,
                    body=config.body,
                )
                response = await self._fetch_with_retry(
                    client, page_request, config.max_retries
                )
        except (httpx.HTTPError, SsrfError) as e:
            return ProbeResult(reachable=False, message=f"请求失败: {e}")
        sample_body = response.content[:_PROBE_SAMPLE_BYTES]
        return ProbeResult(
            reachable=200 <= response.status_code < 400,
            message=f"HTTP {response.status_code}",
            sample={
                "status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "body_preview": sample_body.decode("utf-8", errors="replace"),
                "url": _redact_url(str(response.url)),
            },
        )

    async def read(
        self, spec: SourceSpec, checkpoint: Optional[Checkpoint] = None
    ) -> AsyncIterator[RecordBatch]:
        opts = spec.options or {}
        task_id = opts.get("task_id")
        if not task_id:
            raise ValueError("HTTP API 源缺少 task_id")
        config = _HttpApiConfig.from_spec(spec)
        pager = _build_pager(config)
        warnings: List[str] = []

        # 恢复 checkpoint
        if checkpoint and checkpoint.cursor and pager is not None:
            try:
                pager.restore(json.loads(checkpoint.cursor))
            except (json.JSONDecodeError, TypeError):
                warnings.append("checkpoint 恢复失败，从头开始")

        async with self._make_client(config) as client:
            if pager is None:
                # 无分页：单次请求
                page_request = PageRequest(
                    method=config.method,
                    url=config.url,
                    params=config.params,
                    body=config.body,
                )
                response = await self._fetch_with_retry(
                    client, page_request, config.max_retries
                )
                if response.status_code >= 400:
                    yield self._error_batch(
                        response,
                        warnings=warnings,
                        checkpoint=Checkpoint(is_final=True),
                    )
                    return
                artifact = self._write_artifact(
                    task_id, spec.source_id, response, config.media_type
                )
                yield RecordBatch(
                    artifacts=[artifact],
                    checkpoint=Checkpoint(is_final=True),
                    byte_count=len(response.content),
                    warnings=warnings,
                )
                return

            # 分页循环
            page_request = pager.first_request()
            while page_request is not None:
                response = await self._fetch_with_retry(
                    client, page_request, config.max_retries
                )
                if response.status_code >= 400:
                    yield self._error_batch(
                        response,
                        warnings=warnings,
                        checkpoint=self._pager_checkpoint(pager, is_final=True),
                    )
                    return
                artifact = self._write_artifact(
                    task_id, spec.source_id, response, config.media_type
                )
                page_resp = self._page_response(response)
                next_request = pager.next_request(page_resp)
                is_final = next_request is None
                yield RecordBatch(
                    artifacts=[artifact],
                    checkpoint=self._pager_checkpoint(
                        pager, is_final=is_final
                    ),
                    byte_count=len(response.content),
                    warnings=warnings,
                )
                page_request = next_request


__all__ = ["HttpApiConnector"]
