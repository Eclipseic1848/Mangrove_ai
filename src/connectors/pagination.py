# -*- coding: utf-8 -*-
"""分页状态机（plan.md 第 8.2 节 / Phase 2 Task 8）。

四种分页策略，统一 PaginationStrategy Protocol：
- PageNumberPager：page=N & per_page=K，空页或 max_pages 停止
- OffsetPager：offset=N & limit=K，返回数 < limit 或 max_pages 停止
- CursorPager：用响应中的 cursor 字段作下一页参数，cursor 为空或不推进停止
- LinkHeaderPager：解析 RFC 5988 Link 头 rel="next"，无 next 停止

通用停止条件（基类 _BasePager）：
- max_pages 强制上限
- 重复响应哈希（防死循环）
- 子类标记游标不推进

checkpoint()/restore() 支持断点续跑：状态可序列化、可恢复。

分页协议因 API 而异，无通用成熟库适配，用标准库实现（hashlib 校验、
正则解析 Link 头），不引入新依赖。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class PageRequest:
    """单页请求描述。"""
    method: str = "GET"
    url: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None


@dataclass
class PageResponse:
    """单页响应（已读 body）。"""
    status: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)
    url: str = ""  # 最终 URL（重定向后）


class PaginationStrategy(Protocol):
    """分页策略统一接口。"""
    def first_request(self) -> PageRequest: ...
    def next_request(self, response: PageResponse) -> Optional[PageRequest]: ...
    def checkpoint(self) -> Dict[str, Any]: ...
    def restore(self, state: Dict[str, Any]) -> None: ...


# Link 头 rel="next" 解析（RFC 5988 简化版：匹配 <url>; rel="next"）
_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel\s*=\s*"?next"?', re.IGNORECASE)


def parse_link_header_next(link_header: str) -> Optional[str]:
    """从 Link 头提取 rel="next" 的 URL，无则 None。"""
    if not link_header:
        return None
    match = _LINK_NEXT_RE.search(link_header)
    return match.group(1) if match else None


class _BasePager:
    """分页基类：通用停止条件（max_pages、重复响应哈希）。"""

    def __init__(self, *, max_pages: int = 1000) -> None:
        if max_pages < 1:
            raise ValueError("max_pages 必须 >= 1")
        self.max_pages = max_pages
        self._page_no = 0  # 已处理的页数
        self._seen_hashes: set[str] = set()
        # 上一页停止原因（供调用方诊断，None 表示仍在推进或正常结束）
        self.last_stop_reason: Optional[str] = None

    def _record_and_check(self, response: PageResponse) -> Optional[str]:
        """记录本页响应，返回停止原因或 None。"""
        self._page_no += 1
        digest = hashlib.sha256(response.body).hexdigest()
        if digest in self._seen_hashes:
            return "响应重复（疑似死循环）"
        self._seen_hashes.add(digest)
        # max_pages：已处理页数达到上限即停（max_pages=N 表示最多取 N 页）
        if self._page_no >= self.max_pages:
            return f"达到最大页数 {self.max_pages}"
        return None

    def _stop(self, reason: str) -> None:
        self.last_stop_reason = reason

    def checkpoint(self) -> Dict[str, Any]:
        return {
            "page_no": self._page_no,
            "seen_hashes": sorted(self._seen_hashes),
            "last_stop_reason": self.last_stop_reason,
        }

    def restore(self, state: Dict[str, Any]) -> None:
        self._page_no = state.get("page_no", 0)
        self._seen_hashes = set(state.get("seen_hashes", []))
        self.last_stop_reason = state.get("last_stop_reason")


def _default_is_empty(response: PageResponse) -> bool:
    """默认空页判断：body 为空，或 JSON 为空数组/空对象。"""
    if not response.body:
        return True
    text = response.body.decode("utf-8", errors="replace").strip()
    if not text:
        return True
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if isinstance(data, list):
        return len(data) == 0
    if isinstance(data, dict):
        # 常见模式：{"data": [...], "total": 0} 或空对象
        if "data" in data and isinstance(data["data"], list):
            return len(data["data"]) == 0
        return len(data) == 0
    return False


class PageNumberPager(_BasePager):
    """页码分页：page=N & per_page=K。"""

    def __init__(
        self,
        url: str,
        *,
        page_param: str = "page",
        per_page_param: str = "per_page",
        per_page: int = 100,
        start_page: int = 1,
        max_pages: int = 1000,
        stop_on_empty: bool = True,
        is_empty: Optional[Callable[[PageResponse], bool]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(max_pages=max_pages)
        self.url = url
        self.page_param = page_param
        self.per_page_param = per_page_param
        self.per_page = per_page
        self._current_page = start_page
        self.start_page = start_page
        self.stop_on_empty = stop_on_empty
        self._is_empty = is_empty or _default_is_empty
        self._extra_params = dict(params or {})
        self._headers = dict(headers or {})

    def _make_request(self) -> PageRequest:
        params = dict(self._extra_params)
        params[self.page_param] = self._current_page
        params[self.per_page_param] = self.per_page
        return PageRequest(
            method="GET", url=self.url, params=params, headers=dict(self._headers)
        )

    def first_request(self) -> PageRequest:
        return self._make_request()

    def next_request(self, response: PageResponse) -> Optional[PageRequest]:
        reason = self._record_and_check(response)
        if reason:
            self._stop(reason)
            return None
        if self.stop_on_empty and self._is_empty(response):
            self._stop("空页（无更多数据）")
            return None
        self._current_page += 1
        return self._make_request()

    def checkpoint(self) -> Dict[str, Any]:
        return {
            **super().checkpoint(),
            "current_page": self._current_page,
        }

    def restore(self, state: Dict[str, Any]) -> None:
        super().restore(state)
        self._current_page = state.get("current_page", self.start_page)


class OffsetPager(_BasePager):
    """偏移分页：offset=N & limit=K，返回数 < limit 停止。"""

    def __init__(
        self,
        url: str,
        *,
        offset_param: str = "offset",
        limit_param: str = "limit",
        limit: int = 100,
        start_offset: int = 0,
        max_pages: int = 1000,
        count_records: Optional[Callable[[PageResponse], int]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(max_pages=max_pages)
        self.url = url
        self.offset_param = offset_param
        self.limit_param = limit_param
        self.limit = limit
        self._current_offset = start_offset
        self.start_offset = start_offset
        self._count_records = count_records or _default_count_records
        self._extra_params = dict(params or {})
        self._headers = dict(headers or {})

    def _make_request(self) -> PageRequest:
        params = dict(self._extra_params)
        params[self.offset_param] = self._current_offset
        params[self.limit_param] = self.limit
        return PageRequest(
            method="GET", url=self.url, params=params, headers=dict(self._headers)
        )

    def first_request(self) -> PageRequest:
        return self._make_request()

    def next_request(self, response: PageResponse) -> Optional[PageRequest]:
        reason = self._record_and_check(response)
        if reason:
            self._stop(reason)
            return None
        count = self._count_records(response)
        if count < self.limit:
            self._stop(f"返回 {count} < limit {self.limit}（末页）")
            return None
        self._current_offset += self.limit
        return self._make_request()

    def checkpoint(self) -> Dict[str, Any]:
        return {
            **super().checkpoint(),
            "current_offset": self._current_offset,
        }

    def restore(self, state: Dict[str, Any]) -> None:
        super().restore(state)
        self._current_offset = state.get("current_offset", self.start_offset)


def _default_count_records(response: PageResponse) -> int:
    """默认记录计数：JSON 数组长度，或 {"data": [...]} 的 data 长度。"""
    if not response.body:
        return 0
    text = response.body.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            return len(data["data"])
        if "items" in data and isinstance(data["items"], list):
            return len(data["items"])
    return 0


def _default_extract_cursor(response: PageResponse) -> Optional[str]:
    """默认 cursor 提取：{"next_cursor": "..."} 或 {"cursor": "..."}。"""
    if not response.body:
        return None
    text = response.body.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("next_cursor", "nextCursor", "cursor", "next_page_token"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class CursorPager(_BasePager):
    """游标分页：响应中的 cursor 字段作下一页参数，空或不推进停止。"""

    def __init__(
        self,
        url: str,
        *,
        cursor_param: str = "cursor",
        extract_cursor: Optional[Callable[[PageResponse], Optional[str]]] = None,
        initial_cursor: Optional[str] = None,
        max_pages: int = 1000,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(max_pages=max_pages)
        self.url = url
        self.cursor_param = cursor_param
        self._extract_cursor = extract_cursor or _default_extract_cursor
        self._current_cursor: Optional[str] = initial_cursor
        self._initial_cursor = initial_cursor
        self._extra_params = dict(params or {})
        self._headers = dict(headers or {})

    def _make_request(self) -> PageRequest:
        params = dict(self._extra_params)
        if self._current_cursor:
            params[self.cursor_param] = self._current_cursor
        return PageRequest(
            method="GET", url=self.url, params=params, headers=dict(self._headers)
        )

    def first_request(self) -> PageRequest:
        return self._make_request()

    def next_request(self, response: PageResponse) -> Optional[PageRequest]:
        reason = self._record_and_check(response)
        if reason:
            self._stop(reason)
            return None
        new_cursor = self._extract_cursor(response)
        if not new_cursor:
            self._stop("cursor 为空（末页）")
            return None
        if new_cursor == self._current_cursor:
            self._stop("cursor 未推进（疑似死循环）")
            return None
        self._current_cursor = new_cursor
        return self._make_request()

    def checkpoint(self) -> Dict[str, Any]:
        return {
            **super().checkpoint(),
            "current_cursor": self._current_cursor,
        }

    def restore(self, state: Dict[str, Any]) -> None:
        super().restore(state)
        self._current_cursor = state.get("current_cursor", self._initial_cursor)


class LinkHeaderPager(_BasePager):
    """Link 头分页：解析 RFC 5988 rel="next"，无 next 停止。"""

    def __init__(
        self,
        url: str,
        *,
        max_pages: int = 1000,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(max_pages=max_pages)
        self.url = url
        self._next_url: Optional[str] = None  # 下一页绝对 URL（来自 Link 头）
        self._extra_params = dict(params or {})
        self._headers = dict(headers or {})

    def _make_request(self, url: str) -> PageRequest:
        # Link 头给的 URL 已含分页参数，直接用；首页用配置 url + params
        return PageRequest(
            method="GET", url=url, params={}, headers=dict(self._headers)
        )

    def first_request(self) -> PageRequest:
        return PageRequest(
            method="GET",
            url=self.url,
            params=dict(self._extra_params),
            headers=dict(self._headers),
        )

    def next_request(self, response: PageResponse) -> Optional[PageRequest]:
        reason = self._record_and_check(response)
        if reason:
            self._stop(reason)
            return None
        link = response.headers.get("link") or response.headers.get("Link")
        next_url = parse_link_header_next(link) if link else None
        if not next_url:
            self._stop("Link 头无 rel=next（末页）")
            return None
        self._next_url = next_url
        return self._make_request(next_url)

    def checkpoint(self) -> Dict[str, Any]:
        return {
            **super().checkpoint(),
            "next_url": self._next_url,
        }

    def restore(self, state: Dict[str, Any]) -> None:
        super().restore(state)
        self._next_url = state.get("next_url")


__all__ = [
    "CursorPager",
    "LinkHeaderPager",
    "OffsetPager",
    "PageNumberPager",
    "PageRequest",
    "PageResponse",
    "PaginationStrategy",
    "parse_link_header_next",
]
