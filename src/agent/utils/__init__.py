"""
Agent 工具函数模块

提供通用的工具函数，包括文本处理、格式化、日志打印、结果格式化等功能。
"""
from .text_utils import (
    smart_truncate_snapshot,
    smart_truncate_text,
    extract_url_from_snapshot,
    extract_keyword_relevant_snapshot,
    smart_format_page_info,
)
from .logging_utils import (
    print_startup_message,
    print_execution_summary,
    print_status_summary,
    print_plan_summary,
    print_result_summary,
    log_routing_decision,
    log_error_recovery_decision,
    log_tool_event,
    get_step_status_icon,
    truncate_description,
    extract_result_preview,
)
from .result_formatter import (
    build_execution_result,
    format_tool_records,
    format_decision_records,
    format_error_history,
)
from .url_utils import (
    extract_target_url_from_task,
    clean_url_trailing_punctuation,
    normalize_url,
    is_valid_url,
    is_404_page_url,
)
from .task_utils import (
    extract_task_keywords,
    match_keywords_in_snapshot,
)
from .action_utils import (
    extract_action_type_from_tool_name,
    matches_action_type,
)
from .screenshot_utils import (
    capture_screenshot,
    get_screenshot_dir,
    get_screenshot_path,
)

__all__ = [
    # 文本处理工具
    "smart_truncate_snapshot",
    "smart_truncate_text",
    "extract_url_from_snapshot",
    "extract_keyword_relevant_snapshot",
    "smart_format_page_info",
    # 日志打印工具
    "print_startup_message",
    "print_execution_summary",
    "print_status_summary",
    "print_plan_summary",
    "print_result_summary",
    "log_routing_decision",
    "log_error_recovery_decision",
    "log_tool_event",
    "get_step_status_icon",
    "truncate_description",
    "extract_result_preview",
    # 结果格式化工具
    "build_execution_result",
    "format_tool_records",
    "format_decision_records",
    "format_error_history",
    # URL 处理工具
    "extract_target_url_from_task",
    "clean_url_trailing_punctuation",
    "normalize_url",
    "is_valid_url",
    "is_404_page_url",
    # 任务处理工具
    "extract_task_keywords",
    "match_keywords_in_snapshot",
    # 操作类型识别工具
    "extract_action_type_from_tool_name",
    "matches_action_type",
    # 截图工具
    "capture_screenshot",
    "get_screenshot_dir",
    "get_screenshot_path",
]

