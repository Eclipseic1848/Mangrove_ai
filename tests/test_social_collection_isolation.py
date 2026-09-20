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
    (tmp_path / "main.py").write_text('''
import argparse, json, os, subprocess, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--save_data_path", default="data")
p.add_argument("--keywords")
p.add_argument("--specified_id", default="")
p.add_argument("--get_comment", default="no")
p.add_argument("--platform", default="xhs")
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
             "source_keyword": os.environ.get("SYNTHETIC_SOURCE_KEYWORD", args.keywords)})
f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
if args.get_comment == "yes":
    (f.parent / "search_comments_today.json").write_text(json.dumps([
        {"comment_id": "comment-1", "note_id": args.keywords, "content": "合成评论正文。"}
    ], ensure_ascii=False), encoding="utf-8")
''', encoding="utf-8")
    return tmp_path


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
