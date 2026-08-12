"""
日志打印工具

提供统一的日志打印功能，用于 Agent 执行过程中的信息输出。
"""

import logging
import time
from typing import Dict, Any, Optional, List, Literal, Callable

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.utils.agent_flow_logger import log_routing as _flow_log_routing
from src.agent.utils.terminal_colors import green, grey, red, orange

logger = logging.getLogger(__name__)

# 工具日志预览长度
LOG_TOOL_MAX_CONTENT_PREVIEW = 100
LOG_TOOL_MAX_FILE_PREVIEW = 2000
# 工具输出最大显示长度（超过则截断）
LOG_TOOL_OUTPUT_MAX = 500
LOG_SEPARATOR = "-" * 80


def print_startup_message(task: str, verbose: bool = True):
    """打印启动消息（绿色：重点信息）
    
    Args:
        task: 任务描述
        verbose: 是否详细输出
    """
    if verbose:
        print(green("\n" + "🌐" * 25))
        print(green("🌐 Browser Agent 启动"))
        print(green(f"📝 任务: {task}"))
        print(green("🌐" * 25))


def print_execution_summary(summary: Dict[str, Any], verbose: bool = True):
    """打印执行摘要（颜色区分：绿=重点/红=失败/橙=警告），并写入日志
    
    Args:
        summary: 执行摘要字典
        verbose: 是否详细输出
    """
    if not verbose:
        return
    
    raw_lines: List[str] = []
    raw_lines.append("")
    raw_lines.append("=" * 60)
    raw_lines.append("📊 [执行摘要]")
    raw_lines.append("=" * 60)
    raw_lines.extend(_get_status_summary_lines(summary))
    raw_lines.extend(_get_plan_summary_lines(summary))
    raw_lines.extend(_get_result_summary_lines(summary))
    raw_lines.append("=" * 60)

    for line in raw_lines:
        if "❌" in line or "失败" in line:
            print(red(line))
        elif "错误次数" in line and summary.get("error_count", 0) > 0:
            print(orange(line))
        else:
            print(green(line))
    logger.info("\n".join(raw_lines))


def _get_status_summary_lines(summary: Dict[str, Any]) -> List[str]:
    """返回状态摘要行列表"""
    lines: List[str] = []
    status = "✅ 成功" if summary.get("success") else "❌ 失败"
    lines.append(f"状态: {status}")
    lines.append(f"计划版本: {summary.get('plan_version', 1)}")
    lines.append(f"迭代次数: {summary.get('iteration_count', 0)}")
    if summary.get("error_count", 0) > 0:
        lines.append(f"错误次数: {summary.get('error_count', 0)}")
    return lines


def print_status_summary(summary: Dict[str, Any]):
    """打印状态摘要（颜色：绿/红/橙）
    
    Args:
        summary: 执行摘要字典
    """
    for line in _get_status_summary_lines(summary):
        if "❌" in line or "失败" in line:
            print(red(line))
        elif "错误次数" in line and summary.get("error_count", 0) > 0:
            print(orange(line))
        else:
            print(green(line))


def _get_plan_summary_lines(summary: Dict[str, Any]) -> List[str]:
    """browser-use 无计划，始终返回空列表"""
    return []


def print_plan_summary(summary: Dict[str, Any]):
    """打印计划摘要（绿色：重点信息）
    
    Args:
        summary: 执行摘要字典
    """
    for line in _get_plan_summary_lines(summary):
        print(green(line))


def _get_result_summary_lines(summary: Dict[str, Any]) -> List[str]:
    """返回结果摘要行列表"""
    lines: List[str] = []
    final_result = summary.get("final_result")
    if final_result:
        lines.append("")
        lines.append("最终结果:")
        result_preview = extract_result_preview(final_result)
        lines.append(f"  {result_preview}")
    return lines


def print_result_summary(summary: Dict[str, Any]):
    """打印结果摘要（灰色：输出类信息）
    
    Args:
        summary: 执行摘要字典
    """
    for line in _get_result_summary_lines(summary):
        print(grey(line))


def _extract_tool_names_from_state(state: BrowserAgentState) -> str:
    """从 state 的 messages 中提取最后一条消息的 tool_calls 名称列表"""
    messages = state.get("messages", [])
    if not messages:
        return ""
    last_msg = messages[-1]
    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return ""
    names = []
    for tc in last_msg.tool_calls:
        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        if name:
            names.append(name)
    return ", ".join(names) if names else ""


def log_routing_decision(
    node_name: str,
    result: str,
    state: BrowserAgentState,
    verbose: bool = True,
    max_iterations: int = None
):
    """记录路由决策日志（结构化格式，便于追踪流程）
    
    Args:
        node_name: 节点名称
        result: 路由结果
        state: 状态字典
        verbose: 是否详细输出
        max_iterations: 最大迭代次数
    """
    if not verbose:
        return
    reason = ""
    if result == "error_handler":
        reason = "检测到错误，进入错误处理"
    elif result == "__end__":
        phase = state.get("phase", "")
        if phase == BrowserAgentPhase.COMPLETED.value:
            reason = "任务已完成"
        elif phase == BrowserAgentPhase.FAILED.value:
            reason = "任务失败"
        else:
            iteration_count = state.get("iteration_count", 0)
            max_iter = state.get("max_iterations", max_iterations)
            if max_iter and iteration_count >= max_iter:
                reason = f"达到最大迭代次数 ({max_iter})"
    elif result == "execute":
        reason = "继续执行"
    elif result == "observe":
        reason = "工具执行完成，观察结果"
    elif result == "tools":
        tool_names = _extract_tool_names_from_state(state)
        reason = f"有工具调用，执行: {tool_names}" if tool_names else "有工具调用，执行工具"
    _flow_log_routing(node_name, result, reason)


def log_error_recovery_decision(
    result: str,
    state: BrowserAgentState,
    verbose: bool = True,
    max_recovery_attempts: int = None
):
    """记录错误恢复决策日志（结构化格式）
    
    Args:
        result: 路由结果
        state: 状态字典
        verbose: 是否详细输出
        max_recovery_attempts: 最大恢复尝试次数
    """
    if not verbose:
        return
    reason = ""
    if result == "__end__":
        recovery_attempts = state.get("recovery_attempts", 0)
        max_retries = max_recovery_attempts or state.get("max_recovery_attempts", 3)
        reason = f"达到最大恢复尝试次数 ({max_retries})" if recovery_attempts >= max_retries else "错误恢复失败"
    elif result == "reflect":
        reason = "错误处理后进入反思阶段"
    _flow_log_routing("error_handler", result, reason)


def log_tool_event(
    event_type: Literal["call", "output"],
    verbose: bool,
    *,
    # 工具调用（event_type="call"）
    current_index: Optional[int] = None,
    plan: Optional[list] = None,
    current_step: Optional[dict] = None,
    tool_calls: Optional[list] = None,
    # 工具输出（event_type="output"）
    index: Optional[int] = None,
    tool_name: Optional[str] = None,
    tool_id: Optional[str] = None,
    content: Any = None,
    output_params: Optional[str] = None,
    duration: Optional[float] = None,
    max_content_preview: int = LOG_TOOL_MAX_CONTENT_PREVIEW,
    max_file_preview: int = LOG_TOOL_MAX_FILE_PREVIEW,
    save_callback: Optional[Callable[[str, str, str], None]] = None,
    should_save: bool = False,
) -> None:
    """统一记录工具调用/输出到日志（合并原 _log_tool_calls 与 _log_tool_output）。
    
    Args:
        event_type: "call" 记录工具调用请求，"output" 记录工具返回结果
        verbose: 是否输出
        current_index: 当前步骤索引（call）
        plan: 计划列表（call）
        current_step: 当前步骤字典（call）
        tool_calls: 工具调用列表，每项含 name/args/id（call）
        index: 工具输出序号（output）
        tool_name: 工具名称（output）
        tool_id: 工具ID（output）
        content: 原始返回内容（output）
        output_params: 格式化后的输出参数字符串（output）
        duration: 执行耗时 ms（output）
        max_content_preview: 结果预览最大长度（output）
        max_file_preview: 写入文件时日志预览长度（output）
        save_callback: 保存到文件时的回调 (tool_name, output_params, timestamp)（output）
        should_save: 是否执行保存回调（output）
    """
    if not verbose:
        return

    if event_type == "call":
        if not tool_calls:
            return
        logger.info(LOG_SEPARATOR)
        logger.info(f"[EXECUTION] 执行步骤 {current_index + 1}/{len(plan)}")
        logger.info(f"步骤描述: {current_step['description']}")
        logger.info(f"操作类型: {current_step['action_type']}")
        logger.info(f"工具调用数量: {len(tool_calls)}")
        for i, tc in enumerate(tool_calls):
            name = tc.get("name", "unknown")
            args = tc.get("args", {})
            tid = tc.get("id", "")
            logger.info(f"  工具调用 #{i + 1}:")
            logger.info(f"    工具名称: {name}")
            logger.info(f"    工具ID: {tid}")
            logger.info(f"    输入参数: {args}")
            logger.info(f"   📞 调用工具: {name} | 参数: {args}")
        logger.info(LOG_SEPARATOR)
        return

    # event_type == "output"
    logger.info(f"  工具输出 #{index + 1}:")
    logger.info(f"    工具名称: {tool_name}")
    logger.info(f"    工具ID: {tool_id or ''}")
    logger.info(f"    输出参数类型: {type(content).__name__}")
    logger.info(f"    输出参数长度: {len(str(content))} 字符")
    logger.info(f"    输出参数内容:")
    output_str = str(output_params or "")
    output_truncated = output_str[:LOG_TOOL_OUTPUT_MAX]
    if len(output_str) > LOG_TOOL_OUTPUT_MAX:
        output_truncated += "..."
    if should_save and save_callback:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        logger.info(f"      {output_truncated}")
        save_callback(tool_name or "", output_params or "", timestamp)
    else:
        logger.info(f"      {output_truncated}")
    logger.info("    执行成功: True")
    logger.info(f"    耗时: {duration:.2f}ms")
    content_str = str(content or "")
    preview = content_str[:LOG_TOOL_OUTPUT_MAX]
    if len(content_str) > LOG_TOOL_OUTPUT_MAX:
        preview += "..."
    logger.info(f"    结果预览: {preview}")


def get_step_status_icon(status: Optional[str]) -> str:
    """获取步骤状态图标
    
    Args:
        status: 步骤状态
        
    Returns:
        状态图标字符串
    """
    if status == "completed":
        return "✅"
    elif status == "failed":
        return "❌"
    else:
        return "⏳"


def truncate_description(description: str, max_length: int) -> str:
    """截断描述文本
    
    Args:
        description: 描述文本
        max_length: 最大长度
        
    Returns:
        截断后的文本
    """
    if len(description) > max_length:
        return description[:max_length] + "..."
    return description


def extract_result_preview(final_result: str) -> str:
    """提取结果预览
    
    Args:
        final_result: 最终结果文本
        
    Returns:
        结果预览文本
    """
    if "最终结果" in final_result:
        start = final_result.find("最终结果")
        end = final_result.find("\n", start + 50)
        if end > 0:
            return final_result[start:end] + "..."
        return final_result[start:start + 150] + "..."
    else:
        return final_result[:150] + "..."
