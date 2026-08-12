"""
Agent 流程日志工具

提供结构化、有逻辑、有步骤的日志输出，便于小白理解智能体运行流程和排查问题。

快速定位流程：在日志文件中 grep '[FLOW]' 可得到整条时间线（任务开始 → 首次/第N次规划执行 → 第N次反思 → 路由 → 结束）。

日志格式规范：
1. [FLOW] 单行锚点：关键节点一行一条，便于 grep 精准定位
2. 阶段标题：单行摘要，不刷屏
3. 关键信息：决策、结果、错误等一目了然
4. 排查提示：错误时附带可能原因和排查方向

工具输出：超过 LOG_TOOL_OUTPUT_MAX 字时截断显示（终端与日志文件一致）。
"""
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# 工具输出最大显示长度（终端与日志文件均截断，超过此值显示前 N 字 + ...）
LOG_TOOL_OUTPUT_MAX = 300

# 分隔线
SEP_MAIN = "=" * 80
SEP_PHASE = "-" * 60
SEP_ITEM = "  "

# 阶段名称映射（用于日志标题）
PHASE_NAMES = {
    "intent": "意图分析",
    "observe": "观察",
    "execute": "执行",
    "tools": "工具调用",
    "reflect": "反思",
    "error_handler": "错误处理",
}

# 关键阶段醒目标记（便于在日志中快速定位）
PROMPTENT = {
    "intent": "\n★★★ 意图分析 ★★★\n",
    "observe": "\n★★★ 观察 ★★★\n",
    "routing": "\n★★★ 路由 ★★★\n",
    "execute": "\n★★★ 执行 ★★★\n",
    "reflect": "\n★★★ 反思 ★★★\n",
}


def _phase_title(phase: str, extra: str = "") -> str:
    """生成阶段标题"""
    name = PHASE_NAMES.get(phase, phase)
    return f"[{name}]{extra}"


def log_flow_milestone(milestone: str, **kwargs) -> None:
    """记录流程里程碑（单行），便于 grep '[FLOW]' 精准定位时间线。
    例如：log_flow_milestone('首次规划执行', plan_version=0)；log_flow_milestone('第2次反思', plan_version=1)
    """
    parts = [f"[FLOW] {milestone}"]
    for k, v in kwargs.items():
        if v is not None and str(v).strip():
            parts.append(f"{k}={str(v)[:120]}")
    msg = " | ".join(parts)
    if "规划执行" in milestone:
        msg = PROMPTENT["execute"] + msg
    elif "反思" in milestone:
        msg = PROMPTENT["reflect"] + msg
    elif "路由" in milestone:
        msg = PROMPTENT["routing"] + msg
    logger.info(msg)


def log_phase_start(
    phase: str,
    iteration: int = 0,
    step_info: str = "",
    extra: Dict[str, Any] = None,
    plan_round_label: str = "",
) -> None:
    """记录阶段开始（单行摘要，便于扫读）
    
    Args:
        phase: 阶段标识 (intent/observe/plan/execute/tools/reflect/error_handler)
        iteration: 当前迭代次数（0 表示首次）
        step_info: 步骤信息，如 "步骤 3/12"
        extra: 额外关键信息（仅取前 2 项写入单行，避免刷屏）
        plan_round_label: 规划轮次标签，如「首次规划执行」「第二次规划执行」
    """
    title = _phase_title(phase)
    if plan_round_label:
        title += f" | {plan_round_label}"
    if step_info:
        title += f" | {step_info}"
    if iteration > 0:
        title += f" | 第 {iteration} 轮迭代"
    extra_parts = []
    if extra:
        for k, v in list(extra.items())[:2]:
            if v is not None and str(v).strip():
                # 任务类字段放宽截断，便于完整看到用户指令（如含「先用…再用…」的抖音+视频分析流程）
                max_len = 200 if k in ("任务", "task") else 60
                extra_parts.append(f"{k}={str(v)[:max_len]}")
    if extra_parts:
        title += " | " + " ".join(extra_parts)
    if phase == "intent":
        title = PROMPTENT["intent"] + "▶ " + title
    elif phase == "observe":
        title = PROMPTENT["observe"] + "▶ " + title
    elif phase == "execute":
        title = PROMPTENT["execute"] + "▶ " + title
    elif phase == "reflect":
        title = PROMPTENT["reflect"] + "▶ " + title
    else:
        title = "▶ " + title
    logger.info(title)


def log_phase_result(
    phase: str,
    result: str,
    details: Optional[List[str]] = None,
) -> None:
    """记录阶段结果摘要（单行为主）"""
    msg = f"◀ [{_phase_title(phase)}] 结果: {result}"
    if details and len(details) <= 2:
        msg += " | " + " ".join(str(d)[:80] for d in details[:2])
    elif details:
        msg += " | " + str(details[0])[:80]
    if phase == "intent":
        msg = PROMPTENT["intent"] + msg
    logger.info(msg)


def log_step_execute(
    step_index: int,
    total_steps: int,
    description: str,
    action_type: str,
    tool_name: str = "",
    tool_args: Optional[Dict] = None,
) -> None:
    """记录执行步骤（供 Execute 节点使用）
    
    Args:
        step_index: 当前步骤索引（0-based）
        total_steps: 总步骤数
        description: 步骤描述
        action_type: 操作类型
        tool_name: 工具名称
        tool_args: 工具参数
    """
    step_label = f"步骤 {step_index + 1}/{total_steps}"
    msg = f"▶ [执行] {step_label} | {str(description)[:50]} | {action_type}"
    if tool_name:
        msg += f" | 工具: {tool_name}"
    logger.info(msg)


def log_tool_input(index: int, tool_name: str, args: dict) -> None:
    """记录工具输入（执行前调用）"""
    args_str = str(args) if args else "{}"
    if len(args_str) > LOG_TOOL_OUTPUT_MAX:
        args_str = args_str[:LOG_TOOL_OUTPUT_MAX] + "..."
    logger.info(f"📞 [TOOLS] 工具输入 #{index + 1}: {tool_name} | 参数: {args_str}")


def log_tool_result(
    step_index: int,
    total_steps: int,
    tool_name: str,
    duration_ms: float,
    success: bool,
    result_preview: str = "",
    error_msg: str = "",
    tool_args: Optional[Dict] = None,
) -> None:
    """记录工具执行结果（供 Tools 节点使用）
    
    超过 LOG_TOOL_OUTPUT_MAX 字时截断显示。含工具输入和输出。
    
    Args:
        step_index: 当前步骤索引
        total_steps: 总步骤数
        tool_name: 工具名称
        duration_ms: 耗时(ms)
        success: 是否成功
        result_preview: 结果预览（成功时）
        error_msg: 错误信息（失败时）
        tool_args: 工具输入参数（可选，用于在结果中展示输入）
    """
    step_label = f"步骤 {step_index + 1}/{total_steps}"
    status = "✅ 成功" if success else "❌ 失败"
    content = error_msg if error_msg else result_preview
    content_truncated = content[:LOG_TOOL_OUTPUT_MAX]
    if len(content) > LOG_TOOL_OUTPUT_MAX:
        content_truncated += "..."

    msg = f"◀ [工具] {step_label} | {tool_name} | {status} | {duration_ms:.0f}ms"
    if error_msg:
        msg += " | " + content_truncated.replace("\n", " ")[:80]
    elif result_preview:
        msg += " | " + content_truncated.replace("\n", " ")[:80]
    logger.info(msg)


def log_observe_summary(
    current_url: Optional[str],
    page_count: int,
    snapshot_len: int,
    new_page_info: Optional[str] = None,
) -> None:
    """记录观察摘要（单行）"""
    url_short = (current_url or "未知")[:60]
    msg = f"◀ [观察] URL={url_short} | 页面数={page_count} | 快照={snapshot_len}字"
    if new_page_info:
        msg += " | 新页面: " + str(new_page_info)[:40]
    logger.info(PROMPTENT["observe"] + msg)


def log_routing(
    from_phase: str,
    to_target: str,
    reason: str = "",
) -> None:
    """记录路由决策
    
    Args:
        from_phase: 来源阶段
        to_target: 目标节点
        reason: 路由原因
    """
    to_name = PHASE_NAMES.get(to_target, to_target)
    if to_target == "__end__":
        to_name = "结束"
    msg = f"🔀 [路由] {_phase_title(from_phase)} → {to_name}"
    if reason:
        msg += f" | {reason}"
    logger.info(PROMPTENT["routing"] + msg)


def log_error(
    phase: str,
    error_msg: str,
    step_info: str = "",
    troubleshoot: Optional[List[str]] = None,
) -> None:
    """记录错误（含排查提示）
    
    Args:
        phase: 发生错误的阶段
        error_msg: 错误信息
        step_info: 步骤信息
        troubleshoot: 排查建议列表
    """
    lines = [
        SEP_MAIN,
        f"❌ [错误] {_phase_title(phase)}",
        SEP_PHASE,
        f"{SEP_ITEM}错误: {error_msg}",
    ]
    if step_info:
        lines.append(f"{SEP_ITEM}位置: {step_info}")
    if troubleshoot:
        lines.append(f"{SEP_ITEM}排查建议:")
        for t in troubleshoot:
            lines.append(f"{SEP_ITEM}  - {t}")
    lines.append(SEP_MAIN)
    logger.error("\n".join(lines))


def log_task_start(task: str) -> None:
    """记录任务开始"""
    log_flow_milestone("任务开始", task=(task[:100] + "..." if len(task) > 100 else task))
    logger.info("▶ [任务开始] 任务: %s", (task[:80] + "..." if len(task) > 80 else task))


def log_task_end(success: bool, summary: str = "") -> None:
    """记录任务结束"""
    status = "成功" if success else "失败"
    log_flow_milestone("任务结束", success=success, summary=(summary[:80] + "..." if summary and len(summary) > 80 else (summary or "")))
    logger.info("▶ [任务结束] %s | %s", status, (summary[:80] + "..." if summary and len(summary) > 80 else (summary or "")))
