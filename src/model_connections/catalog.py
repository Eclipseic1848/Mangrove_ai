"""平台维护的版本化 Provider 预设目录。

这里只保存不含秘密的可信连接模板。普通用户 Interface 只返回友好字段，底层 Endpoint、
协议和鉴权方式留在 ConnectionBroker 内部使用。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import re


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
    source_url: str = ""
    key_url: str = ""
    region_note: str = "使用该服务商 API 控制台的密钥；聊天订阅不等于 API 额度。"
    regions: tuple[tuple[str, str, str], ...] = ()

    def for_region(self, region: str | None, workspace_id: str = "") -> ProviderPreset:
        """只从可信地域模板构建地址，不接受用户提供域名。"""
        if not region:
            return self
        template = next((url for key, _label, url in self.regions if key == region), None)
        if template is None:
            raise ValueError("所选服务商不支持该地域")
        if "{workspace}" in template and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", workspace_id):
            raise ValueError("请从百炼控制台复制正确的业务空间 ID（只含字母、数字或连字符）")
        return replace(self, base_url=template.replace("{workspace}", workspace_id))

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
            "model_catalog": [{
                **item.public_dict(), "source_url": self.source_url or self.help_url,
                "verified_on": "2026-09-07",
                "release_status": "experimental" if item.model_id.endswith("-exp") else "current",
            } for item in self.model_catalog],
            "help_url": self.help_url,
            "key_url": self.key_url or self.help_url,
            "region_note": self.region_note,
            "regions": [{"id": key, "label": label, "workspace_required": "{workspace}" in url}
                        for key, label, url in self.regions],
            "catalog_stale": (date.today() - date(2026, 9, 7)).days > 30,
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


_CATALOG_VERSION = "2026-09-07.1"
_DEEPSEEK_CATALOG_VERSION = "2026-09-07.1"

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
            _model("deepseek-v4-flash-vision-exp", "DeepSeek V4 Flash Vision（实验版）", "vision"),
        ),
        help_url="https://api-docs.deepseek.com/",
        key_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderPreset(
        preset_id="qwen",
        version="2026-09-07.2",
        display_name="阿里百炼 Qwen",
        description="中国站默认入口，兼顾中文、工具调用和成本",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_format="openai_responses",
        recommended_model="qwen3.8-flash",
        model_catalog=(
            _model("qwen3.8-27b", "Qwen 3.8 27B", "balanced"),
            _model("qwen3.8-max-0902", "Qwen 3.8 Max（0902）", "quality"),
            _model("qwen3.8-flash", "Qwen 3.8 Flash", "efficiency"),
        ),
        help_url="https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen",
        source_url="https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api",
        key_url="https://help.aliyun.com/zh/model-studio/get-api-key",
        region_note="按量付费 API：密钥、地域与业务空间须对应。Token Plan / Coding Plan 不等于此入口。不同地域的模型权限以实测为准。",
        regions=(
            ("cn-beijing", "中国 · 北京", "https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
            ("ap-southeast-1", "新加坡", "https://{workspace}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"),
        ),
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
            _model("gpt-6-astra", "GPT-6 Astra", "quality"),
            _model("gpt-5.6-terra", "GPT-5.6 Terra", "balanced"),
            _model("gpt-5.6-sol", "GPT-5.6 Sol", "quality"),
            _model("gpt-5.6-luna", "GPT-5.6 Luna", "efficiency"),
        ),
        help_url="https://developers.openai.com/api/docs/models",
        key_url="https://platform.openai.com/api-keys",
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
            _model("claude-fable-5-1", "Claude Fable 5.1", "quality"),
            _model("claude-sonnet-5", "Claude Sonnet 5", "balanced"),
            _model("claude-opus-5", "Claude Opus 5", "quality"),
            _model(
                "claude-haiku-4-5-20251001",
                "Claude Haiku 4.5",
                "efficiency",
            ),
        ),
        help_url="https://platform.claude.com/docs/en/models/overview",
        key_url="https://platform.claude.com/settings/keys",
    ),
    ProviderPreset(
        preset_id="gemini",
        version=_CATALOG_VERSION,
        display_name="Google Gemini",
        description="原生 generateContent，适合多模态与高吞吐任务",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_format="gemini_generate_content",
        recommended_model="gemini-3.8-flash",
        model_catalog=(
            _model("gemini-3.8-flash", "Gemini 3.8 Flash", "quality"),
            _model("gemini-3.7-flash", "Gemini 3.7 Flash", "balanced"),
            _model("gemini-3.6-flash", "Gemini 3.6 Flash", "balanced"),
            _model(
                "gemini-3.5-flash-lite",
                "Gemini 3.5 Flash-Lite",
                "efficiency",
            ),
        ),
        help_url="https://ai.google.dev/gemini-api/docs/models",
        key_url="https://aistudio.google.com/apikey",
        region_note="Google AI Studio / Gemini API 密钥；不是 Vertex AI 凭证。请先确认所在区域支持此服务。",
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
            _model("kimi-k2.7-code", "Kimi K2.7 Code", "coding"),
            _model("kimi-k2.6", "Kimi K2.6", "balanced"),
        ),
        help_url="https://platform.kimi.ai/docs/models",
        key_url="https://platform.kimi.ai/docs/introduction",
        region_note="Kimi API 国际站 api.moonshot.ai 的密钥；国内站与 Coding 订阅不混用。其他站点请由管理员登记对应接口。",
    ),
    ProviderPreset(
        preset_id="zhipu",
        version=_CATALOG_VERSION,
        display_name="智谱 GLM",
        description="适合中文、长程工程和高性价比 Agent 任务",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_format="openai_chat_completions",
        recommended_model="glm-5.3",
        model_catalog=(
            _model("glm-5.2", "GLM-5.2", "quality"),
            _model("glm-5.3", "GLM-5.3", "quality"),
            _model("glm-5.3-flash", "GLM-5.3 Flash", "vision"),
        ),
        help_url="https://docs.bigmodel.cn/cn/guide/start/model-overview",
        key_url="https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        region_note="智谱开放平台中国站按量 API；Z.ai 国际站和 Coding Plan 的密钥/端点不可混用。",
    ),
    ProviderPreset(
        preset_id="xai", version=_CATALOG_VERSION, display_name="xAI Grok",
        description="文本、代码与多步推理；按量 API 与 Grok 聊天订阅分开",
        base_url="https://api.x.ai/v1", api_format="openai_responses",
        recommended_model="grok-4.6",
        model_catalog=(
            _model("grok-4.6", "Grok 4.6", "quality"),
            _model("grok-4.5", "Grok 4.5", "balanced"),
            _model("grok-4.3", "Grok 4.3", "efficiency"),
        ),
        help_url="https://docs.x.ai/developers/models",
        source_url="https://docs.x.ai/developers/pricing",
        key_url="https://console.x.ai",
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
