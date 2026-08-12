"""
统一的路由管理器（browser-use：无 plan 节点，observe→execute/reflect，reflect→execute/end）
"""
import logging
from typing import Optional
from dataclasses import dataclass

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.config.agent_config import BrowserAgentConfig
from src.agent.utils.logging_utils import log_routing_decision, log_error_recovery_decision
from src.agent.utils.agent_flow_logger import log_routing as flow_log_routing, log_flow_milestone

logger = logging.getLogger(__name__)


# ==================== 路由目标常量 ====================

ROUTE_END = "__end__"
ROUTE_EXECUTE = "execute"
ROUTE_TOOLS = "tools"
ROUTE_OBSERVE = "observe"
ROUTE_REFLECT = "reflect"
ROUTE_ERROR_HANDLER = "error_handler"


# ==================== 路由配置 ====================

@dataclass
class RouterConfig:
    """路由配置"""
    max_plan_versions: int = 3
    max_recovery_attempts: int = 3
    observe_after_each_step: bool = False


# ==================== 路由管理器 ====================

class BrowserAgentRouter:
    """统一的路由管理器
    
    职责：
    - 管理 Agent 执行流程中的节点路由
    - 检查执行状态（错误、终止条件等）
    - 决定下一个执行的节点
    
    标准流程：
    - Plan -> Execute -> Tools -> Observe -> Reflect -> Plan/Execute/END
    - Tools (失败) -> Error_Handler -> Reflect -> Plan/END
    - Execute (无工具调用) -> Reflect -> Plan/Execute/END
    """
    
    def __init__(self, config: BrowserAgentConfig, verbose: bool = True):
        """初始化路由器
        
        Args:
            config: Agent 配置
            verbose: 是否详细输出（用于路由决策日志）
        """
        self.config = config
        self.verbose = verbose
        self.max_iterations = config.agent.max_iterations
        self.router_config = RouterConfig(
            max_plan_versions=config.planning.max_plan_versions,
            max_recovery_attempts=config.error_handling.max_recovery_attempts,
            observe_after_each_step=config.agent.observe_after_each_step,
        )
    
    # ==================== 状态检查方法 ====================
    
    def should_handle_error(self, state: BrowserAgentState) -> bool:
        """检查是否需要进入错误处理
        
        Args:
            state: Agent 状态
            
        Returns:
            True 如果需要进入错误处理节点
        """
        # browser-use 仅用 last_error 判定是否进入错误处理
        return bool(state.get("last_error"))
    
    def should_terminate(self, state: BrowserAgentState) -> bool:
        """检查是否应该终止执行
        
        终止条件：
        1. 阶段为 FAILED 或 COMPLETED
        2. 达到最大重规划次数
        3. 达到最大迭代次数
        4. 达到最大恢复尝试次数
        
        Args:
            state: Agent 状态
            
        Returns:
            True 如果应该终止执行
        """
        phase = state.get("phase", "")
        
        # 检查阶段
        if phase in [BrowserAgentPhase.FAILED.value, BrowserAgentPhase.COMPLETED.value]:
            return True
        
        # 检查最大重规划次数（plan_version 为已使用次数，> max 才终止以允许恰好 max 次重新开始）
        plan_version = state.get("plan_version", 0)
        if plan_version > self.router_config.max_plan_versions:
            logger.debug(f"达到最大重规划次数: plan_version={plan_version} > {self.router_config.max_plan_versions}")
            return True
        
        # 检查最大迭代次数
        iteration_count = state.get("iteration_count", 0)
        max_iterations = state.get("max_iterations", self.config.agent.max_iterations)
        if iteration_count >= max_iterations:
            logger.debug(f"达到最大迭代次数: {iteration_count}/{max_iterations}")
            return True
        
        # 检查最大恢复尝试次数
        recovery_attempts = state.get("recovery_attempts", 0)
        if recovery_attempts >= self.router_config.max_recovery_attempts:
            logger.debug(f"达到最大恢复尝试次数: {recovery_attempts}")
            return True
        
        return False
    
    def _should_terminate_excluding_plan_version(self, state: BrowserAgentState) -> bool:
        """检查是否应该终止（排除 plan_version 检查）
        
        plan_version 限制在 route_after_reflect 中生效（禁止再次重规划）；此处仅检查 phase、迭代次数、恢复次数。
        """
        phase = state.get("phase", "")
        if phase in [BrowserAgentPhase.FAILED.value, BrowserAgentPhase.COMPLETED.value]:
            return True
        iteration_count = state.get("iteration_count", 0)
        max_iterations = state.get("max_iterations", self.config.agent.max_iterations)
        if iteration_count >= max_iterations:
            return True
        recovery_attempts = state.get("recovery_attempts", 0)
        if recovery_attempts >= self.router_config.max_recovery_attempts:
            return True
        return False
    
    def should_call_tools(self, state: BrowserAgentState) -> bool:
        """检查是否应该调用工具
        
        Args:
            state: Agent 状态
            
        Returns:
            True 如果应该调用工具
        """
        messages = state.get("messages", [])
        if not messages:
            return False
        
        last_msg = messages[-1]
        return hasattr(last_msg, "tool_calls") and bool(last_msg.tool_calls)
    
    # ==================== 路由方法 ====================
    
    def route_after_execute(self, state: BrowserAgentState) -> str:
        """Execute 节点后的路由
        
        路由逻辑（符合第一性原理）：
        0. 若 phase 为 completed/failed（LLM 填 task_complete 或强制结束）-> __end__
        1. 如果应该终止 -> END
        2. 如果有错误 -> Error_Handler
        3. 如果有工具调用 -> Tools（必须先执行工具，不能直接跳到 Observe）
        4. 如果所有步骤已完成 -> Reflect
        5. 默认 -> Reflect（让 Reflect 决定下一步）
        
        Args:
            state: Agent 状态
            
        Returns:
            下一个节点的名称
        """
        phase = state.get("phase", "")
        if phase in [BrowserAgentPhase.COMPLETED.value, BrowserAgentPhase.FAILED.value]:
            
            if self.verbose:
            #     flow_log_routing("execute", ROUTE_END, f"phase={phase}，任务结束")
            # return ROUTE_END
                flow_log_routing("execute", ROUTE_REFLECT, f"phase={phase}，先进入反思做完成总结")
            return ROUTE_REFLECT
        if self.should_terminate(state):
            result = ROUTE_END
        elif self.should_handle_error(state):
            result = ROUTE_ERROR_HANDLER
        elif self.should_call_tools(state):
            result = ROUTE_TOOLS
        else:
            result = ROUTE_REFLECT
        log_routing_decision("execute", result, state, self.verbose, self.max_iterations)
        return result
    
    def route_after_tools(self, state: BrowserAgentState) -> str:
        """Tools 节点后的路由
        
        路由逻辑（符合文档建议）：
        0. 若 phase 为 completed/failed（如 LLM 填 task_complete 或强制结束）-> __end__，立即结束
        1. 如果有错误 -> Error_Handler
        2. 如果工具调用次数超限 -> Observe（标记步骤完成）
        3. 如果还有待执行的工具调用 -> Execute（继续执行）
        4. 默认 -> Observe（工具执行成功后必须观察）
        
        Args:
            state: Agent 状态
            
        Returns:
            下一个节点的名称
        """
        phase = state.get("phase", "")
        if phase in [BrowserAgentPhase.COMPLETED.value, BrowserAgentPhase.FAILED.value]:
            if self.verbose:
                flow_log_routing("tools", ROUTE_REFLECT, f"phase={phase}，先进入反思做完成总结")
            return ROUTE_REFLECT
        # 错误处理优先（工具执行失败）
        if self.should_handle_error(state):
            result = ROUTE_ERROR_HANDLER
        elif self._is_tool_calls_limit_reached(state):
            result = ROUTE_OBSERVE
        elif self.should_call_tools(state):
            result = ROUTE_EXECUTE
        else:
            result = ROUTE_OBSERVE
        if self.verbose and result == ROUTE_ERROR_HANDLER:
            from src.agent.utils.terminal_colors import orange
            last_error = state.get("last_error", "")
            print(orange(f"\n⚠️ Tools节点检测到错误，进入错误处理: {last_error[:100]}..."))
        return result
    
    def route_after_observe(self, state: BrowserAgentState) -> str:
        """Observe 节点后的路由：观察后一律进 Execute。Reflect 仅在任务可能结束时由 Execute 无工具调用时进入，用于根据历史做完成总结，不按轮次定期进入以节省 token。"""
        if self.verbose:
            flow_log_routing("observe", ROUTE_EXECUTE, "继续执行")
        return ROUTE_EXECUTE
    
    def route_after_reflect(self, state: BrowserAgentState) -> str:
        """Reflect 节点后的路由：已完成/失败或达重规划上限或达最大迭代次数 -> END；否则 -> Execute。"""
        phase = state.get("phase", "")
        if phase in [BrowserAgentPhase.COMPLETED.value, BrowserAgentPhase.FAILED.value]:
            result = ROUTE_END
            log_flow_milestone("路由 → 结束", reason="phase=%s" % phase)
        elif state.get("needs_replan") or phase == BrowserAgentPhase.REPLANNING.value:
            plan_version = state.get("plan_version", 0)
            iteration_count = state.get("iteration_count", 0)
            max_iterations = state.get("max_iterations", self.config.agent.max_iterations)
            # plan_version 表示「已使用的重新开始次数」；允许 1..max 次，超过才结束（> 而非 >=）
            if plan_version > self.router_config.max_plan_versions:
                logger.info("🔀 [ROUTING] 已达最大重规划次数 (%d)，结束", self.router_config.max_plan_versions)
                result = ROUTE_END
                log_flow_milestone("路由 → 结束", reason="已达最大重规划次数")
            elif iteration_count >= max_iterations:
                logger.info("🔀 [ROUTING] 已达最大迭代次数，不再回执行，结束")
                result = ROUTE_END
                log_flow_milestone("路由 → 结束", reason="已达最大迭代次数")
            else:
                logger.info("🔀 [ROUTING] 需要重新规划，路由到 Execute")
                result = ROUTE_EXECUTE
                log_flow_milestone("路由 → 执行", plan_version=plan_version + 1, reason="重新规划")
        else:
            # browser-use：无步骤列表，按 final_result / plan_version 决定继续或结束
            if not state.get("final_result") or state.get("plan_version", 0) < self.router_config.max_plan_versions:
                logger.info("🔀 [ROUTING] 继续执行")
                result = ROUTE_EXECUTE
                log_flow_milestone("路由 → 执行", reason="继续执行")
            else:
                result = ROUTE_END
                log_flow_milestone("路由 → 结束", reason="任务结束")
        log_routing_decision("reflect", result, state, self.verbose, self.max_iterations)
        return result
    
    def route_after_error_handler(self, state: BrowserAgentState) -> str:
        """Error Handler 节点后的路由
        
        路由逻辑：
        1. 如果已经标记为失败 -> END
        2. 如果达到最大恢复尝试次数 -> END
        3. 若为「重试当前步骤」(needs_replan=False) -> Observe，直接重试，不进反思以节省 token、避免重复反思
        4. 若为「需要重新规划」(needs_replan=True) -> Reflect，由 Reflect 评估并回到 Execute
        
        Args:
            state: Agent 状态
            
        Returns:
            下一个节点的名称
        """
        phase = state.get("phase", "")
        if phase == BrowserAgentPhase.FAILED.value:
            result = ROUTE_END
        elif state.get("recovery_attempts", 0) >= self.router_config.max_recovery_attempts:
            result = ROUTE_END
        elif state.get("needs_replan"):
            result = ROUTE_REFLECT
        else:
            # retry_same_step：直接进 observe -> execute 重试，不进反思
            result = ROUTE_OBSERVE
        log_error_recovery_decision(
            result, state, self.verbose, self.router_config.max_recovery_attempts
        )
        return result
    
    def route_after_task_completion_judge(self, state: BrowserAgentState) -> str:
        """Task Completion Judge 节点后的路由（已废弃）
        
        向后兼容：重定向到 route_after_reflect
        
        Args:
            state: Agent 状态
            
        Returns:
            下一个节点的名称
        """
        return self.route_after_reflect(state)
    
    # ==================== 工具相关辅助方法 ====================
    
    def _is_tool_calls_limit_reached(self, state: BrowserAgentState) -> bool:
        """browser-use 无步骤列表，不按步骤限制工具次数，始终返回 False。"""
        return False
    
    def _get_last_tool_name(self, state: BrowserAgentState) -> Optional[str]:
        """获取最近调用的工具名称
        
        Args:
            state: Agent 状态
            
        Returns:
            工具名称，如果不存在则返回 None
        """
        tool_call_records = state.get("tool_call_records", [])
        if not tool_call_records:
            return None
        
        last_record = tool_call_records[-1]
        if hasattr(last_record, "tool_name"):
            return last_record.tool_name
        elif isinstance(last_record, dict):
            return last_record.get("tool_name")
        
        return None
    
    def _is_page_action_tool(self, tool_name: Optional[str]) -> bool:
        """判断是否为页面操作工具（需要观察结果）
        
        Args:
            tool_name: 工具名称
            
        Returns:
            True 如果是页面操作工具
        """
        if not tool_name:
            return False
        
        page_action_tools = self.config.tool_classification.page_action_tools
        return tool_name in page_action_tools
    
    def _is_query_tool(self, tool_name: Optional[str]) -> bool:
        """判断是否为查询工具（可以直接反思）
        
        Args:
            tool_name: 工具名称
            
        Returns:
            True 如果是查询工具
        """
        if not tool_name:
            return False
        
        query_tools = self.config.tool_classification.query_tools
        return tool_name in query_tools

