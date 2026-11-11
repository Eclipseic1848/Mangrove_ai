"""仅在小红书附带评论模式使用的适配入口；不修改外部仓库。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import runpy
import sys
import time
from urllib.parse import quote, urlsplit

if __package__:
    from .mediacrawler_identity import _close_identity, seed_xhs_cookies
else:
    from mediacrawler_identity import _close_identity, seed_xhs_cookies


HOMEPAGE_TIMEOUT_SECONDS = 60.0
STARTUP_TIMEOUT_SECONDS = 90.0
DRIVER_SHUTDOWN_TIMEOUT_SECONDS = 5.0


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
        if deadline is None:
            # 不限时没有预算到期；底层请求失败应停止，不能伪装成可续采的时间缺口。
            raise
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


def _install_runtime_diagnostics(core, run_dir):
    """只记录阶段布尔值和计数；平台拒绝后本次子进程不再发送后续请求。"""
    state = {"browser_started": False, "search_started": False, "search_list_received": False,
             "search_item_count": 0, "note_detail_count": 0, "challenge_detected": False,
             "login_mode": "unknown", "runtime_phase": "browser_starting"}

    def login_mode():
        value = getattr(sys.modules.get("config"), "LOGIN_TYPE", None)
        return value if type(value) is str and value in {"cookie", "qrcode", "phone"} else "unknown"

    parent_reaps_startup = os.environ.get("MANGROVE_XHS_PARENT_REAPS_STARTUP") == "1"

    def notify_parent(name):
        # 父进程只检查标记是否存在，避免 Windows 读取活 JSON 阻塞原子替换。
        if parent_reaps_startup:
            with suppress(OSError):
                (run_dir / name).touch()

    def report(**values):
        state.update(values)
        _write_json(run_dir / "runtime_diagnostic.json", state)
        if values.get("runtime_phase") in {"browser_starting", "login_check", "search"}:
            notify_parent("startup-stage-" + values["runtime_phase"])

    report(login_mode=login_mode())
    crawler = getattr(core, "XiaoHongShuCrawler", None)
    unlimited = os.environ.get("MANGROVE_XHS_UNLIMITED_EXECUTION") == "1"
    startup_active = False
    startup_deadline = None
    track_startup = ((login_mode() == "cookie" or unlimited) and crawler is not None
                     and hasattr(crawler, "create_xhs_client") and hasattr(core, "async_playwright"))

    @asynccontextmanager
    async def startup_step(name, deadline):
        loop = asyncio.get_running_loop()
        started = loop.time()

        def save(status):
            steps = state.setdefault("startup_steps", {})
            steps[name] = {"status": status, "elapsed_ms": int((loop.time() - started) * 1000)}
            try:
                report(startup_steps=steps)
            except OSError:
                # 诊断写盘失败不能覆盖原有异常或取消。
                pass

        save("started")
        if name != "driver_shutdown":
            notify_parent("startup-stage-" + name)
        status = "completed"
        try:
            async with asyncio.timeout_at(deadline):
                if deadline is not None and loop.time() >= deadline:
                    raise TimeoutError()
                yield
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as error:
            kind = type(error)
            playwright_timeout = (kind.__module__ == "playwright._impl._errors" and kind.__name__ == "TimeoutError")
            status = "timeout" if isinstance(error, TimeoutError) or playwright_timeout else "failed"
            raise
        finally:
            # 先保存前置故障，再让上游进入驱动退出。
            save(status)

    def track(operation, step):
        async def run(*args, **kwargs):
            if not startup_active:
                return await operation(*args, **kwargs)
            deadline = startup_deadline
            if step == "homepage_navigation":
                if unlimited:
                    # 上游显式传入60秒；只覆盖本任务首页导航，保留错误与取消。
                    kwargs["timeout"] = 0
                else:
                    deadline = min(deadline, asyncio.get_running_loop().time() + HOMEPAGE_TIMEOUT_SECONDS)
            async with startup_step(step, deadline):
                result = await operation(*args, **kwargs)
                if step == "new_page":
                    result.goto = track(result.goto, "homepage_navigation")
                return result
        return run

    if track_startup:
        crawler.create_xhs_client = track(crawler.create_xhs_client, "create_client")
        original_playwright = core.async_playwright

        class PlaywrightWithBoundedExit:
            def __init__(self):
                self.manager = original_playwright()

            async def __aenter__(self):
                return await self.manager.__aenter__()

            async def __aexit__(self, *error):
                if not startup_active and not unlimited:
                    return await self.manager.__aexit__(*error)
                deadline = asyncio.get_running_loop().time() + DRIVER_SHUTDOWN_TIMEOUT_SECONDS
                try:
                    async with startup_step("driver_shutdown", deadline):
                        return await self.manager.__aexit__(*error)
                except Exception:
                    if parent_reaps_startup:
                        # 驱动未确认退出时保留组长，让父进程按本次 PID 回收所属子树。
                        notify_parent("startup-reap-requested")
                        await asyncio.Event().wait()
                    # 关闭失败独立留证，不能覆盖已发生的导航或初始化故障。
                    if error[0] is None:
                        raise

        core.async_playwright = PlaywrightWithBoundedExit

    if crawler and hasattr(crawler, "launch_browser"):
        original_launch = crawler.launch_browser

        async def launch(self, *args, **kwargs):
            nonlocal startup_active, startup_deadline
            report(runtime_phase="browser_starting")
            context = await original_launch(self, *args, **kwargs)
            if login_mode() == "cookie":
                try:
                    await seed_xhs_cookies(context, sys.modules["config"].COOKIES, self.index_url)
                except BaseException:
                    await _close_identity((context,), True)
                    raise
            report(browser_started=True, runtime_phase="browser_ready")
            if track_startup:
                # 不限执行时长仍跟踪前置步骤，退出清理使用独立上限。
                startup_active = True
                startup_deadline = None if unlimited else asyncio.get_running_loop().time() + STARTUP_TIMEOUT_SECONDS
                context.add_init_script = track(context.add_init_script, "init_script")
                context.new_page = track(context.new_page, "new_page")
            return context
        crawler.launch_browser = launch
    if hasattr(core.XiaoHongShuClient, "pong"):
        original_pong = core.XiaoHongShuClient.pong

        async def pong(self, *args, **kwargs):
            nonlocal startup_active, startup_deadline
            if startup_active:
                notify_parent("startup-finished")
            startup_active = False
            startup_deadline = None
            report(login_mode=login_mode(), runtime_phase="login_check")
            result = await original_pong(self, *args, **kwargs)
            report(login_detected=bool(result), runtime_phase="login_confirmed" if result else "login_check")
            return result
        core.XiaoHongShuClient.pong = pong
    login_class = getattr(core, "XiaoHongShuLogin", None)
    original_begin = getattr(login_class, "begin", None)
    if original_begin is not None:
        async def begin(self, *args, **kwargs):
            mode = login_mode()
            if mode == "cookie":
                # 完整凭据已在首次请求前注入；失败后重注旧值会复活已删除或轮换的会话。
                report(login_mode=mode, login_detected=False, runtime_phase="login_check")
                raise RuntimeError("小红书所选 Cookie 登录尚未确认，已停止采集")
            # 后台任务不能等待管理员在另一个桌面扫码；这不是平台已证的认证拒绝。
            report(login_mode=mode, login_detected=False, login_interaction_required=True, runtime_phase="login_interaction_required")
            raise RuntimeError("小红书后台采集登录尚未确认，请维护本次使用的采集账号后再试")
        login_class.begin = begin
    original_check = getattr(login_class, "check_login_state", None)
    if os.environ.get("MANGROVE_XHS_BROWSER_PROFILE") and hasattr(original_check, "retry_with"):
        from tenacity import retry_if_exception

        def navigating(error):
            # 扫码跳转会使页面内容暂不可读；只重查本地登录状态，不重发任何采集请求。
            transient = (type(error).__module__ == "playwright._impl._errors"
                         and type(error).__name__ == "Error"
                         and "Unable to retrieve content because the page is navigating and changing the content" in str(error))
            if transient:
                report(login_navigation_retries=state.get("login_navigation_retries", 0) + 1)
            return transient

        # 沿用原来的停止/等待预算及False结果重查；关闭页面和其他异常仍按原路径停止。
        check = original_check.retry_with(retry=original_check.retry.retry | retry_if_exception(navigating))

        async def check_login_state(self, *args, **kwargs):
            report(login_mode=login_mode(), runtime_phase="login_check")
            result = await check(self, *args, **kwargs)
            report(login_detected=bool(result), runtime_phase="login_confirmed" if result else "login_check")
            return result
        login_class.check_login_state = check_login_state
    if crawler and hasattr(crawler, "get_note_detail_async_task"):
        original_detail = crawler.get_note_detail_async_task
        note_store = getattr(core, "xhs_store", None)
        if note_store is not None:
            original_save = note_store.update_xhs_note
            saved = set()

            async def save_note(note):
                identity = note.get("note_id")
                if identity and identity in saved:
                    return
                await original_save(note)
                if identity:
                    saved.add(identity)
            note_store.update_xhs_note = save_note

        async def detail(self, *args, **kwargs):
            report(runtime_phase="note_detail")
            result = await original_detail(self, *args, **kwargs)
            if result:
                # 上游等整批 gather 后才写盘；一篇被拒绝不能抹掉此前已完成的详情。
                if note_store is not None:
                    await note_store.update_xhs_note(result)
                report(note_detail_count=state["note_detail_count"] + 1)
            return result
        crawler.get_note_detail_async_task = detail
    if hasattr(core.XiaoHongShuClient, "_request_once"):
        from media_platform.xhs.client import PlatformAccessError
        original_request = core.XiaoHongShuClient._request_once
        note_not_found = getattr(core, "NoteNotFoundError", ())
        blocked = None

        async def request(self, *args, **kwargs):
            nonlocal blocked
            # 当前上游在会话锁内调用此接缝，排队请求也必须在发送前检查停止状态。
            if blocked is not None:
                raise blocked
            try:
                return await original_request(self, *args, **kwargs)
            except PlatformAccessError as error:
                blocked = error
                url = kwargs.get("url") or (args[1] if len(args) > 1 else "")
                path = urlsplit(url).path
                operation = {"/api/sns/web/v1/search/notes": "search",
                             "/api/sns/web/v1/feed": "detail"}.get(path, "unknown")
                if path.startswith("/explore/"):
                    operation = "detail"
                elif path.startswith("/api/sns/web/v2/comment/"):
                    operation = "comments"
                report(challenge_detected=error.http_status in {461, 471}, rejected_operation=operation)
                raise
            except note_not_found:
                # 单篇已明确不存在由上游逐篇处理，不等于全平台拒绝或请求结果未知。
                raise
            except asyncio.CancelledError as error:
                # 取消仍原样传播，同时拦住本批已排队但尚未发送的请求。
                blocked = error
                raise
            except Exception as error:
                # 结果未知也不能让已排队的同批请求继续发送，更不能由上游重试重复访问。
                blocked = error
                raise
        core.XiaoHongShuClient._request_once = request
    return report, state


def main():
    # 子进程 cwd 是已配置的独立 MediaCrawler；只替换此进程的公开评论接缝。
    sys.path.insert(0, str(Path.cwd()))
    import config
    from media_platform.xhs import core
    from tools.async_file_writer import AsyncFileWriter

    AsyncFileWriter.write_single_item_to_json = atomic_json_item

    # 个人/平台 Cookie 由父进程选定；禁止串用持久浏览器或另一用户的 CDP 会话。
    # 只有父入口完成平台/路径/空凭证检查后，才能保留显式平台会话。
    config.SAVE_LOGIN_STATE = bool(os.environ.get("MANGROVE_XHS_BROWSER_PROFILE"))
    config.ENABLE_CDP_MODE = False

    run_dir = Path(sys.argv[sys.argv.index("--save_data_path") + 1])
    report, runtime = _install_runtime_diagnostics(core, run_dir)
    statuses = {}
    comment_deadline = None
    if os.environ.get("MANGROVE_XHS_UNLIMITED_EXECUTION") != "1":
        timeout = float(os.environ.get("MANGROVE_XHS_TIMEOUT_SECONDS", "600"))
        # 有限模式仍给写盘、浏览器关闭和逐篇采集间隔留出余量。
        comment_deadline = time.monotonic() + max(0, timeout - min(30, timeout * 0.1))
    sort = os.environ.get("MANGROVE_XHS_SORT")
    note_type = os.environ.get("MANGROVE_XHS_NOTE_TYPE")
    if sort or note_type:
        from media_platform.xhs.field import SearchNoteType, SearchSortType

    original_search = core.XiaoHongShuClient.get_note_by_keyword
    requested = int(sys.argv[sys.argv.index("--crawler_max_notes_count") + 1])
    remaining_by_keyword, pages, exhausted, candidates = {}, [], set(), {}

    async def search(self, **kwargs):
        keyword = kwargs.get("keyword")
        remaining = remaining_by_keyword.get(keyword, requested)
        if remaining <= 0 or keyword in exhausted:
            return {"items": [], "has_more": False}
        if sort:
            kwargs["sort"] = SearchSortType(sort)
        if note_type:
            kwargs["note_type"] = SearchNoteType({"all": 0, "video": 1, "image": 2}[note_type])
        report(search_started=True, runtime_phase="search")
        result = await original_search(self, **kwargs)
        raw = (result or {}).get("items") or []
        report(search_list_received=True, search_item_count=runtime["search_item_count"] + len(raw), runtime_phase="list_received")
        if not (result or {}).get("has_more"):
            exhausted.add(keyword)
        # 上游会把不足一页的额度提升到20；详情扇出前截断，才能遵守宿主冻结预算。
        items, seen = [], set()
        for item in raw:
            if item.get("model_type") in ("rec_query", "hot_query"):
                continue
            identity = item.get("id")
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            items.append(item)
        items = items[:remaining]
        remaining_by_keyword[keyword] = remaining - len(items)
        pages.append({"keyword": keyword, "page": kwargs.get("page", 1),
                      "item_count": len(raw), "accepted_count": len(items),
                      "has_more": bool((result or {}).get("has_more"))})
        _write_json(run_dir / "search_coverage.json", {"pages": pages})
        for item in items:
            identity, card = item.get("id"), item.get("note_card")
            if not isinstance(identity, str) or not identity or not isinstance(card, dict):
                continue
            # 搜索卡片只是候选，不含完整正文；认证参数不进入这份候选投影。
            candidates.setdefault(identity, {"note_id": identity, "title": card.get("display_title") or card.get("title") or "",
                "note_url": "https://www.xiaohongshu.com/explore/" + quote(identity, safe=""),
                "type": card.get("type"), "source_keyword": keyword, "_search_candidate": True})
        _write_json(run_dir / "search_candidates.json", list(candidates.values()))
        # 上游在has_more=False时先退出；有内容的末页仍须处理，来源真值保留在coverage。
        return {**(result or {}), "items": items, "has_more": True if items else bool((result or {}).get("has_more"))}

    core.XiaoHongShuClient.get_note_by_keyword = search

    async def get_comments(self, note_id, xsec_token, crawl_interval=1.0, callback=None, max_count=10):
        deadline = None
        if comment_deadline is not None:
            remaining = max(1, config.CRAWLER_MAX_NOTES_COUNT - len(statuses))
            # 沿用既定频率和数量上限；有限模式按剩余篇数分摊时间。
            budget = max(0, (comment_deadline - time.monotonic()) / remaining - crawl_interval)
            deadline = time.monotonic() + budget
        rows, status = await bounded_comments(self, note_id, xsec_token, crawl_interval, callback, max_count,
                                             deadline=deadline)
        statuses[note_id] = status
        _write_json(run_dir / "comment_coverage.json", statuses)
        return rows

    core.XiaoHongShuClient.get_note_all_comments = get_comments
    runpy.run_path(str(Path.cwd() / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
