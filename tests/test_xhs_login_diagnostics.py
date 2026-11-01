"""通过真实离线子进程核对登录诊断，既不访问平台也不暴露原始日志。"""
import asyncio
from contextlib import nullcontext
import json
import time
import unittest
from unittest.mock import patch

from tests import test_xhs_browser_session_unittest as session_fixture
from src.collectors.social_media_collector import SocialMediaCollector, collection_diagnostics
from src.config.settings import settings
from src.config.user_ctx import user_overrides_context
from src.conductor.task_spec import TaskSpec


class LoginDiagnosticTests(unittest.TestCase):
    def setUp(self):
        session_fixture.BrowserSessionTests.setUp(self)

    def install_failure(self, *, searched=False, wait=False):
        marker = self.root / 'child-ready'
        runtime = {'browser_started': True, 'login_detected': False, 'search_started': searched,
                   'runtime_phase': 'login_check' if wait else 'login_interaction_required',
                   'login_mode': 'qrcode', 'login_interaction_required': not wait}
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
