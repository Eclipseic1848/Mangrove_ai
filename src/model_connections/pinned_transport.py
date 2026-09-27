# -*- coding: utf-8 -*-
"""Provider HTTP 连接级 DNS 固定 Transport。"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import time

import httpx

from src.connectors.http_security import ValidatedTarget


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """连接预检 IP，同时保留原 Host 与 TLS SNI 身份。"""

    def __init__(
        self,
        *,
        target: ValidatedTarget,
        transport: httpx.AsyncBaseTransport | None = None,
        check_active: Callable[[], None] | None = None,
    ) -> None:
        self._target = target
        self._transport = transport or httpx.AsyncHTTPTransport()
        self._check_active = check_active

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self._target.host
        original_trace = extensions.get("trace")
        connect_timeout = extensions.get("timeout", {}).get("connect")
        deadline = None
        phase = "unknown"

        async def trace(event, info):
            nonlocal phase, deadline, opening_stream
            if event in {"connection.connect_tcp.started", "connection.start_tls.started"} and phase != "sending":
                phase = "connecting"
                if deadline is None and connect_timeout is not None:
                    deadline = time.monotonic() + connect_timeout
                if deadline is not None:
                    # HTTPX 原本逐阶段计时；跨地址的 TCP/TLS 必须共用同一截止时间。
                    budget = deadline - time.monotonic()
                    if budget <= 0:
                        raise httpx.ConnectTimeout("连接预算已耗尽")
                    connection_budget.reschedule(asyncio.get_running_loop().time() + budget)
                if self._check_active is not None:
                    self._check_active()
            elif event in {"connection.connect_tcp.complete", "connection.start_tls.complete"}:
                opening_stream = info.get("return_value")
            elif event.endswith(".send_request_headers.started"):
                # 建联等待期间也可能撤销；发头前最后检查，正文及响应沿用各自超时。
                if self._check_active is not None:
                    self._check_active()
                if deadline is not None and time.monotonic() >= deadline:
                    raise httpx.ConnectTimeout("连接预算已耗尽")
                phase = "sending"
                connection_budget.reschedule(None)
            if original_trace is not None:
                await original_trace(event, info)

        extensions["trace"] = trace
        address, remaining = self._target.ips[0], iter(self._target.ips[1:])
        while True:
            phase = "unknown"
            opening_stream = None
            pinned_request = httpx.Request(
                method=request.method,
                url=request.url.copy_with(host=address),
                headers=request.headers,
                stream=request.stream,
                extensions=extensions,
            )
            try:
                async with asyncio.timeout(None) as connection_budget:
                    return await self._transport.handle_async_request(pinned_request)
            except TimeoutError as exc:
                if not connection_budget.expired():
                    raise
                # 只转换自身建联计时器的到期，真实取消仍向上传播。
                raise httpx.ConnectTimeout("连接预算已耗尽") from exc
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # 仅原生阶段证明尚未开始发送时才能换已校验IP；未知/已发送请求绝不重放。
                address = next(remaining, None)
                if phase != "connecting" or address is None:
                    raise
                if deadline is not None:
                    budget = deadline - time.monotonic()
                    if budget <= 0:
                        raise
                    # 多地址共用原建联预算，不能把调用方的超时按地址数量放大。
                    extensions["timeout"] = {**extensions["timeout"], "connect": budget}
            finally:
                if phase != "sending" and opening_stream is not None:
                    # TLS 中断时 httpcore 尚未接管连接，主动释放已建立的 TCP；不覆盖原故障。
                    with suppress(Exception):
                        await opening_stream.aclose()

    async def aclose(self) -> None:
        await self._transport.aclose()


__all__ = ["PinnedAsyncHTTPTransport"]
