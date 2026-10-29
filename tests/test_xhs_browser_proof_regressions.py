"""浏览器会话不能沿用未参与采集的 Cookie 身份证明；全部使用合成内存数据。"""
import copy
import hashlib
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from src.config.settings import settings
from src.config.user_ctx import user_overrides_context
from src.conductor import evidence_collection as flow
from src.conductor.collection_recovery import adopt_recovery, recovery_request


class XhsBrowserProofTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, overrides, *, platform_mode="auto", platform_cookie="synthetic-platform-cookie"):
        environment = {
            "xhs_login_mode": overrides.get("mc_xhs_login_mode", platform_mode),
            "xhs_browser_selected": overrides.get("mc_xhs_login_mode") == "browser",
            "xhs_user_data_dir": "/synthetic/xhs_user_data_dir",
        }
        proof = {"account_ref": "a" * 64, "environment": "synthetic-environment",
                 "checked_at": time.time(), "binding": "synthetic-cookie-proof"}
        lower = AsyncMock(return_value={"synthetic_result": True})
        with patch.object(settings, "mc_cookie_xhs", platform_cookie), \
                patch.object(settings, "mc_xhs_login_mode", platform_mode), \
                user_overrides_context(overrides), \
                patch("src.config.cookie_probe_binding.capture_probe_environment", return_value=environment), \
                patch("src.config.cookie_identity.read_identity", return_value=proof) as read_identity, \
                patch.object(flow, "_collect_evidence", lower):
            result = await flow.collect_evidence({"task_id": "synthetic-task"}, scope=None, queries=[],
                assess=None, exclude=None, read_images=None)
        self.assertEqual(result, {"synthetic_result": True})
        self.assertEqual(lower.await_count, 1)
        return lower.await_args.kwargs, read_identity.call_args_list, environment, proof

    async def test_personal_cookie_keeps_its_original_proof_and_source(self):
        values, reads, environment, proof = await self.capture({"mc_cookie_xhs": "synthetic-personal-cookie"})
        self.assertIs(values["credential_proof"], proof)
        self.assertEqual(values["credential_source"], "personal")
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0].args, ("mc_cookie_xhs", "synthetic-personal-cookie", environment))

    async def test_platform_cookie_keeps_its_original_proof_and_source(self):
        values, reads, environment, proof = await self.capture({}, platform_mode="cookie")
        self.assertIs(values["credential_proof"], proof)
        self.assertEqual(values["credential_source"], "platform")
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0].args, ("mc_cookie_xhs", "synthetic-platform-cookie", environment))

    async def test_explicit_browser_never_reads_personal_cookie_proof(self):
        values, reads, _, _ = await self.capture({
            "mc_cookie_xhs": "synthetic-personal-cookie", "mc_xhs_login_mode": "browser"})
        self.assertIsNone(values["credential_proof"])
        self.assertEqual(values["credential_source"], "platform")
        self.assertEqual(reads, [])

    async def test_platform_browser_never_uses_cookie_proof_even_without_cookie(self):
        for cookie in ("", "synthetic-stale-platform-cookie"):
            with self.subTest(cookie_configured=bool(cookie)):
                values, reads, _, _ = await self.capture({}, platform_mode="browser", platform_cookie=cookie)
                self.assertIsNone(values["credential_proof"])
                self.assertEqual(values["credential_source"], "platform")
                self.assertEqual(reads, [])

    async def test_platform_browser_setting_does_not_override_personal_cookie(self):
        values, reads, _, proof = await self.capture({"mc_cookie_xhs": "synthetic-personal-cookie"},
                                                    platform_mode="browser")
        self.assertIs(values["credential_proof"], proof)
        self.assertEqual(values["credential_source"], "personal")
        self.assertEqual(len(reads), 1)

    async def test_browser_keeps_existing_credential_binding_contract(self):
        bindings = []
        for cookie in ("synthetic-cookie-a", "synthetic-cookie-b"):
            values, _, environment, _ = await self.capture({
                "mc_cookie_xhs": cookie, "mc_xhs_login_mode": "browser"})
            expected = {"credentials": {"mc_cookie_xhs": {"source": "personal", "value": cookie}},
                        "execution": environment}
            self.assertEqual(values["credential_binding"], hashlib.sha256(
                json.dumps(expected, sort_keys=True).encode("utf-8")).hexdigest())
            bindings.append(values["credential_binding"])
        self.assertNotEqual(*bindings)

    async def test_browser_cannot_use_unrelated_cookie_proof_to_authorize_recovery(self):
        values, _, _, proof = await self.capture({}, platform_mode="browser")
        fields = {"owner": "synthetic-owner", "task": "synthetic-task", "credential_binding": "old"}
        digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        saved = {"binding_fields": fields, "binding": digest, "account_identity": proof,
                 "credential_source": "platform", "groups": [{"done": False}], "recovery_attempts": []}
        before = copy.deepcopy(saved)
        with recovery_request(digest, "b" * 32), self.assertRaisesRegex(ValueError, "无法证明同一账号"):
            adopt_recovery(saved, fields, digest, values["credential_proof"], values["credential_source"])
        self.assertEqual(saved, before)


if __name__ == "__main__":
    unittest.main()
