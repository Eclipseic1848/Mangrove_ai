"""DeepSeek 独立 Search / Fetch；凭证由已授权调用方注入，未知请求不重发。"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from src.api.execution import execution_checkpoint
from src.connectors.http_security import HostResolver, HttpSecurityGuard, SsrfError

from ._net import smart_client

_BASE_URL = "https://api.deepseek.com/v1/tools/"
_STATUS_CODES = {
    400: "DEEPSEEK_INVALID_REQUEST", 401: "DEEPSEEK_AUTH_FAILED",
    402: "DEEPSEEK_INSUFFICIENT_BALANCE", 403: "DEEPSEEK_FETCH_BLOCKED",
    404: "DEEPSEEK_CONTENT_UNAVAILABLE", 408: "DEEPSEEK_REQUEST_TIMEOUT",
    413: "DEEPSEEK_REQUEST_TOO_LARGE", 429: "DEEPSEEK_RATE_LIMITED",
}


class DeepSeekToolsError(RuntimeError):
    """只暴露固定错误代码，禁止传播上游可能回显凭证的正文与异常。"""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _query(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} 必须为 1～128 个非全空白字符")
    return value


def _public_url(url: str) -> None:
    if not isinstance(url, str) or not url or len(url) > 8192 or any(ord(c) < 33 for c in url):
        raise ValueError("Fetch URL 必须为不超过 8192 字符的公开 HTTP/S 地址")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError()
        if "@" in parts.netloc or parts.fragment or "\\" in url:
            raise ValueError()
        parts.port
        for key, _ in parse_qsl(parts.query, keep_blank_values=True):
            name = key.casefold().replace("_", "").replace("-", "")
            if name in {"key", "apikey", "auth", "sign", "passwd", "sid", "jwt", "ticket"} or any(
                part in name for part in ("token", "password", "secret", "signature", "session", "cookie", "credential", "authorization")
            ):
                raise ValueError()
    except ValueError:
        # URL 会发给第三方；不把被拒地址或其凭证带入诊断。
        raise ValueError("Fetch 仅允许无凭证、无敏感查询参数的公开 HTTP/S 地址") from None


class DeepSeekToolsClient:
    """每个任务单独实例化；calls 仅统计请求结果，不保存关键词、URL 或凭证。"""

    def __init__(
        self, api_key: str, *, timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: HostResolver | None = None, required_authorization: bool = False,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise ValueError("必须由已授权调用方提供有效 API Key")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("请求超时必须大于 0 且不超过 30 秒")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._resolver = resolver
        self._required_authorization = required_authorization
        self.calls: list[dict[str, Any]] = []

    async def search(
        self, query: str, *, limit: int = 10, need_content: bool = False,
        ori_question: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": _query(query, "query")}
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit 必须为 1～50 的整数")
        if type(need_content) is not bool:
            raise ValueError("need_content 必须为布尔值")
        payload.update(count=limit, need_content=need_content)
        if ori_question is not None:
            payload["ori_question"] = _query(ori_question, "ori_question")
        return await self._request("search", payload)

    async def fetch(self, url: str) -> dict[str, Any]:
        _public_url(url)
        execution_checkpoint(required=self._required_authorization)
        try:
            # 检查本机 DNS 的全部地址；供应商后续 DNS/跳转不可本机 pin，结果仍须来源核验。
            await asyncio.to_thread(HttpSecurityGuard(resolver=self._resolver).validate, url)
        except (SsrfError, ValueError, OSError):
            raise ValueError("Fetch 目标未通过公开网络地址校验") from None
        return await self._request("fetch", {"url": url})

    async def _request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        execution_checkpoint(required=self._required_authorization)
        started = time.monotonic()
        call: dict[str, Any] = {
            "operation": operation, "outcome": "unknown", "duration_ms": 0,
            "result_count": 0, "billable_calls": 0, "billing_unknown": True,
        }
        self.calls.append(call)
        try:
            kwargs: dict[str, Any] = {"timeout": self._timeout, "follow_redirects": False}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            async with smart_client(_BASE_URL + operation, **kwargs) as client:
                response = await client.post(
                    _BASE_URL + operation, json=payload,
                    headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                )
            call["status_code"] = response.status_code
            if response.status_code != 200:
                call.update(outcome="error", billing_unknown=False)
                code = _STATUS_CODES.get(response.status_code, "DEEPSEEK_HTTP_ERROR")
                if 300 <= response.status_code < 400:
                    code = "DEEPSEEK_REDIRECT_REJECTED"
                raise DeepSeekToolsError(code, status_code=response.status_code)
            try:
                data = response.json()
            except ValueError:
                raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID") from None
            clean = self._response(operation, data)
            count = len(clean["results"]) if operation == "search" else int(bool(clean["content"].strip()))
            call.update(outcome="success" if count else "empty", result_count=count,
                        billable_calls=int(bool(count)), billing_unknown=False)
            if operation == "fetch" and not count:
                raise DeepSeekToolsError("DEEPSEEK_CONTENT_EMPTY")
            return clean
        except httpx.TimeoutException:
            raise DeepSeekToolsError("DEEPSEEK_TIMEOUT_UNKNOWN") from None
        except httpx.RequestError:
            raise DeepSeekToolsError("DEEPSEEK_NETWORK_UNKNOWN") from None
        finally:
            call["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            execution_checkpoint(required=self._required_authorization)

    @staticmethod
    def _response(operation: str, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or "error" in data:
            raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID")
        if operation == "fetch":
            if not all(isinstance(data.get(field), str) for field in ("title", "content")):
                raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID")
            # 此正文是第三方 Markdown；调用方不得标作原站 HTML/原始响应字节。
            return {field: data[field] for field in ("title", "content")}
        if not isinstance(data.get("results"), list):
            raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID")
        results = []
        for item in data["results"]:
            fields = ("title", "url", "snippet", "summary", "content")
            if not isinstance(item, dict) or not all(isinstance(item.get(field), str) for field in fields):
                raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID")
            clean = {field: item[field] for field in fields}
            for field in ("site_name", "site_icon", "date"):
                if item.get(field) is not None and not isinstance(item[field], str):
                    raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID")
                clean[field] = item.get(field)
            authority, score = item.get("authority"), item.get("rerank_score")
            if type(authority) is not int or not -1 <= authority <= 4 or type(score) not in (int, float) or not 0 <= score <= 3:
                raise DeepSeekToolsError("DEEPSEEK_RESPONSE_INVALID")
            clean.update(authority=authority, rerank_score=score)
            results.append(clean)
        return {"results": results}
