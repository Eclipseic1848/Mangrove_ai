"""模型连接 Grant 与 Relay 的公开契约。"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import AsyncIterator, Callable
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field


class AccessGrant(BaseModel):
    """单次 Run、单一用途、短时有效的模型连接使用权。"""

    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(min_length=1)
    token: str = Field(min_length=32)
    connection_id: str = Field(min_length=1)
    connection_version: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    api_format: str = Field(min_length=1)
    model: str = Field(min_length=1)
    expires_at: datetime


class ConnectionBinding(BaseModel):
    """创建 TaskRevision 时冻结的非敏感连接版本引用。"""

    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(min_length=1)
    connection_version: str = Field(min_length=1)
    model: str = Field(min_length=1)


class RelayResponse:
    """保留 Provider 分块语义，并在流关闭时只提取用量元数据。"""

    def __init__(
        self,
        *,
        response: httpx.Response,
        client: httpx.AsyncClient,
        finalize: Callable[[bytes], None],
        max_usage_bytes: int = 2 * 1024 * 1024,
        check_active: Callable[[], None] | None = None,
    ) -> None:
        self.status_code = response.status_code
        self.headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower()
            in {
                "content-type",
                "cache-control",
                "x-request-id",
                "request-id",
            }
        }
        self.content_type = response.headers.get(
            "content-type",
            "application/octet-stream",
        )
        self._response = response
        self._client = client
        self._finalize = finalize
        self._max_usage_bytes = max_usage_bytes
        self._finished = False
        self._check_active = check_active or (lambda: None)

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        """逐块透传；正文只在当前调用内用于抽取 Usage，不写普通日志。"""

        observed: bytearray | None = bytearray()

        def observe(chunk: bytes) -> None:
            nonlocal observed
            if observed is None:
                return
            if len(observed) + len(chunk) > self._max_usage_bytes:
                # 大响应继续逐块转发，但不再为 Usage 解析保留整份正文。
                observed = None
                return
            observed.extend(chunk)

        try:
            self._check_active()
            if self._response.is_stream_consumed:
                chunk = self._response.content
                observe(chunk)
                if chunk:
                    yield chunk
            else:
                # 使用解压后的字节；Relay 不透传 Content-Encoding，二者必须一致。
                chunks = self._response.aiter_bytes()
                while True:
                    self._check_active()
                    pending = asyncio.create_task(anext(chunks))
                    try:
                        while not pending.done():
                            await asyncio.wait({pending}, timeout=0.25)
                            # Provider暂不出块时也复核撤销，不能无限保留连接。
                            self._check_active()
                        try:
                            chunk = pending.result()
                        except StopAsyncIteration:
                            break
                    finally:
                        if not pending.done():
                            pending.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await pending
                    observe(chunk)
                    yield chunk
        except BaseException:
            # 被撤销或中断的流不能把部分正文误计为完整、已知的Provider用量。
            observed = None
            raise
        finally:
            await self._finish(
                bytes(observed) if observed is not None else b""
            )

    async def aclose(self) -> None:
        """下游提前断开时也关闭 Provider 连接并记未知用量。"""

        await self._finish(b"")

    async def _finish(self, body: bytes) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._finalize(body)
        finally:
            try:
                await self._response.aclose()
            finally:
                await self._client.aclose()
