"""
浏览器自动化 Agent - 封装类

此模块提供 BrowserAgent 封装类，用于封装 BrowserUseAgent 的初始化。
实际实现位于 browser_agent_base 模块中（browser-use 风格：无独立 Plan 节点，needs_replan 时回到 Execute）。

使用示例（同步）：
    agent = BrowserAgent(verbose=True)
    result = agent.run("打开 Google 并搜索")
    result2 = agent.run("搜索 Python")  # 会话上下文
    agent.stop()

使用示例（异步，asyncio 环境下）：
    result = await agent.run_async("打开 Google 并搜索")
"""

import logging
from typing import Optional, Dict, Any

from langchain_core.callbacks import BaseCallbackHandler

from .browser_agent_base import BrowserUseAgent, BrowserPlanningAgent
from src.tools.mcp.chrome_devtools import ChromeDevToolsConfig
from src.agent.types.state_types import BrowserAgentContext
from src.agent.config.agent_config import BrowserAgentConfig

logger = logging.getLogger(__name__)


class BrowserAgent:
    """
    浏览器自动化 Agent 封装类
    
    封装 BrowserUseAgent 的初始化逻辑，便于使用和扩展。
    此类设计为方便后期创建 BrowserAgent2 等新的 Agent 实现。
    
    使用示例：
        agent = BrowserAgent(verbose=True)
        result = agent.run("打开 Google 并搜索 Python")
        agent.stop()
    """
    
    def __init__(
        self,
        mcp_config: Optional[ChromeDevToolsConfig] = None,
        verbose: bool = True,
        callbacks: Optional[list[BaseCallbackHandler]] = None,
        auto_start_mcp: bool = True,
        max_iterations: int = None,
        max_tools_per_step: int = None,
        recursion_limit: int = None,
        agent_config: Optional[BrowserAgentConfig] = None,
    ):
        """
        初始化 BrowserAgent
        
        Args:
            mcp_config: Chrome DevTools MCP 配置
            verbose: 是否详细输出
            callbacks: 回调处理器
            auto_start_mcp: 是否自动启动 MCP 服务器
            max_iterations: 最大迭代次数（覆盖配置）
            max_tools_per_step: 每步骤最大工具调用次数（覆盖配置）
            recursion_limit: LangGraph 递归限制（覆盖配置）
            agent_config: Agent 配置（如果为 None，则使用默认配置）
        """
        # 创建内部的 BrowserUseAgent 实例
        self._agent = BrowserUseAgent(
            mcp_config=mcp_config,
            verbose=verbose,
            callbacks=callbacks,
            auto_start_mcp=auto_start_mcp,
            max_iterations=max_iterations,
            max_tools_per_step=max_tools_per_step,
            recursion_limit=recursion_limit,
            agent_config=agent_config,
        )
        
        if verbose:
            logger.info("BrowserAgent 初始化完成")
    
    def run(self, task: str, context: Optional[BrowserAgentContext] = None) -> Dict[str, Any]:
        """
        运行浏览器自动化任务（同步）
        
        Args:
            task: 任务描述
            context: 执行上下文（可选）
            
        Returns:
            执行结果字典
        """
        return self._agent.run(task, context)
    
    async def run_async(self, task: str, context: Optional[BrowserAgentContext] = None) -> Dict[str, Any]:
        """
        异步运行浏览器自动化任务（不阻塞事件循环，支持并发多连接）
        
        Args:
            task: 任务描述
            context: 执行上下文（可选）
            
        Returns:
            执行结果字典
        """
        return await self._agent.run_async(task, context)
    
    def stop(self):
        """停止 Agent 并清理资源"""
        self._agent.stop()
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，自动清理资源"""
        self.stop()
        return False
    
    def __del__(self):
        """析构函数，确保资源被清理"""
        if hasattr(self, '_agent'):
            try:
                self._agent.__del__()
            except Exception:
                pass
    
    # 属性访问器，方便直接访问内部 Agent 的属性
    @property
    def llm(self):
        """LLM 实例"""
        return self._agent.llm
    
    @property
    def llm_with_tools(self):
        """带工具的 LLM 实例"""
        return self._agent.llm_with_tools
    
    @property
    def mcp_client(self):
        """MCP 客户端实例"""
        return self._agent.mcp_client
    
    @property
    def verbose(self):
        """是否详细输出"""
        return self._agent.verbose
    
    @property
    def tools(self):
        """工具列表"""
        return self._agent.tools
    
    @property
    def graph(self):
        """工作流图"""
        return self._agent.graph
    
    @property
    def nodes(self):
        """节点实现"""
        return self._agent.nodes
    
    @property
    def recursion_limit(self):
        """LangGraph 递归限制"""
        return self._agent.recursion_limit


__all__ = [
    "BrowserAgent",
    "BrowserUseAgent",
    "BrowserPlanningAgent",  # 向后兼容别名
]