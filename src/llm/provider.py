"""
多模型 Provider —— 多供应商注册表

设计目标：
- 默认走公网 API（deepseek），同时支持百炼 qwen 与本地部署模型，可按会话或按节点切换。
- 三者均为 OpenAI 兼容接口，统一用 langchain 的 ChatOpenAI 封装。
- 所有 Key 从 settings(.env) 读取，绝不硬编码。
- 与旧的 src/services/llm_provider.py 并存：旧模块继续服务现有 browser agent / VOC，
  本模块面向新的"总指挥"Conductor。其中 "local" profile 复用旧的 llm_* 配置，避免重复维护。

典型用法：
    from src.llm import chat, get_chat_model
    text = chat([{"role": "user", "content": "你好"}])          # 用默认供应商
    text = chat(messages, provider="qwen")                       # 临时切换
    model = get_chat_model(provider="deepseek", temperature=0)   # 拿到 langchain 模型对象
"""
from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import settings
from src.api.execution import execution_http_checkpoint, execution_http_checkpoint_async

# 请求级 LLM token 用量累积器：chat.py pipeline 进入时 set 一个 dict，
# achat/chat 每次调用把 resp.usage_metadata 累加进去；未 set 时零开销跳过。
_usage_ctx: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("llm_usage", default=None)
_bound_chat: contextvars.ContextVar[Any] = contextvars.ContextVar("bound_model_chat", default=None)
_bound_chat_failure: contextvars.ContextVar[Optional[list[str]]] = contextvars.ContextVar("bound_model_failure", default=None)


def verify_bound_model() -> None:
    failure = _bound_chat_failure.get()
    if failure:
        raise ValueError(failure[0])


def _collect_usage(resp: Any) -> None:
    """若当前上下文设了 usage sink，累加本次 resp 的 token 用量（供对话页展示）。"""
    sink = _usage_ctx.get()
    if sink is None:
        return
    um = getattr(resp, "usage_metadata", None) or {}
    for source, target in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
        value = um.get(source)
        if type(value) is int and value >= 0:
            sink[target] += value
        else:
            # 缺失统计不等于零消耗；部分调用缺失时只能展示已知用量。
            sink["incomplete"] = True
            missing = sink.setdefault("missing_fields", [])
            if target not in missing:
                missing.append(target)
    sink["calls"] += 1

logger = logging.getLogger(__name__)

# 统一的消息类型：既接受 langchain 消息，也接受 {"role","content"} 字典
MessageLike = Union[Dict[str, str], Any]

# 额外的本地模型端点：{模型名: base_url}。
# .env 的 LLM_MODEL_NAME@LLM_BASE_URL 始终作为默认本地模型并入；这里登记其它独立端点的本地模型。
# 同一供应商不同模型可有各自 URL（本地常按模型分端口部署）。
# 来源为 .env 的 LOCAL_EXTRA_MODELS（格式 "名称@URL,名称@URL"），便于在配置里统一管理。
def _parse_local_models(raw: str) -> Dict[str, str]:
    """解析 .env 的额外本地模型配置：'名称@URL,名称@URL' -> {名称: base_url}。"""
    out: Dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or "@" not in item:
            continue
        name, url = item.split("@", 1)
        name, url = name.strip(), url.strip()
        if name and url:
            out[name] = url
    return out


LOCAL_MODELS: Dict[str, str] = _parse_local_models(settings.local_extra_models)

# qwen 思考模式"伪模型"：UI 选 qwen3-max-thinking 时，实际调用 qwen3-max 并强制 enable_thinking=True。
# （DashScope 上"思考"是 qwen3-max 的一种模式，而非独立模型 id。）
_QWEN_THINKING_ALIASES: Dict[str, str] = {
    "qwen3-max-thinking": "qwen3-max",
}


@dataclass(frozen=True)
class ModelProfile:
    """单个供应商的模型配置。"""
    name: str
    base_url: str
    api_key: str
    model: str
    # 透传到 OpenAI create() 的额外 body 字段（如 DashScope Qwen3 思考模型需 enable_thinking=False）
    extra_body: Optional[Dict[str, Any]] = None

    def is_configured(self) -> bool:
        """是否具备最小可用配置（有地址与模型名）。本地模型 api_key 允许为占位值。"""
        return bool(self.base_url and self.model)


@dataclass(frozen=True)
class ResolvedModelConnection:
    """已解析的 OpenAI 兼容连接，供 LangChain 与 Instructor 复用。"""

    provider: str
    requested_model: str
    model: str
    base_url: str
    api_key: str
    timeout: int
    trust_env: bool
    extra_body: Optional[Dict[str, Any]] = None


def _build_profiles() -> Dict[str, ModelProfile]:
    """从全局 settings 构造各供应商 profile。"""
    return {
        "deepseek": ModelProfile(
            name="deepseek",
            base_url=(settings.deepseek_base_url or "").rstrip("/"),
            api_key=settings.deepseek_api_key or "",
            model=settings.deepseek_model,
        ),
        "qwen": ModelProfile(
            name="qwen",
            base_url=(settings.qwen_base_url or "").rstrip("/"),
            api_key=settings.qwen_api_key or "",
            model=settings.qwen_model,
            # enable_thinking 不在此预置；改由 get_chat_model 按"实际所选模型"决定，
            # 仅对 qwen3 思考模型发送，避免切到 qwen-max/plus 等非思考模型时报参数错误。
        ),
        # local 复用旧的 llm_* 字段，保持与现有模块一致
        "local": ModelProfile(
            name="local",
            base_url=(settings.llm_base_url or "").rstrip("/"),
            api_key=settings.llm_api_key or "local",
            model=settings.llm_model_name,
        ),
    }


# 统一注入的"系统事实"前缀：解决两类通病——
# (1) 本地/公网模型训练数据停在过去，不知今天几号，会误判用户提到的年份/事件是否已发生；
# (2) 模型越权用训练知识去"事实核查"素材，导致明明采到数据却断言"事件不存在/尚未发生"。
# 在 LLM 总出口(chat/achat)统一注入，一次覆盖 intent/planner/analyze/checker/模板蒸馏等所有节点。
_SYS_CONTEXT_TMPL = (
    "【系统事实·最高优先】当前真实日期：{today}。这是权威事实，优先于你训练数据中的任何时间认知；"
    "凡涉及“今天/最近/本周/某年份是否已到来/某事件是否已发生”，一律以此日期为准。\n"
    "你是数据采集分析智能体：处理时以采集到的数据/给定素材为准，"
    "【严禁】用你的训练知识去质疑或否定用户所述的赛事、事件、年份是否“真实存在”或“是否已发生”；"
    "确实没有相关数据时，只如实说明“未采集到相关数据”，不得臆断“该事件不存在/尚未发生”。\n\n"
)


def _inject_system_context(messages: List[MessageLike]) -> List[MessageLike]:
    """在首条 system 消息前并入"系统事实"前缀；无 system 消息则在最前插入一条。

    合并进已有 system（而非新增一条），避免部分本地 chat_template 只认单条 system。
    """
    prefix = _SYS_CONTEXT_TMPL.format(today=datetime.now().strftime("%Y年%m月%d日"))
    msgs = list(messages)
    for i, m in enumerate(msgs):
        if isinstance(m, dict) and (m.get("role") or "").lower() == "system":
            msgs[i] = {**m, "content": prefix + (m.get("content") or "")}
            return msgs
        if isinstance(m, SystemMessage):
            msgs[i] = SystemMessage(content=prefix + (m.content or ""))
            return msgs
    msgs.insert(0, {"role": "system", "content": prefix})
    return msgs


def _to_lc_messages(messages: List[MessageLike]) -> List[Any]:
    """把 {"role","content"} 字典统一转换为 langchain 消息对象。"""
    role_map = {
        "system": SystemMessage,
        "user": HumanMessage,
        "human": HumanMessage,
        "assistant": AIMessage,
        "ai": AIMessage,
    }
    converted: List[Any] = []
    for m in messages:
        if isinstance(m, dict):
            role = (m.get("role") or "user").lower()
            cls = role_map.get(role, HumanMessage)
            converted.append(cls(content=m.get("content", "")))
        else:
            converted.append(m)  # 已是 langchain 消息
    return converted


class MultiModelProvider:
    """多供应商注册表（单例）。按需懒加载并缓存 ChatOpenAI 实例。"""

    _instance: Optional["MultiModelProvider"] = None

    def __new__(cls) -> "MultiModelProvider":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_once()
        return cls._instance

    def _init_once(self) -> None:
        self._profiles: Dict[str, ModelProfile] = _build_profiles()
        # 缓存键：(provider, temperature, max_tokens) —— 不同参数对应不同实例
        self._cache: Dict[tuple, BaseChatModel] = {}
        self._default = (settings.llm_default_provider or "deepseek").lower()
        if self._default not in self._profiles:
            logger.warning("默认供应商 %s 未知，回退到 local", self._default)
            self._default = "local"

    # ---- 公共接口 ----
    @property
    def default_provider(self) -> str:
        return self._default

    def available_providers(self) -> List[str]:
        """返回已正确配置（有地址/模型，且非 local 时需有 Key）的供应商列表。"""
        from src.config.user_ctx import get_user_override

        result = []
        for name, p in self._profiles.items():
            if not p.is_configured():
                continue
            user_key = (
                get_user_override(f"{name}_api_key")
                if name in ("deepseek", "qwen")
                else None
            )
            if name != "local" and not (user_key or p.api_key):
                continue  # 公网供应商缺 Key 视为不可用
            result.append(name)
        return result

    def _resolve_extra_body(
        self, name: str, model_name: str, *, force_thinking: bool = False
    ) -> Optional[Dict[str, Any]]:
        """按"实际所选模型"决定要透传的额外 body 字段。

        - 本地 Qwen3 思考模型（vLLM/SGLang）：经 chat_template_kwargs.enable_thinking 关/开思考
          （非流式下默认关，否则模型一直思考耗尽 max_tokens 返回空 content）；
        - qwen（DashScope）：force_thinking=True 强制 enable_thinking=True；否则只对 qwen3 按 settings 发送。
        """
        # 本地 Qwen3：用 chat_template_kwargs 关思考（与 DashScope 的顶层 enable_thinking 机制不同）
        if name == "local":
            if settings.local_enable_thinking is not None and "qwen3" in (model_name or "").lower():
                return {"chat_template_kwargs": {"enable_thinking": settings.local_enable_thinking}}
            return None
        if name != "qwen":
            return None
        if force_thinking:
            return {"enable_thinking": True}
        if settings.qwen_enable_thinking is not None and "qwen3" in (model_name or "").lower():
            return {"enable_thinking": settings.qwen_enable_thinking}
        return None

    def list_models(self) -> Dict[str, List[str]]:
        """返回各供应商可选模型：{provider: [models]}。

        只返回已配置供应商的实际模型与额外本地端点，不将预设目录冒充可用配置。
        """
        names = self.available_providers()
        out: Dict[str, List[str]] = {}
        for name in names:
            profile = self._profiles.get(name)
            if profile is None:
                continue
            extra = list(LOCAL_MODELS) if name == "local" else []
            models: List[str] = []
            for m in [profile.model, *extra]:
                if m and m not in models:
                    models.append(m)
            out[name] = models
        return out

    def get_chat_model(
        self,
        provider: Optional[str] = None,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> BaseChatModel:
        """获取指定供应商/模型的 langchain 模型对象（带缓存）。

        model 为 None 时用该供应商在 .env 的默认模型；传入则覆盖（同一 Key 调用旗下其他模型）。
        """
        if _bound_chat.get() is not None:
            raise ValueError("当前任务必须通过已绑定连接的异步文本接口调用模型")
        connection = self.resolve_model(provider, model=model)
        name = connection.provider
        requested = connection.requested_model
        real_model = connection.model
        api_key = connection.api_key

        temp = settings.llm_temperature if temperature is None else temperature
        # 本地模型用专用上限（开思考需给足）；公网模型用全局 llm_max_tokens（避免超 deepseek/qwen 输出上限）
        if max_tokens is not None:
            mtok = max_tokens
        elif name == "local":
            mtok = settings.local_max_tokens
        else:
            mtok = settings.llm_max_tokens

        # 缓存键含 API Key 哈希：不同用户的私有 Key 各建各的客户端，互不串用
        key = (name, requested, temp, mtok, hash(api_key or ""))
        if key not in self._cache:
            kwargs: Dict[str, Any] = dict(
                model=real_model,
                base_url=connection.base_url,
                api_key=api_key or "local",
                temperature=temp,
                max_tokens=mtok,
                timeout=connection.timeout,
            )
            if connection.extra_body:
                # 直接透传额外 body 字段（如 enable_thinking）到底层 OpenAI create()
                kwargs["extra_body"] = connection.extra_body
            kwargs["http_client"] = httpx.Client(
                trust_env=connection.trust_env,
                timeout=connection.timeout,
                event_hooks={"request": [execution_http_checkpoint], "response": [execution_http_checkpoint]},
            )
            kwargs["http_async_client"] = httpx.AsyncClient(
                trust_env=connection.trust_env,
                timeout=connection.timeout,
                event_hooks={"request": [execution_http_checkpoint_async], "response": [execution_http_checkpoint_async]},
            )
            self._cache[key] = ChatOpenAI(**kwargs)
            logger.info("已创建模型: provider=%s model=%s(real=%s)", name, requested, real_model)
        return self._cache[key]

    def resolve_model(
        self,
        provider: Optional[str] = None,
        *,
        model: Optional[str] = None,
    ) -> ResolvedModelConnection:
        """解析供应商、用户密钥、模型别名与端点，不创建具体 SDK 客户端。"""
        name = (provider or self._default).lower()
        profile = self._profiles.get(name)
        if profile is None:
            raise ValueError(f"未知模型供应商: {name}，可选: {list(self._profiles)}")
        if not profile.is_configured():
            raise RuntimeError(f"供应商 {name} 未配置 base_url/model，请检查 .env")

        # 按用户隔离的 API Key 覆盖（deepseek/qwen）：用户配了自己的 Key 则本次任务用它
        from src.config.user_ctx import get_user_override
        user_key = get_user_override(f"{name}_api_key") if name in ("deepseek", "qwen") else None
        api_key = user_key or profile.api_key
        if name != "local" and not api_key:
            raise RuntimeError(f"供应商 {name} 缺少 API Key，请在设置页或 .env 配置")

        requested = (model or profile.model).strip()
        # 思考伪模型：解析为真实模型 id，并强制开启思考
        real_model = _QWEN_THINKING_ALIASES.get(requested, requested)
        force_thinking = requested in _QWEN_THINKING_ALIASES

        # 本地模型可按模型走各自端点（常按模型分端口部署）；未登记则用 .env 默认 base_url
        base_url = profile.base_url
        if name == "local" and requested in LOCAL_MODELS:
            base_url = LOCAL_MODELS[requested].rstrip("/")

        return ResolvedModelConnection(
            provider=name,
            requested_model=requested,
            model=real_model,
            base_url=base_url,
            api_key=api_key or "local",
            timeout=settings.llm_timeout,
            trust_env=name != "local",
            extra_body=self._resolve_extra_body(
                name,
                real_model,
                force_thinking=force_thinking,
            ),
        )

    def chat(
        self,
        messages: List[MessageLike],
        provider: Optional[str] = None,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """同步对话，返回纯文本内容。"""
        chat_model = self.get_chat_model(
            provider, model=model, temperature=temperature, max_tokens=max_tokens
        )
        resp = chat_model.invoke(_to_lc_messages(_inject_system_context(messages)))
        _collect_usage(resp)
        return resp.content if hasattr(resp, "content") else str(resp)

    async def achat(
        self,
        messages: List[MessageLike],
        provider: Optional[str] = None,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """异步对话，返回纯文本内容。"""
        bound = _bound_chat.get()
        if bound is not None:
            # 工作台已冻结连接时，全部编排节点沿用该连接，禁止回退到全局模型。
            return await bound(_inject_system_context(messages))
        chat_model = self.get_chat_model(
            provider, model=model, temperature=temperature, max_tokens=max_tokens
        )
        resp = await chat_model.ainvoke(_to_lc_messages(_inject_system_context(messages)))
        _collect_usage(resp)
        return resp.content if hasattr(resp, "content") else str(resp)


# ---- 模块级便捷函数 ----
def get_provider() -> MultiModelProvider:
    return MultiModelProvider()


def reload_provider() -> None:
    """运行时配置变更后热重建：重解析本地多端点表 + 重建供应商 profile 与客户端缓存。"""
    global LOCAL_MODELS
    LOCAL_MODELS = _parse_local_models(settings.local_extra_models)
    inst = MultiModelProvider._instance
    if inst is not None:
        inst._init_once()
    logger.info("LLM 供应商配置已热重载")


def get_chat_model(provider: Optional[str] = None, **kwargs) -> BaseChatModel:
    return get_provider().get_chat_model(provider, **kwargs)


def chat(messages: List[MessageLike], provider: Optional[str] = None, **kwargs) -> str:
    return get_provider().chat(messages, provider, **kwargs)


async def achat(messages: List[MessageLike], provider: Optional[str] = None, **kwargs) -> str:
    return await get_provider().achat(messages, provider, **kwargs)


def available_providers() -> List[str]:
    return get_provider().available_providers()


def list_models() -> Dict[str, List[str]]:
    return get_provider().list_models()
