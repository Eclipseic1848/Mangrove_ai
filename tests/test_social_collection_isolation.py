"""用本地合成采集进程验证结果隔离，不访问站点或真实凭据。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from src.collectors.social_media_collector import SocialMediaCollector
from src.config.settings import settings
from src.config.user_ctx import user_overrides_context
from src.conductor.task_spec import TaskSpec


@pytest.fixture
def crawler(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mediacrawler_path", str(tmp_path))
    monkeypatch.setattr(settings, "mediacrawler_python", sys.executable)
    monkeypatch.setattr(settings, "mc_enable_ip_proxy", False)
    monkeypatch.setattr(settings, "mc_enable_cdp_mode", False)
    monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "webui.db"))
    package = tmp_path / "media_platform" / "xhs"
    package.mkdir(parents=True)
    (package / "core.py").write_text("class XiaoHongShuClient:\n    async def get_note_by_keyword(self, **kwargs): return {}\n", encoding="utf-8")
    (package / "field.py").write_text(
        "from enum import Enum\nclass SearchSortType(Enum):\n    GENERAL = 'general'\n    LATEST = 'time_descending'\n"
        "class SearchNoteType(Enum):\n    ALL = 0\n    VIDEO = 1\n    IMAGE = 2\n", encoding="utf-8",
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tools/async_file_writer.py").write_text("class AsyncFileWriter: pass\n", encoding="utf-8")
    (tmp_path / "config.py").write_text("SAVE_LOGIN_STATE = True\n", encoding="utf-8")
    (tmp_path / "main.py").write_text('''
import argparse, json, os, subprocess, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--save_data_path", default="data")
p.add_argument("--keywords")
p.add_argument("--specified_id", default="")
p.add_argument("--get_comment", default="no")
p.add_argument("--platform", default="xhs")
p.add_argument("--start", default="1")
args, _ = p.parse_known_args()
if os.environ.get("SYNTHETIC_EMPTY"):
    raise SystemExit(0)
if os.environ.get("SYNTHETIC_WAIT"):
    if os.environ.get("SYNTHETIC_CHILD"):
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
        Path("child-ready").write_text("ready", encoding="utf-8")
    time.sleep(60)
root = Path(args.save_data_path)
platform_dir = {"dy": "douyin", "wb": "weibo", "ks": "kuaishou"}.get(args.platform, args.platform)
f = root / platform_dir / "json/search_contents_today.json"
f.parent.mkdir(parents=True, exist_ok=True)
rows = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
rows.append({"note_id": args.specified_id.rsplit("/", 1)[-1] or args.keywords,
             "note_url": args.specified_id, "title": args.keywords or "详情",
             "desc": "用于核对采集目标的合成正文。",
             "page_seen": args.start,
             "source_keyword": os.environ.get("SYNTHETIC_SOURCE_KEYWORD", args.keywords)})
if os.environ.get("SYNTHETIC_NOTE_FIELDS"):
    rows[-1].update(json.loads(os.environ["SYNTHETIC_NOTE_FIELDS"]))
f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
if args.get_comment == "yes":
    (f.parent / "search_comments_today.json").write_text(os.environ.get("SYNTHETIC_COMMENTS") or json.dumps([
        {"comment_id": "comment-1", "note_id": args.keywords, "content": "合成评论正文。"}
    ], ensure_ascii=False), encoding="utf-8")
''', encoding="utf-8")
    return tmp_path


def test_identity_probe_child_does_not_search(crawler):
    (crawler / "media_platform/xhs/core.py").write_text('''
class Client:
    async def query_self(self):
        return {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": "private-account"}}}
class XiaoHongShuCrawler:
    xhs_client = Client()
    async def search(self):
        raise AssertionError("身份验证不应搜索")
''', encoding="utf-8")
    (crawler / "main.py").write_text('''
import asyncio, config
from media_platform.xhs.core import XiaoHongShuCrawler
assert config.COOKIES == "synthetic-cookie"
assert not config.SAVE_LOGIN_STATE
assert not config.ENABLE_CDP_MODE
asyncio.run(XiaoHongShuCrawler().search())
''', encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().verify_cookie(TaskSpec(
            intent="验证", platforms=["小红书"], keywords=["验证"], max_items=1,
        )))
    assert result.success, result.message
    assert result.authentication["status"] == "valid"
    assert not result.items
    assert "private-account" not in json.dumps(result.authentication)


@pytest.mark.parametrize("platform", ["dy", "ks", "tieba", "zhihu"])
def test_unsupported_identity_probe_does_not_start_crawler(crawler, platform):
    (crawler / "main.py").write_text('raise AssertionError("不支持身份探针时不启动采集")', encoding="utf-8")
    with user_overrides_context({f"mc_cookie_{platform}": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().verify_cookie(TaskSpec(
            intent="验证", platforms=[platform], keywords=["验证"], max_items=1,
        )))
    assert result.authentication == {"status": "unknown", "reason": "identity_probe_unsupported"}
    assert not result.success and not result.items


def test_cookie_probe_results_are_not_reused_by_task(crawler):
    async def run():
        with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
            collector = SocialMediaCollector()
            probe = await collector.collect(TaskSpec(intent="验证", platforms=["小红书"], keywords=["你好"], max_items=1))
            task = await collector.collect(TaskSpec(intent="中信私银", platforms=["小红书"], keywords=["中信私银"], max_items=1))
        assert probe.success and task.success
        assert [item.title for item in task.items] == ["中信私银"]

    asyncio.run(run())


def test_empty_run_does_not_read_shared_history(crawler, monkeypatch):
    history = crawler / "data/xhs/json/search_contents_today.json"
    history.parent.mkdir(parents=True)
    previous = json.dumps([{"note_id": "old", "title": "旧资料", "source_keyword": "中信私银"}])
    history.write_text(previous, encoding="utf-8")
    monkeypatch.setenv("SYNTHETIC_EMPTY", "1")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="中信私银", platforms=["小红书"], keywords=["中信私银"], max_items=1,
        )))
    assert not result.has_data
    assert history.read_text(encoding="utf-8") == previous


@pytest.mark.parametrize("mode", ["success", "timeout", "cancel", "inherited-output"])
def test_run_directory_and_writer_are_reclaimed(crawler, monkeypatch, mode):
    processes = []
    directories = []
    launch = asyncio.create_subprocess_exec

    async def run():
        started = asyncio.Event()

        async def capture(*args, **kwargs):
            proc = await launch(*args, **kwargs)
            processes.append(proc)
            directories.append(Path(args[args.index("--save_data_path") + 1]))
            started.set()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        if mode != "success":
            monkeypatch.setenv("SYNTHETIC_WAIT", "1")
        if mode == "inherited-output":
            monkeypatch.setenv("SYNTHETIC_CHILD", "1")
        if mode == "timeout":
            monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", 0.1)
        with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
            task = asyncio.create_task(SocialMediaCollector().collect(TaskSpec(
                intent="中信私银", platforms=["小红书"], keywords=["中信私银"], max_items=1,
            )))
            await asyncio.wait_for(started.wait(), timeout=5)
            if mode == "inherited-output":
                async def ready():
                    while not (crawler / "child-ready").exists():
                        await asyncio.sleep(0.02)
                await asyncio.wait_for(ready(), timeout=5)
            if mode in ("cancel", "inherited-output"):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=2.5)
            else:
                result = await task
                assert result.success == (mode == "success")
        assert all(proc.returncode is not None for proc in processes)
        assert all(not directory.exists() for directory in directories)

    asyncio.run(run())


@pytest.mark.parametrize("platform", ["dy", "wb", "ks", "bili", "zhihu", "tieba"])
def test_platform_store_directory_names(crawler, monkeypatch, platform):
    monkeypatch.setattr(settings, f"mc_cookie_{platform}", "synthetic-cookie")
    result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
        intent="目标", platforms=[platform], keywords=["目标"], max_items=1,
    )))
    assert result.success
    assert [item.title for item in result.items] == ["目标"]


def test_detail_and_comment_collection_still_work(crawler):
    async def run():
        with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
            collector = SocialMediaCollector()
            url = "https://www.xiaohongshu.com/explore/abcdef123456"
            detail = await collector.collect(TaskSpec(intent="详情", platforms=["小红书"], urls=[url], max_items=1))
            comments = await collector.collect(TaskSpec(
                intent="评论", platforms=["小红书"], keywords=["中信私银"], data_type="comment", max_items=1,
            ))
        assert detail.success and detail.items[0].url == url
        assert comments.success and comments.items[0].content == "合成评论正文。"

    asyncio.run(run())


@pytest.mark.parametrize("source_keyword", ["你好", ""])
def test_wrong_or_missing_search_origin_is_rejected(crawler, monkeypatch, source_keyword):
    monkeypatch.setenv("SYNTHETIC_SOURCE_KEYWORD", source_keyword)
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="中信私银", platforms=["小红书"], keywords=["中信私银"], max_items=1,
        )))
    assert not result.success
    assert not result.items
    assert "搜索词" in result.message


def test_parallel_calls_do_not_share_results(crawler):
    async def collect(keyword, cookie):
        with user_overrides_context({"mc_cookie_xhs": cookie}):
            return await SocialMediaCollector().collect(TaskSpec(
                intent=keyword, platforms=["小红书"], keywords=[keyword], max_items=1,
            ))

    async def run():
        first, second = await asyncio.gather(
            collect("中信私银", "synthetic-owner-a"), collect("另一任务", "synthetic-owner-b"),
        )
        assert [item.title for item in first.items] == ["中信私银"]
        assert [item.title for item in second.items] == ["另一任务"]

    asyncio.run(run())


def test_xhs_post_retains_public_fields_and_attached_comments(crawler, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_NOTE_FIELDS", json.dumps({
        "note_id": "note-1", "type": "normal", "time": 1767225600000,
        "note_url": "https://www.xiaohongshu.com/explore/note-1?xsec_token=private-token",
        "video_url": "https://example.com/video.mp4?token=private-token",
        "user_id": "author-1", "nickname": "公开作者", "liked_count": "12",
        "collected_count": "4", "comment_count": "2",
        "image_list": "https://example.com/one.jpg,https://example.com/two.jpg",
        "avatar": "excluded-avatar", "ip_location": "excluded-location", "xsec_token": "private-token",
    }))
    monkeypatch.setenv("SYNTHETIC_COMMENTS", json.dumps([
        {"comment_id": "root", "note_id": "note-1", "parent_comment_id": 0,
         "content": "每季度一次", "avatar": "excluded-avatar", "ip_location": "excluded-location"},
        {"comment_id": "reply", "note_id": "note-1", "parent_comment_id": "root", "content": "需要达标"},
        {"comment_id": "other", "note_id": "another-note", "content": "不属于该帖子"},
    ]))
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="中信私银", platforms=["小红书"], keywords=["中信私银"],
            max_items=1, include_comments=True,
        )))
    assert result.success and len(result.items) == 1
    item = result.items[0]
    assert item.metadata["kind"] == "post"
    assert item.url == "https://www.xiaohongshu.com/explore/note-1"
    assert item.metadata["publish_time"] == "2026-01-01T00:00:00+00:00"
    assert item.metadata["author_id"] == "author-1"
    assert item.metadata["author_name"] == "公开作者"
    assert item.metadata["image_urls"] == ["https://example.com/one.jpg", "https://example.com/two.jpg"]
    assert item.metadata["like_count"] == 12
    assert [row["comment_id"] for row in item.metadata["comments"]] == ["root", "reply"]
    assert item.metadata["comments"][1]["parent_comment_id"] == "root"
    exported = json.dumps(item.to_dict(), ensure_ascii=False)
    assert all(value not in exported for value in ["private-token", "excluded-avatar", "excluded-location", "不属于该帖子"])


def test_comment_limit_survives_task_draft_conversion():
    spec = TaskSpec.from_draft({"include_comments": True, "comment_limit": 5}, "采集小红书笔记及评论")
    assert spec.include_comments and spec.comment_limit == 5


def test_attached_comments_reject_unsupported_platform(crawler):
    with user_overrides_context({"mc_cookie_dy": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="帖子及评论", platforms=["抖音"], keywords=["目标"], include_comments=True,
        )))
    assert not result.success
    assert "附带评论" in result.message


def test_ten_users_with_shared_cookie_serialize_and_cancel_waiter(crawler, monkeypatch):
    from src.collectors.base import CollectResult

    active = 0
    peak = 0
    completed = 0
    started = asyncio.Event()

    async def fake_collect(self, spec, run_dir, cookie):
        nonlocal active, peak, completed
        active += 1
        peak = max(peak, active)
        started.set()
        try:
            await asyncio.sleep(0.02)
            completed += 1
            return CollectResult(True, self.name)
        finally:
            active -= 1

    monkeypatch.setattr(SocialMediaCollector, "_collect", fake_collect)

    async def run():
        with user_overrides_context({"mc_cookie_xhs": "web_session=shared-secret; extra=one"}):
            tasks = [asyncio.create_task(SocialMediaCollector().collect(TaskSpec(
                intent=str(index), platforms=["小红书"], keywords=["权益"],
            ))) for index in range(10)]
            await asyncio.wait_for(started.wait(), timeout=5)
            tasks[-1].cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert isinstance(results[-1], asyncio.CancelledError)
    asyncio.run(run())
    assert peak == 1 and completed == 9
    assert all("shared-secret" not in path.name for path in crawler.rglob("*.lock"))


def test_xhs_search_options_reach_isolated_subprocess(crawler, monkeypatch):
    launched = []
    launch = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        launched.append((args, kwargs["env"]))
        return await launch(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="权益", keywords=["中信权益"], platforms=["小红书"],
            xhs_search_page=2, xhs_sort="general", xhs_note_type="image",
        )))
    assert result.success
    cmd, env = launched[0]
    assert cmd[cmd.index("--start") + 1] == "2"
    assert env["MANGROVE_XHS_SORT"] == "general"
    assert env["MANGROVE_XHS_NOTE_TYPE"] == "image"
    assert cmd[1].endswith("mediacrawler_isolated.py") and cmd[2] == "xhs"


def test_xhs_adapter_retains_last_page_and_freezes_real_search_options(crawler):
    (crawler / "media_platform/xhs/core.py").write_text('''
class XiaoHongShuClient:
    async def get_note_by_keyword(self, **kwargs):
        assert kwargs['sort'].value == 'time_descending'
        assert kwargs['note_type'].value == 2
        return {'items': [{'id': 'last-note'}], 'has_more': False}
''', encoding="utf-8")
    entry = crawler / "main.py"
    entry.write_text(entry.read_text(encoding="utf-8") + '''
import asyncio, config
from media_platform.xhs.core import XiaoHongShuClient
assert not config.SAVE_LOGIN_STATE and not config.ENABLE_CDP_MODE
result = asyncio.run(XiaoHongShuClient().get_note_by_keyword(keyword=args.keywords, page=int(args.start)))
assert result['items'] and result['has_more'] is True
''', encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="权益", keywords=["中信权益"], platforms=["小红书"],
            xhs_search_page=3, xhs_sort="time_descending", xhs_note_type="image")))
    assert result.success
    assert result.coverage["pages"] == [{"keyword": "中信权益", "page": 3, "item_count": 1, "has_more": False}]


@pytest.mark.parametrize("failure, expected", [
    ("media_platform.xhs.exception.DataFetchError: request failed", "采集失败"),
    ("CAPTCHA", "验证码"),
    ("登录已过期", "登录已过期"),
    ("登录失败: network error", "采集失败"),
    ("登录失败: CAPTCHA", "验证码"),
])
def test_initial_anonymous_login_probe_does_not_mask_collection_failure(crawler, failure, expected):
    (crawler / "main.py").write_text(
        "print('Login state result: False')\n"
        "print('Begin login xiaohongshu by cookie')\n"
        "print('Begin search Xiaohongshu keywords')\n"
        f"print({failure!r})\nraise SystemExit(1)\n", encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="权益", keywords=["中信权益"], platforms=["小红书"])))
    assert not result.success
    assert expected in result.message
    if expected != "登录已过期":
        assert "登录已过期" not in result.message


def test_cookie_verification_uses_isolated_browser(crawler, monkeypatch):
    from src.api.routes import config_routes
    from src.collectors import registry
    monkeypatch.setattr(registry, "get_registry", lambda: {"mediacrawler": SocialMediaCollector()})
    (crawler / "media_platform/xhs/core.py").write_text('''
class Client:
    async def query_self(self): return None
class XiaoHongShuCrawler:
    xhs_client = Client()
    async def search(self): raise AssertionError("不应搜索")
''', encoding="utf-8")
    (crawler / "main.py").write_text('''
import asyncio, config
from media_platform.xhs.core import XiaoHongShuCrawler
assert not config.SAVE_LOGIN_STATE
assert not config.ENABLE_CDP_MODE
asyncio.run(XiaoHongShuCrawler().search())
''', encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        with pytest.raises(config_routes.CookieProbeError) as error:
            asyncio.run(config_routes._verify_mc_cookie("mc_cookie_xhs"))
    assert error.value.reason == "identity_unverified"
    assert "尚未取得可确认的登录身份" in str(error.value)


@pytest.mark.parametrize("source_keyword", ["中信权益", "错误来源"])
def test_comment_timeout_retains_only_current_valid_results(crawler, monkeypatch, source_keyword):
    monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", 1.5)
    monkeypatch.setenv("SYNTHETIC_SOURCE_KEYWORD", source_keyword)
    entry = crawler / "main.py"
    entry.write_text(entry.read_text(encoding="utf-8") + "\ntime.sleep(60)\n", encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="权益", platforms=["小红书"], keywords=["中信权益"],
            max_items=1, include_comments=True)))
    if source_keyword != "中信权益":
        assert not result.success and not result.items
        return
    assert result.success and len(result.items) == 1
    assert result.items[0].metadata["comments"][0]["comment_id"] == "comment-1"
    assert result.coverage["partial"] and result.coverage["collector_timeout"]
    coverage = result.items[0].metadata["comment_coverage"]
    assert coverage["truncated"] and "collector_timeout" in coverage["reasons"]
    assert "超时" in result.message


def test_adapter_shares_remaining_time_between_notes(crawler, monkeypatch):
    monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", 3)
    (crawler / "config.py").write_text("SAVE_LOGIN_STATE=True\nCRAWLER_MAX_NOTES_COUNT=2\n", encoding="utf-8")
    (crawler / "media_platform/xhs/core.py").write_text("""
import asyncio
class XiaoHongShuClient:
    calls = []
    async def get_note_comments(self, **kwargs):
        self.calls.append(kwargs['note_id'])
        return {'comments': [{'id': kwargs['note_id'], 'sub_comment_has_more': True}], 'has_more': False}
    async def get_note_sub_comments(self, **kwargs):
        await asyncio.sleep(30)
""", encoding="utf-8")
    entry = crawler / "main.py"
    entry.write_text(entry.read_text(encoding="utf-8") + """
import asyncio
from media_platform.xhs.core import XiaoHongShuClient
async def fetch():
    client = XiaoHongShuClient()
    for note_id in [args.keywords, 'second']:
        rows = await client.get_note_all_comments(note_id, 'synthetic', crawl_interval=0)
        assert len(rows) == 1
    assert client.calls == [args.keywords, 'second']
asyncio.run(fetch())
""", encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="权益", platforms=["小红书"], keywords=["中信权益"], include_comments=True, max_items=2)))
    assert result.success and result.coverage["partial"]
    assert result.coverage["collector_timeout"] is False
    assert "comment_time_budget" in result.items[0].metadata["comment_coverage"]["reasons"]

@pytest.mark.parametrize("platform,key", [
    ("小红书", "mc_cookie_xhs"), ("抖音", "mc_cookie_dy"), ("微博", "mc_cookie_wb"),
    ("B站", "mc_cookie_bili"), ("知乎", "mc_cookie_zhihu"), ("快手", "mc_cookie_ks"), ("贴吧", "mc_cookie_tieba"),
])
def test_all_platforms_use_selected_cookie_without_shared_profile_or_secret_argv(crawler, monkeypatch, platform, key):
    secret = "synthetic-selected-session-secret"
    monkeypatch.setenv(key.upper(), secret)
    entry = crawler / "main.py"
    entry.write_text("import config, sys\nassert not config.SAVE_LOGIN_STATE\nassert not config.ENABLE_CDP_MODE\n"
                     "assert sys.argv[sys.argv.index('--cookies') + 1] == 'synthetic-selected-session-secret'\n"
                     + entry.read_text(encoding="utf-8"), encoding="utf-8")
    launch = asyncio.create_subprocess_exec
    async def capture(*args, **kwargs):
        assert secret not in str(args)
        assert secret not in str(kwargs.get("env"))
        return await launch(*args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with user_overrides_context({key: secret}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="读取指定账号资料", platforms=[platform], keywords=["合成主题"], max_items=1)))
    assert result.success


def test_empty_selected_cookie_does_not_use_external_repository_credentials(crawler, monkeypatch):
    monkeypatch.setattr(settings, "mc_cookie_xhs", "")
    (crawler / "config.py").write_text("SAVE_LOGIN_STATE = True\nCOOKIES = 'unselected-old-cookie'\nLOGIN_TYPE = 'cookie'\n", encoding="utf-8")
    entry = crawler / "main.py"
    entry.write_text("import config\nassert config.COOKIES == ''\nassert config.LOGIN_TYPE == 'qrcode'\n"
                     + entry.read_text(encoding="utf-8"), encoding="utf-8")
    with user_overrides_context({}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="正常扫码登录", platforms=["小红书"], keywords=["合成主题"], max_items=1)))
    assert result.success


def test_shared_personal_and_rotated_credentials_reach_their_own_process(crawler, monkeypatch):
    entry = crawler / "main.py"
    entry.write_text("import sys, config\n"
                     "expected = {'共享':'shared-one','个人':'personal-one','轮换':'shared-two'}\n"
                     "assert config.COOKIES == expected[sys.argv[sys.argv.index('--keywords') + 1]]\n"
                     + entry.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "mc_cookie_xhs", "shared-one")
    async def run():
        collector = SocialMediaCollector()
        with user_overrides_context({}):
            shared = asyncio.create_task(collector.collect(TaskSpec(intent="共享", platforms=["小红书"], keywords=["共享"])))
        with user_overrides_context({"mc_cookie_xhs": "personal-one"}):
            personal = asyncio.create_task(collector.collect(TaskSpec(intent="个人", platforms=["小红书"], keywords=["个人"])))
        results = await asyncio.gather(shared, personal)
        assert all(result.success for result in results)
        monkeypatch.setattr(settings, "mc_cookie_xhs", "shared-two")
        with user_overrides_context({}):
            rotated = await collector.collect(TaskSpec(intent="轮换", platforms=["小红书"], keywords=["轮换"]))
        assert rotated.success
    asyncio.run(run())
