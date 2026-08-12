"""
Agent 类型定义模块

定义浏览器自动化 Agent 相关的所有类型和数据结构。

包含内容：
1. 状态类型（state_types.py）
   - BrowserAgentState: Agent 状态定义
   - BrowserPlanStep: 计划步骤类型
   - BrowserAgentPhase: 执行阶段枚举
   - BrowserAgentContext: 执行上下文

2. 错误类型（error_types.py）
   - ErrorCategory: 错误分类枚举
   - RecoveryStrategy: 恢复策略枚举
   - ErrorRecord: 错误记录数据类

3. 执行记录（execution_records.py）
   - ToolCallRecord: 工具调用记录
   - DecisionRecord: 决策记录

状态管理机制：
- 使用 LangGraph 原生状态管理机制
- 节点函数接收 BrowserAgentState 作为参数
- 节点返回字典（dict）包含需要更新的字段
- LangGraph 自动将返回的字典合并到当前状态中

说明：
- BrowserAgentStateManager 已删除（2026-01-29）
- 原因：当前系统使用 LangGraph 原生状态管理，无需自定义状态管理器
- 如需状态历史追踪功能，可在未来重新实现或使用 LangGraph 的检查点功能
- 文件夹名称更新：state -> types（2026-01-30），更准确地反映文件夹内容（类型定义）
"""
from .state_types import (
    BrowserAgentState,
    BrowserPlanStep,
    BrowserAgentPhase,
    BrowserAgentContext,
    TaskState,
    BrowserPageState,
    PlanExecutionState,
    ReflectionState,
    ErrorState,
)
from .error_types import (
    ErrorCategory,
    RecoveryStrategy,
    ErrorRecord,
)
from .execution_records import (
    ToolCallRecord,
    DecisionRecord,
)

__all__ = [
    # 浏览器状态
    "BrowserAgentState",
    "BrowserPlanStep",
    "BrowserAgentPhase",
    "BrowserAgentContext",
    # 状态子结构（逻辑分组）
    "TaskState",
    "BrowserPageState",
    "PlanExecutionState",
    "ReflectionState",
    "ErrorState",
    # 浏览器错误处理
    "ErrorCategory",
    "RecoveryStrategy",
    "ErrorRecord",
    # 记录类
    "ToolCallRecord",
    "DecisionRecord",
]

