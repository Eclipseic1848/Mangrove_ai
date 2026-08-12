"""
LLM 服务提供者模块
封装 LLM 客户端的创建和管理
"""
import logging
from typing import Optional
from urllib.parse import urlsplit

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI, OpenAI

from src.config import settings

logger = logging.getLogger(__name__)


def _is_lan(url: str) -> bool:
    """base_url 是否指向局域网/本机（这类地址须绕过系统代理，否则 Clash 等会拦成 502/超时）。"""
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost",) or host.startswith(("127.", "10.", "192.168.")):
        return True
    if host.startswith("172."):  # 172.16.0.0 ~ 172.31.255.255 为私网段
        try:
            return 16 <= int(host.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


class LLMProvider:
    """LLM 服务提供者类，负责创建和管理 LLM 实例"""
    
    _instance: Optional["LLMProvider"] = None
    _llm: Optional[BaseChatModel] = None
    _openai: Optional[OpenAI] = None
    _async_openai: Optional[AsyncOpenAI] = None
    
    def __new__(cls) -> "LLMProvider":
        """单例模式实现"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """初始化 LLM Provider"""
        if self._llm is None:
            self._initialize_llm()
    
    def _initialize_llm(self) -> None:
        """初始化 LLM 客户端"""
        try:
            kwargs = dict(
                model=settings.llm_model_name,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout,
            )
            if _is_lan(settings.llm_base_url):
                # 局域网/本机模型绕过系统代理，否则 Clash 等会拦截 LAN 请求导致 502/超时
                kwargs["http_client"] = httpx.Client(trust_env=False, timeout=settings.llm_timeout)
                kwargs["http_async_client"] = httpx.AsyncClient(trust_env=False, timeout=settings.llm_timeout)
            self._llm = ChatOpenAI(**kwargs)
            logger.info(f"LLM 初始化成功: {settings.llm_model_name}")
        except Exception as e:
            logger.error(f"LLM 初始化失败: {e}")
            raise
    
    @property
    def llm(self) -> BaseChatModel:
        """获取 LLM 实例
        
        Returns:
            LLM 实例
        """
        if self._llm is None:
            self._initialize_llm()
        return self._llm
    
    @property
    def openai(self) -> OpenAI:
        """同步 OpenAI 兼容客户端（与主 LLM 同源 base_url / api_key / timeout）。"""
        if self._openai is None:
            kwargs = dict(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                          timeout=settings.llm_timeout)
            if _is_lan(settings.llm_base_url):
                kwargs["http_client"] = httpx.Client(trust_env=False, timeout=settings.llm_timeout)
            self._openai = OpenAI(**kwargs)
        return self._openai

    @property
    def async_openai(self) -> AsyncOpenAI:
        """异步 OpenAI 兼容客户端（与主 LLM 同源 base_url / api_key / timeout）。"""
        if self._async_openai is None:
            kwargs = dict(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                          timeout=settings.llm_timeout)
            if _is_lan(settings.llm_base_url):
                kwargs["http_client"] = httpx.AsyncClient(trust_env=False, timeout=settings.llm_timeout)
            self._async_openai = AsyncOpenAI(**kwargs)
        return self._async_openai

    def get_llm_with_tools(self, tools: list) -> BaseChatModel:
        """获取绑定工具的 LLM 实例
        
        Args:
            tools: 工具列表
        
        Returns:
            绑定工具的 LLM 实例
        """
        return self.llm.bind_tools(tools)
    
    def reset(self) -> None:
        """重置 LLM 实例"""
        self._llm = None
        self._openai = None
        self._async_openai = None
        logger.info("LLM 实例已重置")


def get_llm_provider() -> LLMProvider:
    """获取 LLM Provider 实例
    
    Returns:
        LLMProvider 实例
    """
    return LLMProvider()

