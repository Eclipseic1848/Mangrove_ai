"""
浏览器 Agent 节点模块

包含所有工作流节点的实现（v2 模块化版本）。
"""

from .intent_node_v2 import IntentNode
from .execute_node_v2 import ExecuteNode
from .tools_wrapper_v2 import tools_wrapper
from .observe_node_v2 import ObserveNode
from .reflect_node_v2 import ReflectNode
from .error_handler_node_v2 import ErrorHandlerNode

# 导出工具函数（从 node_utils 和 utils 中导出）
from ..node_utils import analyze_page_status
from src.agent.utils.url_utils import extract_target_url_from_task
from src.agent.utils.task_utils import extract_task_keywords

__all__ = [
    # v2 节点类（browser-use 模式无 PlanNode）
    "IntentNode",
    "ExecuteNode",
    "ObserveNode",
    "ReflectNode",
    "ErrorHandlerNode",
    # 工具函数
    "tools_wrapper",
    # 工具函数（从 node_utils）
    "extract_target_url_from_task",
    "analyze_page_status",
    "extract_task_keywords",
]

