"""
浏览器 Agent 节点管理器

统一管理所有工作流节点的实现，提供对节点所需资源的最小接口访问。

设计原则：
- 单一职责：仅负责节点实例管理和资源访问代理
- 最小接口：只暴露节点实现所需的必要接口，不持有完整 Agent 引用
- 解耦循环依赖：通过依赖注入接收资源，避免 Agent ↔ NodeManager 循环引用
"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any, Optional, List

# 导入各个节点实现（browser-use 模式无 Plan 节点）
from .browser_nodes.intent_node_v2 import IntentNode
from .browser_nodes.execute_node_v2 import ExecuteNode
from .browser_nodes.tools_wrapper_v2 import tools_wrapper
from .browser_nodes.observe_node_v2 import ObserveNode
from .browser_nodes.reflect_node_v2 import ReflectNode
from .browser_nodes.error_handler_node_v2 import ErrorHandlerNode

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.agent.types.state_types import BrowserAgentState


class BrowserNodeManager:
    """浏览器 Agent 节点管理器
    
    职责：
    - 统一管理所有工作流节点的实现实例
    - 通过依赖注入接收节点所需资源
    - 封装节点相关的初始化逻辑
    
    架构说明：
    - 初始化时接收 llm、mcp_client、tools 等最小依赖，不持有 Agent 引用
    - 节点通过 node_impl 访问本管理器的属性，不得访问 Agent
    - 打破 Agent ↔ NodeManager 循环依赖，便于测试和独立演进
    """
    
    # ==================== 初始化 ====================
    
    def __init__(
        self,
        llm: Any,
        llm_with_tools: Any,
        mcp_client: Any,
        verbose: bool,
        config: Any,
        tools: List[Any],
        log_dir_name: str,
    ) -> None:
        """初始化节点管理器（依赖注入，不持有 Agent）
        
        Args:
            llm: LLM 实例
            llm_with_tools: 带工具的 LLM 实例
            mcp_client: MCP 客户端实例
            verbose: 是否详细输出
            config: Agent 配置
            tools: 工具列表
            log_dir_name: 日志目录名称
            
        Raises:
            ValueError: 如果必要参数为 None 或 log_dir_name 为空
            OSError: 如果日志目录创建失败
        """
        if not log_dir_name or not log_dir_name.strip():
            raise ValueError("log_dir_name 参数不能为空")
        
        self._llm = llm
        self._llm_with_tools = llm_with_tools
        self._mcp_client = mcp_client
        self._verbose = verbose
        self._config = config
        self._tools = tools or []
        self._log_dir_name = log_dir_name.strip()
        
        self._initialize_log_directory()
        self._initialize_node_implementations()
    
    def _initialize_log_directory(self) -> None:
        """初始化日志目录路径（目录在首次写入时按需创建，避免空目录）"""
        try:
            self.logs_dir = Path("logs") / self._log_dir_name
            self.log_dir = str(self.logs_dir)
            if self._verbose:
                logger.debug(f"日志目录已初始化: {self.logs_dir}")
        except OSError as e:
            logger.error(f"创建日志目录失败: {self.logs_dir}, 错误: {e}")
            raise

    def set_log_dir(self, log_dir_name: str) -> None:
        """按任务更新日志目录（使每次任务的日志时间戳统一）
        
        目录在首次写入时按需创建，避免空目录。
        
        Args:
            log_dir_name: 日志目录名（格式如 YYYY-MM-DD_HHMMSS）
        """
        if not log_dir_name or not log_dir_name.strip():
            return
        self._log_dir_name = log_dir_name.strip()
        self.logs_dir = Path("logs") / self._log_dir_name
        self.log_dir = str(self.logs_dir)
        if self._verbose:
            logger.debug(f"日志目录已切换为: {self.logs_dir}")
    
    def _initialize_node_implementations(self) -> None:
        """初始化各个节点实现类"""
        try:
            self.intent_node_impl = IntentNode(self)
            self.execute_node_impl = ExecuteNode(self)
            self.observe_node_impl = ObserveNode(self)
            self.reflect_node_impl = ReflectNode(self)
            self.error_handler_node_impl = ErrorHandlerNode(self)
            if self._verbose:
                logger.debug("所有节点实现已初始化")
        except Exception as e:
            logger.error(f"初始化节点实现失败: {e}")
            raise
    
    # ==================== 节点上下文接口 ====================
    
    @property
    def llm(self) -> Any:
        """获取 LLM 实例"""
        return self._llm
    
    @property
    def llm_with_tools(self) -> Any:
        """获取带工具的 LLM 实例"""
        return self._llm_with_tools
    
    @property
    def mcp_client(self) -> Any:
        """获取 MCP 客户端实例"""
        return self._mcp_client
    
    @property
    def verbose(self) -> bool:
        """获取是否详细输出"""
        return self._verbose
    
    @property
    def config(self) -> Any:
        """获取 Agent 配置"""
        return self._config
    
    @property
    def tools(self) -> List[Any]:
        """获取工具列表"""
        return self._tools
    
    # ==================== 节点方法（工作流节点入口） ====================
    
    def intent_node(self, state: "BrowserAgentState") -> Dict[str, Any]:
        """📎 Intent 节点：分析任务关联性，无关联时清理多余标签页
        
        Args:
            state: 当前状态
            
        Returns:
            状态更新
        """
        return self.intent_node_impl.intent(state)
    
    def execute_node(self, state: "BrowserAgentState") -> Dict[str, Any]:
        """🔧 Execute 节点：执行计划中的当前步骤
        
        Args:
            state: 当前状态
            
        Returns:
            更新后的状态字典
        """
        return self.execute_node_impl.execute(state)
    
    def wrap_tool(self, tool_node) -> Any:
        """包装工具节点：创建工具节点的包装器
        
        Args:
            tool_node: 工具节点（LangGraph ToolNode 实例）
            
        Returns:
            包装后的工具节点（可调用对象）
        """
        return tools_wrapper(self, tool_node)
    
    def observe_node(self, state: "BrowserAgentState") -> Dict[str, Any]:
        """👁️ Observe 节点：观察页面状态变化
        
        Args:
            state: 当前状态
            
        Returns:
            更新后的状态字典
        """
        return self.observe_node_impl.observe(state)
    
    def reflect_node(self, state: "BrowserAgentState") -> Dict[str, Any]:
        """🤔 Reflect 节点：反思执行结果并判断任务完成情况
        
        Args:
            state: 当前状态
            
        Returns:
            更新后的状态字典
        """
        return self.reflect_node_impl.reflect(state)
    
    def error_handler_node(self, state: "BrowserAgentState") -> Dict[str, Any]:
        """⚠️ 错误处理节点：处理执行过程中的错误
        
        Args:
            state: 当前状态
            
        Returns:
            更新后的状态字典
        """
        return self.error_handler_node_impl.handle_error(state)


__all__ = [
    "BrowserNodeManager",
]
