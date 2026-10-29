"""工作台登录方式的保存边界，不访问数据库或平台。"""
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from src.api.routes import config_routes as routes
from src.config import runtime_config as rc


class WorkspaceLoginTests(unittest.TestCase):
    def test_unknown_login_choice_is_rejected_before_storage(self):
        store = Mock()
        with patch.object(routes, 'get_store', return_value=store):
            with self.assertRaises(HTTPException) as caught:
                routes.set_self_config('mc_xhs_login_mode', routes.ConfigValueIn(value='invalid-mode'), user={'user_id':'synthetic-owner'})
        self.assertEqual(caught.exception.status_code, 400)
        store.config_set.assert_not_called()

    def test_browser_choice_is_saved_for_current_owner_only(self):
        store = Mock()
        with patch.object(routes, 'get_store', return_value=store):
            result = routes.set_self_config('mc_xhs_login_mode', routes.ConfigValueIn(value='browser'), user={'user_id':'synthetic-owner'})
        self.assertTrue(result['ok'])
        store.config_set.assert_called_once_with('synthetic-owner', 'mc_xhs_login_mode', 'browser', updated_by='synthetic-owner')

    def test_login_choice_is_described_as_select_outside_cookie_batch(self):
        store = Mock()
        store.config_all.return_value = {}
        store.cookie_health_all.return_value = {}
        groups = rc.describe(store)
        cookies = next(group['items'] for group in groups if group['key'] == 'cookies')
        self.assertNotIn('mc_xhs_login_mode', [item['key'] for item in cookies])
        mode = next(item for group in groups for item in group['items'] if item['key'] == 'mc_xhs_login_mode')
        self.assertEqual(mode['type'], 'select')
        self.assertEqual(mode['choices'], ['auto', 'cookie', 'browser'])
        self.assertFalse(mode['secret'])


if __name__ == '__main__':
    unittest.main()
