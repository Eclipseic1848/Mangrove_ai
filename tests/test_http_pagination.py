# -*- coding: utf-8 -*-
"""分页状态机测试（plan.md 第 8.2 节 / Phase 2 Task 8）。

覆盖：
- PageNumberPager：推进、空页停止、max_pages 停止、重复响应停止
- OffsetPager：offset 推进、返回数 < limit 停止
- CursorPager：cursor 推进、末页停止、cursor 不推进停止
- LinkHeaderPager：解析 rel=next、无 next 停止
- parse_link_header_next 单元
- checkpoint/restore 断点续跑
"""
from __future__ import annotations

import pytest

from src.connectors.pagination import (
    CursorPager,
    LinkHeaderPager,
    OffsetPager,
    PageNumberPager,
    PageResponse,
    parse_link_header_next,
)

URL = "https://api.example.com/items"


def make_resp(body: bytes, headers=None) -> PageResponse:
    return PageResponse(
        status=200, body=body, headers=headers or {}, url=URL
    )


class TestPageNumberPager:
    def test_first_request_page_1(self):
        pager = PageNumberPager(URL, per_page=20, start_page=1)
        req = pager.first_request()
        assert req.method == "GET"
        assert req.url == URL
        assert req.params["page"] == 1
        assert req.params["per_page"] == 20

    def test_advance_to_next_page(self):
        pager = PageNumberPager(URL, per_page=2, max_pages=10)
        pager.first_request()
        resp = make_resp(b'[{"id":1},{"id":2}]')  # 非空
        nxt = pager.next_request(resp)
        assert nxt is not None
        assert nxt.params["page"] == 2

    def test_stop_on_empty_page(self):
        pager = PageNumberPager(URL, per_page=2, max_pages=10)
        pager.first_request()
        nxt = pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))
        assert nxt is not None
        # 第 2 页空数组 -> 停止
        nxt2 = pager.next_request(make_resp(b"[]"))
        assert nxt2 is None
        assert "空页" in pager.last_stop_reason

    def test_stop_on_empty_object(self):
        pager = PageNumberPager(URL, per_page=2, max_pages=10)
        pager.first_request()
        pager.next_request(make_resp(b'[{"id":1}]'))
        # {"data": []} 也视为空
        nxt = pager.next_request(make_resp(b'{"data":[],"total":0}'))
        assert nxt is None

    def test_max_pages_limit(self):
        pager = PageNumberPager(URL, per_page=2, max_pages=2, stop_on_empty=False)
        pager.first_request()
        nxt1 = pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))
        assert nxt1 is not None  # page 2
        nxt2 = pager.next_request(make_resp(b'[{"id":3},{"id":4}]'))
        assert nxt2 is None  # 达到 max_pages
        assert "最大页数" in pager.last_stop_reason

    def test_duplicate_response_stops(self):
        pager = PageNumberPager(URL, per_page=2, max_pages=10, stop_on_empty=False)
        pager.first_request()
        resp = make_resp(b'[{"id":1},{"id":2}]')
        nxt1 = pager.next_request(resp)
        assert nxt1 is not None
        # 相同响应再次出现 -> 死循环防护
        nxt2 = pager.next_request(resp)
        assert nxt2 is None
        assert "重复" in pager.last_stop_reason

    def test_custom_param_names(self):
        pager = PageNumberPager(
            URL, page_param="pageNum", per_page_param="pageSize", per_page=50
        )
        req = pager.first_request()
        assert req.params["pageNum"] == 1
        assert req.params["pageSize"] == 50

    def test_extra_params_merged(self):
        pager = PageNumberPager(URL, per_page=10, params={"filter": "active"})
        req = pager.first_request()
        assert req.params["filter"] == "active"
        assert req.params["page"] == 1


class TestOffsetPager:
    def test_first_request_offset_0(self):
        pager = OffsetPager(URL, limit=100, start_offset=0)
        req = pager.first_request()
        assert req.params["offset"] == 0
        assert req.params["limit"] == 100

    def test_advance_offset(self):
        pager = OffsetPager(URL, limit=2, max_pages=10)
        pager.first_request()
        nxt = pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))  # 2 == limit
        assert nxt is not None
        assert nxt.params["offset"] == 2

    def test_stop_when_less_than_limit(self):
        pager = OffsetPager(URL, limit=2, max_pages=10)
        pager.first_request()
        pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))
        # 返回 1 < 2 -> 末页
        nxt = pager.next_request(make_resp(b'[{"id":3}]'))
        assert nxt is None
        assert "末页" in pager.last_stop_reason

    def test_stop_on_empty(self):
        pager = OffsetPager(URL, limit=2, max_pages=10)
        pager.first_request()
        nxt = pager.next_request(make_resp(b"[]"))  # 0 < 2
        assert nxt is None

    def test_custom_param_names(self):
        pager = OffsetPager(
            URL, offset_param="skip", limit_param="take", limit=50
        )
        req = pager.first_request()
        assert req.params["skip"] == 0
        assert req.params["take"] == 50


class TestCursorPager:
    def test_first_request_no_cursor(self):
        pager = CursorPager(URL, cursor_param="cursor")
        req = pager.first_request()
        assert "cursor" not in req.params

    def test_advance_with_cursor(self):
        pager = CursorPager(URL, cursor_param="cursor")
        pager.first_request()
        resp = make_resp(b'{"data":[1,2],"next_cursor":"abc"}')
        nxt = pager.next_request(resp)
        assert nxt is not None
        assert nxt.params["cursor"] == "abc"

    def test_stop_on_null_cursor(self):
        pager = CursorPager(URL)
        pager.first_request()
        pager.next_request(make_resp(b'{"next_cursor":"abc"}'))
        # 末页 cursor 为 null
        nxt = pager.next_request(make_resp(b'{"next_cursor":null}'))
        assert nxt is None
        assert "末页" in pager.last_stop_reason

    def test_stop_on_empty_cursor(self):
        pager = CursorPager(URL)
        pager.first_request()
        pager.next_request(make_resp(b'{"next_cursor":"abc"}'))
        nxt = pager.next_request(make_resp(b'{"next_cursor":""}'))
        assert nxt is None

    def test_stop_when_cursor_not_advancing(self):
        pager = CursorPager(URL)
        pager.first_request()
        nxt1 = pager.next_request(make_resp(b'{"next_cursor":"abc","data":[1]}'))
        assert nxt1 is not None
        assert nxt1.params["cursor"] == "abc"
        # 不同 body 但 cursor 相同 -> 不推进，停止（不先触发响应重复）
        nxt2 = pager.next_request(make_resp(b'{"next_cursor":"abc","data":[2]}'))
        assert nxt2 is None
        assert "未推进" in pager.last_stop_reason

    def test_initial_cursor(self):
        pager = CursorPager(URL, initial_cursor="start", cursor_param="page_token")
        req = pager.first_request()
        assert req.params["page_token"] == "start"

    def test_alternative_cursor_keys(self):
        # next_page_token 也能识别
        pager = CursorPager(URL)
        pager.first_request()
        nxt = pager.next_request(make_resp(b'{"next_page_token":"xyz"}'))
        assert nxt is not None
        assert nxt.params["cursor"] == "xyz"


class TestLinkHeaderPager:
    def test_first_request_uses_configured_url(self):
        pager = LinkHeaderPager(URL, params={"per_page": 10})
        req = pager.first_request()
        assert req.url == URL
        assert req.params["per_page"] == 10

    def test_follow_next_link(self):
        pager = LinkHeaderPager(URL, max_pages=10)
        pager.first_request()
        resp = make_resp(
            b'[{"id":1}]',
            headers={"link": '<https://api.example.com/items?page=2>; rel="next"'},
        )
        nxt = pager.next_request(resp)
        assert nxt is not None
        assert nxt.url == "https://api.example.com/items?page=2"

    def test_stop_when_no_next_link(self):
        pager = LinkHeaderPager(URL, max_pages=10)
        pager.first_request()
        pager.next_request(
            make_resp(
                b'[{"id":1}]',
                headers={"link": '<https://api.example.com/items?page=2>; rel="next"'},
            )
        )
        # 无 next -> 末页
        nxt = pager.next_request(make_resp(b'[{"id":2}]'))
        assert nxt is None
        assert "末页" in pager.last_stop_reason

    def test_multiple_links_picks_next(self):
        pager = LinkHeaderPager(URL, max_pages=10)
        pager.first_request()
        resp = make_resp(
            b'[]',
            headers={
                "link": '<https://x/p1>; rel="prev", <https://x/p2>; rel="next"'
            },
        )
        nxt = pager.next_request(resp)
        assert nxt is not None
        assert nxt.url == "https://x/p2"

    def test_case_insensitive_rel(self):
        # rel=NEXT 也应识别
        pager = LinkHeaderPager(URL, max_pages=10)
        pager.first_request()
        resp = make_resp(
            b'[]', headers={"link": '<https://x/p2>; rel="NEXT"'}
        )
        nxt = pager.next_request(resp)
        assert nxt is not None


class TestParseLinkHeader:
    def test_simple_next(self):
        assert (
            parse_link_header_next('<https://x/p2>; rel="next"')
            == "https://x/p2"
        )

    def test_next_without_quotes(self):
        assert parse_link_header_next("<https://x/p2>; rel=next") == "https://x/p2"

    def test_multiple_links(self):
        assert (
            parse_link_header_next(
                '<https://x/p1>; rel="prev", <https://x/p2>; rel="next"'
            )
            == "https://x/p2"
        )

    def test_no_next(self):
        assert parse_link_header_next('<https://x/p1>; rel="prev"') is None

    def test_empty(self):
        assert parse_link_header_next("") is None
        assert parse_link_header_next(None) is None  # type: ignore[arg-type]


class TestCheckpointRestore:
    def test_page_number_checkpoint(self):
        pager = PageNumberPager(URL, start_page=1, max_pages=10)
        pager.first_request()
        pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))  # 推进到 page 2
        state = pager.checkpoint()
        assert state["current_page"] == 2
        assert state["page_no"] == 1

    def test_page_number_restore(self):
        pager = PageNumberPager(URL, start_page=1, max_pages=10)
        pager.first_request()
        pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))
        state = pager.checkpoint()

        # 新 pager 从 checkpoint 恢复，应从 page 2 继续
        restored = PageNumberPager(URL, start_page=1, max_pages=10)
        restored.restore(state)
        req = restored.first_request()
        assert req.params["page"] == 2

    def test_offset_checkpoint_restore(self):
        pager = OffsetPager(URL, limit=2, start_offset=0, max_pages=10)
        pager.first_request()
        pager.next_request(make_resp(b'[{"id":1},{"id":2}]'))  # offset -> 2
        state = pager.checkpoint()
        assert state["current_offset"] == 2

        restored = OffsetPager(URL, limit=2, max_pages=10)
        restored.restore(state)
        req = restored.first_request()
        assert req.params["offset"] == 2

    def test_cursor_checkpoint_restore(self):
        pager = CursorPager(URL)
        pager.first_request()
        pager.next_request(make_resp(b'{"next_cursor":"abc"}'))
        state = pager.checkpoint()
        assert state["current_cursor"] == "abc"

        restored = CursorPager(URL)
        restored.restore(state)
        req = restored.first_request()
        assert req.params["cursor"] == "abc"

    def test_seen_hashes_restored(self):
        # 恢复后已见过的响应哈希应保留，再次出现即停
        pager = PageNumberPager(URL, max_pages=10, stop_on_empty=False)
        pager.first_request()
        resp = make_resp(b'[{"id":1},{"id":2}]')
        pager.next_request(resp)
        state = pager.checkpoint()

        restored = PageNumberPager(URL, max_pages=10, stop_on_empty=False)
        restored.restore(state)
        restored.first_request()  # 模拟恢复后继续
        # 同一响应再次出现应立即停（重复检测）
        nxt = restored.next_request(resp)
        assert nxt is None
        assert "重复" in restored.last_stop_reason

    def test_link_header_checkpoint_restore(self):
        pager = LinkHeaderPager(URL, max_pages=10)
        pager.first_request()
        pager.next_request(
            make_resp(
                b'[]',
                headers={"link": '<https://x/p2>; rel="next"'},
            )
        )
        state = pager.checkpoint()
        assert state["next_url"] == "https://x/p2"

        restored = LinkHeaderPager(URL, max_pages=10)
        restored.restore(state)
        # next_url 恢复（供诊断；实际下次请求由 Link 头驱动）
        assert restored._next_url == "https://x/p2"
