"""持久健康状态只适用于本次凭证和验证环境。"""
import asyncio
import platform
import sys

import pytest

from src.api.routes import config_routes as cr
from src.api.store import WebUIStore
from src.config import runtime_config as rc
from tests.database_migration_helpers import migrated_webui_database
from tests.test_social_collection_isolation import crawler


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = WebUIStore(str(migrated_webui_database(tmp_path / "health.db")))
    monkeypatch.setattr(cr, "get_store", lambda: value)
    return value


@pytest.mark.parametrize("key", cr._COOKIE_HEALTH_KEYS)
def test_environment_credential_rotation_invalidates_saved_health(store, monkeypatch, key):
    monkeypatch.setattr(rc, "_BASELINE", {key: "synthetic-old"})
    store.cookie_health_set(key, "valid", "身份通过", "manual", expected_version="")
    assert store.cookie_health_all()[key]["status"] == "valid"
    monkeypatch.setitem(rc._BASELINE, key, "synthetic-new")
    assert store.cookie_health_all()[key]["status"] == "unknown"


@pytest.mark.parametrize("changed", ["node", "proxy", "collector", "python", "environment_proxy"])
def test_other_probe_environment_cannot_reuse_health(store, monkeypatch, changed):
    key = "mc_cookie_xhs"
    store.config_set("global", key, "synthetic-cookie")
    store.cookie_health_set(key, "valid", "身份通过", "manual")
    if changed == "node":
        monkeypatch.setattr(platform, "node", lambda: "another-node")
    elif changed == "proxy":
        monkeypatch.setattr(cr.settings, "mc_enable_ip_proxy", True)
        monkeypatch.setattr(cr.settings, "mc_static_proxy_url", "http://synthetic.invalid:9999")
    elif changed == "collector":
        monkeypatch.setattr(cr.settings, "mediacrawler_path", "another-collector")
    elif changed == "python":
        monkeypatch.setattr(cr.settings, "mediacrawler_python", "another-python")
    else:
        monkeypatch.setenv("HTTPS_PROXY", "http://synthetic.invalid:9999")
    assert store.cookie_health_all()[key]["status"] == "unknown"


def test_legacy_record_cannot_claim_current_identity(store):
    key = "mc_cookie_xhs"
    with store._conn() as connection:
        connection.execute("INSERT INTO cookie_health (key,status,message,checked_at,checked_by) VALUES (?,?,?,?,?)",
                           (key, "valid", "历史绿标", "2020-01-01", "manual"))
    assert store.cookie_health_all()[key]["status"] == "unknown"


def test_late_environment_probe_is_discarded(store, monkeypatch):
    key = "mc_cookie_xhs"
    monkeypatch.setattr(rc, "_BASELINE", {key: "synthetic-old"})
    async def verify(_key):
        monkeypatch.setitem(rc._BASELINE, key, "synthetic-new")
        return "旧身份通过"
    monkeypatch.setattr(cr, "_verify_target", verify)
    with pytest.raises(cr.CookieProbeError, match="验证环境已变化"):
        asyncio.run(cr._verify_global_cookie(key, "manual"))
    assert key not in store.cookie_health_all()


def test_binding_survives_store_reopen_without_exposing_digest(store):
    key = "mc_cookie_xhs"
    store.config_set("global", key, "synthetic-cookie")
    store.cookie_health_set(key, "valid", "身份通过", "manual")
    result = WebUIStore(store.db_path).cookie_health_all()[key]
    assert result["status"] == "valid"
    assert "verification_binding" not in result


def test_empty_collector_path_stays_unconfigured(store, monkeypatch):
    from src.collectors import registry
    from src.collectors.social_media_collector import SocialMediaCollector

    monkeypatch.setattr(cr.settings, "mediacrawler_path", "")
    monkeypatch.setattr(registry, "get_registry", lambda: {"mediacrawler": SocialMediaCollector()})
    async def forbidden(*args, **kwargs):
        raise AssertionError("空配置不能启动采集进程")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    store.config_set("global", "mc_cookie_xhs", "synthetic-cookie")
    with pytest.raises(cr.CookieProbeError, match="MediaCrawler 未配置"):
        asyncio.run(cr._verify_global_cookie("mc_cookie_xhs", "manual"))
    assert store.cookie_health_all()["mc_cookie_xhs"]["status"] == "unknown"


def test_probe_executes_frozen_environment_during_a_b_a_change(store, crawler, monkeypatch):
    from src.collectors import registry
    from src.collectors.social_media_collector import SocialMediaCollector

    (crawler / "media_platform/xhs/core.py").write_text('''
class Client:
    async def query_self(self):
        return {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": "synthetic"}}}
class XiaoHongShuCrawler:
    xhs_client = Client()
''', encoding="utf-8")
    (crawler / "main.py").write_text('''
import asyncio, os
from media_platform.xhs.core import XiaoHongShuCrawler
assert os.environ["NO_PROXY"] == "environment-A"
asyncio.run(XiaoHongShuCrawler().search())
''', encoding="utf-8")
    monkeypatch.setenv("NO_PROXY", "environment-A")
    store.config_set("global", "mc_cookie_xhs", "synthetic-cookie")
    monkeypatch.setattr(registry, "get_registry", lambda: {"mediacrawler": SocialMediaCollector()})
    original = SocialMediaCollector._collect
    async def changed(self, *args, **kwargs):
        monkeypatch.setenv("NO_PROXY", "environment-B")
        monkeypatch.setattr(cr.settings, "mediacrawler_path", "missing-environment-B")
        monkeypatch.setattr(cr.settings, "mediacrawler_python", "missing-python-B")
        try:
            return await original(self, *args, **kwargs)
        finally:
            monkeypatch.setenv("NO_PROXY", "environment-A")
            monkeypatch.setattr(cr.settings, "mediacrawler_path", str(crawler))
            monkeypatch.setattr(cr.settings, "mediacrawler_python", sys.executable)
    monkeypatch.setattr(SocialMediaCollector, "_collect", changed)
    assert "通过" in asyncio.run(cr._verify_global_cookie("mc_cookie_xhs", "manual"))
    assert store.cookie_health_all()["mc_cookie_xhs"]["status"] == "valid"
