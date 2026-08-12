"""
浏览器 Agent 状态定义

定义浏览器自动化 Agent 的状态类型和相关数据结构。

结构说明（逻辑分组）：
- TaskState: 任务与会话（user_task, session_id, final_result）
- BrowserPageState: 浏览器页面（current_url, page_snapshot, pages_info, new_page_* 等）
- PlanExecutionState: 计划与执行（plan_version, tool_call_records 等，browser-use 无 plan）
- ReflectionState: 反思与完成（reflection, needs_replan, task_completion_judgment 等）
- ErrorState: 错误处理（error_count, last_error, error_history 等）

只读字段（任务启动后不应被节点修改）：user_task, session_id, max_iterations, max_tools_per_step
"""
import uuid
from typing import Annotated, TypedDict, List, Optional, Any, Dict
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from src.agent.types.execution_records import ToolCallRecord, DecisionRecord


# ==================== 子结构定义（逻辑分组，状态仍扁平以兼容 LangGraph） ====================

class TaskState(TypedDict, total=False):
    """任务与会话状态（只读: user_task, session_id）"""
    user_task: str
    session_id: Optional[str]
    final_result: Optional[str]


class BrowserPageState(TypedDict, total=False):
    """浏览器页面状态。Observe 写 pages_info/new_page_*/popup_hint；Tools 写 page_snapshot/current_url"""
    current_url: Optional[str]
    checkpoint_url: Optional[str]
    page_snapshot: Optional[str]
    page_status: Optional[Dict[str, Any]]
    console_messages: List[str]
    network_requests: List[str]
    current_screenshot: Optional[str]
    pages_info: Dict[str, Dict[str, Any]]
    new_page_id: Optional[str]
    new_page_url: Optional[str]
    popup_hint: Optional[str]  # Observe 节点检测到的弹窗/广告提示，供 Execute 使用
    page_records: List[Dict[str, Any]]
    screenshots: List[str]
    screenshot_count: int


class PlanExecutionState(TypedDict, total=False):
    """计划与执行状态（browser-use 仅用 plan_version）"""
    plan_version: int
    phase: str
    tool_call_records: List[ToolCallRecord]
    decision_records: List[DecisionRecord]
    iteration_count: int
    max_iterations: int
    max_tools_per_step: int


class ReflectionState(TypedDict, total=False):
    """反思与任务完成状态"""
    reflection: Optional[str]
    needs_replan: bool
    step_reflection_done: bool
    task_completion_judgment: Optional[str]


class ErrorState(TypedDict, total=False):
    """错误处理状态"""
    error_count: int
    last_error: Optional[str]
    error_history: List
    last_error_category: Optional[str]
    recovery_attempts: int


# ==================== 计划步骤与页面记录 ====================

class BrowserPlanStep(TypedDict):
    """浏览器操作步骤结构（仅类型/文档用，browser-use 无步骤列表）"""
    step_id: int
    description: str
    tool_name: str  # 工具名称（如 browser_navigate、browser_click 等）
    action_type: Optional[str]  # 操作类型（如 navigate、click、fill 等），从 tool_name 提取，用于执行逻辑判断
    target: Optional[Any]  # URL, selector, uid, pageId等（可以是字符串、整数或浮点数）
    value: Optional[Any]  # 输入值、脚本、超时时间、页面尺寸等（可以是字符串、整数或浮点数）
    keywords: Optional[List[str]]  # 该步骤涉及的关键词列表，用于后续验证任务完成情况
    status: str  # pending, executing, completed, failed
    result: Optional[str]
    tool_calls_count: int  # 该步骤调用工具的次数


class PageRecord(TypedDict, total=False):
    """打开的页面记录（操作记录上下文）"""
    url: str
    open_time: str  # 打开时间，如 ISO 或可读格式
    step_name: str  # 打开该页面的步骤名称
    tool_name: str  # 使用的工具名，如 browser_navigate_page、browser_new_page
    page_id: Optional[str]  # 可选，浏览器 pageId


class BrowserAgentPhase(Enum):
    """执行阶段"""
    INITIALIZED = "initialized"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_TOOL = "waiting_tool"  # 等待工具执行结果
    PROCESSING_RESULT = "processing_result"  # 处理工具结果
    OBSERVING = "observing"  # 观察页面状态
    REFLECTING = "reflecting" # 反思阶段
    REPLANNING = "replanning" # 重新规划阶段
    COMPLETED = "completed" # 任务完成阶段
    FAILED = "failed" # 任务失败阶段


class BrowserAgentState(TypedDict):
    """浏览器 Agent 状态（扁平结构，LangGraph 兼容）
    
    逻辑分组子类型: TaskState, BrowserPageState,
    PlanExecutionState, ReflectionState, ErrorState。
    
    迭代与重规划：iteration_count/max_iterations, plan_version
    反思阶段：step_reflection_done 区分步骤反思与任务反思
    """
    messages: Annotated[List[BaseMessage], add_messages]
    
    # === TaskState ===
    user_task: str
    intent_task_related: Optional[bool]  # 意图节点分析结果：当前任务与此前任务是否有关联
    
    # === PlanExecutionState ===
    plan_version: int  # 重规划次数，与 max_plan_versions 配合
    
    # 执行阶段
    phase: str
    
    # 工具执行追踪
    tool_call_records: List[ToolCallRecord]  # 工具调用记录
    decision_records: List[DecisionRecord]   # 决策记录
    
    # === BrowserPageState ===
    current_url: Optional[str]
    checkpoint_url: Optional[str]  # 步骤执行前的 URL 检查点，用于错误时回退
    page_snapshot: Optional[str]
    page_status: Optional[Dict[str, Any]]  # 页面状态分析结果（由规划节点分析），包含 is_logged_in、has_login_form、contains_target_elements、page_type、recommendations 等
    console_messages: List[str]
    network_requests: List[str]
    current_screenshot: Optional[str]
    # 页面信息：当前所有页面的详细信息（由观察节点更新）
    pages_info: Dict[str, Dict[str, Any]]  # {page_id: {"url": str, "title": str, "open_time": float, "last_active": float, "selected": bool}}
    # 新页面信息：观察节点检测到的新页面信息（由观察节点设置，供执行节点使用）
    new_page_id: Optional[str]  # 新页面的pageId
    new_page_url: Optional[str]  # 新页面的URL
    # 弹窗/广告检测：观察节点检测到弹窗时写入，供执行节点注入 observation_result
    popup_hint: Optional[str]
    # 操作记录上下文：已打开的页面（由观察节点更新，可用 list_pages 获取当前页面列表）
    page_records: List[Dict[str, Any]]  # List[PageRecord] 记录每个页面的 url、打开时间、步骤名称、使用工具
    
    # === PlanExecutionState (续) ===
    # iteration_count: 当前任务已发生的「规划/执行轮次」计数（仅针对本次 run，每次任务从 0 开始）。
    #   - 在 Execute 节点发出工具调用（tool_calls）时 +1。
    #   - 与 max_iterations 比较用于终止条件：达到上限则停止，防止单次任务无限循环。
    #   - 注意：不是「重规划次数」（那是 plan_version / max_plan_versions），也不是计划内的「第几步」。
    iteration_count: int
    # max_iterations: 单次任务允许的最大迭代轮次上限；与 iteration_count 配合使用。默认值为 50。
    max_iterations: int
    max_tools_per_step: int  # 每步骤最大工具调用次数，用于限制单步骤的工具调用数量
    screenshots: List[str]
    screenshot_count: int  # 截图计数器，用于命名截图文件
    
    # === ReflectionState ===
    reflection: Optional[str]
    needs_replan: bool
    step_reflection_done: bool  # 步骤反思是否已完成（用于区分步骤反思和任务反思）
    
    # 任务完成判断
    task_completion_judgment: Optional[str]  # 任务完成判断Agent的判断结果和决定信息
    
    # 最终结果
    final_result: Optional[str]
    
    # === TaskState (续) / 会话隔离 ===
    session_id: Optional[str]
    # 上一任务中工具产生的已保存结果（json/mp4 等），供 execute 节点在历史中补充「来自哪个工具、什么结果、绝对路径」
    prior_saved_results: Optional[List[Dict[str, str]]]

    # === ErrorState ===
    error_count: int
    last_error: Optional[str]  # 供 Reflect / ErrorHandler / Execute 等使用
    error_history: List  # 错误历史记录（使用 Any 避免循环导入）
    last_error_category: Optional[str]  # 最近错误分类
    recovery_attempts: int  # 恢复尝试次数


@dataclass
class BrowserAgentContext:
    """执行上下文
    
    注意：如果字段为 None，将使用 Agent 配置中的默认值
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # 连接/会话标识，用于跨任务上下文存储；同一连接下多次任务共享同一 connection_id
    connection_id: Optional[str] = None
    max_iterations: Optional[int] = None  # 如果为 None，使用配置中的值
    max_tools_per_step: Optional[int] = None  # 如果为 None，使用配置中的值
    recursion_limit: Optional[int] = None  # 如果为 None，使用配置中的值
    take_screenshots: bool = True
    headless: bool = False
    # 任务级日志目录名（格式 YYYY-MM-DD_HHMMSS），用于统一该任务下所有日志的时间戳
    log_dir_name: Optional[str] = None

