"""
Agent 配置定义
提供结构化的配置管理，避免硬编码

说明：
- SupervisorConfig 和 ReflectorConfig 已删除（2026-01-29）
- 原因：配置定义但未实际使用，只有提示词模板使用了相关概念
- 提示词模板保留在 browser_prompts.py 中，供未来扩展使用
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from src.config import settings


@dataclass
class AgentConfig:
    """Agent 基础配置"""
    max_iterations: int = field(default_factory=lambda: getattr(settings, 'agent_max_iterations', 50))
    max_tools_per_step: int = 3
    recursion_limit: int = 200
    observe_after_each_step: bool = False  # 是否在每步后观察页面


@dataclass
class PlanningConfig:
    """规划配置（browser-use：仅用 max_plan_versions 限制重规划次数）"""
    max_plan_versions: int = 3  # 最大重规划次数
    enable_replanning: bool = True
    replanning_triggers: List[str] = field(default_factory=lambda: [
        "needs_replan",
        "task_not_completed",
        "error_recovery",
    ])


@dataclass
class ErrorHandlingConfig:
    """错误处理配置"""
    max_recovery_attempts: int = field(default_factory=lambda: getattr(settings, 'agent_retry_count', 3))
    enable_auto_recovery: bool = True
    auto_recovery_strategies: List[str] = field(default_factory=lambda: [
        "stale_snapshot",  # 自动重新获取快照
        "retry_same_step",  # 重试相同步骤
    ])
    error_detection_patterns: List[str] = field(default_factory=lambda: [
        "MCP error -",
        "MCP error -32602",
        "MCP error -32603",
        "Invalid arguments",
        "invalid_type",
        "Required",
        "Field required",  # Pydantic 校验错误，如 browser_fill 缺少 value
        "Error invoking tool",  # LangChain 工具调用失败
        "执行失败",
        "工具调用失败",
        "选择页面错误",
        "导航错误",
        "stale snapshot",
        "No such element",  # MCP fill/click 元素不存在于快照
        "Timed out",  # wait_for 超时
        "Locator.waitHandle",  # Playwright 超时
        # "解析处理失败",  # VOC 分析等工具解析失败
        # "内容过滤未通过",  # VOC 筛选 need_analysis 为 false 时触发重规划
    ])


@dataclass
class ToolClassificationConfig:
    """工具分类配置（消除硬编码）"""
    # 页面操作工具列表（执行后需要观察页面状态变化）
    page_action_tools: List[str] = field(default_factory=lambda: [
        "browser_navigate",
        "browser_click",
        "browser_fill",
        "browser_fill_form",
        "browser_press_key",
        "browser_hover",
        "browser_drag",
        "browser_upload_file",
        "browser_handle_dialog",
    ])
    
    # 查询工具列表（可以直接反思，无需观察）
    query_tools: List[str] = field(default_factory=lambda: [
        "browser_snapshot",
        "browser_screenshot",
        "browser_evaluate",
        "browser_list_pages",
        "browser_list_console_messages",
        "browser_list_network_requests",
    ])
    
    # 导航工具列表（已移除 browser_go_back，需回退时用 browser_navigate 或 browser_select_page）
    navigation_tools: List[str] = field(default_factory=lambda: [
        "browser_navigate",
        "browser_new_page",
        "browser_select_page",
        "browser_close_page",
    ])
    
    # 等待工具列表
    wait_tools: List[str] = field(default_factory=lambda: [
        "browser_wait_for",
    ])

    # 工具选择不匹配：当计划为以下操作类型，但实际调用了预备性工具时，不推进步骤，重新执行
    action_tools_need_uid: List[str] = field(default_factory=lambda: [
        "fill", "click", "hover", "drag", "upload_file", "fill_form",
    ])
    # 预备性工具：仅更新快照/状态，不完成计划步骤
    preparatory_tools: List[str] = field(default_factory=lambda: [
        "browser_snapshot",
    ])

    # 可能打开新页面/新标签的工具（用于 observe 切换新页面、page_records 记录）
    page_opening_tools: List[str] = field(default_factory=lambda: [
        "browser_new_page",     # 显式打开新页
        "browser_navigate",     # 导航可能在新标签打开
        "browser_navigate_page",
        "browser_click",        # 点击链接可能 target="_blank"
        "browser_fill",         # 表单提交可能跳转或新开
        "browser_fill_form",
        "browser_press_key",    # Enter 提交等
        "browser_hover",        # 悬停可能触发
        "browser_drag",         # 拖拽可能触发
        "browser_upload_file",  # 上传可能触发提交
        "browser_evaluate",     # JS 可能 window.open()
        "browser_handle_dialog", # 对话框可能影响导航
    ])


# SupervisorConfig 和 ReflectorConfig 已删除（2026-01-29）
# 原因：配置定义但未实际使用，只有提示词模板使用了相关概念
# 提示词模板保留在 browser_prompts.py 中，供未来扩展使用
# 如需实现 Supervisor 模式，可参考提示词模板重新实现配置类


@dataclass
class BrowserAgentConfig:
    """完整的 Browser Agent 配置"""
    agent: AgentConfig = field(default_factory=AgentConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    error_handling: ErrorHandlingConfig = field(default_factory=ErrorHandlingConfig)
    tool_classification: ToolClassificationConfig = field(default_factory=ToolClassificationConfig)
    
    # SupervisorConfig 和 ReflectorConfig 已删除（2026-01-29）
    # 原因：配置定义但未实际使用
    # 提示词模板保留在 browser_prompts.py 中
    
    @classmethod
    def from_settings(cls) -> "BrowserAgentConfig":
        """从全局设置创建配置"""
        return cls(
            agent=AgentConfig(),
            planning=PlanningConfig(),
            error_handling=ErrorHandlingConfig(),
            tool_classification=ToolClassificationConfig(),
            # SupervisorConfig 和 ReflectorConfig 已删除（2026-01-29）
        )
    
    def validate(self) -> None:
        """验证配置的有效性"""
        if self.agent.max_iterations <= 0:
            raise ValueError("max_iterations 必须大于 0")
        if self.agent.max_tools_per_step <= 0:
            raise ValueError("max_tools_per_step 必须大于 0")
        if self.planning.max_plan_versions <= 0:
            raise ValueError("max_plan_versions 必须大于 0")
        if self.error_handling.max_recovery_attempts <= 0:
            raise ValueError("max_recovery_attempts 必须大于 0")
        # SupervisorConfig 和 ReflectorConfig 的验证已删除（2026-01-29）

