"""
浏览器 Agent 错误处理

定义错误分类、恢复策略和错误记录。
"""
import time
import uuid
from typing import Optional
from dataclasses import dataclass
from enum import Enum


class ErrorCategory(Enum):
    """错误分类"""
    TOOL_EXECUTION = "tool_execution"      # 工具执行错误
    TOOL_NOT_FOUND = "tool_not_found"      # 工具未找到
    INVALID_ARGS = "invalid_args"          # 参数无效
    NAVIGATION_ERROR = "navigation_error"   # 导航错误
    ELEMENT_NOT_FOUND = "element_not_found"  # 元素未找到
    LLM_ERROR = "llm_error"                # LLM 调用错误
    TIMEOUT = "timeout"                    # 超时
    UNKNOWN = "unknown"                    # 未知错误


class RecoveryStrategy(Enum):
    """恢复策略"""
    RETRY_SAME_STEP = "retry_same_step"     # 重试当前步骤
    REPLAN = "replan"                       # 重新规划
    SKIP_STEP = "skip_step"                 # 跳过当前步骤
    ABORT = "abort"                         # 终止执行


@dataclass
class ErrorRecord:
    """错误记录"""
    error_id: str
    category: ErrorCategory
    message: str
    step_index: Optional[int]  # 发生错误的步骤索引
    tool_name: Optional[str]   # 相关工具名称
    timestamp: float
    recovery_strategy: RecoveryStrategy
    recovery_result: Optional[str] = None  # 恢复结果
    
    def to_dict(self) -> dict:
        return {
            "error_id": self.error_id,
            "category": self.category.value,
            "message": self.message,
            "step_index": self.step_index,
            "tool_name": self.tool_name,
            "recovery_strategy": self.recovery_strategy.value,
            "recovery_result": self.recovery_result,
        }

