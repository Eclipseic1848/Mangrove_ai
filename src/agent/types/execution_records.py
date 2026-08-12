"""
Agent 记录类模块

提供工具调用记录和决策记录的数据结构。
这些记录用于追踪 Agent 的执行过程和决策历史。

说明：
- 此模块只包含实际使用的记录类
- SubAgentContext、ContextManager、ExecutionRecord 已删除（2026-01-29）
- 原因：这些类定义但未实际使用

位置说明：
- 从 src/agent/sub_agents/records.py 移动到 src/agent/state/execution_records.py（2026-01-29）
- 原因：这些记录类主要用于状态管理（BrowserAgentState），放在 state 目录更符合逻辑
- 文件名更新：records.py -> execution_records.py（2026-01-30），更清晰地表达文件用途
"""
import uuid
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    """
    工具调用记录 - 详细记录每次工具调用的输入、输出和反馈
    
    用于追踪 Agent 执行过程中的工具调用历史。
    """
    call_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    tool_name: str = ""
    tool_args: Dict[str, Any] = field(default_factory=dict)
    tool_result: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    duration_ms: Optional[float] = None
    next_action: Optional[str] = None  # 基于此工具反馈的下一步决策
    reasoning: Optional[str] = None    # 决策推理过程
    step_index: Optional[int] = None   # 🔧 新增：关联的计划步骤索引，用于界面显示
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "success": self.success,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "next_action": self.next_action,
            "reasoning": self.reasoning,
            "step_index": self.step_index,  # 🔧 新增：包含step_index
        }


@dataclass
class DecisionRecord:
    """
    决策记录 - 记录Agent基于工具反馈做出的决策
    
    用于追踪 Agent 的决策过程和推理历史。
    """
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    based_on_tool_call: Optional[str] = None  # 基于哪个工具调用
    tool_feedback: Optional[str] = None       # 工具反馈内容
    decision: str = ""                         # 决策内容
    reasoning: str = ""                        # 推理过程
    next_step: str = ""                        # 下一步操作
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "decision_id": self.decision_id,
            "based_on_tool_call": self.based_on_tool_call,
            "tool_feedback": self.tool_feedback,
            "decision": self.decision,
            "reasoning": self.reasoning,
            "next_step": self.next_step,
            "timestamp": self.timestamp,
        }


__all__ = [
    "ToolCallRecord",
    "DecisionRecord",
]
