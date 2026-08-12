"""
浏览器 Agent 节点工具方法

包含节点实现中使用的工具函数和辅助方法，按服务节点分类组织。

代码结构：
1. PlanNode 专用工具
   - 页面分析（analyze_page_status）
2. ErrorHandlerNode 专用工具
   - 错误分类
   - 工具调用记录查询
3. ReflectNode 专用工具
   - 执行统计收集
   - 步骤结果收集
   - 错误信息收集
   - 状态图标和格式化

注意：通用工具函数已移至 utils 模块：
- URL 处理：src.agent.utils.url_utils
- 任务处理：src.agent.utils.task_utils
- 操作类型识别：src.agent.utils.action_utils
"""
import re
import uuid
import time
import json
import logging
from typing import List, Optional, Dict, Set, Any, Tuple
from pathlib import Path

from src.agent.types.state_types import BrowserAgentState, BrowserPlanStep
from src.agent.types.error_types import ErrorCategory, RecoveryStrategy, ErrorRecord
from src.agent.config.text_processing_config import default_text_config
from src.agent.utils.text_utils import smart_truncate_text
# 导入通用工具函数（用于内部使用）
from src.agent.utils.task_utils import extract_task_keywords
from src.agent.utils.url_utils import is_404_page_url

logger = logging.getLogger(__name__)


# ==================== 通用工具 ====================

def save_tool_result_to_file(
    tool_name: str,
    output_params: str,
    timestamp: str,
    logs_dir: Optional[Path] = None,
    verbose: bool = False
) -> None:
    """保存工具结果到文件（供 execute_node、tools_wrapper 等复用）
    
    Args:
        tool_name: 工具名称
        output_params: 输出参数字符串
        timestamp: 时间戳字符串（如 20260211_162723）
        logs_dir: 日志目录路径，如果为 None 则使用 settings.logs_dir 默认值
        verbose: 是否输出详细日志
    """
    # 如果未提供 logs_dir，使用 settings 中的默认值
    if logs_dir is None:
        from src.config import settings
        logs_dir = Path(settings.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
    else:
        # 确保 logs_dir 是 Path 对象
        if not isinstance(logs_dir, Path):
            logs_dir = Path(logs_dir)
        # 确保目录存在
        logs_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        filename = logs_dir / f"{tool_name}_result_{timestamp}.json"
        with open(filename, "w", encoding="utf-8") as f:
            if isinstance(output_params, str):
                try:
                    parsed = json.loads(output_params)
                    json.dump(parsed, f, ensure_ascii=False, indent=2)
                except (json.JSONDecodeError, TypeError):
                    f.write(output_params)
            else:
                json.dump(output_params, f, ensure_ascii=False, indent=2, default=str)
        if verbose:
            logger.info(f"      💾 保存 {tool_name} 工具结果到: {filename}")
    except Exception as e:
        logger.error(f"      [保存文件失败: {e}]")


# ==================== URL 处理工具 ====================
# 注意：URL 处理相关函数已移至 src.agent.utils.url_utils
# 请直接从 utils.url_utils 导入使用


# ==================== 页面分析工具 ====================

def analyze_page_status(page_snapshot: str, current_url: Optional[str], user_task: str) -> Dict[str, Any]:
    """
    通用页面状态分析函数
    
    服务节点：PlanNode（用于初始规划时分析页面状态，制定智能计划）
    
    功能：
    - 检测用户登录状态
    - 检测登录表单
    - 提取任务关键词并匹配页面元素
    - 判断页面类型
    - 提供规划建议
    
    Args:
        page_snapshot: 页面快照文本
        current_url: 当前URL
        user_task: 用户任务描述
    
    Returns:
        页面状态分析结果字典
    """
    status_info = {
        "is_logged_in": False,
        "has_login_form": False,
        "contains_target_elements": [],
        "page_type": "unknown",
        "recommendations": []
    }

    # 优先检测 404 等无效页面 URL（如汽车之家 https://s.autoimg.cn/club/bbs/pc/blank/404.html）
    if current_url and is_404_page_url(current_url):
        status_info["page_type"] = "error_404"
        status_info["recommendations"].append(
            "当前 URL 为 404 错误页，需返回上一页或重新选择有效链接"
        )
        return status_info

    # 检查是否已登录的通用指标
    login_indicators = [
        "个人资料", "用户头像", "我的账户", "登出", "logout", "sign out",
        "欢迎", "您好", "用户名", "user name", "profile", "account"
    ]

    for indicator in login_indicators:
        if indicator.lower() in page_snapshot.lower():
            status_info["is_logged_in"] = True
            status_info["recommendations"].append(f"检测到登录状态指示器: {indicator}")
            break

    # 检查是否有登录表单
    login_form_indicators = [
        "邮箱", "email", "用户名", "username", "密码", "password",
        "登录", "login", "signin", "sign in", "登录按钮"
    ]

    login_form_count = 0
    for indicator in login_form_indicators:
        if indicator.lower() in page_snapshot.lower():
            login_form_count += 1

    if login_form_count >= 2:
        status_info["has_login_form"] = True
        status_info["page_type"] = "login_page"
        status_info["recommendations"].append("检测到登录表单，可能需要执行登录操作")

    # 从任务中提取可能的目标元素
    task_keywords = extract_task_keywords(user_task)
    for keyword in task_keywords:
        if keyword.lower() in page_snapshot.lower():
            status_info["contains_target_elements"].append(keyword)
            status_info["recommendations"].append(f"页面中包含任务关键词: {keyword}")

    # 判断页面类型
    if "dashboard" in page_snapshot.lower() or "首页" in page_snapshot.lower():
        status_info["page_type"] = "dashboard"
    elif "search" in page_snapshot.lower() or "搜索" in page_snapshot.lower():
        status_info["page_type"] = "search_page"
    elif status_info["page_type"] == "unknown" and current_url:
        if "login" in current_url.lower() or "signin" in current_url.lower():
            status_info["page_type"] = "login_page"

    return status_info


# ==================== 任务关键词提取 ====================
# 注意：任务关键词提取函数已移至 src.agent.utils.task_utils
# 请直接从 utils.task_utils 导入使用


# ==================== 数据转换工具 ====================


# ==================== ErrorHandlerNode 专用工具 ====================

def classify_error(error_msg: Optional[str], state: BrowserAgentState) -> tuple:
    """
    分类错误并确定恢复策略
    
    服务节点：ErrorHandlerNode（用于错误分类和恢复策略决策）
    
    功能：
    - 根据错误消息内容分类错误类型
    - 确定对应的恢复策略
    - 提供恢复建议
    
    错误分类：
    - TOOL_NOT_FOUND: 工具未找到
    - INVALID_ARGS: 参数无效
    - NAVIGATION_ERROR: 导航错误
    - ELEMENT_NOT_FOUND: 元素未找到
    - LLM_ERROR: LLM调用错误
    - TIMEOUT: 执行超时
    - TOOL_EXECUTION: 工具执行错误
    - UNKNOWN: 未知错误
    
    Args:
        error_msg: 错误消息（可能为None）
        state: 浏览器 Agent 状态
    
    Returns:
        (ErrorCategory, RecoveryStrategy, str) - 分类、策略、建议
    """
    # 处理 None 值
    if not error_msg:
        error_msg = "未知错误"
    
    error_lower = error_msg.lower()
    
    # 工具未找到
    if "tool not found" in error_lower or "no tool" in error_lower or "找不到工具" in error_msg:
        return (
            ErrorCategory.TOOL_NOT_FOUND,
            RecoveryStrategy.REPLAN,
            "工具不存在，需要重新规划使用其他工具"
        )
    
    # 参数无效
    if "invalid" in error_lower or "参数" in error_msg or "argument" in error_lower:
        return (
            ErrorCategory.INVALID_ARGS,
            RecoveryStrategy.RETRY_SAME_STEP,
            "参数无效，尝试使用正确的参数重试"
        )
    
    # 导航错误
    if "navigation" in error_lower or "导航" in error_msg or "url" in error_lower or "连接" in error_msg:
        return (
            ErrorCategory.NAVIGATION_ERROR,
            RecoveryStrategy.REPLAN,
            "导航错误，检查 URL 是否正确"
        )
    
    # 元素未找到
    if "element" in error_lower or "元素" in error_msg or "not found" in error_lower or "未找到" in error_msg:
        return (
            ErrorCategory.ELEMENT_NOT_FOUND,
            RecoveryStrategy.REPLAN,
            "元素未找到，可能需要先获取页面快照"
        )
    
    # LLM 错误
    if "api" in error_lower or "llm" in error_lower or "模型" in error_msg:
        return (
            ErrorCategory.LLM_ERROR,
            RecoveryStrategy.RETRY_SAME_STEP,
            "LLM 调用错误，尝试重试"
        )
    
    # 超时
    if "timeout" in error_lower or "超时" in error_msg:
        return (
            ErrorCategory.TIMEOUT,
            RecoveryStrategy.RETRY_SAME_STEP,
            "执行超时，尝试重试"
        )
    
    # 工具执行错误
    if "工具" in error_msg or "tool" in error_lower or "执行" in error_msg:
        return (
            ErrorCategory.TOOL_EXECUTION,
            RecoveryStrategy.REPLAN,
            "工具执行失败，可能需要调整计划"
        )
    
    # 未知错误
    return (
        ErrorCategory.UNKNOWN,
        RecoveryStrategy.REPLAN,
        "未知错误，尝试重新规划"
    )


def get_last_tool_name(state: BrowserAgentState) -> Optional[str]:
    """
    获取最近调用的工具名称
    
    服务节点：ErrorHandlerNode（用于错误记录时记录相关工具）
    
    功能：
    - 从工具调用记录中获取最近一次调用的工具名称
    - 用于错误分类和恢复策略决策
    
    Args:
        state: 浏览器 Agent 状态
        
    Returns:
        最近调用的工具名称，如果没有则返回 None
    """
    tool_records = state.get("tool_call_records", [])
    if tool_records:
        return tool_records[-1].tool_name
    return None


# ==================== ReflectNode 专用工具 ====================

# 步骤状态常量
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_PENDING = "pending"


def get_status_icon(
    status: str,
    status_completed: str = STEP_STATUS_COMPLETED,
    status_failed: str = STEP_STATUS_FAILED,
    status_pending: str = STEP_STATUS_PENDING
) -> str:
    """
    获取步骤状态图标
    
    服务节点：ReflectNode（用于格式化步骤状态显示）
    
    Args:
        status: 步骤状态
        status_completed: 完成状态常量，默认为 "completed"
        status_failed: 失败状态常量，默认为 "failed"
        status_pending: 待处理状态常量，默认为 "pending"
        
    Returns:
        状态图标字符串
    """
    icon_map = {
        status_completed: "✅",
        status_failed: "❌",
        status_pending: "⏳"
    }
    return icon_map.get(status, "⏳")


def format_result_preview(result: Any, max_length: int) -> str:
    """
    格式化结果预览
    
    服务节点：ReflectNode（用于格式化步骤结果预览）
    
    Args:
        result: 结果对象
        max_length: 最大长度
        
    Returns:
        格式化后的结果预览字符串
    """
    if result and isinstance(result, str):
        return result[:max_length]
    elif result:
        return str(result)[:max_length]
    else:
        return "无结果"


def collect_error_information(
    state: BrowserAgentState,
    status_failed: str = STEP_STATUS_FAILED
) -> str:
    """
    收集错误信息
    
    服务节点：ReflectNode（用于反思时收集错误信息）
    
    Args:
        state: 浏览器Agent状态
        status_failed: 失败状态常量，默认为 "failed"
        
    Returns:
        错误信息字符串
    """
    config = default_text_config
    error_info = ""
    
    last_error = state.get("last_error")
    if last_error:
        error_info = f"\n\n【错误信息】\n{last_error[:config.error_info_preview_length]}"
    return error_info


def collect_execution_statistics(
    state: BrowserAgentState,
    status_completed: str = STEP_STATUS_COMPLETED,
    status_failed: str = STEP_STATUS_FAILED
) -> Dict[str, Any]:
    """
    收集执行统计数据
    
    服务节点：ReflectNode（用于反思时收集执行统计信息）
    
    Args:
        state: 浏览器Agent状态
        status_completed: 完成状态常量，默认为 "completed"
        status_failed: 失败状态常量，默认为 "failed"
        
    Returns:
        包含执行统计信息的字典（browser-use 无步骤列表，均为 0/True/[]）
    """
    return {
        "completed_count": 0,
        "failed_count": 0,
        "total_steps": 0,
        "current_index": 0,
        "all_steps_done": True,
        "plan": []
    }


def collect_step_results(
    plan: List[Dict[str, Any]],
    status_completed: str = STEP_STATUS_COMPLETED,
    status_failed: str = STEP_STATUS_FAILED,
    status_pending: str = STEP_STATUS_PENDING
) -> Tuple[List[str], List[str]]:
    """
    收集步骤结果摘要
    
    服务节点：ReflectNode（用于反思时收集步骤执行结果）
    
    Args:
        plan: 执行计划列表
        status_completed: 完成状态常量，默认为 "completed"
        status_failed: 失败状态常量，默认为 "failed"
        status_pending: 待处理状态常量，默认为 "pending"
        
    Returns:
        (步骤摘要列表, 步骤详细结果列表) 的元组
    """
    step_summary = []
    step_results = []
    config = default_text_config
    
    for step in plan:
        if not isinstance(step, dict):
            continue
            
        status = step.get("status", status_pending)
        status_icon = get_status_icon(
            status,
            status_completed=status_completed,
            status_failed=status_failed,
            status_pending=status_pending
        )
        step_id = step.get("step_id", "?")
        description = step.get("description", "无描述")
        desc_preview_length = config.step_description_preview_length
        
        # 构建步骤摘要
        step_summary.append(
            f"{status_icon} 步骤{step_id}: {description[:desc_preview_length]}"
        )
        
        # 收集详细结果
        result = step.get("result", "未执行")
        result_preview = format_result_preview(result, config.step_result_preview_length)
        step_results.append(
            f"{status_icon} 步骤{step_id}: {description[:desc_preview_length]}\n"
            f"   结果: {result_preview}"
        )
    
    return step_summary, step_results


# ==================== 关键词匹配 ====================
# 注意：关键词匹配函数已移至 src.agent.utils.task_utils
# 请直接从 utils.task_utils 导入使用


# ==================== 页面切换相关工具 ====================

def detect_new_pages(
    current_pages_info: Dict[str, Dict[str, Any]],
    state: BrowserAgentState
) -> List[str]:
    """检测新出现的页面（需要执行 select_page 切换的页面）
    
    服务节点：ObserveNode、ExecuteNode（用于检测是否有新页面打开）
    
    功能：
    - 对比当前页面列表与 state 中的 pages_info / page_records
    - 仅当存在「多个标签且其中有关键字面新开的」时才返回新页面，用于提示切换
    - **若当前仅剩一个标签页**（如意图节点关闭多余标签后），不视为「新页面」，避免反复提示 select_page
    
    Args:
        current_pages_info: 当前页面信息字典 {page_id: {...}}
        state: 浏览器 Agent 状态
        
    Returns:
        新页面ID列表（按 pageId 数字大小排序，最大的在前）
    """
    if not current_pages_info:
        return []
    # 仅剩一个标签页时（如意图清理后只剩 page 1）：已是当前页，无需「选择新页面」，直接不报新页面，避免循环 select_page
    if len(current_pages_info) == 1:
        return []

    # 获取之前已知的页面ID
    prev_pages_info = state.get("pages_info") or {}
    prev_page_records = state.get("page_records") or []
    
    # 从 page_records 中提取页面ID
    prev_page_ids_from_records = set()
    for record in prev_page_records:
        page_id = getattr(record, "page_id", None) or (record.get("page_id") if isinstance(record, dict) else None)
        if page_id:
            prev_page_ids_from_records.add(str(page_id))
    
    # 合并之前已知的页面ID
    all_prev_page_ids = set(prev_pages_info.keys()) | prev_page_ids_from_records
    
    # 检测新页面
    new_page_ids = [pid for pid in current_pages_info if pid not in all_prev_page_ids]
    
    # 按 pageId 数字大小排序（最大的在前）
    new_page_ids.sort(key=lambda x: int(x) if str(x).isdigit() else 0, reverse=True)
    
    return new_page_ids


__all__ = [
    # PlanNode 专用工具
    "analyze_page_status",
    # ErrorHandlerNode 专用工具
    "classify_error",
    "get_last_tool_name",
    # ReflectNode 专用工具
    "get_status_icon",
    "format_result_preview",
    "collect_error_information",
    "collect_execution_statistics",
    "collect_step_results",
    # 步骤状态常量
    "STEP_STATUS_COMPLETED",
    "STEP_STATUS_FAILED",
    "STEP_STATUS_PENDING",
    # 页面切换相关工具
    "detect_new_pages",
]