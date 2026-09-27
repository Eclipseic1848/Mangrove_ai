"""登录态验证不能把传输失败当成凭证失效。"""
import asyncio
import pytest
from src.api.routes import config_routes as cr
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize("key", cr._COOKIE_HEALTH_KEYS)
def test_transport_failure_is_unknown_for_every_platform(tmp_path, monkeypatch, key):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "health.db")))
    store.config_set("global", key, "synthetic-cookie")
    monkeypatch.setattr(cr, "get_store", lambda: store)
    async def fail(_target):
        raise TimeoutError("connection timed out")
    monkeypatch.setattr(cr, "_verify_target", fail)
    result = asyncio.run(cr.verify_config(cr.VerifyIn(target=key), {"user_id": "admin", "role": "admin"}))
    assert result["ok"] is False
    assert "connection timed out" in result["detail"]
    assert store.cookie_health_all()[key]["status"] == "unknown"

@pytest.mark.parametrize("key", ["jd_cookie", "tb_cookie", "pdd_cookie"])
def test_reachable_page_does_not_prove_login(monkeypatch, key):
    import httpx
    from src.config.user_ctx import user_overrides_context
    original = httpx.AsyncClient
    def respond(request):
        return httpx.Response(200, text="<html>Loading...</html>", request=request)
    monkeypatch.setattr(cr.httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    with user_overrides_context({key: "synthetic-cookie"}):
        with pytest.raises(cr.CookieProbeError) as error:
            asyncio.run(cr._verify_ecommerce_cookie(key))
    assert error.value.status == "unknown"
    assert error.value.reason == "identity_unverified"


@pytest.mark.parametrize("key", cr._MC_COOKIE_PLATFORM)
def test_empty_search_does_not_prove_login(monkeypatch, key):
    from src.collectors.base import CollectResult
    from src.collectors import registry
    from src.config.user_ctx import user_overrides_context
    class EmptyCollector:
        def is_available(self):
            return True
        async def collect(self, spec):
            raise AssertionError("缺少身份探针时不能搜索")
    monkeypatch.setattr(registry, "get_registry", lambda: {"mediacrawler": EmptyCollector()})
    with user_overrides_context({key: "synthetic-cookie"}):
        with pytest.raises(cr.CookieProbeError) as error:
            asyncio.run(cr._verify_mc_cookie(key))
    assert error.value.status == "unknown"
    assert error.value.reason == "identity_probe_unsupported"

@pytest.mark.parametrize("key", cr._COOKIE_HEALTH_KEYS)
def test_replacing_global_cookie_invalidates_previous_health(tmp_path, key):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "rotation.db")))
    store.config_set("global", key, "synthetic-old")
    store.cookie_health_set(key, "valid", "旧版本探测成功", "manual")
    store.config_set("global", key, "synthetic-new")
    assert key not in store.cookie_health_all()


@pytest.mark.parametrize("key", cr._MC_COOKIE_PLATFORM)
def test_public_search_results_do_not_prove_authenticated_identity(monkeypatch, key):
    from src.collectors.base import CollectedItem, CollectResult
    from src.collectors import registry
    from src.config.user_ctx import user_overrides_context

    class PublicSearchCollector:
        def is_available(self):
            return True

        async def collect(self, spec):
            raise AssertionError("不能通过公开搜索验证身份")

    monkeypatch.setattr(registry, "get_registry", lambda: {"mediacrawler": PublicSearchCollector()})
    with user_overrides_context({key: "synthetic-cookie"}):
        with pytest.raises(cr.CookieProbeError) as error:
            asyncio.run(cr._verify_mc_cookie(key))
    assert error.value.status == "unknown"
    assert error.value.reason == "identity_probe_unsupported"
    assert "暂不支持独立身份验证" in str(error.value)

@pytest.mark.parametrize("key", cr._COOKIE_HEALTH_KEYS)
def test_late_probe_cannot_validate_replaced_cookie(tmp_path, monkeypatch, key):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "late.db")))
    store.config_set("global", key, "synthetic-old")
    monkeypatch.setattr(cr, "get_store", lambda: store)
    async def replace_during_probe(_target):
        store.config_set("global", key, "synthetic-new")
        return "旧版本探测成功"
    monkeypatch.setattr(cr, "_verify_target", replace_during_probe)
    asyncio.run(cr.verify_config(cr.VerifyIn(target=key), {"user_id": "admin", "role": "admin"}))
    assert key not in store.cookie_health_all()

@pytest.mark.parametrize("operation", ["reset", "personal", "personal_reset"])
def test_health_invalidation_respects_configuration_scope(tmp_path, operation):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "scope.db")))
    key = "mc_cookie_xhs"
    store.config_set("global", key, "shared-test")
    store.cookie_health_set(key, "valid", "共享凭证有效", "manual")
    if operation == "reset":
        store.config_delete("global", key)
        assert key not in store.cookie_health_all()
    else:
        store.config_set("user-a", key, "personal-test")
        if operation == "personal_reset":
            store.config_delete("user-a", key)
        assert store.cookie_health_all()[key]["status"] == "valid"

@pytest.mark.parametrize("failure", ["expired", "secret"])
def test_late_failure_neither_exposes_old_secret_nor_invalidates_new_cookie(tmp_path, monkeypatch, failure):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "failed-probe.db")))
    key = "mc_cookie_xhs"
    old = "synthetic-old-sensitive-value"
    store.config_set("global", key, old)
    monkeypatch.setattr(cr, "get_store", lambda: store)
    async def fail(_target):
        store.config_set("global", key, "synthetic-new")
        if failure == "expired":
            raise cr.CookieProbeError("登录已过期", reason="login_required", status="invalid")
        raise RuntimeError("请求失败 " + old)
    monkeypatch.setattr(cr, "_verify_target", fail)
    result = asyncio.run(cr.verify_config(cr.VerifyIn(target=key), {"user_id": "admin", "role": "admin"}))
    assert old not in result["detail"]
    assert "凭证已更新" in result["detail"]
    assert key not in store.cookie_health_all()


def test_global_secret_snapshot_is_atomic_across_connections(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from src.config import runtime_config

    database = str(migrated_webui_database(tmp_path / "snapshot.db"))
    reader, writer = WebUIStore(database), WebUIStore(database)
    key = "mc_cookie_xhs"
    writer.config_set("global", key, "synthetic-old")
    with writer._conn() as conn:
        old_version = conn.execute("SELECT value FROM runtime_config WHERE scope='global' AND key=?", (key,)).fetchone()[0]
    original = reader._conn
    rotated = False

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            return self.cursor.fetchall()

        def fetchone(self):
            nonlocal rotated
            row = self.cursor.fetchone()
            self.cursor.close()
            if not rotated:
                rotated = True
                writer.config_set("global", key, "synthetic-new")
            return row

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, parameters=()):
            cursor = self.conn.execute(sql, parameters)
            return Cursor(cursor) if "FROM runtime_config " in sql else cursor

    @contextmanager
    def interleaved_connection():
        with original() as conn:
            yield Connection(conn)

    monkeypatch.setattr(reader, "_conn", interleaved_connection)
    version, value = runtime_config.global_secret_snapshot(reader, key)
    assert rotated
    assert (version, value) == (old_version, "synthetic-old")
    assert writer.cookie_health_set(key, "valid", "旧快照", "manual", expected_version=version) is False


@pytest.mark.parametrize("key", cr._COOKIE_HEALTH_KEYS)
@pytest.mark.parametrize("baseline", ["", "synthetic-env-cookie"])
def test_deleted_shared_cookie_does_not_reuse_process_cache(tmp_path, monkeypatch, key, baseline):
    from src.config import runtime_config as rc

    database = str(migrated_webui_database(tmp_path / "deleted.db"))
    reader, writer = WebUIStore(database), WebUIStore(database)
    monkeypatch.setattr(rc, "_BASELINE", {key: baseline})
    writer.config_set("global", key, "synthetic-shared-cookie")
    monkeypatch.setattr(rc.settings, key, "synthetic-shared-cookie")
    writer.config_delete("global", key)
    assert rc.global_secret_snapshot(reader, key) == ("", baseline)


def test_startup_override_keeps_environment_baseline_after_remote_delete(tmp_path, monkeypatch):
    from src.config import runtime_config as rc

    database = str(migrated_webui_database(tmp_path / "startup.db"))
    reader, writer = WebUIStore(database), WebUIStore(database)
    key = "mc_cookie_xhs"
    monkeypatch.setattr(rc, "_BASELINE", {})
    monkeypatch.setattr(rc.settings, key, "synthetic-env-cookie")
    monkeypatch.setattr(rc, "_after_set", lambda key: None)
    writer.config_set("global", key, "synthetic-shared-cookie")
    assert rc.apply_global_overrides(reader) == 1
    assert getattr(rc.settings, key) == "synthetic-shared-cookie"
    writer.config_delete("global", key)
    assert rc.global_secret_snapshot(reader, key) == ("", "synthetic-env-cookie")


@pytest.mark.parametrize("source_scope, source_key", [("user-a", "mc_cookie_xhs"), ("global", "mc_cookie_dy")])
def test_secret_snapshot_rejects_reference_from_other_identity(tmp_path, source_scope, source_key):
    from src.config.secret_refs import SecretRefResolutionError

    store = WebUIStore(str(migrated_webui_database(tmp_path / "identity.db")))
    store.config_set(source_scope, source_key, "synthetic-sensitive-value")
    with store._conn() as conn:
        reference = conn.execute("SELECT value FROM runtime_config WHERE scope=? AND key=?", (source_scope, source_key)).fetchone()[0]
        conn.execute("INSERT INTO runtime_config (scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
                     ("global", "mc_cookie_xhs", reference, "test", "test"))
    with pytest.raises(SecretRefResolutionError, match="SecretRef 无法解析"):
        store.config_secret_snapshot("global", "mc_cookie_xhs")


def test_empty_frozen_cookie_never_falls_back_to_process_setting(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "empty.db")))
    key = "mc_cookie_xhs"
    store.config_set("global", key, "")
    monkeypatch.setattr(cr, "get_store", lambda: store)
    monkeypatch.setattr(cr.settings, key, "synthetic-stale-process-value")
    calls = []

    async def probe(target):
        calls.append(target)
        return "错误使用进程内旧凭证"

    monkeypatch.setattr(cr, "_verify_target", probe)
    with pytest.raises(cr.CookieProbeError) as error:
        asyncio.run(cr._verify_global_cookie(key, "manual"))
    assert error.value.reason == "credential_missing"
    assert calls == []
    assert store.cookie_health_all()[key]["status"] == "unknown"
