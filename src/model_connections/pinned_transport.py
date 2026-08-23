# -*- coding: utf-8 -*-
"""Provider HTTP 连接级 DNS 固定 Transport。"""
from __future__ import annotations

import httpx

from src.connectors.http_security import ValidatedTarget


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """连接预检 IP，同时保留原 Host 与 TLS SNI 身份。"""

    def __init__(
        self,
        *,
        target: ValidatedTarget,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._target = target
        self._transport = transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        pinned_url = request.url.copy_with(host=self._target.ips[0])
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self._target.host
        pinned_request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._transport.aclose()


__all__ = ["PinnedAsyncHTTPTransport"]
