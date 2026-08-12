"""系统提示词模块

仅保留 browser-use 风格所需：
- 意图分析、任务完成判断
- browser-use 系统提示词与状态消息
- 提示词格式化工具函数
"""
from .browser_prompts import (
    INTENT_TASK_RELATION_PROMPT_TEMPLATE,
    BROWSER_TASK_COMPLETION_JUDGE_PROMPT_TEMPLATE,
    BROWSER_USE_SYSTEM_PROMPT_TEMPLATE,
    BROWSER_USE_STATE_MESSAGE_TEMPLATE,
)
from .browser_prompt_utils import (
    format_tools_description,
    get_task_completion_judge_prompt_with_format,
    get_execute_output_format,
    get_browser_use_system_prompt,
)

__all__ = [
    "INTENT_TASK_RELATION_PROMPT_TEMPLATE",
    "BROWSER_TASK_COMPLETION_JUDGE_PROMPT_TEMPLATE",
    "BROWSER_USE_SYSTEM_PROMPT_TEMPLATE",
    "BROWSER_USE_STATE_MESSAGE_TEMPLATE",
    "format_tools_description",
    "get_task_completion_judge_prompt_with_format",
    "get_execute_output_format",
    "get_browser_use_system_prompt",
]

