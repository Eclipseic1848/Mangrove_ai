"""
浏览器自动化 Agent（browser-use 风格）

结合 Chrome DevTools MCP，实现智能浏览器自动化。采用 browser-use 模式：无独立 Plan 节点，
LLM 在 Execute 节点每步自主决定动作；需要重规划时由 Reflect 设置 needs_replan，路由回到 Execute。

功能：
1. 📎 Intent: 意图分析，任务关联性判断，无关联时清理多余标签页
2. 🔧 Execute: LLM 每步自主决定动作并产出工具调用（或 task_complete）
3. 🔧 Tools / Observe: 执行工具后观察页面变化
4. 🤔 Reflect: 评估执行结果、任务完成判断；未完成或需重规划时设置 needs_replan，回到 Execute
5. 📋 Session Context: 任务间上下文存储（基于 LangChain InMemoryChatMessageHistory）
"""

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator

from langgraph.graph import StateGraph
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, AIMessage
from datetime import datetime

from src.config.settings import LOG_TIMESTAMP_FORMAT
from src.services import get_llm_provider
from src.services.session_context_service import (
    get_recent_messages,
    get_prior_saved_results,
    extract_saved_results_from_messages,
    add_task_result,
    clear_session,
)
from src.tools.mcp.chrome_devtools import (
    ChromeDevToolsMCP,
    ChromeDevToolsConfig,
    create_browser_tools,
)
from src.tools.browser_evaluate_parser import (
    get_browser_evaluate_parser_tools,
)
from src.agent.types.state_types import (
    BrowserAgentState,
    BrowserAgentPhase,
    BrowserAgentContext,
)
from src.agent.nodes.node_manager import BrowserNodeManager
from src.agent.config.agent_config import BrowserAgentConfig
from src.agent.routing.router import BrowserAgentRouter
from src.agent.graph_builder import BrowserGraphBuilder
from src.agent.utils.logging_utils import (
    print_startup_message,
    print_execution_summary,
)
from src.agent.utils.agent_flow_logger import log_task_start, log_task_end
from src.agent.utils.terminal_colors import green, red, orange
from src.agent.utils.result_formatter import (
    build_execution_result,
    format_tool_records,
    format_decision_records,
    format_error_history,
)

logger = logging.getLogger(__name__)


class BrowserUseAgent:
    """
    浏览器自动化 Agent（browser-use 风格）
    
    工作流程：
    START → intent → observe → execute ⇄ tools → observe → reflect → (execute / END)
    - needs_replan 时由 reflect 路由回 execute，由 Execute 的 LLM 在上下文中继续决策（隐式重规划）
    - 无独立 Plan 节点，LLM 在 Execute 每步自主决定动作
    
    特点：
    1. LLM 每步自主决定动作（browser-use 模式）
    2. 正确的工具结果处理与步骤索引更新
    3. 智能错误恢复与 error_handler → reflect 流程
    4. 支持 needs_replan 动态重规划（回到 Execute）
    """
    
    # ==================== 初始化 ====================
    
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
        初始化
        
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
        self.verbose = verbose
        self.callbacks = callbacks or []
        self._mcp_started = False
        # 会话上下文：用于跨任务存储，同一 Agent 实例下多次 run 共享
        self._connection_id = str(uuid.uuid4())
        
        # 初始化配置
        if agent_config is None:
            agent_config = BrowserAgentConfig.from_settings()
            agent_config.validate()
        
        # 覆盖配置（如果提供了参数）
        if max_iterations is not None:
            agent_config.agent.max_iterations = max_iterations
        if max_tools_per_step is not None:
            agent_config.agent.max_tools_per_step = max_tools_per_step
        if recursion_limit is not None:
            agent_config.agent.recursion_limit = recursion_limit
        
        self.config = agent_config
        self.max_iterations = self.config.agent.max_iterations
        self.max_tools_per_step = self.config.agent.max_tools_per_step
        self.recursion_limit = self.config.agent.recursion_limit
        
        # 初始化路由管理器（verbose 用于路由决策日志）
        self.router = BrowserAgentRouter(self.config, verbose=self.verbose)
        
        # 初始化 MCP 客户端
        self.mcp_client = ChromeDevToolsMCP(
            config=mcp_config or ChromeDevToolsConfig(),
            verbose=verbose,
        )
        
        # 初始化 LLM
        self.llm_provider = get_llm_provider()
        self.llm = self.llm_provider.llm
        
        # 工具相关
        self.tools = []
        self.tools_by_name = {}
        self.llm_with_tools = None
        self.graph = None
        
        # 节点实现
        self.nodes = None
        
        # 如果需要自动启动，现在启动
        if auto_start_mcp:
            self._start_mcp_and_build_graph()
        
        logger.info("Browser Agent 初始化完成")
    
    # ==================== 公共接口 ====================
    
    def run(self, task: str, context: Optional[BrowserAgentContext] = None) -> Dict[str, Any]:
        """
        执行浏览器自动化任务
        
        Args:
            task: 任务描述
            context: 执行上下文
            
        Returns:
            执行结果
        """
        # 确保 MCP 服务器已启动
        if not self._mcp_started:
            self._start_mcp_and_build_graph()
        
        # 若上下文指定了 log_dir_name，按任务更新日志目录（统一时间戳）
        if context and getattr(context, "log_dir_name", None):
            self.nodes.set_log_dir(context.log_dir_name)
        
        print_startup_message(task, self.verbose)
        if self.verbose:
            log_task_start(task)
        
        # 提取上下文配置
        config = self._extract_context_config(context)
        
        # 创建初始状态
        initial_state = self._create_initial_state(task, config)
        
        try:
            # 执行工作流
            final_state = self._invoke_graph(initial_state, config["recursion_limit"])
            
            # 构建执行结果
            result = build_execution_result(task, final_state)
            
            # 会话上下文：任务完成后存入历史，供后续任务参考；并收集本任务中工具产生的保存结果（json/mp4 等）供下一任务历史补充
            connection_id = config.get("connection_id") or self._connection_id
            result_summary = result.get("final_result") or ("成功" if result.get("success") else result.get("error", "未知"))
            final_url = final_state.get("current_url")
            saved_results = extract_saved_results_from_messages(final_state.get("messages", []))
            add_task_result(connection_id, task, str(result_summary)[:500], final_url, saved_results=saved_results)
            
            if self.verbose:
                log_task_end(result.get("success", False), str(result_summary))
                print_execution_summary(result, self.verbose)
            
            return result
            
        except Exception as e:
            if self.verbose:
                log_task_end(False, str(e))
            logger.error(f"执行失败: {e}")
            if self.verbose:
                print(red(f"\n❌ 执行失败: {e}"))
            return {
                "success": False,
                "task": task,
                "error": str(e),
            }
    
    async def run_async(self, task: str, context: Optional[BrowserAgentContext] = None) -> Dict[str, Any]:
        """
        异步执行浏览器自动化任务（不阻塞事件循环）
        
        使用 graph.ainvoke，适合在 asyncio 环境下并发处理多连接。
        
        Args:
            task: 任务描述
            context: 执行上下文
            
        Returns:
            执行结果
        """
        if not self._mcp_started:
            self._start_mcp_and_build_graph()
        
        # 若上下文指定了 log_dir_name，按任务更新日志目录（统一时间戳）
        if context and getattr(context, "log_dir_name", None):
            self.nodes.set_log_dir(context.log_dir_name)
        
        print_startup_message(task, self.verbose)
        if self.verbose:
            log_task_start(task)
        config = self._extract_context_config(context)
        initial_state = self._create_initial_state(task, config)
        
        try:
            final_state = await self._invoke_graph_async(initial_state, config["recursion_limit"])
            result = build_execution_result(task, final_state)
            
            connection_id = config.get("connection_id") or self._connection_id
            result_summary = result.get("final_result") or ("成功" if result.get("success") else result.get("error", "未知"))
            final_url = final_state.get("current_url")
            saved_results = extract_saved_results_from_messages(final_state.get("messages", []))
            add_task_result(connection_id, task, str(result_summary)[:500], final_url, saved_results=saved_results)
            
            if self.verbose:
                log_task_end(result.get("success", False), str(result_summary))
                print_execution_summary(result, self.verbose)
            
            return result
        except Exception as e:
            if self.verbose:
                log_task_end(False, str(e))
            logger.error(f"执行失败: {e}")
            if self.verbose:
                print(red(f"\n❌ 执行失败: {e}"))
            return {
                "success": False,
                "task": task,
                "error": str(e),
            }
    
    def stop(self):
        """停止 MCP 服务器并清除会话上下文"""
        if self._mcp_started:
            self.mcp_client.stop()
            self._mcp_started = False
            logger.info("MCP 服务器已停止")
            if self.verbose:
                print(orange("🛑 MCP 服务器已停止"))
        # 清除该 Agent 的会话上下文（连接断开时）
        clear_session(self._connection_id)
    
    # ==================== MCP 和工具初始化 ====================
    
    def _start_mcp_and_build_graph(self):
        """启动 MCP 服务器并构建工作流"""
        if self._mcp_started:
            return
        
        if self.verbose:
            print(green(f"\n🚀 正在启动 Chrome DevTools MCP 服务器... browser_url: {self.mcp_client.config.browser_url}"))
        
        # 启动 MCP 服务器
        if not self.mcp_client.start():
            raise RuntimeError("无法启动 MCP 服务器，请检查 Node.js 和 Chrome 是否已安装")
        
        self._mcp_started = True
        logger.info("MCP 服务器启动成功，browser_url: %s", self.mcp_client.config.browser_url)
        
        if self.verbose:
            print(green(f"✅ MCP 服务器已启动，browser_url: {self.mcp_client.config.browser_url}"))
        
        # 构建工具
        self._build_tools()
        
        # 初始化节点实现
        self._initialize_nodes()
        
        # 构建工作流
        self.graph = self._build_graph()
    
    def _get_routing_callbacks(self) -> dict:
        """获取路由回调函数字典（直接使用 Router 方法，消除 Agent 代理层）"""
        r = self.router
        return {
            "after_observe": r.route_after_observe,
            "after_execute": r.route_after_execute,
            "after_tools": r.route_after_tools,
            "after_reflect": r.route_after_reflect,
            "error_recovery": r.route_after_error_handler,
        }
    
    def _build_tools(self):
        """构建工具列表"""
        # 创建浏览器工具
        self.tools = create_browser_tools(self.mcp_client)
        
        # 添加 browser_evaluate 解析工具
        parser_tools = get_browser_evaluate_parser_tools()
        self.tools.extend(parser_tools)
        
        # 构建工具字典和带工具的 LLM
        self.tools_by_name = {tool.name: tool for tool in self.tools}
        self.llm_with_tools = self.llm_provider.get_llm_with_tools(self.tools)
    
    def _initialize_nodes(self):
        """初始化节点实现（依赖注入，避免 Agent ↔ NodeManager 循环引用）"""
        now = datetime.now()
        time_str = now.strftime(LOG_TIMESTAMP_FORMAT)
        self.nodes = BrowserNodeManager(
            llm=self.llm,
            llm_with_tools=self.llm_with_tools,
            mcp_client=self.mcp_client,
            verbose=self.verbose,
            config=self.config,
            tools=self.tools,
            log_dir_name=time_str,
        )
    
    # ==================== 图构建 ====================
    
    def _build_graph(self) -> StateGraph:
        """构建工作流图（符合第一性原理的标准ReAct循环）
        
        使用 BrowserGraphBuilder 来构建工作流图。
        """
        graph_builder = BrowserGraphBuilder(
            tools=self.tools,
            nodes=self.nodes,
            routing_callbacks=self._get_routing_callbacks(),
        )
        return graph_builder.build()
    
    # ==================== 执行相关方法 ====================
    
    def _extract_context_config(self, context: Optional[BrowserAgentContext]) -> Dict[str, Any]:
        """提取上下文配置
        
        Args:
            context: 执行上下文
            
        Returns:
            配置字典，包含 max_iterations, max_tools_per_step, recursion_limit, session_id, connection_id
        """
        connection_id = (
            getattr(context, "connection_id", None) if context else None
        ) or self._connection_id
        return {
            "max_iterations": (
                context.max_iterations 
                if context and context.max_iterations is not None 
                else self.max_iterations
            ),
            "max_tools_per_step": (
                context.max_tools_per_step 
                if context and context.max_tools_per_step is not None 
                else self.max_tools_per_step
            ),
            "recursion_limit": (
                context.recursion_limit 
                if context and context.recursion_limit is not None 
                else self.recursion_limit
            ),
            "session_id": (
                context.session_id
                if context and getattr(context, "session_id", None)
                else None
            ),
            "connection_id": connection_id,
        }
    
    def _clear_screenshots_directory(self, session_id: Optional[str] = None) -> None:
        """清空截图文件夹（仅清理当前会话子目录，实现多会话隔离）
        
        每次新任务开始时调用。若提供 session_id，只清空 screenshots/<session_id>/；
        否则清空整个 screenshots/（兼容旧行为）。
        """
        screenshot_base = Path.cwd() / "screenshots"
        screenshot_dir = screenshot_base / session_id if session_id else screenshot_base
        if screenshot_dir.exists() and screenshot_dir.is_dir():
            try:
                for file_path in screenshot_dir.iterdir():
                    if file_path.is_file():
                        file_path.unlink()
                logger.info(f"🧹 已清空截图文件夹: {screenshot_dir}")
            except Exception as e:
                logger.warning(f"清空截图文件夹失败: {e}")
        else:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"📁 创建截图文件夹: {screenshot_dir}")
    
    def _create_initial_state(
        self, 
        task: str, 
        config: Dict[str, Any]
    ) -> BrowserAgentState:
        """创建初始状态
        
        Args:
            task: 任务描述
            config: 配置字典
            
        Returns:
            初始状态字典
        """
        # 🔧 更新：每次新任务开始时清空当前会话的截图文件夹（多会话隔离）
        session_id = config.get("session_id")
        self._clear_screenshots_directory(session_id=session_id)
        
        # 会话上下文：只注入当前窗口的最近对话，由模型自主思考；上一任务中工具产生的保存结果（json/mp4 等）供历史信息补充
        connection_id = config.get("connection_id") or self._connection_id
        recent_messages = get_recent_messages(connection_id)
        prior_saved_results = get_prior_saved_results(connection_id)

        logger.info(f"------debug---------> recent_messages: {recent_messages}")
        
        return {
            "messages": list(recent_messages),
            "prior_saved_results": prior_saved_results,
            "user_task": task,
            "plan_version": 0,
            "phase": BrowserAgentPhase.INITIALIZED.value,
            "tool_call_records": [],
            "decision_records": [],
            "current_url": None,
            "checkpoint_url": None,
            "page_snapshot": None,
            "page_status": None,  # 页面状态分析结果（由规划节点分析）
            "console_messages": [],
            "network_requests": [],
            "pages_info": {},  # 页面信息字典：{page_id: {"url": str, "title": str, "open_time": float, "last_active": float, "selected": bool}}
            "new_page_id": None,  # 新页面的pageId（由观察节点设置）
            "new_page_url": None,  # 新页面的URL（由观察节点设置）
            "popup_hint": None,  # 弹窗/广告检测提示（由观察节点设置，供执行节点注入 observation_result）
            "iteration_count": 0,
            "max_iterations": config["max_iterations"],
            "screenshots": [],
            "screenshot_count": 0,  # 截图计数器，用于命名截图文件
            "current_screenshot": None,  # 当前截图（base64编码）
            "session_id": session_id,  # 会话 ID，截图等资源按此分目录，多机/多连接互不干扰
            "reflection": None,
            "needs_replan": False,
            "task_completion_judgment": None,
            "final_result": None,
            # 错误处理（增强版）
            "error_count": 0,
            "last_error": None,
            "error_history": [],
            "last_error_category": None,
            "recovery_attempts": 0,
            # 🔧 新增：工具调用限制（确保传递到状态）
            "max_tools_per_step": config["max_tools_per_step"],
            # 操作记录上下文：已打开的页面（由观察节点更新）
            "page_records": [],  # List[PageRecord] 记录每个页面的 url、打开时间、步骤名称、使用工具
        }
    
    def _invoke_graph(
        self, 
        initial_state: BrowserAgentState, 
        recursion_limit: int
    ) -> BrowserAgentState:
        """执行工作流图（同步）
        
        Args:
            initial_state: 初始状态
            recursion_limit: 递归限制
            
        Returns:
            最终状态
        """
        return self.graph.invoke(
            initial_state,
            config={"recursion_limit": recursion_limit}
        )
    
    async def _invoke_graph_async(
        self, 
        initial_state: BrowserAgentState, 
        recursion_limit: int
    ) -> BrowserAgentState:
        """执行工作流图（异步，不阻塞事件循环）
        
        使用 LangGraph 的 ainvoke，内部 sync 节点会在线程池执行。
        
        Args:
            initial_state: 初始状态
            recursion_limit: 递归限制
            
        Returns:
            最终状态
        """
        return await self.graph.ainvoke(
            initial_state,
            config={"recursion_limit": recursion_limit}
        )
    
    async def astream_graph(
        self,
        initial_state: BrowserAgentState,
        recursion_limit: int,
    ) -> AsyncIterator[Dict[str, Any]]:
        """异步流式执行工作流图，逐块产出状态更新
        
        Yields:
            每个 chunk 为 {node_name: state_update}
        """
        async for chunk in self.graph.astream(
            initial_state,
            config={"recursion_limit": recursion_limit},
        ):
            yield chunk
    
    # ==================== 清理 ====================
    
    def __del__(self):
        """析构函数：停止 MCP 服务器"""
        try:
            self.stop()
        except Exception:
            pass


# 向后兼容别名
BrowserPlanningAgent = BrowserUseAgent

# 导出
__all__ = [
    "BrowserUseAgent",
    "BrowserPlanningAgent",
    "BrowserAgentContext",
]