"""
错误处理节点（v2 - 模块化版本）

负责处理执行过程中出现的错误，包括：
- 错误分类和记录
- 智能恢复策略选择
- 错误上下文构建
- 将错误信息传递给后续节点
"""
import logging
import uuid
import time
from typing import Dict, Any, Tuple

from langchain_core.messages import AIMessage

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.types.error_types import ErrorCategory, RecoveryStrategy, ErrorRecord
from src.agent.nodes.node_utils import classify_error, get_last_tool_name

logger = logging.getLogger(__name__)


# ==================== 常量定义 ====================

# 默认错误消息
DEFAULT_ERROR_MESSAGE = "未知错误"

# 恢复策略相关常量
MAX_RETRY_ATTEMPTS_BEFORE_REPLAN = 2

# 日志消息模板
ERROR_LOG_TEMPLATE = "⚠️ [错误处理节点]"
ERROR_CONTEXT_TEMPLATE = """
=== 错误报告 ===
错误信息: {error_msg}
错误分类: {error_category}
发生位置: 步骤 {step_index}
恢复策略: {recovery_strategy}
建议: {recovery_suggestion}
================
"""


# ==================== 错误处理节点类 ====================

class ErrorHandlerNode:
    """错误处理节点（v2 - 模块化版本）
    
    负责处理执行过程中出现的错误，提供智能的错误分类和恢复策略。
    """
    
    def __init__(self, implementation):
        """初始化错误处理节点
        
        Args:
            implementation: 节点实现对象，包含配置等依赖
        """
        self.node_impl = implementation

    # ==================== 错误分类方法 ====================

    def _classify_error(
        self, 
        error_msg: str, 
        state: BrowserAgentState
    ) -> Tuple[ErrorCategory, RecoveryStrategy, str]:
        """分类错误并获取恢复策略
        
        Args:
            error_msg: 错误消息
            state: 浏览器Agent状态
            
        Returns:
            (错误分类, 恢复策略, 恢复建议) 的元组
        """
        return classify_error(error_msg, state)

    # ==================== 恢复策略方法 ====================

    def _check_recovery_attempts(
        self, 
        recovery_attempts: int,
        recovery_strategy: RecoveryStrategy
    ) -> Tuple[RecoveryStrategy, str]:
        """检查恢复尝试次数并调整策略
        
        根据恢复尝试次数和当前策略，决定是否需要调整恢复策略。
        
        Args:
            recovery_attempts: 当前恢复尝试次数
            recovery_strategy: 当前恢复策略
            
        Returns:
            (调整后的恢复策略, 恢复建议) 的元组
        """
        max_retries = self.node_impl.config.error_handling.max_recovery_attempts
        
        # 如果达到最大重试次数，终止执行
        if recovery_attempts >= max_retries:
            return (
                RecoveryStrategy.ABORT,
                f"达到最大重试次数 ({max_retries})，终止执行"
            )
        
        # 如果同一步骤重试超过阈值，升级为重新规划
        if (recovery_strategy == RecoveryStrategy.RETRY_SAME_STEP and 
            recovery_attempts > MAX_RETRY_ATTEMPTS_BEFORE_REPLAN):
            return (
                RecoveryStrategy.REPLAN,
                "多次重试失败，尝试重新规划"
            )
        
        # 默认继续执行恢复策略
        return recovery_strategy, "继续执行恢复策略"

    def _determine_phase(self, recovery_strategy: RecoveryStrategy) -> str:
        """根据恢复策略确定阶段
        
        Args:
            recovery_strategy: 恢复策略
            
        Returns:
            阶段字符串
        """
        if recovery_strategy == RecoveryStrategy.ABORT:
            return BrowserAgentPhase.FAILED.value
        else:
            return BrowserAgentPhase.REPLANNING.value

    # ==================== 错误记录方法 ====================

    def _create_error_record(
        self,
        error_msg: str,
        error_category: ErrorCategory,
        current_step_index: int,
        recovery_strategy: RecoveryStrategy,
        state: BrowserAgentState
    ) -> ErrorRecord:
        """创建错误记录
        
        Args:
            error_msg: 错误消息
            error_category: 错误分类
            current_step_index: 当前步骤索引
            recovery_strategy: 恢复策略
            state: 浏览器Agent状态
            
        Returns:
            错误记录对象
        """
        return ErrorRecord(
            error_id=str(uuid.uuid4())[:8],
            category=error_category,
            message=error_msg,
            step_index=current_step_index,
            tool_name=get_last_tool_name(state),
            timestamp=time.time(),
            recovery_strategy=recovery_strategy,
            recovery_result=None,
        )

    def _update_error_history(
        self,
        state: BrowserAgentState,
        error_record: ErrorRecord
    ) -> list:
        """更新错误历史
        
        Args:
            state: 浏览器Agent状态
            error_record: 错误记录对象
            
        Returns:
            更新后的错误历史列表
        """
        error_history = list(state.get("error_history", []))
        error_history.append(error_record)
        return error_history

    # ==================== 日志记录方法 ====================

    def _log_error_info(
        self,
        error_msg: str,
        error_category: ErrorCategory,
        recovery_strategy: RecoveryStrategy,
        recovery_suggestion: str,
        recovery_attempts: int,
        max_retries: int,
        error_history: list
    ) -> None:
        """记录错误信息到日志
        
        Args:
            error_msg: 错误消息
            error_category: 错误分类
            recovery_strategy: 恢复策略
            recovery_suggestion: 恢复建议
            recovery_attempts: 恢复尝试次数
            max_retries: 最大重试次数
            error_history: 错误历史列表
        """
        if not self.node_impl.verbose:
            return
        
        logger.info(ERROR_LOG_TEMPLATE)
        logger.info(f"   错误信息: {error_msg}")
        logger.info(f"   错误分类: {error_category.value}")
        logger.info(f"   恢复策略: {recovery_strategy.value}")
        logger.info(f"   恢复建议: {recovery_suggestion}")
        logger.info(f"   恢复尝试: {recovery_attempts}/{max_retries}")
        logger.info(f"   错误历史: {len(error_history)} 条记录")

    # ==================== 上下文构建方法 ====================

    def _build_error_context(
        self,
        error_msg: str,
        error_category: ErrorCategory,
        current_step_index: int,
        recovery_strategy: RecoveryStrategy,
        recovery_suggestion: str
    ) -> str:
        """构建错误上下文信息
        
        Args:
            error_msg: 错误消息
            error_category: 错误分类
            current_step_index: 当前步骤索引
            recovery_strategy: 恢复策略
            recovery_suggestion: 恢复建议
            
        Returns:
            格式化的错误上下文字符串
        """
        return ERROR_CONTEXT_TEMPLATE.format(
            error_msg=error_msg,
            error_category=error_category.value,
            step_index=current_step_index + 1,
            recovery_strategy=recovery_strategy.value,
            recovery_suggestion=recovery_suggestion
        )

    def _build_error_message(self, error_context: str) -> str:
        """构建错误消息
        
        Args:
            error_context: 错误上下文字符串
            
        Returns:
            完整的错误消息字符串
        """
        return f"遇到错误，正在分析恢复策略...\n{error_context}"

    # ==================== 主方法 ====================

    def handle_error(self, state: BrowserAgentState) -> Dict[str, Any]:
        """错误处理节点主入口（增强版）
        
        功能：
        1. 错误分类和记录
        2. 智能恢复策略选择
        3. 将错误信息携带到后续节点
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            包含错误处理结果的状态更新字典
        """
        error_msg = state.get("last_error") or DEFAULT_ERROR_MESSAGE
        recovery_attempts = state.get("recovery_attempts", 0) + 1
        current_step_index = 0
        
        # 分类错误
        error_category, recovery_strategy, recovery_suggestion = self._classify_error(
            error_msg, state
        )
        
        # 检查恢复尝试次数并调整策略
        recovery_strategy, recovery_suggestion = self._check_recovery_attempts(
            recovery_attempts,
            recovery_strategy
        )
        
        # 创建错误记录
        error_record = self._create_error_record(
            error_msg,
            error_category,
            current_step_index,
            recovery_strategy,
            state
        )
        
        # 更新错误历史
        error_history = self._update_error_history(state, error_record)
        
        # 记录错误信息
        max_retries = self.node_impl.config.error_handling.max_recovery_attempts
        self._log_error_info(
            error_msg,
            error_category,
            recovery_strategy,
            recovery_suggestion,
            recovery_attempts,
            max_retries,
            error_history
        )
        
        # 构建错误上下文
        error_context = self._build_error_context(
            error_msg,
            error_category,
            current_step_index,
            recovery_strategy,
            recovery_suggestion
        )
        
        # 确定阶段
        phase = self._determine_phase(recovery_strategy)
        
        # 构建返回结果
        # 若为「重试当前步骤」，清除 last_error 以免路由到 observe->execute 后再次被判为需进 error_handler
        clear_error_on_retry = recovery_strategy == RecoveryStrategy.RETRY_SAME_STEP
        return {
            "messages": [AIMessage(content=self._build_error_message(error_context))],
            "last_error": None if clear_error_on_retry else error_msg,
            "last_error_category": error_category.value,
            "error_history": error_history,
            "recovery_attempts": recovery_attempts,
            "needs_replan": recovery_strategy == RecoveryStrategy.REPLAN,
            "phase": phase,
        }