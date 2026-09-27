"""离线验证评论分页预算和父子关联，不访问小红书。"""
import asyncio

from scripts.mediacrawler_xhs import bounded_comments


def test_replies_do_not_consume_root_budget_and_pagination_is_bounded():
    class Client:
        roots = 0
        replies = 0

        async def get_note_comments(self, **kwargs):
            self.roots += 1
            return {"comments": [{"id": f"root-{self.roots}", "sub_comments": [],
                                  "sub_comment_has_more": True, "sub_comment_cursor": "first"}],
                    "has_more": True, "cursor": str(self.roots)}

        async def get_note_sub_comments(self, **kwargs):
            self.replies += 1
            return {"comments": [{"id": f"{kwargs['root_comment_id']}-reply-{self.replies}"}],
                    "has_more": True, "cursor": "unchanged"}

    client = Client()
    captured = []

    async def save(note_id, rows):
        assert note_id == "note"
        captured.extend(rows)

    rows, status = asyncio.run(bounded_comments(client, "note", "secret", 0, save, 3))
    assert client.roots == 3
    assert client.replies == 6
    assert len(rows) == len(captured) == 9
    assert status["top_level_count"] == 3
    assert status["truncated"] is True
    assert "reply_cursor_stalled" in status["reasons"]
    assert rows[1]["target_comment"]["id"] == "root-1"
    assert "secret" not in str(status)


def test_failed_reply_keeps_root_and_reports_partial():
    class Client:
        async def get_note_comments(self, **kwargs):
            return {"comments": [{"id": "root", "sub_comment_has_more": True}], "has_more": False}

        async def get_note_sub_comments(self, **kwargs):
            raise RuntimeError("private-token")

    rows, status = asyncio.run(bounded_comments(Client(), "note", "secret", 0, None, 20))
    assert [row["id"] for row in rows] == ["root"]
    assert status["truncated"] and "reply_fetch_failed" in status["reasons"]
    assert "private-token" not in str(status)


def test_comment_deadline_keeps_saved_rows_without_waiting_for_slow_reply():
    import time
    class Client:
        async def get_note_comments(self, **kwargs):
            return {"comments": [{"id": "root", "sub_comment_has_more": True},
                                 {"id": "second", "sub_comments": [{"id": "embedded"}]}], "has_more": False}
        async def get_note_sub_comments(self, **kwargs):
            await asyncio.sleep(30)
    captured = []
    async def save(note_id, rows):
        captured.extend(rows)
    async def run():
        return await asyncio.wait_for(bounded_comments(
            Client(), "note", "secret", 0, save, 20, deadline=time.monotonic() + 0.05), timeout=1)
    rows, status = asyncio.run(run())
    assert [r["id"] for r in rows] == [r["id"] for r in captured] == ["root", "second", "embedded"]
    assert status["top_level_count"] == 2 and status["truncated"]
    assert "comment_time_budget" in status["reasons"]


def test_interrupted_comment_write_preserves_previous_json(tmp_path, monkeypatch):
    import json
    import pytest
    from pathlib import Path
    from scripts.mediacrawler_xhs import atomic_json_item
    target = tmp_path / "comments.json"
    target.write_text('[{"id":"saved"}]', encoding="utf-8")
    class Writer:
        lock = asyncio.Lock()
        def _get_file_path(self, file_type, item_type):
            return str(target)
    write = Path.write_text
    def interrupted(path, data, **kwargs):
        if path.suffix == ".tmp":
            write(path, data[:5], **kwargs)
            raise asyncio.CancelledError()
        return write(path, data, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", interrupted)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(atomic_json_item(Writer(), {"id": "new"}, "comments"))
    assert json.loads(target.read_text(encoding="utf-8")) == [{"id": "saved"}]
    asyncio.run(atomic_json_item(Writer(), {"id": "next"}, "comments"))
    assert json.loads(target.read_text(encoding="utf-8")) == [{"id": "saved"}, {"id": "next"}]


@__import__("pytest").mark.parametrize("limit,returned", [(1, 20), (5, 2)])
def test_search_adapter_caps_details_before_upstream_fanout(tmp_path, monkeypatch, limit, returned):
    import sys
    import types
    from scripts import mediacrawler_xhs as adapter
    class Client:
        calls = 0
        async def get_note_by_keyword(self, **kwargs):
            self.calls += 1
            return {"items": [{"id": "ad", "model_type": "rec_query"}] +
                    [{"id": str(n), "model_type": "note"} for n in range(returned)], "has_more": False}
    config = types.ModuleType("config")
    config.CRAWLER_MAX_NOTES_COUNT = 20
    core = types.ModuleType("core")
    core.XiaoHongShuClient = Client
    xhs = types.ModuleType("media_platform.xhs")
    xhs.core = core
    writer = types.ModuleType("tools.async_file_writer")
    writer.AsyncFileWriter = type("Writer", (), {})
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setitem(sys.modules, "media_platform.xhs", xhs)
    monkeypatch.setitem(sys.modules, "tools.async_file_writer", writer)
    monkeypatch.setattr(sys, "argv", ["adapter", "--save_data_path", str(tmp_path), "--crawler_max_notes_count", str(limit)])
    monkeypatch.delenv("MANGROVE_XHS_SORT", raising=False)
    monkeypatch.delenv("MANGROVE_XHS_NOTE_TYPE", raising=False)
    async def search():
        client = Client()
        first = await client.get_note_by_keyword(keyword="甲", page=1)
        assert len(first["items"]) == min(limit, returned)
        assert first["items"][0]["id"] == "0"
        assert first["has_more"] is True  # 上游须处理已经返回的末页内容。
        second = await client.get_note_by_keyword(keyword="甲", page=2)
        assert second["items"] == [] and second["has_more"] is False
        assert client.calls == 1
        other = await client.get_note_by_keyword(keyword="乙", page=1)
        assert len(other["items"]) == min(limit, returned)
    monkeypatch.setattr(adapter.runpy, "run_path", lambda *a, **kw: asyncio.run(search()))
    adapter.main()
