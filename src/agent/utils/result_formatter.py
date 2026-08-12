"""
结果格式化工具

提供统一的结果格式化功能，用于将 Agent 执行结果转换为标准格式。
"""

from typing import Dict, Any

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.types.error_types import ErrorRecord
from src.agent.types.execution_records import ToolCallRecord, DecisionRecord


def build_execution_result(
    task: str,
    final_state: BrowserAgentState
) -> Dict[str, Any]:
    """构建执行结果
    
    Args:
        task: 任务描述
        final_state: 最终状态
        
    Returns:
        执行结果字典
    """
    # 转换工具调用记录和决策记录为字典格式
    tool_call_records = final_state.get("tool_call_records", [])
    decision_records = final_state.get("decision_records", [])
    
    return {
        "success": final_state.get("phase") == BrowserAgentPhase.COMPLETED.value,
        "task": task,
        "plan_version": final_state.get("plan_version", 1),
        "final_result": final_state.get("final_result"),
        "reflection": final_state.get("reflection"),
        "screenshots": final_state.get("screenshots", []),
        "iteration_count": final_state.get("iteration_count", 0),
        "tool_calls": len(tool_call_records),
        "tool_call_records": format_tool_records(tool_call_records),
        "decisions": len(decision_records),
        "decision_records": format_decision_records(decision_records),
        # 错误恢复信息
        "error_count": len(final_state.get("error_history", [])),
        "recovery_attempts": final_state.get("recovery_attempts", 0),
        "error_history": format_error_history(final_state.get("error_history", [])),
    }


def format_tool_records(tool_call_records: list) -> list:
    """格式化工具调用记录
    
    Args:
        tool_call_records: 工具调用记录列表
        
    Returns:
        格式化后的工具调用记录列表
    """
    return [
        record.to_dict() if isinstance(record, ToolCallRecord) else record
        for record in tool_call_records
    ]


def format_decision_records(decision_records: list) -> list:
    """格式化决策记录
    
    Args:
        decision_records: 决策记录列表
        
    Returns:
        格式化后的决策记录列表
    """
    return [
        record.to_dict() if isinstance(record, DecisionRecord) else record
        for record in decision_records
    ]


def format_error_history(error_history: list) -> list:
    """格式化错误历史
    
    Args:
        error_history: 错误历史列表
        
    Returns:
        格式化后的错误历史列表
    """
    return [
        err.to_dict() if isinstance(err, ErrorRecord) else err
        for err in error_history
    ]
