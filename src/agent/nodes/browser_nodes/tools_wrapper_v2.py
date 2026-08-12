"""
工具包装器节点（v2 - 模块化版本）

负责包装 LangGraph 的 ToolNode，提供工具执行的统一接口，包括：
- 工具调用提取和执行
- 错误处理和恢复
- 结果记录和日志
- 状态更新和快照管理
"""
import logging
import time
import json
import threading
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage

from src.agent.types.state_types import BrowserAgentState
from src.agent.types.execution_records import ToolCallRecord, DecisionRecord
from src.agent.utils.agent_flow_logger import log_tool_result, log_tool_input
from src.agent.utils.screenshot_utils import capture_screenshot, capture_screenshot_for_vl, SCREENSHOT_DIR_NAME
from src.agent.nodes.node_utils import save_tool_result_to_file



logger = logging.getLogger(__name__)


# ==================== 常量定义 ====================

# 截图：同步执行（带可配置超时），保证「每步操作后的页面状态」与截图一一对应；超时则跳过本步截图

# 错误模式匹配（从 agent_config 读取，此处为默认兜底）
def _get_error_patterns() -> List[str]:
    """从配置获取错误检测模式，避免硬编码"""
    try:
        from src.agent.config.agent_config import BrowserAgentConfig
        cfg = BrowserAgentConfig.from_settings()
        return cfg.error_handling.error_detection_patterns
    except Exception:
        return [
            "MCP error -", "stale snapshot", "No such element", "Timed out",
            "Locator.waitHandle", "解析处理失败",
        ]

# 日志相关常量
LOG_SEPARATOR = "-" * 80
MAX_CONTENT_PREVIEW_LENGTH = 100
MAX_ERROR_MESSAGE_LENGTH = 500
MAX_FILE_CONTENT_PREVIEW = 2000


# ==================== 工具包装器节点类 ====================

class ToolsWrapperNode:
    """工具包装器节点（v2 - 模块化版本）
    
    负责包装 LangGraph 的 ToolNode，提供工具执行的统一接口。
    """
    
    def __init__(self, implementation, tool_node: ToolNode):
        """初始化工具包装器节点
        
        Args:
            implementation: 节点实现对象，包含配置和日志目录等
            tool_node: LangGraph 的 ToolNode 实例
        """
        self.node_impl = implementation
        self.tool_node = tool_node

    # ==================== 数据提取方法 ====================

    def _extract_pending_calls(self, state: BrowserAgentState) -> List[Dict[str, Any]]:
        """提取待执行的工具调用
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            待执行的工具调用列表，每个调用包含 id、name、args、start_time
        """
        pending_calls = []
        messages = state.get("messages", [])
        
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                for tc in last_msg.tool_calls:
                    pending_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                        "start_time": time.time(),
                    })
        
        return pending_calls

    # ==================== 错误处理方法 ====================

    def _handle_tool_execution_error(
        self,
        error: Exception,
        pending_calls: List[Dict[str, Any]],
        tool_call_records: List[ToolCallRecord],
        duration: float,
        state: BrowserAgentState
    ) -> Dict[str, Any]:
        """处理工具执行错误
        
        Args:
            error: 执行异常
            pending_calls: 待执行的工具调用列表
            tool_call_records: 工具调用记录列表
            duration: 执行耗时（毫秒）
            state: 浏览器Agent状态
            
        Returns:
            包含错误信息的状态更新字典
        """
        logger.error(f"工具执行错误: {error}")
        self._log_tool_execution_error(error, pending_calls, duration)
        
        # 为每个失败的调用创建记录
        for pending in pending_calls:
            record = ToolCallRecord(
                tool_name=pending["name"],
                tool_args=pending["args"],
                tool_result=None,
                success=False,
                error_message=str(error),
                duration_ms=duration,
                next_action="error_recovery",
                reasoning="工具执行失败",
            )
            tool_call_records.append(record)
        
        return {
            "messages": [AIMessage(content=f"工具执行失败: {error}")],
            "error_count": state.get("error_count", 0) + 1,
            "last_error": str(error),
            "tool_call_records": tool_call_records,
        }

    def _log_tool_execution_error(
        self,
        error: Exception,
        pending_calls: List[Dict[str, Any]],
        duration: float
    ) -> None:
        """记录工具执行错误到日志
        
        Args:
            error: 执行异常
            pending_calls: 待执行的工具调用列表
            duration: 执行耗时（毫秒）
        """
        logger.error(LOG_SEPARATOR)
        logger.error(f"[TOOLS] 工具执行失败")
        logger.error(f"执行耗时: {duration:.2f}ms")
        logger.error(f"错误信息: {str(error)}")
        
        for pending in pending_calls:
            logger.error(f"  失败的工具:")
            logger.error(f"    工具名称: {pending['name']}")
            logger.error(f"    工具ID: {pending.get('id', '')}")
            logger.error(f"    输入参数:")
            
            # 格式化输入参数
            try:
                input_params_str = json.dumps(pending['args'], ensure_ascii=False, indent=2)
                for line in input_params_str.split('\n'):
                    logger.error(f"      {line}")
            except (TypeError, ValueError):
                logger.error(f"      {pending['args']}")
            
            logger.error(f"    输出参数: None (执行失败)")
            logger.error(f"    错误信息: {str(error)}")
            logger.error(f"    错误类型: {type(error).__name__}")
        
        logger.error(LOG_SEPARATOR)

    def _detect_error_in_response(
        self, 
        content_str: str, 
        tool_name: str
    ) -> Tuple[bool, bool]:
        """检测响应中是否包含错误信息
        
        Args:
            content_str: 响应内容字符串
            tool_name: 工具名称
            
        Returns:
            (是否包含错误, 是否是过期快照错误) 的元组
        """
        is_error_response = False
        is_stale_snapshot_error = False
        
        if not content_str:
            return is_error_response, is_stale_snapshot_error
        
        # 检查是否包含错误模式（从配置读取）
        for pattern in _get_error_patterns():
            if pattern in content_str:
                is_error_response = True
                if pattern == "stale snapshot":
                    is_stale_snapshot_error = True
                break
        
        # 额外检查：如果内容以 "MCP error" 开头，肯定是错误
        if content_str.strip().startswith("MCP error"):
            is_error_response = True
        
        # 对于快照工具，如果内容包含 "Latest page snapshot"，通常是成功的
        if "browser_snapshot" in tool_name and "Latest page snapshot" in content_str:
            is_error_response = False
        
        return is_error_response, is_stale_snapshot_error

    # ==================== 日志记录方法 ====================

    def _parse_output_params(self, content: Any) -> str:
        """解析输出参数
        
        尝试将内容解析为格式化的 JSON 字符串。
        
        Args:
            content: 工具返回的内容
            
        Returns:
            格式化后的输出参数字符串
        """
        output_params = content
        try:
            if isinstance(content, str):
                # 尝试解析为 JSON
                parsed = json.loads(content)
                output_params = json.dumps(parsed, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            # 如果不是 JSON，直接使用原始内容
            pass
        
        return str(output_params)

    def _should_save_to_file(self, output_params: str, tool_name: str) -> bool:
        """判断是否应该保存到文件
        
        Args:
            output_params: 输出参数字符串
            tool_name: 工具名称
            
        Returns:
            是否应该保存到文件
        """
        return len(str(output_params)) > 0 or tool_name == 'browser_evaluate'

    def _save_tool_result_to_file(
        self,
        tool_name: str,
        output_params: str,
        timestamp: str
    ) -> None:
        """保存工具结果到文件（使用统一的保存函数）
        
        Args:
            tool_name: 工具名称
            output_params: 输出参数字符串
            timestamp: 时间戳字符串
        """
        # 使用统一的保存函数，优先使用 node_impl.logs_dir，如果没有则使用默认值
        logs_dir = self.node_impl.logs_dir if hasattr(self.node_impl, 'logs_dir') else None
        save_tool_result_to_file(
            tool_name=tool_name,
            output_params=output_params,
            timestamp=timestamp,
            logs_dir=logs_dir,
            verbose=self.node_impl.verbose
        )

    def _log_tool_output(
        self,
        index: int,
        pending: Dict[str, Any],
        content: Any,
        output_params: str,
        duration: float
    ) -> None:
        """保存工具结果到文件（详细日志由 agent_flow_logger.log_tool_result 统一输出，避免重复）"""
        tool_name = pending.get("name", "")
        should_save = self._should_save_to_file(output_params, tool_name)
        if should_save:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self._save_tool_result_to_file(tool_name or "", output_params or "", timestamp)

    def _log_tool_execution_start(
        self,
        current_index: int,
        duration: float,
        result_messages_count: int,
        plan: Optional[List] = None,
    ) -> None:
        """记录工具执行开始信息
        
        Args:
            current_index: 当前步骤索引
            duration: 执行耗时（毫秒）
            result_messages_count: 返回消息数量
            plan: 计划列表（用于总步骤数）
        """
        if self.node_impl.verbose:
            total = len(plan) if plan else 0
            logger.info(LOG_SEPARATOR)
            logger.info(f"[TOOLS] 工具执行结果 (步骤 {current_index + 1}/{total}) | 耗时 {duration:.0f}ms | 返回 {result_messages_count} 条")

    def _log_tool_execution_end(self) -> None:
        """记录工具执行结束信息"""
        if self.node_impl.verbose:
            logger.info(LOG_SEPARATOR)

    # ==================== 记录创建方法 ====================

    def _create_tool_call_record(
        self,
        pending: Dict[str, Any],
        content: Any,
        content_str: str,
        is_error_response: bool,
        duration: float,
        current_index: int
    ) -> ToolCallRecord:
        """创建工具调用记录
        
        Args:
            pending: 待执行的工具调用信息
            content: 工具返回的内容
            content_str: 工具返回的内容字符串
            is_error_response: 是否包含错误响应
            duration: 执行耗时（毫秒）
            current_index: 当前步骤索引
            
        Returns:
            工具调用记录对象
        """
        return ToolCallRecord(
            tool_name=pending["name"],
            tool_args=pending["args"],
            tool_result=content,
            success=not is_error_response,
            error_message=content_str[:200] if is_error_response else None,
            duration_ms=duration,
            next_action="error_recovery" if is_error_response else "continue",
            reasoning=f"步骤 {current_index + 1} {'执行失败' if is_error_response else '执行完成'}",
            step_index=current_index,  # 🔧 新增：明确设置步骤索引，确保界面正确关联
        )

    def _create_decision_record(
        self,
        pending: Dict[str, Any],
        content: Any,
        content_str: str,
        is_error_response: bool,
        current_index: int
    ) -> DecisionRecord:
        """创建决策记录
        
        Args:
            pending: 待执行的工具调用信息
            content: 工具返回的内容
            content_str: 工具返回的内容字符串
            is_error_response: 是否包含错误响应
            current_index: 当前步骤索引
            
        Returns:
            决策记录对象
        """
        return DecisionRecord(
            based_on_tool_call=pending["id"],
            tool_feedback=str(content)[:MAX_CONTENT_PREVIEW_LENGTH],
            decision=f"步骤 {current_index + 1} {'失败' if is_error_response else '完成'}",
            reasoning=(
                f"工具 {pending['name']} 返回"
                f"{'错误' if is_error_response else '结果'}: {content_str[:MAX_CONTENT_PREVIEW_LENGTH]}"
            ),
            next_step="错误恢复或反思" if is_error_response else "继续下一步或反思",
        )

    # ==================== 主方法 ====================

    def wrap(self, state: BrowserAgentState) -> Dict[str, Any]:
        """工具节点包装器主入口
        
        执行工具调用并处理结果，包括：
        1. 提取待执行的工具调用
        2. 执行工具
        3. 处理结果和错误
        4. 更新状态和记录
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            更新后的状态字典
        """
        # 初始化记录列表
        tool_call_records = list(state.get("tool_call_records", []))
        decision_records = list(state.get("decision_records", []))
        
        # 提取待执行的工具调用
        pending_calls = self._extract_pending_calls(state)
        state_for_invoke = state

        # 执行工具入口：便于排查本步是否有其他操作
        tool_names = [p.get("name", "") for p in pending_calls]
        logger.info(f"🔧 [TOOLS] 执行工具入口 - 本步将执行 {len(pending_calls)} 个工具: {tool_names}")
        
        # 执行前：记录工具输入
        for i, p in enumerate(pending_calls):
            log_tool_input(i, p.get("name", ""), p.get("args", {}))
        
        # 执行工具
        start_time = time.time()
        try:
            invoke_result = self.tool_node.invoke(state_for_invoke)
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            return self._handle_tool_execution_error(
                e, pending_calls, tool_call_records, duration, state_for_invoke
            )
        
        duration = (time.time() - start_time) * 1000
        
        result_messages = invoke_result.get("messages", [])
        plan: List[Dict[str, Any]] = []
        current_index = 0
        real_tool_count = 0

        self._log_tool_execution_start(current_index, duration, len(result_messages), plan)

        # 处理每个工具的输出（处理结果 + 更新记录）
        for i, msg in enumerate(result_messages):
            if i >= len(pending_calls):
                continue

            pending = pending_calls[i]
            tool_name = pending.get("name", "")

            content = msg.content if hasattr(msg, "content") else str(msg)
            content_str = str(content) if content else ""

            # 解析和记录输出
            output_params = self._parse_output_params(content)
            self._log_tool_output(i, pending, content, output_params, duration)

            real_tool_count += 1
            effective_index = current_index + real_tool_count - 1

            # 检测错误
            is_error_response, _ = self._detect_error_in_response(content_str, tool_name)
            if self.node_impl.verbose:
                log_tool_result(
                    step_index=effective_index,
                    total_steps=0,
                    tool_name=tool_name,
                    duration_ms=duration,
                    success=not is_error_response,
                    result_preview=str(content_str) if not is_error_response else "",
                    error_msg=content_str if is_error_response else "",
                    tool_args=pending.get("args"),
                )

            # 更新记录（浏览器状态由观察节点统一 list_pages / take_snapshot 更新）
            tool_record = self._create_tool_call_record(
                pending, content, content_str, is_error_response, duration, effective_index
            )
            tool_call_records.append(tool_record)

            decision = self._create_decision_record(
                pending, content, content_str, is_error_response, effective_index
            )
            decision_records.append(decision)

            if is_error_response:
                logger.warning(f"    ⚠️ 工具返回错误信息: {content_str[:500]}")
                if not invoke_result.get("last_error"):
                    invoke_result["last_error"] = content_str[:MAX_ERROR_MESSAGE_LENGTH]
            
            # 截图：同步执行（带超时），保证截图对应「本步操作后的页面状态」；固定 JPEG 减小体积与超时风险
            screenshot_count = state.get("screenshot_count", 0)
            if self.node_impl.mcp_client.is_connected():
                try:
                    new_count = screenshot_count + 1
                    temp_state = {**state, "screenshot_count": new_count}
                    _b64, relative_path = capture_screenshot(
                        self.node_impl.mcp_client,
                        temp_state,
                        verbose=bool(self.node_impl.verbose),
                    )
                    if relative_path:
                        invoke_result["current_screenshot"] = relative_path
                        invoke_result["screenshot_count"] = new_count
                        state["screenshot_count"] = new_count
                        if self.node_impl.verbose:
                            logger.info(f"📸 工具调用后截图已保存 [{i+1}/{len(result_messages)}]: {relative_path}")
                        # VL 模型口子：与步骤 JPEG 意义不同，单独保存 PNG 供视觉模型用
                        try:
                            from src.config import settings
                            if getattr(settings, "screenshot_vl_png_enabled", False):
                                _vl_b64, vl_relative_path = capture_screenshot_for_vl(
                                    self.node_impl.mcp_client,
                                    temp_state,
                                    verbose=bool(self.node_impl.verbose),
                                )
                                if vl_relative_path:
                                    invoke_result["current_screenshot_vl"] = vl_relative_path
                                    state["current_screenshot_vl"] = vl_relative_path
                        except Exception as _e:
                            logger.debug(f"VL PNG 截图跳过: {_e}")
                    else:
                        if self.node_impl.verbose:
                            logger.info("📸 本步截图跳过（超时或失败），不影响后续流程")
                except Exception as e:
                    logger.warning(f"截图失败: {e}")
        
        # 记录工具执行结束
        self._log_tool_execution_end()

        if "last_error" not in invoke_result:
            invoke_result["last_error"] = None

        invoke_result["tool_call_records"] = tool_call_records
        invoke_result["decision_records"] = decision_records

        return invoke_result

    def __call__(self, state: BrowserAgentState) -> Dict[str, Any]:
        """使实例可调用
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            更新后的状态字典
        """
        return self.wrap(state)


# ==================== 工厂函数 ====================

def tools_wrapper(node_impl, tool_node: ToolNode) -> ToolsWrapperNode:
    """工具节点包装器工厂函数
    
    返回一个可调用对象，供 LangGraph 使用。
    
    Args:
        node_impl: 节点实现对象
        tool_node: LangGraph 的 ToolNode 实例
        
    Returns:
        ToolsWrapperNode 实例
    """
    wrapper_node = ToolsWrapperNode(node_impl, tool_node)
    return wrapper_node