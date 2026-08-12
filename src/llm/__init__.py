"""多模型 LLM 层：统一管理 deepseek / qwen / local 等供应商，支持运行时切换。"""
from .provider import (
    MultiModelProvider,
    get_provider,
    get_chat_model,
    chat,
    achat,
    available_providers,
    list_models,
)

__all__ = [
    "MultiModelProvider",
    "get_provider",
    "get_chat_model",
    "chat",
    "achat",
    "available_providers",
    "list_models",
]
