"""模型目录路由：供前端展示「供应商 · 模型」选项与默认值。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.config.runtime_config import USER_KEYS
from src.config.settings import settings
from src.config.user_ctx import effective, user_overrides_context
from src.llm import available_providers, list_models

from ..auth import get_current_user, get_store

router = APIRouter(prefix="/api/models", tags=["models"])

# 供应商内部 key -> 展示名（仅影响展示，路由仍用内部 key）
_PROVIDER_LABELS = {"local": "本地模型"}


@router.get("")
def get_models(user=Depends(get_current_user)):
    mine = get_store().config_all(user["user_id"]) or {}
    overrides = {key: value for key, value in mine.items() if key in USER_KEYS}
    with user_overrides_context(overrides):
        catalog = list_models()  # {provider: [models]}
        options = []
        for prov, models in catalog.items():
            for model in models:
                options.append({
                    "provider": prov,
                    "model": model,
                    "label": f"{_PROVIDER_LABELS.get(prov, prov)} · {model}",
                })
        dp = (settings.llm_default_provider or "deepseek").lower()
        default = next(
            (option for option in options if option["provider"] == dp),
            options[0] if options else None,
        )
        document_ref = effective("document_extraction_model")
        document_provider, separator, document_model = document_ref.partition("::")
        document_default = next(
            (
                option
                for option in options
                if separator
                and option["provider"] == document_provider
                and option["model"] == document_model
            ),
            default,
        )
        return {
            "options": options,
            "available": available_providers(),
            "pi_runtime_enabled": settings.pi_runtime_enabled,
            "pi_capability_host_enabled": settings.pi_capability_host_enabled,
            "default": default,
            "document_default": document_default,
            "document_default_source": (
                "user" if "document_extraction_model" in mine else "global"
            ),
        }
