"""模型连接产品 Interface。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from src.model_connections import (
    ConnectionBroker,
    ConnectionError,
    ConnectionValidationError,
    get_default_broker,
    public_presets,
)
from src.config.settings import settings
from src.model_connections.catalog import PRESETS_BY_ID

from ..auth import get_current_user, get_store, is_admin_role, require_admin

router = APIRouter(prefix="/api/model-connections", tags=["model-connections"])


class PersonalConnectionIn(BaseModel):
    api_key: str
    model: str | None = None


class NamedPersonalConnectionIn(PersonalConnectionIn):
    display_name: str = Field(min_length=1, max_length=80)


class ManagedConnectionIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=500)
    api_format: str
    model: str = Field(min_length=1, max_length=200)
    models: list[str] | None = Field(default=None, max_length=8)
    api_key: str = ""


class PlatformPresetConnectionIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    api_key: str
    model: str | None = None


class RetryModelsIn(BaseModel):
    model_ids: list[str] = Field(min_length=1, max_length=4)


class DefaultModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class ModelEnabledIn(BaseModel):
    enabled: bool


class ConnectionEnabledIn(BaseModel):
    enabled: bool


class UsagePreferenceIn(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)


class ManagedDiscoveryIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = ""
    model_ids: list[str] = Field(default_factory=list, max_length=8)


def get_connection_broker() -> ConnectionBroker:
    """FastAPI 依赖 Seam，测试注入真实 Broker + 假 Provider Transport。"""

    return get_default_broker()


@router.get("/presets")
def list_provider_presets(_user=Depends(get_current_user)):
    """返回平台预设卡片；不把 Endpoint、协议或鉴权细节转嫁给普通用户。"""

    return {"items": public_presets()}


@router.get("")
def list_model_connections(
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """列出当前用户可使用的连接，不返回 Endpoint 或秘密。"""

    return {
        "items": broker.list_connections(
            user["user_id"],
            can_manage=is_admin_role(user.get("role")),
        )
    }


@router.get("/preferences/default")
def get_default_model_preference(
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    return {"preference": broker.get_usage_preference(user["user_id"])}


@router.put("/preferences/default")
def set_default_model_preference(
    body: UsagePreferenceIn,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    try:
        return broker.set_usage_preference(
            user["user_id"],
            body.connection_id,
            body.model_id,
        )
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/imports/legacy")
def import_legacy_model_configuration(
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """显式扫描旧配置；只复制为待验证连接，不访问 Provider。"""

    store = get_store()
    user_id = user["user_id"]
    imported: list[dict[str, object]] = []
    personal = store.config_all(user_id)
    for preset_id, key_name in (
        ("deepseek", "deepseek_api_key"),
        ("qwen", "qwen_api_key"),
    ):
        secret = str(personal.get(key_name, "")).strip()
        if not secret:
            continue
        preset = PRESETS_BY_ID[preset_id]
        configured_url = str(getattr(settings, f"{preset_id}_base_url"))
        official_urls = {preset.base_url.rstrip("/")}
        if preset_id == "deepseek":
            official_urls.add(f"{preset.base_url.rstrip('/')}/v1")
        official = configured_url.rstrip("/") in official_urls
        imported.append(
            broker.import_legacy_connection(
                source_scope=user_id,
                source_key=key_name,
                owner_user_id=user_id,
                created_by=user_id,
                display_name=f"导入的 {preset.display_name}",
                base_url=configured_url,
                api_format=preset.api_format,
                model=str(getattr(settings, f"{preset_id}_model")),
                api_key=secret,
                preset_id=preset_id if official else None,
            )
        )
    if is_admin_role(user.get("role")):
        for preset_id, key_name in (
            ("deepseek", "deepseek_api_key"),
            ("qwen", "qwen_api_key"),
        ):
            secret = str(getattr(settings, key_name, "")).strip()
            if not secret:
                continue
            preset = PRESETS_BY_ID[preset_id]
            configured_url = str(getattr(settings, f"{preset_id}_base_url"))
            official_urls = {preset.base_url.rstrip("/")}
            if preset_id == "deepseek":
                official_urls.add(f"{preset.base_url.rstrip('/')}/v1")
            official = configured_url.rstrip("/") in official_urls
            imported.append(
                broker.import_legacy_connection(
                    source_scope="global",
                    source_key=key_name,
                    owner_user_id=None,
                    created_by=user_id,
                    display_name=f"导入的平台 {preset.display_name}",
                    base_url=configured_url,
                    api_format=preset.api_format,
                    model=str(getattr(settings, f"{preset_id}_model")),
                    api_key=secret,
                    preset_id=preset_id if official else None,
                )
            )
        local_models = [(settings.llm_model_name, settings.llm_base_url)]
        for raw in str(settings.local_extra_models).split(","):
            if "@" in raw:
                name, endpoint = raw.rsplit("@", 1)
                if name.strip() and endpoint.strip():
                    local_models.append((name.strip(), endpoint.strip()))
        for index, (model, endpoint) in enumerate(local_models):
            imported.append(
                broker.import_legacy_connection(
                    source_scope="global",
                    source_key=f"local_model:{index}",
                    owner_user_id=None,
                    created_by=user_id,
                    display_name=f"导入的本地模型 · {model}",
                    base_url=endpoint,
                    api_format="openai_chat_completions",
                    model=model,
                    api_key="",
                    locality="managed_private",
                )
            )
    return {"items": imported}


@router.put("/presets/{preset_id}")
async def configure_personal_preset(
    preset_id: str,
    body: PersonalConnectionIn,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """普通用户只选择 Preset、模型并填写自己的 Key。"""

    try:
        return await broker.configure_personal(
            owner_user_id=user["user_id"],
            preset_id=preset_id,
            api_key=body.api_key,
            model=body.model,
        )
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/presets/{preset_id}", status_code=201)
async def create_personal_preset(
    preset_id: str,
    body: NamedPersonalConnectionIn,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """创建独立命名个人连接；同一 Provider 可保存多套 Key。"""

    try:
        return await broker.create_personal(
            owner_user_id=user["user_id"],
            display_name=body.display_name,
            preset_id=preset_id,
            api_key=body.api_key,
            model=body.model,
        )
    except ConnectionValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "detail": str(exc),
                "model_results": [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "native_usage_json"
                    }
                    for item in exc.model_results
                ],
            },
        )
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/managed")
async def create_managed_connection(
    body: ManagedConnectionIn,
    admin=Depends(require_admin),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """管理员与超级管理员共享同一连接治理权限，不引入新角色。"""

    try:
        return await broker.register_managed(
            actor_user_id=admin["user_id"],
            display_name=body.display_name,
            base_url=body.base_url,
            api_format=body.api_format,
            model=body.model,
            models=body.models,
            api_key=body.api_key,
        )
    except ConnectionValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "detail": str(exc),
                "model_results": [
                    {key: value for key, value in item.items() if key != "native_usage_json"}
                    for item in exc.model_results
                ],
            },
        )
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/managed/discover")
async def discover_managed_connection(
    body: ManagedDiscoveryIn,
    _admin=Depends(require_admin),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    try:
        return await broker.discover_custom(
            base_url=body.base_url,
            api_key=body.api_key,
            model_ids=body.model_ids,
        )
    except (ConnectionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/managed/presets/{preset_id}", status_code=201)
async def publish_platform_preset_connection(
    preset_id: str,
    body: PlatformPresetConnectionIn,
    admin=Depends(require_admin),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """管理员用平台 Preset 验证并发布共享连接，技术端点不由表单输入。"""

    try:
        return await broker.configure_platform_preset(
            actor_user_id=admin["user_id"],
            display_name=body.display_name,
            preset_id=preset_id,
            api_key=body.api_key,
            model=body.model,
        )
    except ConnectionValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "detail": str(exc),
                "model_results": [
                    {key: value for key, value in item.items() if key != "native_usage_json"}
                    for item in exc.model_results
                ],
            },
        )
    except ConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{connection_id}/models/retry")
async def retry_personal_connection_models(
    connection_id: str,
    body: RetryModelsIn,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """只重验失败模型，复用当前连接的加密 Key。"""

    try:
        return await broker.retry_models(
            owner_user_id=user["user_id"],
            connection_id=connection_id,
            model_ids=body.model_ids,
            can_manage=is_admin_role(user.get("role")),
        )
    except ConnectionError as exc:
        status = 404 if str(exc) == "模型连接不存在" else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.put("/{connection_id}/default-model")
def change_personal_connection_default_model(
    connection_id: str,
    body: DefaultModelIn,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """默认模型只能从当前连接已启用的可用模型中选择。"""

    try:
        return broker.set_default_model(
            owner_user_id=user["user_id"],
            connection_id=connection_id,
            model_id=body.model,
            can_manage=is_admin_role(user.get("role")),
        )
    except ConnectionError as exc:
        status = 404 if str(exc) == "模型连接不存在" else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.patch("/{connection_id}/models/{model_id}")
def change_personal_connection_model_state(
    connection_id: str,
    model_id: str,
    body: ModelEnabledIn,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """独立启停连接模型；停用默认模型时不静默选择替代项。"""

    try:
        return broker.set_model_enabled(
            owner_user_id=user["user_id"],
            connection_id=connection_id,
            model_id=model_id,
            enabled=body.enabled,
            can_manage=is_admin_role(user.get("role")),
        )
    except ConnectionError as exc:
        status = 404 if str(exc) == "模型连接不存在" else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.patch("/{connection_id}")
def change_platform_connection_state(
    connection_id: str,
    body: ConnectionEnabledIn,
    _admin=Depends(require_admin),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """管理员启停平台连接；普通用户无管理入口。"""

    try:
        return broker.set_platform_enabled(connection_id, enabled=body.enabled)
    except ConnectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{connection_id}")
def delete_model_connection(
    connection_id: str,
    user=Depends(get_current_user),
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """个人连接只允许 Owner 删除，平台连接只允许管理权限删除。"""

    if not broker.delete_connection(
        connection_id,
        user["user_id"],
        can_manage=is_admin_role(user.get("role")),
    ):
        raise HTTPException(status_code=404, detail="连接不存在")
    return {"ok": True}
