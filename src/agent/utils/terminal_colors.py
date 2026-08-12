"""
终端颜色工具

为终端输出提供颜色区分，便于快速识别信息类型：
- 绿色：重点信息、节点（意图/观察/规划/执行/工具/反思）
- 灰色：工具输入、工具输出
- 红色：报错
- 橙色：警告

工具输入/输出终端显示开关（日志文件仍完整记录）：
- True：终端显示工具输入和输出
- False：终端不显示，仅写入日志文件
"""
import logging
import sys

# ========== 工具输入/输出终端显示开关（方便开启/关闭） ==========
SHOW_TOOL_IO_IN_TERMINAL = False
# ==============================================================

# ANSI 转义码
RESET = "\033[0m"
GREEN = "\033[32m"
GREY = "\033[90m"
RED = "\033[31m"
ORANGE = "\033[33m"  # 黄色/橙色，兼容性更好；可用 \033[38;5;208m 为更纯橙色


def _supports_color() -> bool:
    """检测终端是否支持颜色"""
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    return True


def colorize(text: str, color: str, force: bool = False) -> str:
    """为文本添加颜色（若终端支持）"""
    if not force and not _supports_color():
        return text
    return f"{color}{text}{RESET}"


def green(text: str) -> str:
    """绿色：重点信息、节点"""
    return colorize(text, GREEN)


def grey(text: str) -> str:
    """灰色：工具输出"""
    return colorize(text, GREY)


def red(text: str) -> str:
    """红色：报错"""
    return colorize(text, RED)


def orange(text: str) -> str:
    """橙色：警告"""
    return colorize(text, ORANGE)


def is_tool_io_message(msg: str) -> bool:
    """判断是否为工具输入或工具输出（供 Filter 和着色使用）"""
    markers = [
        "[TOOLS]", "工具调用", "工具输出", "工具输入", "工具名称", "输入参数", "输出参数",
        "browser_", "执行耗时", "结果预览", "  • 结果:", "  • 参数:",
        "[EXECUTION]", "步骤描述", "操作类型", "工具调用数量", "📞", "调用工具",
        "步骤1结果:", "步骤2结果:", "步骤3结果:", "步骤4结果:", "步骤5结果:",
        "步骤6结果:", "步骤7结果:", "步骤8结果:", "步骤9结果:", "步骤10结果:",
    ]
    return any(m in msg for m in markers)


def _is_key_info(msg: str) -> bool:
    """判断是否为重点信息（节点、阶段、路由等）"""
    markers = [
        "▶", "◀", "🔀", "🚀", "🏁", "📋", "📊",
        "[意图分析]", "[观察]", "[规划]", "[执行]", "[工具]", "[反思]", "[错误处理]",
        "[路由]", "任务开始", "任务结束", "步骤 ", "路由]",
    ]
    return any(m in msg for m in markers)


def get_color_for_log_record(levelname: str, message: str) -> str:
    """
    根据日志级别和消息内容返回对应颜色码。
    用于 ColoredFormatter。
    """
    if levelname == "ERROR":
        return RED
    if levelname == "WARNING":
        return ORANGE
    if levelname == "INFO":
        if _is_key_info(message):
            return GREEN
        if is_tool_io_message(message):
            return GREY
        # 默认 INFO 视为一般信息，用绿色（用户要求重点信息为绿）
        return GREEN
    return RESET


class ColoredFormatter(logging.Formatter):
    """仅对控制台输出添加颜色的 Formatter：前缀白色，消息内容换行左对齐并按类型着色"""

    def format(self, record: logging.LogRecord) -> str:
        full = super().format(record)
        msg = record.message
        color = get_color_for_log_record(record.levelname, msg)
        if color and msg and _supports_color():
            # 前缀（时间 - 名称 - 级别 - ）保持白色；消息内容换行左对齐并着色
            prefix = full[: len(full) - len(msg)]
            return prefix.rstrip() + "\n" + f"{color}{msg}{RESET}"
        return full


class ToolIOFilter(logging.Filter):
    """控制台 Filter：当 SHOW_TOOL_IO_IN_TERMINAL=False 时，过滤掉工具输入/输出（仍写入日志文件）"""

    def filter(self, record: logging.LogRecord) -> bool:
        if SHOW_TOOL_IO_IN_TERMINAL:
            return True
        msg = record.getMessage()
        # 路由等重点信息即使含「工具调用」也不过滤
        if _is_key_info(msg):
            return True
        return not is_tool_io_message(msg)
