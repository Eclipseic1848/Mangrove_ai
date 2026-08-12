"""系统级功能模块

提供系统级别的功能，包括：
- 日志配置
"""
from .logs import (
    setup_logging,
    setup_global_logging,
    attach_task_log_file,
    detach_task_log_file,
    set_current_task_log_id,
    reset_current_task_log_id,
    get_current_task_log_id,
    install_context_propagating_default_executor,
    get_logger,
    get_task_log_paths,
)

__all__ = [
    "setup_logging",
    "setup_global_logging",
    "attach_task_log_file",
    "detach_task_log_file",
    "set_current_task_log_id",
    "reset_current_task_log_id",
    "get_current_task_log_id",
    "install_context_propagating_default_executor",
    "get_logger",
    "get_task_log_paths",
]
