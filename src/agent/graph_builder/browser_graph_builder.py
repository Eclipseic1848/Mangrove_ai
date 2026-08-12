"""
浏览器 Agent 图构建器

负责构建浏览器自动化 Agent 的工作流图。
"""

import logging
from pathlib import Path
from typing import Callable

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from src.agent.types.state_types import BrowserAgentState

logger = logging.getLogger(__name__)


class BrowserGraphBuilder:
    """
    浏览器 Agent 图构建器
    
    负责构建符合第一性原理的标准 ReAct 循环工作流图。
    
    架构说明（browser-use 风格，无 plan-first）：
    - intent: 意图节点，分析任务关联性，无关联时清理多余标签页
    - execute: 执行节点，LLM 每步自主决定动作
    - tools: 工具执行节点，负责调用MCP工具
    - observe: 观察节点，负责观察工具执行后的结果（遵循因果律）
    - reflect: 反思节点，评估执行结果并判断任务完成
    
    标准流程：
    1. START → intent → observe (观察当前页面)
    2. observe → execute (无计划直接执行) 或 observe → reflect (步骤完成时反思)
    3. execute → tools (有工具调用) OR → reflect (无工具调用)
    4. tools → observe (成功) OR → error_handler (失败)
    5. reflect → execute (继续执行) OR → END (完成)
    6. error_handler → observe (重试当前步) OR → reflect (需重新规划) OR → END (不可恢复)
    """
    
    def __init__(
        self,
        tools: list,
        nodes: object,
        routing_callbacks: dict[str, Callable],
    ):
        """
        初始化图构建器
        
        Args:
            tools: 工具列表
            nodes: 节点实现对象
            routing_callbacks: 路由回调函数字典，包含：
                - after_observe: observe 节点后的路由函数
                - after_execute: execute 节点后的路由函数
                - after_tools: tools 节点后的路由函数
                - after_reflect: reflect 节点后的路由函数
                - error_recovery: error_handler 节点后的路由函数
        """
        self.tools = tools
        self.nodes = nodes
        self.routing_callbacks = routing_callbacks
    
    def build(self) -> StateGraph:
        """构建工作流图
        
        Returns:
            编译后的工作流图
        """
        graph_builder = StateGraph(BrowserAgentState)
        
        # 步骤1: 添加所有节点
        self._add_nodes(graph_builder)
        
        # 步骤2: 添加所有边
        self._add_edges(graph_builder)
        
        # 步骤3: 编译图
        app = graph_builder.compile()
        
        # 步骤4: 生成图结构可视化
        self._generate_visualization(app)
        
        return app
    
    def _add_nodes(self, graph_builder: StateGraph):
        """添加所有节点到图中"""
        # 工具节点
        tool_node = ToolNode(self.tools)
        
        # 添加节点（按工作流顺序，browser-use 模式无 plan 节点）
        graph_builder.add_node("intent", self.nodes.intent_node)
        graph_builder.add_node("execute", self.nodes.execute_node)
        graph_builder.add_node("tools", self.nodes.wrap_tool(tool_node))
        graph_builder.add_node("observe", self.nodes.observe_node)
        graph_builder.add_node("reflect", self.nodes.reflect_node)
        graph_builder.add_node("error_handler", self.nodes.error_handler_node)
    
    def _add_edges(self, graph_builder: StateGraph):
        """添加所有边到图中（按工作流顺序）"""
        # 起始边：先意图分析，再观察
        graph_builder.add_edge(START, "intent")
        graph_builder.add_edge("intent", "observe")
        
        # observe 后条件边：browser-use 模式无 plan，仅 execute 或 reflect
        graph_builder.add_conditional_edges(
            "observe",
            self.routing_callbacks["after_observe"],
            {
                "execute": "execute",
                "reflect": "reflect",
            }
        )
        
        # 条件边（按工作流顺序）
        self._add_execute_edges(graph_builder)
        self._add_tools_edges(graph_builder)
        self._add_reflect_edges(graph_builder)
        self._add_error_handler_edges(graph_builder)
    
    def _add_execute_edges(self, graph_builder: StateGraph):
        """添加 execute 节点后的条件边
        
        🔧 修复：遵循因果律，execute 不能直接流向 observe
        execute -> tools (如果有工具调用) -> observe -> reflect
        execute -> reflect (如果没有工具调用，直接反思)
        """
        graph_builder.add_conditional_edges(
            "execute",
            self.routing_callbacks["after_execute"],
            {
                "tools": "tools",  # 有工具调用时，必须先执行工具
                "reflect": "reflect",  # 没有工具调用时，直接反思
                "error_handler": "error_handler",  # 执行错误
                "__end__": END,  # task_complete 或 phase=completed 时直接结束
            }
        )
    
    def _add_tools_edges(self, graph_builder: StateGraph):
        """添加 tools 节点后的条件边（符合第一性原理）
        
        🔧 修复：tools 成功后必须流向 observe（遵循因果律：执行->观察）
        tools 失败后流向 error_handler
        """
        graph_builder.add_conditional_edges(
            "tools",
            self.routing_callbacks["after_tools"],
            {
                "error_handler": "error_handler",  # 工具执行失败
                "observe": "observe",  # 工具执行成功，必须观察结果
                "execute": "execute",  # 仍有工具调用需要继续执行（内部闭环）
                "__end__": END,  # task_complete 或 phase=completed 时直接结束
            }
        )
    
    def _add_reflect_edges(self, graph_builder: StateGraph):
        """添加 reflect 节点后的条件边（合并了任务完成判断功能）"""
        graph_builder.add_conditional_edges(
            "reflect",
            self.routing_callbacks["after_reflect"],
            {
                "execute": "execute",  # browser-use 模式：needs_replan 时也路由到 execute
                "reflect": "reflect",
                "__end__": END,
            }
        )
    
    def _add_error_handler_edges(self, graph_builder: StateGraph):
        """添加 error_handler 节点后的条件边
        
        - retry_same_step（needs_replan=False）-> observe，直接重试，不进反思
        - replan（needs_replan=True）-> reflect，由 reflect 评估并回到 execute
        - 无法恢复 -> __end__
        """
        graph_builder.add_conditional_edges(
            "error_handler",
            self.routing_callbacks["error_recovery"],
            {
                "observe": "observe",  # 重试当前步骤，直接观察后继续执行
                "reflect": "reflect",  # 需要重新规划时反思
                "__end__": END,  # 无法恢复的错误
            }
        )
    
    def _generate_visualization(self, app):
        """生成图结构可视化"""
        try:
            png_bytes = app.get_graph().draw_mermaid_png()
            output_path = Path("browser_agent_graph.png")
            with open(output_path, "wb") as f:
                f.write(png_bytes)
            logger.info(f"图结构已保存到: {output_path.absolute()}")
        except Exception as e:
            logger.debug(f"无法生成流程图: {e}")
