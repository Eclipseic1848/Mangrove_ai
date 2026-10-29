"""用标准库运行离线子进程回归，不访问真实平台或凭据。"""
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from src.collectors.social_media_collector import SocialMediaCollector
from src.config.settings import settings
from src.config.user_ctx import user_overrides_context
from src.conductor.task_spec import TaskSpec


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "xhs_user_data_dir"
        self.profile.mkdir()
        for directory in ["media_platform/xhs", "tools"]:
            (self.root / directory).mkdir(parents=True)
        (self.root / "config.py").write_text("SAVE_LOGIN_STATE = True\n", encoding="utf-8")
        (self.root / "media_platform/xhs/core.py").write_text(
            "class XiaoHongShuClient:\n    async def get_note_by_keyword(self, **kw): return {}\n", encoding="utf-8")
        (self.root / "tools/async_file_writer.py").write_text("class AsyncFileWriter: pass\n", encoding="utf-8")
        (self.root / "main.py").write_text('''
import config, json, os, sys
from pathlib import Path
browser = os.environ.get("EXPECT_BROWSER") == "yes"
assert config.SAVE_LOGIN_STATE is browser
assert config.ENABLE_CDP_MODE is False
if browser:
    assert config.LOGIN_TYPE == "qrcode" and config.COOKIES == ""
    assert config.HEADLESS is False
    assert Path(config.USER_DATA_DIR % "xhs") == Path(os.environ["EXPECTED_PROFILE"])
    assert sys.argv[sys.argv.index("--lt") + 1] == "qrcode"
    assert "--cookies" not in sys.argv
assert os.environ["MC_ENABLE_CDP_MODE"] == "false"
assert os.environ["DISPLAY"] == "synthetic-display:0"
assert os.environ["HOME"] == "synthetic-home"
assert str(Path.cwd()) == os.environ["EXPECTED_CWD"]
assert "MC_COOKIE_XHS" not in os.environ
if os.environ.get("EXPECT_EMPTY"):
    out = Path(sys.argv[sys.argv.index("--save_data_path") + 1])
    (out / "search_coverage.json").write_text(json.dumps({"pages": [{"item_count": 0, "has_more": False}]}), encoding="utf-8")
    raise SystemExit(0)
platform = sys.argv[sys.argv.index("--platform") + 1]
out = Path(sys.argv[sys.argv.index("--save_data_path") + 1]) / {"wb": "weibo"}.get(platform, platform) / "json"
out.mkdir(parents=True)
(out / "notes.json").write_text(json.dumps([{"note_id": "note-1", "title": "合成笔记", "desc": "合成正文", "source_keyword": "agent"}]), encoding="utf-8")
''', encoding="utf-8")
        values = {"mediacrawler_path": str(self.root), "mediacrawler_python": sys.executable,
                  "webui_db_path": str(self.root / "test.db"), "mc_cookie_xhs": "stale-platform-cookie",
                  "mc_enable_ip_proxy": False, "mc_xhs_login_mode": "browser",
                  "mc_xhs_user_data_dir": str(self.profile), "collect_timeout_mediacrawler_seconds": 5}
        for key, value in values.items():
            if not hasattr(settings, key):
                object.__setattr__(settings, key, value)
                self.addCleanup(object.__delattr__, settings, key)
            else:
                context = patch.object(settings, key, value)
                context.start()
                self.addCleanup(context.stop)
        context = patch.dict(os.environ, {"DISPLAY": "synthetic-display:0", "HOME": "synthetic-home",
            "MC_COOKIE_XHS": "must-not-inherit", "EXPECT_BROWSER": "yes", "EXPECTED_PROFILE": str(self.profile),
            "EXPECTED_CWD": str(self.root)})
        context.start()
        self.addCleanup(context.stop)

    def collect(self, **kwargs):
        return asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="离线回归", platforms=["小红书"], keywords=["agent"], max_items=1, **kwargs)))

    def test_browser_reuses_explicit_profile_instead_of_stale_platform_cookie(self):
        result = self.collect()
        self.assertTrue(result.success, result.message)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.coverage["connection"]["login_mode"], "browser")

    def test_personal_cookie_keeps_isolated_context(self):
        with patch.dict(os.environ, EXPECT_BROWSER="no"), user_overrides_context({"mc_cookie_xhs": "personal-cookie"}):
            result = self.collect()
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.coverage["connection"]["source"], "personal")

    def test_owner_can_explicitly_select_platform_session_without_erasing_cookie(self):
        from src.config.user_ctx import effective
        with user_overrides_context({"mc_cookie_xhs": "personal-cookie", "mc_xhs_login_mode": "browser"}):
            result = self.collect()
            self.assertEqual(effective("mc_cookie_xhs"), "personal-cookie")
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.coverage["connection"], {"platform": "xhs", "source": "platform", "login_mode": "browser"})

    def test_owner_cookie_selection_does_not_use_platform_browser(self):
        with user_overrides_context({"mc_cookie_xhs": "personal-cookie", "mc_xhs_login_mode": "cookie"}), \
                patch.dict(os.environ, EXPECT_BROWSER="no"):
            result = self.collect()
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.coverage["connection"]["source"], "personal")

    def test_empty_search_is_failure(self):
        with patch.object(settings, "mc_xhs_login_mode", "auto"), patch.dict(os.environ, EXPECT_EMPTY="yes", EXPECT_BROWSER="no"):
            result = self.collect()
        self.assertFalse(result.success)

    def test_display_failure_points_to_selected_login_method(self):
        (self.root / "main.py").write_text(
            'raise RuntimeError("Missing X server or $DISPLAY")\n', encoding="utf-8")
        for mode, action in [("browser", "restore_collection_display"),
                             ("auto", "configure_collection_headless")]:
            with self.subTest(mode=mode), patch.object(settings, "mc_xhs_login_mode", mode):
                result = self.collect()
            self.assertFalse(result.success)
            self.assertEqual(result.coverage["failure"]["reason"], "collector_display_unavailable")
            self.assertEqual(result.coverage["failure"]["next_action"], action)
            if mode == "browser":
                self.assertNotIn("配置为无头模式", result.message)

    def test_busy_browser_is_not_launched(self):
        lock = self.profile / "SingletonLock"
        fallback = nullcontext()
        try:
            lock.symlink_to("synthetic-host-123")
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 1314:
                raise
            # Windows 缺符号链接权限时只替身该悬空锁；Linux 仍检查真实链接。
            original = Path.is_symlink
            fallback = patch.object(Path, "is_symlink", lambda path: path == lock or original(path))
        self.assertFalse(lock.exists())
        with fallback, patch("asyncio.create_subprocess_exec", side_effect=AssertionError("不应启动浏览器")):
            result = self.collect()
        self.assertFalse(result.success)
        self.assertEqual(result.coverage["failure"]["reason"], "browser_profile_busy")

    def test_identity_probe_never_uses_browser_session(self):
        async def probe(spec, run_dir, cookie, **kwargs):
            from src.collectors.base import CollectResult
            self.assertEqual(cookie, "stale-platform-cookie")
            self.assertTrue(kwargs["identity_probe"])
            return CollectResult(False, "mediacrawler")
        with patch.object(SocialMediaCollector, "_collect", side_effect=probe):
            result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
                intent="验证", platforms=["小红书"], keywords=["agent"]), identity_probe=True))
        self.assertFalse(result.success)

    def test_real_identity_entry_clears_inherited_browser_profile(self):
        (self.root / "media_platform/xhs/core.py").write_text('''
class Client:
    async def query_self(self):
        return {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": "synthetic-account"}}}
class XiaoHongShuCrawler:
    xhs_client = Client()
    async def search(self):
        raise AssertionError("身份入口不能搜索")
''', encoding="utf-8")
        (self.root / "main.py").write_text('''
import asyncio, config, os
from media_platform.xhs.core import XiaoHongShuCrawler
assert config.COOKIES == "stale-platform-cookie"
assert config.LOGIN_TYPE == "cookie"
assert config.SAVE_LOGIN_STATE is False
assert config.ENABLE_CDP_MODE is False
assert "MANGROVE_XHS_BROWSER_PROFILE" not in os.environ
asyncio.run(XiaoHongShuCrawler().search())
''', encoding="utf-8")
        with patch.dict(os.environ, MANGROVE_XHS_BROWSER_PROFILE=str(self.profile)):
            result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
                intent="身份回归", platforms=["小红书"], keywords=["agent"]), identity_probe=True))
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.authentication["status"], "valid")
        self.assertEqual(result.items, [])

    def test_shared_profile_collectors_are_serialized(self):
        from src.collectors.base import CollectResult
        active, peak = 0, 0
        async def collect(spec, run_dir, cookie):
            nonlocal active, peak
            self.assertEqual(cookie, "")
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return CollectResult(False, "mediacrawler")
        async def run():
            await asyncio.gather(*(SocialMediaCollector().collect(TaskSpec(
                intent="并发回归", platforms=["xhs"], keywords=["agent"])) for _ in range(3)))
        with patch.object(SocialMediaCollector, "_collect", side_effect=collect):
            asyncio.run(run())
        self.assertEqual(peak, 1)

    def test_default_mode_preserves_cookie_login(self):
        with patch.object(settings, "mc_xhs_login_mode", "auto"), patch.dict(os.environ, EXPECT_BROWSER="no"):
            result = self.collect()
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.coverage["connection"], {"platform": "xhs", "source": "platform"})

    def test_cookie_mode_without_cookie_fails_without_scanning(self):
        with patch.object(settings, "mc_xhs_login_mode", "cookie"), patch.object(settings, "mc_cookie_xhs", ""), \
                patch("asyncio.create_subprocess_exec", side_effect=AssertionError("不能回退扫码")):
            result = self.collect()
        self.assertFalse(result.success)
        self.assertIn("未配置", result.message)

    def test_missing_profile_does_not_fallback_to_stale_cookie(self):
        with patch.object(settings, "mc_xhs_user_data_dir", ""), \
                patch("asyncio.create_subprocess_exec", side_effect=AssertionError("不能回退旧 Cookie")):
            result = self.collect()
        self.assertFalse(result.success)
        self.assertEqual(result.coverage["failure"]["reason"], "browser_profile_unavailable")

    def test_frozen_environment_retains_explicit_login_selection(self):
        from src.config.cookie_probe_binding import capture_probe_environment, probe_environment_context
        environment = capture_probe_environment()
        with patch.object(settings, "mc_xhs_login_mode", "auto"), probe_environment_context(environment):
            result = self.collect()
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.coverage["connection"]["login_mode"], "browser")

    def test_cookie_never_enters_child_argv_or_environment(self):
        original = asyncio.create_subprocess_exec
        calls = []

        async def launch(*args, **kwargs):
            self.assertNotIn("stale-platform-cookie", str(args) + str(kwargs["env"]))
            self.assertEqual(kwargs["cwd"], str(self.root))
            calls.append(args)
            return await original(*args, **kwargs)

        with patch("asyncio.create_subprocess_exec", side_effect=launch):
            result = self.collect()
        self.assertTrue(result.success, result.message)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.coverage["runtime"]["exit_code"], 0)
        self.assertEqual(result.coverage["runtime"]["termination"], "exited")

    def test_http_rejections_are_reported_without_retry_or_secret_output(self):
        (self.root / "media_platform/xhs/client.py").write_text('''
class PlatformAccessError(Exception):
    def __init__(self, status):
        self.http_status = status
        self.platform_code = None
        super().__init__("synthetic-private-cookie")
''', encoding="utf-8")
        for status, reason in [(461, "challenge_required"), (401, "login_required"), (429, "rate_limited")]:
            with self.subTest(status=status):
                (self.root / "main.py").write_text(
                    "from media_platform.xhs.client import PlatformAccessError\n"
                    f"raise PlatformAccessError({status})\n", encoding="utf-8")
                result = self.collect()
                self.assertFalse(result.success)
                failure = result.coverage["failure"]
                self.assertEqual(failure["reason"], reason)
                self.assertEqual(failure["diagnostic_hint"]["http_status"], status)
                self.assertTrue(failure["rejection_confirmed"])
                self.assertNotIn("synthetic-private-cookie", result.message + json.dumps(result.coverage))

    def test_process_exit_does_not_masquerade_as_captcha(self):
        (self.root / "main.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
        result = self.collect()
        self.assertFalse(result.success)
        self.assertEqual(result.coverage["runtime"]["exit_code"], 7)
        self.assertEqual(result.coverage["failure"]["reason"], "collection_failed")

    def test_timeout_is_distinct_from_platform_rejection(self):
        (self.root / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        with patch.object(settings, "collect_timeout_mediacrawler_seconds", 0.2):
            result = self.collect()
        self.assertFalse(result.success)
        self.assertEqual(result.coverage["runtime"]["termination"], "timeout")
        self.assertEqual(result.coverage["failure"]["reason"], "collector_timeout")

    def test_cancellation_reaps_only_its_subprocess(self):
        (self.root / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        original = asyncio.create_subprocess_exec
        created = asyncio.Event()
        processes = []
        async def launch(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            created.set()
            return process
        async def run():
            task = asyncio.create_task(SocialMediaCollector().collect(TaskSpec(
                intent="取消回归", platforms=["xhs"], keywords=["agent"])))
            await asyncio.wait_for(created.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNotNone(processes[0].returncode)
        with patch("asyncio.create_subprocess_exec", side_effect=launch):
            asyncio.run(run())

    def test_timeout_cancellation_preserves_safe_diagnostics_before_temp_cleanup(self):
        self._assert_cancelled_diagnostics()

    def test_diagnostic_write_failure_does_not_swallow_cancellation(self):
        self._assert_cancelled_diagnostics(fail_write=True)

    def test_malformed_diagnostic_does_not_swallow_cancellation(self):
        self._assert_cancelled_diagnostics(malformed=True)

    def _assert_cancelled_diagnostics(self, *, fail_write=False, malformed=False):
        from src.collectors.social_media_collector import collection_diagnostics
        marker = self.root / "diagnostic-ready"
        (self.root / "main.py").write_text('''
import json, sys, time
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--save_data_path") + 1])
(out / "runtime_diagnostic.json").write_text(json.dumps({"browser_started": True,
    "login_detected": False, "search_started": False, "secret": "must-not-escape"}), encoding="utf-8")
Path("diagnostic-ready").touch()
time.sleep(30)
''', encoding="utf-8")
        if malformed:
            path = self.root / "main.py"
            path.write_text(path.read_text(encoding="utf-8").replace('"secret": "must-not-escape"',
                '"secret": "must-not-escape", "termination": {}'), encoding="utf-8")
        observations = []
        original_write = Path.write_text
        def diagnostic_write(path, *args, **kwargs):
            if fail_write and path.name == "process_diagnostic.json":
                raise OSError("synthetic disk full")
            return original_write(path, *args, **kwargs)
        async def run():
            with collection_diagnostics(observations.append):
                task = asyncio.create_task(SocialMediaCollector().collect(TaskSpec(
                    intent="取消诊断回归", platforms=["xhs"], keywords=["agent"])))
                try:
                    async with asyncio.timeout(5):
                        while not marker.exists():
                            await asyncio.sleep(0.01)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
        with patch.object(Path, "write_text", diagnostic_write):
            asyncio.run(run())
        self.assertEqual(len(observations), 1)
        self.assertTrue(observations[0]["browser_started"])
        self.assertFalse(observations[0]["login_detected"])
        self.assertFalse(observations[0]["search_started"])
        if fail_write:
            self.assertNotIn("termination", observations[0])
            self.assertNotIn("exit_code", observations[0])
        else:
            self.assertEqual(observations[0]["termination"], "cancelled")
            self.assertIsInstance(observations[0]["exit_code"], int)
        self.assertNotIn("must-not-escape", json.dumps(observations))
        self.assertNotIn("secret", observations[0])

    def test_evidence_timeout_persists_diagnostics_without_authorizing_replay(self):
        from src.conductor import evidence_collection as flow
        from src.conductor.collection_recovery import progress_location
        from unittest.mock import AsyncMock
        marker = self.root / "diagnostic-ready"
        (self.root / "main.py").write_text('''
import json, sys, time
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--save_data_path") + 1])
(out / "runtime_diagnostic.json").write_text(json.dumps({"browser_started": True,
    "login_detected": False, "search_started": False}), encoding="utf-8")
Path("diagnostic-ready").touch()
time.sleep(30)
''', encoding="utf-8")
        (self.root / "media_platform/xhs/field.py").write_text(
            "SearchSortType = SearchNoteType = str\n", encoding="utf-8")
        spec = TaskSpec(intent="超时诊断", evidence_collection={"source_evidence": "小红书",
            "queries": [{"name": "合成", "keyword": "agent"}], "raw_only": True,
            "include_images": False, "target_count": 1, "candidate_limit": 1, "initial_candidates": 1})
        # 实际阶段超时缩短到1秒，进度必须保留“未知”，不能因诊断存在而授权重发。
        with patch.object(flow, "execution_owner", return_value="synthetic-owner"), \
                patch.object(flow, "emit_progress"), \
                patch.object(settings, "semantic_execution_root", str(self.root / "execution")), \
                patch.object(settings, "data_prep_artifact_root", str(self.root / "artifacts")), \
                patch.dict(flow.STEP_TIMEOUT_SECONDS, discovery=1):
            result = asyncio.run(flow.collect_evidence({"task_spec": spec, "task_id": "diagnostic-task"},
                scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
                assess=AsyncMock(side_effect=AssertionError("未检索成功不能分析")),
                exclude=lambda *a, **kw: None, read_images=AsyncMock()))
            store, key = progress_location("synthetic-owner", "diagnostic-task")
            saved = store.read_json_if_exists(key, "progress.json")
        self.assertTrue(marker.exists())
        self.assertTrue(saved["last_collection_diagnostic"]["browser_started"])
        self.assertFalse(saved["last_collection_diagnostic"]["login_detected"])
        self.assertEqual(saved["last_collection_diagnostic"]["termination"], "cancelled")
        self.assertEqual(saved["last_collection_diagnostic"]["phase"], "discovery")
        self.assertTrue(saved["groups"][0]["discovery_inflight"])
        self.assertEqual(saved["groups"][0]["stop_reason"], "discovery_outcome_unknown")
        self.assertIsNone(saved["account_identity"])
        self.assertFalse(result["collection_outcome"]["ready_for_review"])

    def test_comment_timeout_updates_its_own_private_diagnostic(self):
        from src.conductor import evidence_collection as flow
        from src.conductor.collection_recovery import progress_location
        from src.collectors import social_media_collector as collector_module
        from src.collectors.base import CollectedItem, CollectResult
        from unittest.mock import AsyncMock
        spec = TaskSpec(intent="评论诊断", include_comments=True, evidence_collection={
            "source_evidence": "小红书", "queries": [{"name": "甲", "keyword": "agent"}],
            "raw_only": True, "include_images": False, "target_count": 1,
            "candidate_limit": 1, "initial_candidates": 1})
        async def collect(request):
            if request.include_comments:
                collector_module._DIAGNOSTIC_SINK.get()({"browser_started": True, "search_started": True})
                await asyncio.sleep(30)
            return CollectResult(True, "mediacrawler", items=[CollectedItem(content="合成正文",
                url="https://www.xiaohongshu.com/explore/0",
                metadata={"note_id": "0", "note_type": "normal"})])
        with patch.object(flow.SocialMediaCollector, "collect", side_effect=collect), \
                patch.object(flow, "execution_owner", return_value="synthetic-owner"), \
                patch.object(flow, "emit_progress"), patch.dict(flow.STEP_TIMEOUT_SECONDS, comments=0.01), \
                patch.object(settings, "semantic_execution_root", str(self.root / "execution")), \
                patch.object(settings, "data_prep_artifact_root", str(self.root / "artifacts")), \
                patch("src.collectors.source_access.open_access", return_value="https://www.xiaohongshu.com/explore/0"):
            asyncio.run(flow.collect_evidence({"task_spec": spec, "task_id": "comments-diagnostic"},
                scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
                assess=AsyncMock(), exclude=lambda *a, **kw: None, read_images=AsyncMock()))
            store, key = progress_location("synthetic-owner", "comments-diagnostic")
            saved = store.read_json_if_exists(key, "progress.json")
        self.assertEqual(saved["last_collection_diagnostic"]["phase"], "comments")
        self.assertTrue(saved["last_collection_diagnostic"]["browser_started"])
        self.assertTrue(saved["last_collection_diagnostic"]["search_started"])
        self.assertIsNone(saved["account_identity"])

    def test_deeply_nested_diagnostic_does_not_override_cancellation(self):
        from src.collectors.social_media_collector import collection_diagnostics
        observations = []
        async def cancelled(spec, run_dir, cookie):
            (run_dir / "runtime_diagnostic.json").write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
            raise asyncio.CancelledError()
        async def run():
            with collection_diagnostics(observations.append), \
                    patch.object(SocialMediaCollector, "_collect", side_effect=cancelled):
                with self.assertRaises(asyncio.CancelledError):
                    await SocialMediaCollector().collect(TaskSpec(
                        intent="深度诊断回归", platforms=["xhs"], keywords=["agent"]))
        asyncio.run(run())
        self.assertEqual(observations, [])

    def test_browser_setting_does_not_change_other_platforms(self):
        with patch.object(settings, "mc_cookie_wb", "synthetic-weibo"), patch.dict(os.environ, EXPECT_BROWSER="no"):
            result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
                intent="微博回归", platforms=["微博"], keywords=["agent"], max_items=1)))
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.coverage["connection"], {"platform": "wb", "source": "platform"})

    def test_runtime_login_choice_rejects_unknown_modes(self):
        from src.config.runtime_config import cast_value
        self.assertEqual(cast_value("mc_xhs_login_mode", "browser"), "browser")
        with self.assertRaises(ValueError):
            cast_value("mc_xhs_login_mode", "unrecognized")

    def test_xhs_failure_never_runs_generic_fallback(self):
        from src.collectors.base import CollectResult
        from src.conductor.nodes import collect as node
        from unittest.mock import AsyncMock
        crawler = types.SimpleNamespace(collect=AsyncMock(return_value=CollectResult(
            False, "mediacrawler", message="需要平台验证", coverage={"failure": {"reason": "challenge_required"}})))
        fallback = types.SimpleNamespace(collect=AsyncMock(side_effect=AssertionError("不能运行网页兜底")))
        with patch.object(node, "get_registry", return_value={"mediacrawler": crawler, "browser": fallback}), \
                patch.object(node, "emit_progress"), patch.object(node, "metrics_record"), patch.object(node, "domain_health_record"):
            result = asyncio.run(node.collect_node({"task_spec": TaskSpec(
                intent="回归", platforms=["xhs"], keywords=["agent"]), "collector_candidates": ["mediacrawler", "browser"]}))
        self.assertEqual(result["raw_dataset"], [])
        self.assertEqual(len(result["collector_attempts"]), 1)
        self.assertIn("coverage", result["collector_attempts"][0])
        fallback.collect.assert_not_called()

    def test_runtime_diagnostics_and_rejection_stop_at_http_seam(self):
        from scripts.mediacrawler_xhs import _install_runtime_diagnostics
        class PlatformAccessError(Exception):
            http_status = 461
        class Client:
            sent = 0
            async def pong(self):
                return True
            async def _request_once(self):
                self.sent += 1
                raise PlatformAccessError()
        class Crawler:
            async def launch_browser(self):
                return object()
            async def get_note_detail_async_task(self):
                return {"note_id": "synthetic"}
        module = types.ModuleType("media_platform.xhs.client")
        module.PlatformAccessError = PlatformAccessError
        with patch.dict(sys.modules, {"media_platform.xhs.client": module}):
            _install_runtime_diagnostics(types.SimpleNamespace(XiaoHongShuClient=Client, XiaoHongShuCrawler=Crawler), self.root)
        async def run():
            client, crawler = Client(), Crawler()
            await crawler.launch_browser()
            await client.pong()
            await crawler.get_note_detail_async_task()
            for _ in range(2):
                with self.assertRaises(PlatformAccessError):
                    await client._request_once()
            self.assertEqual(client.sent, 1)
        asyncio.run(run())
        diagnostic = json.loads((self.root / "runtime_diagnostic.json").read_text(encoding="utf-8"))
        self.assertTrue(diagnostic["browser_started"])
        self.assertTrue(diagnostic["login_detected"])
        self.assertTrue(diagnostic["challenge_detected"])
        self.assertEqual(diagnostic["note_detail_count"], 1)

    def test_browser_login_retries_only_transient_navigation(self):
        self._assert_login_navigation("Error", "Page.content: Unable to retrieve content because the page is navigating and changing the content.", recovered=True)

    def test_browser_login_does_not_retry_closed_page(self):
        self._assert_login_navigation("TargetClosedError", "Target page, context or browser has been closed", recovered=False)

    def test_browser_login_does_not_retry_other_playwright_errors(self):
        self._assert_login_navigation("Error", "synthetic unrelated error", recovered=False)

    def test_cookie_login_does_not_adopt_browser_retry(self):
        self._assert_login_navigation("Error", "Page.content: Unable to retrieve content because the page is navigating and changing the content.", recovered=False, browser=False)

    def _assert_login_navigation(self, error_name, message, *, recovered, browser=True):
        from tenacity import retry, retry_if_result, stop_after_attempt, wait_none
        from scripts.mediacrawler_xhs import _install_runtime_diagnostics
        error_type = type(error_name, (Exception,), {"__module__": "playwright._impl._errors"})
        class Login:
            calls = 0
            @retry(stop=stop_after_attempt(3), wait=wait_none(), retry=retry_if_result(lambda value: value is False))
            async def check_login_state(self, old_session):
                self.calls += 1
                if self.calls == 1:
                    raise error_type(message)
                # 保留上游原有False结果重试，不把页面跳转本身当登录成功。
                return self.calls == 3
        class Client:
            async def pong(self):
                return False
        core = types.SimpleNamespace(XiaoHongShuClient=Client, XiaoHongShuLogin=Login)
        with patch.dict(os.environ, MANGROVE_XHS_BROWSER_PROFILE=str(self.profile) if browser else ""):
            _install_runtime_diagnostics(core, self.root)
        async def run():
            login = Login()
            if recovered:
                self.assertTrue(await login.check_login_state("synthetic-original-session"))
                self.assertEqual(login.calls, 3)
            else:
                with self.assertRaises(error_type):
                    await login.check_login_state("synthetic-original-session")
                self.assertEqual(login.calls, 1)
        asyncio.run(run())
        diagnostic = json.loads((self.root / "runtime_diagnostic.json").read_text(encoding="utf-8"))
        if recovered:
            self.assertTrue(diagnostic["login_detected"])
            self.assertEqual(diagnostic["login_navigation_retries"], 1)
        else:
            self.assertNotIn("login_navigation_retries", diagnostic)

    def test_persistent_navigation_stops_at_original_login_budget(self):
        from tenacity import retry, retry_if_result, stop_after_attempt, wait_none, RetryError
        from scripts.mediacrawler_xhs import _install_runtime_diagnostics
        error_type = type("Error", (Exception,), {"__module__": "playwright._impl._errors"})
        class Login:
            calls = 0
            @retry(stop=stop_after_attempt(3), wait=wait_none(), retry=retry_if_result(lambda value: value is False))
            async def check_login_state(self, old_session):
                self.calls += 1
                raise error_type("Page.content: Unable to retrieve content because the page is navigating and changing the content.")
        core = types.SimpleNamespace(XiaoHongShuClient=type("Client", (), {}), XiaoHongShuLogin=Login)
        with patch.dict(os.environ, MANGROVE_XHS_BROWSER_PROFILE=str(self.profile)):
            _install_runtime_diagnostics(core, self.root)
        async def run():
            login = Login()
            with self.assertRaises(RetryError):
                await login.check_login_state("synthetic-original-session")
            self.assertEqual(login.calls, 3)
        asyncio.run(run())
        diagnostic = json.loads((self.root / "runtime_diagnostic.json").read_text(encoding="utf-8"))
        self.assertNotIn("login_detected", diagnostic)
        self.assertFalse(diagnostic["search_started"])

    def test_xhs_candidates_cannot_start_generic_engine_first(self):
        from src.conductor.nodes import collect as node
        from unittest.mock import AsyncMock
        fallback = types.SimpleNamespace(collect=AsyncMock(side_effect=AssertionError("不能运行通用来源")))
        with patch.object(node, "get_registry", return_value={"browser": fallback}), patch.object(node, "emit_progress"), \
                patch.object(node, "metrics_record"), patch.object(node, "domain_health_record"):
            result = asyncio.run(node.collect_node({"task_spec": TaskSpec(
                intent="回归", platforms=["xhs"], keywords=["agent"]), "collector_candidates": ["browser"]}))
        fallback.collect.assert_not_called()
        self.assertEqual(result["raw_dataset"], [])
        self.assertTrue(result["error"])

    def test_discovery_rejection_stops_all_evidence_groups(self):
        self._assert_evidence_stop(comments=False)

    def test_comment_rejection_stops_remaining_notes_and_groups(self):
        self._assert_evidence_stop(comments=True)

    def _assert_evidence_stop(self, *, comments):
        from src.collectors.base import CollectedItem, CollectResult
        from src.conductor import evidence_collection as flow
        from unittest.mock import AsyncMock
        spec = TaskSpec(intent="回归", include_comments=comments, evidence_collection={
            "source_evidence": "小红书", "queries": [{"name": "甲", "keyword": "甲"}, {"name": "乙", "keyword": "乙"}],
            "raw_only": True, "include_images": False, "target_count": 2, "candidate_limit": 2, "initial_candidates": 2})
        rejected = CollectResult(False, "mediacrawler", message="需要正常完成平台验证", coverage={
            "failure": {"reason": "challenge_required", "rejection_confirmed": True, "diagnostic_hint": {"http_status": 461}}})
        success = CollectResult(True, "mediacrawler", items=[CollectedItem(
            title="合成笔记", content="合成正文", url=f"https://www.xiaohongshu.com/explore/{index}",
            metadata={"note_id": str(index), "note_type": "normal", "publish_time": "2026-10-01T00:00:00+00:00"}) for index in range(2)])
        responses = [success, rejected] if comments else [rejected]
        collector = AsyncMock(side_effect=responses + [AssertionError("平台拒绝后不能继续下一组")])
        with patch.object(flow.SocialMediaCollector, "collect", collector), patch.object(flow, "execution_owner", return_value=None), \
                patch.object(flow, "emit_progress"), patch.object(settings, "data_prep_artifact_root", str(self.root / "artifacts")), \
                patch("src.collectors.source_access.open_access", return_value="https://www.xiaohongshu.com/explore/0"):
            result = asyncio.run(flow.collect_evidence({"task_spec": spec, "task_id": "offline-groups"},
                scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
                assess=AsyncMock(return_value={"relevant": True}), exclude=lambda *a, **kw: None,
                read_images=AsyncMock(side_effect=AssertionError("不读取图片"))))
        self.assertEqual(collector.await_count, 2 if comments else 1)
        groups = result["evidence_collection"]["groups"]
        self.assertEqual(groups[0]["stop_reason"], "collection_failed")
        self.assertEqual(groups[0]["batches"][-1]["coverage"]["failure"]["reason"], "challenge_required")
        self.assertFalse(result["collection_outcome"]["ready_for_review"])


if __name__ == "__main__":
    unittest.main()
