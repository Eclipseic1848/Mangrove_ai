"""通过真实离线子进程核对登录诊断，既不访问平台也不暴露原始日志。"""
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_xhs_browser_session_unittest as session_fixture
from src.collectors.social_media_collector import SocialMediaCollector, collection_diagnostics
from src.config.settings import settings
from src.config.user_ctx import user_overrides_context
from src.conductor.collection_results import collection_stop_explanation
from src.conductor.task_spec import TaskSpec


class LoginDiagnosticTests(unittest.TestCase):
    def setUp(self):
        session_fixture.BrowserSessionTests.setUp(self)

    def install_failure(self, *, searched=False, wait=False, runtime_extra=None):
        marker = self.root / 'child-ready'
        runtime = {'browser_started': True, 'login_detected': False, 'search_started': searched,
                   'runtime_phase': 'login_check' if wait else 'login_interaction_required',
                   'login_mode': 'qrcode', 'login_interaction_required': not wait}
        runtime.update(runtime_extra or {})
        (self.root / 'main.py').write_text(f'''
import json,sys,time
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--save_data_path')+1])
(root/'runtime_diagnostic.json').write_text({json.dumps(runtime)!r},encoding='utf-8')
print('MANGROVE_MC_EXCEPTION_TYPE:PlaywrightTimeoutError',flush=True)
print('Authorization: Bearer synthetic-private-never-save; Cookie=synthetic-private-never-save',flush=True)
Path({str(marker)!r}).write_text('ready',encoding='utf-8')
{'time.sleep(30)' if wait else "raise RuntimeError('synthetic-private-never-save')"}
''', encoding='utf-8')
        return marker

    def test_interaction_is_actionable_without_becoming_authenticated_rejection(self):
        self.install_failure()
        for personal in (False, True):
            with self.subTest(personal=personal), (user_overrides_context({'mc_cookie_xhs': 'synthetic-personal'}) if personal else nullcontext()):
                result = asyncio.run(SocialMediaCollector().collect(TaskSpec(
                    intent='离线登录诊断', platforms=['小红书'], keywords=['synthetic'], max_items=1)))
            self.assertFalse(result.success)
            self.assertEqual(result.coverage['failure']['reason'], 'login_interaction_required')
            self.assertIsNot(result.coverage['failure'].get('rejection_confirmed'), True)
            self.assertEqual(result.coverage['operations']['search']['status'], 'unknown')
            self.assertIn('个人' if personal else '平台', result.message)
            self.assertNotIn('synthetic-private-never-save', json.dumps(result.coverage) + result.message)

    def test_search_already_started_is_never_relabelled_as_prelogin_failure(self):
        self.install_failure(searched=True)
        result = session_fixture.BrowserSessionTests.collect(self)
        self.assertEqual(result.coverage['failure']['reason'], 'collection_failed')
        self.assertEqual(result.coverage['operations']['search']['status'], 'unknown')

    def test_user_cancellation_preserves_stage_and_closed_hint_without_raw_log(self):
        marker = self.install_failure(wait=True)
        saved = []
        async def run():
            with collection_diagnostics(saved.append):
                task = asyncio.create_task(SocialMediaCollector().collect(TaskSpec(
                    intent='离线取消诊断', platforms=['小红书'], keywords=['synthetic'], max_items=1)))
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.02)
                self.assertTrue(marker.exists())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        asyncio.run(run())
        self.assertEqual(saved[-1]['runtime_phase'], 'login_check')
        self.assertEqual(saved[-1]['login_mode'], 'browser')
        self.assertEqual(saved[-1]['termination'], 'cancelled')
        self.assertEqual(saved[-1]['process_error_hint'], 'timeout')
        self.assertNotIn('synthetic-private-never-save', json.dumps(saved))

    def test_timeout_preserves_closed_diagnostics_in_failure_coverage(self):
        self.install_failure(wait=True)
        with patch.object(settings, 'collect_timeout_mediacrawler_seconds', 2):
            result = session_fixture.BrowserSessionTests.collect(self)
        self.assertFalse(result.success)
        self.assertEqual(result.coverage['failure']['reason'], 'collector_timeout')
        runtime = result.coverage['runtime']
        self.assertEqual(runtime['runtime_phase'], 'login_check')
        self.assertEqual(runtime['login_mode'], 'browser')
        self.assertEqual(runtime['termination'], 'timeout')
        self.assertEqual(runtime['process_error_hint'], 'timeout')
        self.assertNotIn('synthetic-private-never-save', json.dumps(result.coverage))

    def test_startup_timeout_uses_first_failure_and_filters_diagnostics(self):
        steps = {
            'init_script': {'status': 'completed', 'elapsed_ms': True},
            'new_page': {'status': ['failed'], 'elapsed_ms': 1},
            'homepage_navigation': {'status': 'timeout', 'elapsed_ms': 60000,
                                    'url': 'synthetic-private-never-save'},
            'create_client': {'status': 'started', 'elapsed_ms': 600001},
            'driver_shutdown': {'status': 'failed', 'elapsed_ms': 17},
            'unknown_step': {'status': 'failed', 'elapsed_ms': 1},
        }
        self.install_failure(wait=True, runtime_extra={
            'runtime_phase': 'browser_ready', 'startup_steps': steps,
        })
        with patch.object(settings, 'collect_timeout_mediacrawler_seconds', 2):
            result = session_fixture.BrowserSessionTests.collect(self)
        runtime = result.coverage['runtime']
        self.assertEqual(runtime['startup_steps'], {
            'homepage_navigation': {'status': 'timeout', 'elapsed_ms': 60000},
            'create_client': {'status': 'started', 'elapsed_ms': 600001},
            'driver_shutdown': {'status': 'failed', 'elapsed_ms': 17},
        })
        self.assertEqual(result.coverage['failure']['reason'], 'collector_timeout')
        group = {'name': '光大银行', 'stop_reason': 'collection_failed',
                 'batches': [{'coverage': result.coverage}]}
        diagnostic = {**runtime, 'phase': 'discovery', 'group_name': group['name']}
        message = collection_stop_explanation(group, diagnostic, 'platform')
        self.assertIn('打开小红书首页超时', message)
        self.assertIn('搜索尚未开始', message)
        self.assertNotIn('重新验证', message)

    def install_stuck_driver(self):
        (self.root / "media_platform/xhs/core.py").write_text('''
import asyncio, subprocess, sys
from pathlib import Path
from types import SimpleNamespace

class XiaoHongShuClient:
    async def get_note_by_keyword(self, **kwargs):
        raise AssertionError("不应发起搜索")

class Context:
    async def add_cookies(self, rows):
        pass
    async def add_init_script(self, **kwargs):
        pass
    async def new_page(self):
        async def goto(*args, **kwargs):
            raise TimeoutError("synthetic-private-never-save")
        return SimpleNamespace(goto=goto)

class async_playwright:
    async def __aenter__(self):
        driver = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        Path("driver-pid").write_text(str(driver.pid), encoding="utf-8")
    async def __aexit__(self, *error):
        await asyncio.Event().wait()

class XiaoHongShuCrawler:
    index_url = "https://www.xiaohongshu.com"
    async def launch_browser(self):
        return Context()
    async def create_xhs_client(self):
        raise AssertionError("不应准备登录")
    async def start(self):
        async with async_playwright():
            context = await self.launch_browser()
            await context.add_init_script(path="synthetic.js")
            page = await context.new_page()
            await page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
''', encoding="utf-8")
        (self.root / "main.py").write_text(
            "import asyncio\nfrom media_platform.xhs.core import XiaoHongShuCrawler\n"
            "asyncio.run(XiaoHongShuCrawler().start())\n", encoding="utf-8")
    def test_failed_driver_shutdown_reaps_actual_owned_process_tree(self):
        self.install_stuck_driver()
        with patch.object(settings, "collect_timeout_mediacrawler_seconds", 10), user_overrides_context({
                "mc_xhs_login_mode": "cookie", "mc_cookie_xhs": "web_session=synthetic"}):
            result = session_fixture.BrowserSessionTests.collect(self)
        self.assertFalse(result.success)
        runtime = result.coverage["runtime"]
        self.assertEqual(runtime["termination"], "startup_cleanup_failed")
        self.assertEqual(runtime["startup_steps"]["homepage_navigation"]["status"], "timeout")
        self.assertEqual(runtime["startup_steps"]["driver_shutdown"]["status"], "timeout")
        self.assertNotIn("synthetic-private-never-save", json.dumps(result.coverage))
        pid = int((self.root / "driver-pid").read_text(encoding="utf-8"))
        if os.name == "nt":
            processes = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                       capture_output=True, timeout=5, check=True)
            self.assertNotIn(f',"{pid}",'.encode("utf-8"), processes.stdout)
        else:
            stat = Path(f"/proc/{pid}/stat")
            try:
                # 内核已终止的僵尸进程没有可运行的浏览器资源。
                self.assertEqual(stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0], "Z")
            except FileNotFoundError:
                pass

    @unittest.skipUnless(os.name == "nt", "覆盖 Windows 的 taskkill 取消竞态")
    def test_cancel_during_driver_reap_preserves_unconfirmed_cleanup(self):
        self.install_stuck_driver()
        original_run = subprocess.run
        saved = []

        async def run():
            loop = asyncio.get_running_loop()
            started = asyncio.Event()
            release = threading.Event()

            def delayed_taskkill(args, **kwargs):
                if args[0] != "taskkill":
                    return original_run(args, **kwargs)
                loop.call_soon_threadsafe(started.set)
                if not release.wait(4):
                    raise AssertionError("未释放合成回收")
                original_run(args, **kwargs)
                # 真实结束合成子树，再模拟回收工具无法确认结果。
                return subprocess.CompletedProcess(args, 1)

            with patch("subprocess.run", side_effect=delayed_taskkill), collection_diagnostics(saved.append):
                task = asyncio.create_task(SocialMediaCollector().collect(TaskSpec(
                    intent="离线取消回归", platforms=["小红书"], keywords=["synthetic"], max_items=1)))
                try:
                    await asyncio.wait_for(started.wait(), timeout=9)
                    task.cancel()
                    await asyncio.sleep(0.05)
                    task.cancel()
                    await asyncio.sleep(0.05)
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        with patch.object(settings, "collect_timeout_mediacrawler_seconds", 12), user_overrides_context({
                "mc_xhs_login_mode": "cookie", "mc_cookie_xhs": "web_session=synthetic"}):
            asyncio.run(run())
        self.assertEqual(saved[-1]["termination"], "cancelled")
        self.assertTrue(saved[-1]["startup_tree_reap_failed"])
        self.assertEqual(saved[-1]["startup_steps"]["homepage_navigation"]["status"], "timeout")

    def test_interrupted_startup_step_only_explains_current_discovery(self):
        group = {'name': '光大银行', 'stop_reason': 'discovery_outcome_unknown'}
        diagnostic = {'phase': 'discovery', 'group_name': group['name'],
                      'search_started': False, 'termination': 'cancelled',
                      'startup_steps': {'init_script': {'status': 'completed', 'elapsed_ms': 1},
                                        'new_page': {'status': 'started', 'elapsed_ms': 0}}}
        for reason in ('discovery_outcome_unknown', 'time_budget'):
            with self.subTest(reason=reason):
                message = collection_stop_explanation({**group, 'stop_reason': reason}, diagnostic)
                self.assertIn('创建采集页面未完成', message)
                self.assertNotIn('超时', message)
        self.assertIn('搜索已发出', collection_stop_explanation(group, {**diagnostic, 'search_started': True}))
        for override in ({'group_name': '其他组'}, {'phase': 'comments'}, {'search_started': None},
                         {'termination': 'exited'}):
            with self.subTest(override=override):
                self.assertEqual(collection_stop_explanation(group, {**diagnostic, **override}), '候选检索结果尚未确认')

        diagnostic['startup_steps']['new_page']['status'] = 'cancelled'
        for shutdown in ('completed', 'timeout'):
            with self.subTest(shutdown=shutdown):
                diagnostic['startup_steps']['driver_shutdown'] = {'status': shutdown, 'elapsed_ms': 5}
                self.assertIn('创建采集页面未完成', collection_stop_explanation(group, diagnostic))
        after_login = {**diagnostic, 'runtime_phase': 'login_check',
                       'startup_steps': {'new_page': {'status': 'completed', 'elapsed_ms': 1},
                                         'driver_shutdown': {'status': 'timeout', 'elapsed_ms': 5000}}}
        self.assertIn('未确认登录成功', collection_stop_explanation(group, {**after_login, 'browser_started': True}))

        challenge = '小红书要求完成平台安全验证'
        group.update(stop_reason='collection_failed', message=challenge, batches=[{'coverage': {'failure': {
            'reason': 'challenge_required', 'rejection_confirmed': True}}}])
        diagnostic['startup_steps']['new_page']['status'] = 'failed'
        self.assertEqual(collection_stop_explanation(group, diagnostic), challenge)
