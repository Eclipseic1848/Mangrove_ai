"""Agent 核心逻辑模块

包含：
- 浏览器自动化 Agent（browser-use 风格）
"""
# 浏览器自动化 Agent
from .browser_agent import (
    BrowserAgent,
    BrowserUseAgent,
    BrowserPlanningAgent,
)

# 浏览器状态
from .types import (
    BrowserAgentState,
    BrowserPlanStep,
    BrowserAgentPhase,
    BrowserAgentContext,
    ErrorCategory,
    RecoveryStrategy,
    ErrorRecord,
)

__all__ = [
    # 浏览器自动化 Agent
    "BrowserAgent",
    "BrowserUseAgent",
    "BrowserPlanningAgent",
    # 浏览器状态
    "BrowserAgentState",
    "BrowserPlanStep",
    "BrowserAgentPhase",
    "BrowserAgentContext",
    "ErrorCategory",
    "RecoveryStrategy",
    "ErrorRecord",
]

