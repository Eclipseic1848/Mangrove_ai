"""模型启停按真实目标生效，覆盖目录、旧直连与并发签发。"""
from types import SimpleNamespace
import json

import httpx
import pytest

from src.account_execution import ExecutionAuthorization, execution_context
from src.config.user_ctx import user_overrides_context
from src.llm import provider
from src.model_connections import ConnectionBroker, ConnectionError, GrantError
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def broker(tmp_path):
    database = migrated_webui_database(tmp_path / "models.db")
    seed_execution_owner(database, "admin-a")
    return ConnectionBroker(repository=ModelConnectionRepository(str(database)), vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})),
        resolver=lambda host: ["192.168.1.10"])


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
async def test_direct_model_catalog_and_resolution_follow_exact_target(broker, monkeypatch, cloud):
    import src.model_connections as connections
    monkeypatch.setattr(connections, "get_default_broker", lambda: broker)
    name, key = ("deepseek", "test-cloud-key") if cloud else ("local", "")
    first, second = "http://192.168.1.10:8010/v1", "http://192.168.1.10:8011/v1"
    create = lambda endpoint: broker.register_managed(actor_user_id="admin-a", display_name="测试模型", base_url=endpoint, api_format="openai_chat_completions",
        model="same-name", models=["same-name"], api_key=key)
    a, b = await create(first), await create(second)
    instance = object.__new__(provider.MultiModelProvider)
    instance._default = name
    instance._profiles = {name: SimpleNamespace(model="same-name", base_url=first, api_key=key if cloud else "local", is_configured=lambda: True)}
    monkeypatch.setattr(instance, "available_providers", lambda: [name])
    monkeypatch.setattr(instance, "_resolve_extra_body", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "LOCAL_MODELS", {"unmanaged": "http://192.168.1.10:8012/v1"} if not cloud else {})
    assert instance.resolve_model(name, model="same-name").base_url == first
    assert "same-name" in instance.list_models()[name]
    broker.set_model_enabled(owner_user_id="admin-a", connection_id=a["connection_id"], model_id="same-name", enabled=False, can_manage=True)
    with pytest.raises(provider.ModelUnavailableError):
        instance.resolve_model(name, model="same-name")
    assert "same-name" not in instance.list_models()[name]
    assert broker.legacy_model_available(second, "same-name", key)
    assert broker.legacy_model_available(first, "unmanaged", key)
    if not cloud:
        assert "unmanaged" in instance.list_models()[name]
    else:
        # 个人凭据是独立授权范围，不因平台端点停用而被拒绝。
        with user_overrides_context({"deepseek_api_key": "independent-personal-key"}):
            assert instance.resolve_model(name, model="same-name").api_key == "independent-personal-key"
    assert broker.delete_connection(a["connection_id"], "admin-a", can_manage=True)
    assert not broker.legacy_model_available(first, "same-name", key)
    assert broker.legacy_model_available(second, "same-name", key)


@pytest.mark.asyncio
async def test_grant_cannot_be_inserted_after_concurrent_model_disable(broker, monkeypatch):
    connection = await broker.register_managed(actor_user_id="admin-a",
        display_name="并发验证", base_url="http://192.168.1.10:8010/v1", api_format="openai_chat_completions",
        model="default", models=["default", "secondary"], api_key="")
    connection_id = connection["connection_id"]
    binding = broker.freeze_connection("admin-a", connection_id)
    repository = broker._repository
    original = repository.create_grant
    def disable_before_insert(**kwargs):
        broker.set_model_enabled(owner_user_id="admin-a", connection_id=connection_id, model_id="secondary", enabled=False, can_manage=True)
        return original(**kwargs)
    monkeypatch.setattr(repository, "create_grant", disable_before_insert)
    with execution_context(ExecutionAuthorization("admin-a", 0)), pytest.raises(GrantError, match="已变化"):
        broker.issue_grant(owner_user_id="admin-a", connection_id=connection_id, connection_version=binding.connection_version,
            model_id="secondary", task_id="race-task", revision=1, run_id="race-run", purpose="agent_inference")
    broker.set_model_enabled(owner_user_id="admin-a", connection_id=connection_id, model_id="secondary", enabled=True, can_manage=True)
    with repository._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_connection_grants WHERE run_id='race-run'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_multi_model_delete_retains_each_unavailable_target(broker):
    connection = await broker.register_managed(actor_user_id="admin-a", display_name="多型号连接",
        base_url="http://192.168.1.10:8010/v1", api_format="openai_chat_completions",
        model="first", models=["first", "second"], api_key="")
    assert broker.delete_connection(connection["connection_id"], "admin-a", can_manage=True)
    for name in ("first", "second"):
        assert not broker.legacy_model_available("http://192.168.1.10:8010/v1", name, "local")


@pytest.mark.asyncio
async def test_stopped_current_preset_never_resurrects_old_import(broker):
    # 新旧预设不是配置编辑产生的 previous_id 链，也必须遵守目录退役。
    from src.model_connections.catalog import PRESETS_BY_ID
    broker._resolver = lambda host: ["8.8.8.8"]
    preset = PRESETS_BY_ID["deepseek"]
    key = "synthetic-shared-key"
    imported = broker.import_legacy_connection(source_scope="global", source_key="deepseek_api_key",
        owner_user_id=None, created_by="admin-a", display_name="旧导入", base_url=preset.base_url,
        api_format=preset.api_format, model="deepseek-flash", api_key=key, preset_id="deepseek")
    # 复现升级前已保存的旧预设版本，而不是在当前版本再次导入。
    with broker._repository._conn() as conn:
        conn.execute("UPDATE model_connections SET preset_version=? WHERE connection_id=?",
            ("historical-preset", imported["connection_id"]))
        conn.commit()
    await broker.retry_models(owner_user_id="admin-a", connection_id=imported["connection_id"],
        model_ids=["deepseek-flash", "deepseek-v4-pro"], can_manage=True)
    current = await broker.configure_platform_preset(actor_user_id="admin-a", preset_id="deepseek",
        display_name="当前连接", api_key=key, model="deepseek-flash", verify_all=True)
    def old_pro_visible():
        old = next(item for item in broker.list_connections("admin-a", can_manage=True) if item["connection_id"] == imported["connection_id"])
        return next(model for model in old["models"] if model["model_id"] == "deepseek-v4-pro")["current_catalog"]
    assert not old_pro_visible()
    broker.set_model_enabled(owner_user_id="admin-a", connection_id=current["connection_id"],
        model_id="deepseek-v4-pro", enabled=False, can_manage=True)
    assert not old_pro_visible()
    assert not broker.legacy_model_available(preset.base_url, "deepseek-v4-pro", key)
    assert broker.delete_connection(current["connection_id"], "admin-a", can_manage=True)
    assert not old_pro_visible()
    for name in ("deepseek-flash", "deepseek-v4-pro"):
        assert not broker.legacy_model_available(preset.base_url, name, key)


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["pending", "partial", "failed"])
async def test_unverified_replacement_keeps_existing_model_available(broker, replacement):
    from src.model_connections.catalog import PRESETS_BY_ID
    broker._resolver = lambda host: ["8.8.8.8"]
    preset, key, model = PRESETS_BY_ID["deepseek"], "synthetic-shared-key", "deepseek-v4-pro"
    old = broker._repository.create_managed(created_by="admin-a", display_name="原正常型号",
        base_url=preset.base_url, model=model, api_format=preset.api_format, locality="public_external",
        ciphertext=broker._vault.encrypt(key), key_hint="", verified_at="2026-09-01",
        preset_id=preset.preset_id, preset_version="historical-preset")
    if replacement == "pending":
        current = broker.import_legacy_connection(source_scope="global", source_key="deepseek_api_key",
            owner_user_id=None, created_by="admin-a", display_name="未验证导入", base_url=preset.base_url,
            api_format=preset.api_format, model=model, api_key=key, preset_id=preset.preset_id)
    else:
        if replacement == "failed":
            def respond(request):
                if json.loads(request.content)["model"] == model:
                    return httpx.Response(404, json={"error": {"message": "model not found"}})
                return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
            broker._transport = httpx.MockTransport(respond)
        current = await broker.configure_platform_preset(actor_user_id="admin-a", preset_id=preset.preset_id,
            display_name="仅 Flash 通过", api_key=key, model="deepseek-flash", verify_all=replacement == "failed")
    def verify_original():
        item = broker.get_connection("admin-a", old["connection_id"])
        assert item["models"][0]["current_catalog"]
        assert broker.legacy_model_available(preset.base_url, model, key)
    verify_original()
    assert broker.delete_connection(current["connection_id"], "admin-a", can_manage=True)
    verify_original()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["removed", "relocated", "dedicated_default"])
async def test_removed_legacy_local_model_leaves_new_catalog_without_changing_history(broker, monkeypatch, replacement):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "llm_base_url", "http://192.168.1.10:8010/v1")
    monkeypatch.setattr(settings, "llm_model_name", "current-primary")
    configured = "current-second@http://192.168.1.10:8011/v1,current-third@http://192.168.1.10:8012/v1"
    if replacement == "dedicated_default":
        configured += ",current-primary@http://192.168.1.10:8014/v1"
    monkeypatch.setattr(settings, "local_extra_models", configured + ",removed-local@http://192.168.1.10:8013/v1")
    imported = {}
    for index, name in enumerate(("current-primary", "current-second", "current-third", "removed-local")):
        item = broker.import_legacy_connection(source_scope="global", source_key=name,
            owner_user_id=None, created_by="admin-a", display_name=name,
            base_url=f"http://192.168.1.10:{8014 if replacement == 'dedicated_default' and index == 0 else 8010 + index}/v1",
            api_format="openai_chat_completions", model=name, api_key="", locality="managed_private")
        await broker.retry_models(owner_user_id="admin-a", connection_id=item["connection_id"],
            model_ids=[name], can_manage=True)
        imported[name] = item["connection_id"]

    broker.set_usage_preference("admin-a", imported["removed-local"], "removed-local")
    if replacement == "relocated":
        configured += ",removed-local@http://192.168.1.10:8030/v1"
    monkeypatch.setattr(settings, "local_extra_models", configured)
    visible = {model["model_id"] for connection in broker.list_connections("admin-a")
        for model in connection["models"] if model["current_catalog"]}
    assert visible == {"current-primary", "current-second", "current-third"}
    old = broker.get_connection("admin-a", imported["removed-local"])
    assert old["status"] == "verified" and old["models"][0]["enabled"]
    assert old["models"][0]["status"] == "available"
    assert broker.freeze_connection("admin-a", old["connection_id"]).connection_id == old["connection_id"]
    assert not broker.get_usage_preference("admin-a")["available"]
    with pytest.raises(ConnectionError):
        broker.set_usage_preference("admin-a", old["connection_id"], "removed-local")
    with pytest.raises(ConnectionError):
        broker.freeze_connection_for_new_task("admin-a", old["connection_id"], model_id="removed-local")
    # 同名独立连接不是旧配置残留，不能一起退出目录。
    custom = await broker.register_managed(actor_user_id="admin-a", display_name="独立同名连接",
        base_url="http://192.168.1.10:8020/v1", api_format="openai_chat_completions",
        model="removed-local", api_key="")
    assert custom["models"][0]["current_catalog"]
    assert broker.freeze_connection_for_new_task("admin-a", custom["connection_id"]).model == "removed-local"
