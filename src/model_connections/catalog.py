"""平台维护的版本化 Provider 预设目录。

这里只保存不含秘密的可信连接模板。普通用户 Interface 只返回友好字段，底层 Endpoint、
协议和鉴权方式留在 ConnectionBroker 内部使用。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderModelPreset:
    """Provider 目录中少量、面向普通用户的推荐模型。"""

    model_id: str
    display_name: str
    role: str
    context_window: int | None = None

    def public_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """一个不含秘密、可冻结版本的模型 Provider 预设。"""

    preset_id: str
    version: str
    display_name: str
    description: str
    base_url: str
    api_format: str
    recommended_model: str
    model_catalog: tuple[ProviderModelPreset, ...]
    help_url: str

    @property
    def models(self) -> tuple[str, ...]:
        """兼容旧调用方的模型 ID 列表。"""

        return tuple(item.model_id for item in self.model_catalog)

    def model_preset(self, model_id: str) -> ProviderModelPreset:
        for item in self.model_catalog:
            if item.model_id == model_id:
                return item
        raise KeyError(model_id)

    def public_dict(self) -> dict[str, object]:
        """返回普通用户能理解且不会暴露内部连接细节的目录项。"""

        return {
            "preset_id": self.preset_id,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "recommended_model": self.recommended_model,
            "models": list(self.models),
            "model_catalog": [item.public_dict() for item in self.model_catalog],
            "help_url": self.help_url,
        }


def _model(
    model_id: str,
    display_name: str,
    role: str,
    *,
    context_window: int | None = None,
) -> ProviderModelPreset:
    return ProviderModelPreset(
        model_id=model_id,
        display_name=display_name,
        role=role,
        context_window=context_window,
    )


_CATALOG_VERSION = "2026-07-30.2"
_DEEPSEEK_CATALOG_VERSION = "2026-08-02.1"

_PRESETS = (
    ProviderPreset(
        preset_id="deepseek",
        version=_DEEPSEEK_CATALOG_VERSION,
        display_name="DeepSeek",
        description="适合中文、推理和通用 Agent 任务",
        base_url="https://api.deepseek.com",
        api_format="openai_chat_completions",
        recommended_model="deepseek-v4-flash",
        model_catalog=(
            _model(
                "deepseek-v4-flash",
                "DeepSeek V4 Flash（0731 正式版）",
                "balanced",
                context_window=1_000_000,
            ),
            _model(
                "deepseek-v4-pro",
                "DeepSeek V4 Pro",
                "quality",
                context_window=1_000_000,
            ),
        ),
        help_url="https://api-docs.deepseek.com/",
    ),
    ProviderPreset(
        preset_id="qwen",
        version=_CATALOG_VERSION,
        display_name="阿里百炼 Qwen",
        description="中国站默认入口，兼顾中文、工具调用和成本",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_format="openai_responses",
        recommended_model="qwen3.7-plus-2026-05-26",
        model_catalog=(
            _model("qwen3.7-plus-2026-05-26", "Qwen 3.7 Plus", "balanced"),
            _model("qwen3.7-max-2026-06-08", "Qwen 3.7 Max", "quality"),
            _model("qwen3.7-flash-2026-07-15", "Qwen 3.7 Flash", "efficiency"),
        ),
        help_url="https://help.aliyun.com/zh/model-studio/",
    ),
    ProviderPreset(
        preset_id="openai",
        version=_CATALOG_VERSION,
        display_name="OpenAI",
        description="原生 Responses API，适合复杂推理与工具任务",
        base_url="https://api.openai.com/v1",
        api_format="openai_responses",
        recommended_model="gpt-5.6-terra",
        model_catalog=(
            _model("gpt-5.6-terra", "GPT-5.6 Terra", "balanced"),
            _model("gpt-5.6-sol", "GPT-5.6 Sol", "quality"),
            _model("gpt-5.6-luna", "GPT-5.6 Luna", "efficiency"),
        ),
        help_url="https://developers.openai.com/api/docs/models",
    ),
    ProviderPreset(
        preset_id="anthropic",
        version=_CATALOG_VERSION,
        display_name="Anthropic Claude",
        description="原生 Messages API，适合长程 Agent 与复杂文本任务",
        base_url="https://api.anthropic.com",
        api_format="anthropic_messages",
        recommended_model="claude-sonnet-5",
        model_catalog=(
            _model("claude-sonnet-5", "Claude Sonnet 5", "balanced"),
            _model("claude-opus-5", "Claude Opus 5", "quality"),
            _model(
                "claude-haiku-4-5-20251001",
                "Claude Haiku 4.5",
                "efficiency",
            ),
        ),
        help_url="https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ProviderPreset(
        preset_id="gemini",
        version=_CATALOG_VERSION,
        display_name="Google Gemini",
        description="原生 generateContent，适合多模态与高吞吐任务",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_format="gemini_generate_content",
        recommended_model="gemini-3.6-flash",
        model_catalog=(
            _model("gemini-3.6-flash", "Gemini 3.6 Flash", "balanced"),
            _model(
                "gemini-3.5-flash-lite",
                "Gemini 3.5 Flash-Lite",
                "efficiency",
            ),
        ),
        help_url="https://ai.google.dev/gemini-api/docs/models",
    ),
    ProviderPreset(
        preset_id="kimi",
        version=_CATALOG_VERSION,
        display_name="月之暗面 Kimi",
        description="兼顾超长上下文、通用推理和 Agent 任务",
        base_url="https://api.moonshot.ai/v1",
        api_format="openai_chat_completions",
        recommended_model="kimi-k3",
        model_catalog=(
            _model("kimi-k3", "Kimi K3", "quality"),
            _model("kimi-k2.6", "Kimi K2.6", "balanced"),
        ),
        help_url="https://platform.kimi.ai/docs/models",
    ),
    ProviderPreset(
        preset_id="zhipu",
        version=_CATALOG_VERSION,
        display_name="智谱 GLM",
        description="适合中文、长程工程和高性价比 Agent 任务",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_format="openai_chat_completions",
        recommended_model="glm-5.2",
        model_catalog=(
            _model("glm-5.2", "GLM-5.2", "quality"),
            _model("glm-5.1-highspeed", "GLM-5.1 Highspeed", "balanced"),
            _model("glm-4.7-flash", "GLM-4.7 Flash", "efficiency"),
        ),
        help_url="https://docs.bigmodel.cn/cn/guide/start/model-overview",
    ),
)

PRESETS_BY_ID = {preset.preset_id: preset for preset in _PRESETS}


def runtime_context_window(model_id: str, *, fallback: int) -> int:
    """返回平台已验证模型的真实窗口；自定义模型继续使用保守配置。"""

    for preset in _PRESETS:
        for model in preset.model_catalog:
            if model.model_id == model_id and model.context_window is not None:
                return model.context_window
    return fallback


def public_presets() -> list[dict[str, object]]:
    """按产品固定顺序返回普通用户目录。"""

    return [preset.public_dict() for preset in _PRESETS]
