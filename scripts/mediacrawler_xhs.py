"""仅在小红书附带评论模式使用的适配入口；不修改外部仓库。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
import time


async def bounded_comments(client, note_id, xsec_token, crawl_interval, callback, max_count, *, deadline=None):
    """一级评论单独计数；每条最多 20 个回复、两页补取，显式记录截断。"""
    result, seen, reasons = [], set(), set()
    root_count, cursor, has_more = 0, "", True

    async def save(rows, parent="", limit=20):
        accepted = []
        for row in rows:
            key = str(row.get("id") or "")
            if not key or key in seen or len(accepted) >= limit:
                continue
            seen.add(key)
            row = dict(row, note_id=note_id)
            # 回复接口有时省略父 ID，不能把它误记为一级评论。
            if parent and not (row.get("target_comment") or {}).get("id"):
                row["target_comment"] = {"id": parent}
            accepted.append(row)
        if accepted and callback:
            await callback(note_id, accepted)
        result.extend(accepted)
        return accepted

    async def crawl():
        nonlocal root_count, cursor, has_more
        for _ in range(max_count):
            page = await client.get_note_comments(note_id=note_id, xsec_token=xsec_token, cursor=cursor)
            roots = await save(page.get("comments") or [], limit=max_count - root_count)
            root_count += len(roots)
            embedded_counts = {}
            # 已返回的回复先全部落盘，不能被首条评论的补页请求拖住。
            for root in roots:
                parent = str(root["id"])
                embedded = root.get("sub_comments") or []
                embedded_counts[parent] = len(await save(embedded, parent))
                if len(embedded) > 20:
                    reasons.add("reply_limit")
            for root in roots:
                parent = str(root["id"])
                reply_count = embedded_counts[parent]
                reply_more = bool(root.get("sub_comment_has_more"))
                reply_cursor = root.get("sub_comment_cursor") or ""
                for _ in range(2):
                    if not reply_more or reply_count >= 20:
                        break
                    await asyncio.sleep(crawl_interval)
                    try:
                        sub = await client.get_note_sub_comments(
                            note_id=note_id, root_comment_id=parent, xsec_token=xsec_token,
                            num=10, cursor=reply_cursor,
                        )
                    except Exception:
                        # 一条回复失败不抹掉已采一级评论；取消仍向上传播。
                        reasons.add("reply_fetch_failed")
                        break
                    raw = sub.get("comments") or []
                    replies = await save(raw, parent, 20 - reply_count)
                    reply_count += len(replies)
                    reply_more = bool(sub.get("has_more"))
                    if len(raw) > len(replies):
                        reasons.add("reply_limit_or_duplicates")
                    next_cursor = sub.get("cursor") or ""
                    if reply_more and (not replies or next_cursor == reply_cursor):
                        reasons.add("reply_cursor_stalled")
                        break
                    reply_cursor = next_cursor
                if reply_more:
                    reasons.add("reply_limit")
            has_more = bool(page.get("has_more"))
            if len(page.get("comments") or []) > len(roots):
                reasons.add("top_level_limit_or_duplicates")
            if not has_more or root_count >= max_count:
                break
            next_cursor = page.get("cursor") or ""
            if not roots or next_cursor == cursor:
                reasons.add("top_level_cursor_stalled")
                break
            cursor = next_cursor
            await asyncio.sleep(crawl_interval)

    try:
        if deadline is None:
            await crawl()
        else:
            # 只取消本篇未完成的请求，已落盘的评论和父子关系仍返回给调用方。
            await asyncio.wait_for(crawl(), timeout=max(0, deadline - time.monotonic()))
    except asyncio.TimeoutError:
        reasons.add("comment_time_budget")
    if has_more and root_count >= max_count:
        reasons.add("top_level_limit")
    return result, {"top_level_count": root_count, "reply_count": len(result) - root_count,
                    "top_level_limit": max_count, "reply_limit_per_comment": 20,
                    "truncated": bool(reasons), "reasons": sorted(reasons)}


def _write_json(path, value):
    # 父进程硬超时时也只能看见完整的上一版数据。
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


async def atomic_json_item(self, item, item_type):
    path = Path(self._get_file_path("json", item_type))
    async with self.lock:
        # 本批数据有数量上限；临时文件提交前不让出执行权，软取消不会截断原文件。
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(existing, list):
            existing = [existing]
        _write_json(path, [*existing, item])


def main():
    # 子进程 cwd 是已配置的独立 MediaCrawler；只替换此进程的公开评论接缝。
    sys.path.insert(0, str(Path.cwd()))
    import config
    from media_platform.xhs import core
    from tools.async_file_writer import AsyncFileWriter

    AsyncFileWriter.write_single_item_to_json = atomic_json_item

    # 个人/平台 Cookie 由父进程选定；禁止串用持久浏览器或另一用户的 CDP 会话。
    config.SAVE_LOGIN_STATE = False
    config.ENABLE_CDP_MODE = False

    run_dir = Path(sys.argv[sys.argv.index("--save_data_path") + 1])
    statuses = {}
    timeout = float(os.environ.get("MANGROVE_XHS_TIMEOUT_SECONDS", "600"))
    # 在父进程硬截止前留出写盘、浏览器关闭和逐篇采集间隔的余量。
    comment_deadline = time.monotonic() + max(0, timeout - min(30, timeout * 0.1))
    sort = os.environ.get("MANGROVE_XHS_SORT")
    note_type = os.environ.get("MANGROVE_XHS_NOTE_TYPE")
    if sort or note_type:
        from media_platform.xhs.field import SearchNoteType, SearchSortType

    original_search = core.XiaoHongShuClient.get_note_by_keyword
    requested = int(sys.argv[sys.argv.index("--crawler_max_notes_count") + 1])
    remaining_by_keyword, pages, exhausted = {}, [], set()

    async def search(self, **kwargs):
        keyword = kwargs.get("keyword")
        remaining = remaining_by_keyword.get(keyword, requested)
        if remaining <= 0 or keyword in exhausted:
            return {"items": [], "has_more": False}
        if sort:
            kwargs["sort"] = SearchSortType(sort)
        if note_type:
            kwargs["note_type"] = SearchNoteType({"all": 0, "video": 1, "image": 2}[note_type])
        result = await original_search(self, **kwargs)
        raw = (result or {}).get("items") or []
        if not (result or {}).get("has_more"):
            exhausted.add(keyword)
        # 上游会把不足一页的额度提升到20；详情扇出前截断，才能遵守宿主冻结预算。
        items = [item for item in raw if item.get("model_type") not in ("rec_query", "hot_query")][:remaining]
        remaining_by_keyword[keyword] = remaining - len(items)
        pages.append({"keyword": keyword, "page": kwargs.get("page", 1),
                      "item_count": len(raw), "accepted_count": len(items),
                      "has_more": bool((result or {}).get("has_more"))})
        _write_json(run_dir / "search_coverage.json", {"pages": pages})
        # 上游在has_more=False时先退出；有内容的末页仍须处理，来源真值保留在coverage。
        return {**(result or {}), "items": items, "has_more": True if items else bool((result or {}).get("has_more"))}

    core.XiaoHongShuClient.get_note_by_keyword = search

    async def get_comments(self, note_id, xsec_token, crawl_interval=1.0, callback=None, max_count=10):
        remaining = max(1, config.CRAWLER_MAX_NOTES_COUNT - len(statuses))
        # 沿用既定频率和数量上限；按剩余篇数分摊时间，避免首篇回复耗尽整批预算。
        budget = max(0, (comment_deadline - time.monotonic()) / remaining - crawl_interval)
        rows, status = await bounded_comments(self, note_id, xsec_token, crawl_interval, callback, max_count,
                                             deadline=time.monotonic() + budget)
        statuses[note_id] = status
        _write_json(run_dir / "comment_coverage.json", statuses)
        return rows

    core.XiaoHongShuClient.get_note_all_comments = get_comments
    runpy.run_path(str(Path.cwd() / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
