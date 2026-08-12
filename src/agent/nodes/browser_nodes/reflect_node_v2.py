"""
反思节点（v2）

根据完整执行记录（agent_history，不截断）进行总结并判断任务是否完成，仅依赖 LLM 判断，无硬性检查。
"""
import logging
import re
from typing import Dict, Any, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import ValidationError

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.prompts.browser_prompt_utils import get_task_completion_judge_prompt_with_format
from src.agent.schema.browser_use_models import TaskCompletionJudgmentOutput
from src.agent.utils.text_utils import smart_format_page_info
from src.agent.config.text_processing_config import default_text_config
from src.agent.nodes.node_utils import (
    collect_execution_statistics,
    collect_step_results,
    collect_error_information,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_FAILED,
    STEP_STATUS_PENDING,
)
from src.services.session_context_service import format_messages_as_context
from src.agent.utils.task_utils import match_keywords_in_snapshot
from src.agent.utils.json_summary_utils import summarize_task_jsons_for_frontend
from src.agent.utils.agent_flow_logger import log_flow_milestone
from src.agent.nodes.parse_utils import parse_with_retry

# 反思用：Human/AI 消息不截断（单条上限，仅防极端超长）；工具结果单独截断避免快照/JSON 撑爆 prompt
REFLECT_MAX_MESSAGE_CHARS = 200000
REFLECT_MAX_TOOL_CHARS = 200

logger = logging.getLogger(__name__)


# ==================== 常量定义 ====================

# 任务状态常量
STATUS_COMPLETED = "completed"
STATUS_NOT_COMPLETED = "not_completed"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"

# 硬性检查状态
HARD_CHECK_POSSIBLE_COMPLETED = "possible_completed"
HARD_CHECK_NOT_COMPLETED = "not_completed"

# 可恢复的错误关键词
RECOVERABLE_ERROR_KEYWORDS = [
    "SSL", "ssl", "证书", "协议错误", "网络", "连接",
    "ERR_SSL", "ERR_CERT", "ERR_CONNECTION"
]

# 重规划触发关键词
REPLAN_TRIGGER_KEYWORDS = ["建议", "可以尝试", "需要检查"]


# ==================== 反思节点类 ====================

class ReflectNode:
    """反思节点（v2）
    
    根据完整执行记录（agent_history）进行总结并判断任务是否完成，仅依赖 LLM 判断。
    """
    
    def __init__(self, implementation):
        """初始化反思节点
        
        Args:
            implementation: 节点实现对象，包含LLM、MCP客户端等依赖
        """
        self.node_impl = implementation
        self.llm = self.node_impl.llm
        self.output_parser: Optional[PydanticOutputParser] = None

    # ==================== 数据收集方法 ====================

    def _collect_execution_statistics(self, state: BrowserAgentState) -> Dict[str, Any]:
        """收集执行统计数据
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            包含执行统计信息的字典
        """
        return collect_execution_statistics(
            state,
            status_completed=STATUS_COMPLETED,
            status_failed=STATUS_FAILED
        )

    def _collect_step_results(self, plan: list) -> Tuple[list, list]:
        """收集步骤结果摘要
        
        Args:
            plan: 执行计划列表
            
        Returns:
            (步骤摘要列表, 步骤详细结果列表) 的元组
        """
        return collect_step_results(
            plan,
            status_completed=STATUS_COMPLETED,
            status_failed=STATUS_FAILED,
            status_pending=STATUS_PENDING
        )

    def _collect_error_information(self, state: BrowserAgentState) -> str:
        """收集错误信息
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            错误信息字符串
        """
        return collect_error_information(state, status_failed=STATUS_FAILED)

    # ==================== 硬性检查方法 ====================

    def _check_voc_json_tools_required(
        self, state: BrowserAgentState, user_task: str
    ) -> Optional[Dict[str, Any]]:
        """检查任务要求 JSON 保存或 VOC 分析时，是否实际调用了对应工具
        
        任务要求「保存 JSON」「VOC 分析」「保存到数据库」时，必须调用对应工具，
        否则判定未完成，避免误判。
        
        Returns:
            若需验证且未通过，返回 hard_check_result；否则返回 None
        """
        task_lower = (user_task or "").lower()
        if not any(
            k in task_lower
            for k in ("json", "voc", "保存", "提取", "数据库", "mongo", "mongodb", "入库")
        ):
            return None
        
        requires_extract = any(k in task_lower for k in ("保存", "提取", "json", "内容", "帖子"))
        requires_voc = any(k in task_lower for k in ("voc", "分析")) and "数据库" not in task_lower
        requires_db = any(
            k in task_lower for k in ("数据库", "mongo", "mongodb", "入库")
        )
        if not (requires_extract or requires_voc or requires_db):
            return None
        
        records = state.get("tool_call_records", [])
        called_tools = set()
        for rec in records:
            name = getattr(rec, "tool_name", None) or (rec.get("tool_name", "") if isinstance(rec, dict) else "")
            if name:
                called_tools.add(name)
        
        extract_tools = {
            "browser_extract_dcd_post_detail",
            "browser_extract_autohome_post_detail",
            "browser_extract_autohome_chejiahao_info",
            "browser_extract_dcd_video",
        }
        has_extract = bool(called_tools & extract_tools)
        voc_tools = {"browser_analyze_voc", "browser_filter_voc"}
        has_voc = bool(called_tools & voc_tools)
        store_tools = {"browser_voc_store_from_json_file", "browser_voc_store_crawl_result"}
        has_db_store = bool(called_tools & store_tools)
        
        missing = []
        if requires_extract and not has_extract:
            missing.append(
                "browser_extract_dcd_post_detail / browser_extract_autohome_post_detail / "
                "browser_extract_autohome_chejiahao_info / browser_extract_dcd_video（保存 JSON）"
            )
        if requires_voc and not has_voc:
            missing.append("browser_analyze_voc 或 browser_filter_voc（VOC 分析）")
        if requires_db and not has_db_store:
            missing.append(
                "browser_voc_store_from_json_file（将 extract 产出的 JSON 入库 MongoDB）"
            )
        
        if not missing:
            return None
        
        if self.node_impl.verbose:
            logger.info(f"📋 [REFLECT] 工具调用验证未通过：任务要求 JSON/VOC 但未调用 {missing}")
        
        return {
            "status": HARD_CHECK_NOT_COMPLETED,
            "passed": False,
            "found_keywords": [],
            "missing_keywords": missing,
            "reason": f"任务要求保存 JSON 或 VOC 分析，但未调用必需工具：{', '.join(missing)}。"
        }

    def _perform_hard_check(
        self, 
        state: BrowserAgentState, 
        all_steps_done: bool, 
        user_task: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """执行硬性检查（关键词匹配）
        
        在所有步骤完成后，通过关键词匹配来验证任务是否完成。
        关键词从计划步骤中提取，而不是从任务描述中提取。
        
        Args:
            state: 浏览器Agent状态
            all_steps_done: 是否所有步骤都已完成
            user_task: 用户任务描述（保留用于日志）
            
        Returns:
            (硬性检查结果字典, 最新快照文本) 的元组
        """
        if not (all_steps_done and self.node_impl.mcp_client.is_connected()):
            return None, None
        
        # 工具调用验证：任务要求保存 JSON 或 VOC 分析时，必须实际调用对应工具
        tool_check = self._check_voc_json_tools_required(state, user_task)
        if tool_check is not None:
            return tool_check, (state.get("page_snapshot") or "")
        
        try:
            if self.node_impl.verbose:
                logger.info("🔍 硬性判定：获取最新页面快照进行关键词匹配")
            
            # 获取最新页面快照
            latest_snapshot = self.node_impl.mcp_client.take_snapshot()
            latest_snapshot_text = str(latest_snapshot)
            
            if self.node_impl.verbose:
                logger.info(f"   快照长度: {len(latest_snapshot_text)} 字符")
            
            task_keywords = []
            
            if self.node_impl.verbose:
                logger.info(f"   从计划步骤中提取到的关键词: {task_keywords}")
            
            if not task_keywords:
                if self.node_impl.verbose:
                    logger.info("   ⚠️ 计划步骤中未包含关键词，跳过硬性判定")
                return None, latest_snapshot_text
            
            # 在快照中查找关键词
            found_keywords, missing_keywords = match_keywords_in_snapshot(
                task_keywords, 
                latest_snapshot_text
            )
            
            if self.node_impl.verbose:
                logger.info(f"   匹配结果: 找到 {found_keywords}, 未找到 {missing_keywords}")
            
            # 生成硬性检查结果
            hard_check_result = self._build_hard_check_result(
                found_keywords, 
                missing_keywords
            )
            
            if self.node_impl.verbose:
                logger.info(f"   硬性判定结果: status={hard_check_result['status']}, passed={hard_check_result['passed']}")
            
            return hard_check_result, latest_snapshot_text
            
        except Exception as e:
            logger.warning(f"硬性判定失败: {e}")
            if self.node_impl.verbose:
                logger.info("⚠️ 硬性判定：获取快照失败，跳过户性判定")
            return None, None

    def _build_hard_check_result(
        self, 
        found_keywords: list, 
        missing_keywords: list
    ) -> Dict[str, Any]:
        """构建硬性检查结果
        
        Args:
            found_keywords: 找到的关键词列表
            missing_keywords: 未找到的关键词列表
            
        Returns:
            硬性检查结果字典
        """
        if found_keywords:
            result = {
                "status": HARD_CHECK_POSSIBLE_COMPLETED,
                "passed": True,
                "found_keywords": found_keywords,
                "missing_keywords": missing_keywords,
                "reason": f"找到关键词: {', '.join(found_keywords)}"
            }
            if self.node_impl.verbose:
                logger.info(f"✅ 硬性判定：找到关键词 {found_keywords}，可能已完成")
        else:
            result = {
                "status": HARD_CHECK_NOT_COMPLETED,
                "passed": False,
                "found_keywords": [],
                "missing_keywords": missing_keywords,
                "reason": f"未找到任何关键词: {', '.join(missing_keywords)}"
            }
            if self.node_impl.verbose:
                logger.info(f"❌ 硬性判定：未找到任何关键词 {missing_keywords}，未完成")
        
        return result

    # ==================== 提示词生成方法 ====================

    def _generate_reflection_prompt(
        self,
        state: BrowserAgentState,
        statistics: dict,
        is_step_reflection: bool = False
    ) -> str:
        """生成反思提示词（基于完整执行记录，不截断）
        
        Args:
            state: 浏览器Agent状态
            statistics: 执行统计数据
            is_step_reflection: 是否为步骤反思（True=步骤反思，False=任务反思）
            
        Returns:
            反思提示词字符串
        """
        user_task = state.get("user_task", "")
        messages = state.get("messages", [])
        # 完整 agent_history，不截断，供反思根据完整执行记录总结判断
        agent_history_full = format_messages_as_context(
            messages,
            max_human_chars=REFLECT_MAX_MESSAGE_CHARS,
            max_ai_chars=REFLECT_MAX_MESSAGE_CHARS,
            max_tool_chars=REFLECT_MAX_TOOL_CHARS,
            compress_urls=True,
            add_round_labels=True,
            include_tool_results=True,
            log_content=False,
        )
        if self.node_impl.verbose and agent_history_full:
            logger.info(f"[REFLECT] 完整执行记录长度: {len(agent_history_full)} 字符")
        
        current_url = state.get("current_url", "")
        page_snapshot = state.get("page_snapshot", "")
        page_info = smart_format_page_info(
            page_snapshot=page_snapshot or "",
            current_url=current_url or "",
            context='judge'
        )
        error_info = self._collect_error_information(state)
        
        return self._build_reflection_prompt_content(
            user_task=user_task,
            statistics=statistics,
            error_info=error_info,
            agent_history_full=agent_history_full,
            page_info=page_info,
            is_step_reflection=is_step_reflection
        )

    def _build_hard_check_section(
        self, 
        hard_check_result: Optional[Dict[str, Any]]
    ) -> str:
        """构建硬性检查结果说明部分
        
        Args:
            hard_check_result: 硬性检查结果
            
        Returns:
            硬性检查结果说明字符串
        """
        if not hard_check_result:
            return ""
        
        passed_icon = "✅ 通过" if hard_check_result['passed'] else "❌ 未通过"
        found_keywords = hard_check_result.get('found_keywords', [])
        missing_keywords = hard_check_result.get('missing_keywords', [])
        
        return f"""
    【程序化验证结果】
    机器验证结论: {passed_icon}
    证据: {hard_check_result['reason']}
    找到的关键词: {', '.join(found_keywords) if found_keywords else '无'}
    未找到的关键词: {', '.join(missing_keywords) if missing_keywords else '无'}

    **⚠️ 关键决策规则**：
    1. **如果机器验证已通过（✅ 通过）**：必须判定任务为 Completed，忽略 URL 是否变化、忽略页面是否跳转
    2. **对于搜索任务**：如果找到了搜索关键词，说明搜索操作已成功，即使URL未变化（AJAX加载）也应判定为完成
    3. **搜索成功的标志**：关键词出现在页面中（包括搜索框、搜索建议列表、搜索结果等任何位置）
    4. **优先级**：机器验证结果 > LLM主观判断
    """

    def _build_reflection_prompt_content(
        self,
        user_task: str,
        statistics: dict,
        error_info: str,
        agent_history_full: str,
        page_info: str,
        is_step_reflection: bool = False
    ) -> str:
        """构建反思提示词内容（任务反思：基于完整执行记录总结并判断）
        
        Args:
            user_task: 用户任务
            statistics: 执行统计数据
            error_info: 错误信息
            agent_history_full: 完整执行记录（未截断的 agent_history）
            page_info: 当前页面信息
            is_step_reflection: 是否为步骤反思（当前仅任务反思）
            
        Returns:
            完整的反思提示词
        """
        if is_step_reflection:
            return f"""请基于以下信息评估当前步骤的执行结果：

    【原始任务】
    {user_task}
    【当前页面状态】
    {page_info}
    {error_info}
    请给出步骤执行结果的评估。"""
        # 任务反思：根据完整执行记录总结并判断任务是否完成
        return f"""请根据以下**完整执行记录**总结执行结果，并判断任务是否完成。

    【原始任务】
    {user_task}
    {error_info}

    【完整执行记录】（请据此总结与判断，勿遗漏关键步骤与工具调用）
    {agent_history_full or "（无执行记录）"}

    【当前页面状态】
    {page_info}

    请完成两件事：
    1. **总结**：基于完整执行记录，简要总结已执行的操作与结果（如：打开了哪些页面、调用了哪些工具、是否完成搜索/点击/提取/VOC 分析等）。
    2. **判断**：根据任务要求与执行记录，判断任务是否完成，并输出 task_status（completed/not_completed/failed）、final_result、needs_replan、missing_operations（若未完成）。

    **判断原则**：
    - 若执行记录中已包含任务要求的所有关键操作（如搜索、进入帖子、提取内容、VOC 分析等）且工具调用成功，应判为 completed。
    - 若执行记录显示任务以 task_complete 且 **success=false** 结束（如「任务无法完成」、无法找到帖子、仅发现视频无主贴等），应设 **needs_replan=True**，以便重新规划尝试其他路径（如车友圈、社区、换关键词「xxx 车友圈」），系统最多允许 3 次重新开始。**此时请在 missing_operations 中写出建议的替代路径**（如「点击车友圈」「换关键词问界M9 车友圈」），供下一轮执行参考。
    - 勿因「执行统计总步骤数为 0」等统计口径问题误判未完成；以执行记录中的实际工具调用与结果为准。
    - 许多网站为 AJAX/SPA，点击后 URL 可能不变、内容动态加载，只要执行记录中有对应点击与结果即可视为完成。

    请输出符合格式的综合评估与判断。"""

    # ==================== 结果解析方法 ====================

    def _parse_judgment_result_v2(
        self, 
        result: dict, 
        state: BrowserAgentState
    ) -> Tuple[str, bool, bool, bool, str]:
        """解析判断结果
        
        Args:
            result: LLM返回的结果字典
            state: 浏览器Agent状态
            
        Returns:
            (明确状态, 是否完成, 是否失败, 是否需要重规划, 最终结果) 的元组
        """
        if self.node_impl.verbose:
            st = result.get("task_status", "")
            replan = result.get("needs_replan", False)
            summary = str(result.get("final_result", ""))[:80]
            logger.info("▶ [反思] 判断结果: task_status=%s | needs_replan=%s | %s", st, replan, summary)
            logger.debug("反思完整结果: %s", result)
        
        # 提取明确状态
        explicit_status = result.get("task_status", STATUS_NOT_COMPLETED)
        
        # 根据明确状态设置标志
        if explicit_status == STATUS_COMPLETED:
            is_completed = True
            is_failed = False
        elif explicit_status == STATUS_FAILED:
            is_completed = False
            is_failed = True
        else:  # not_completed or others
            is_completed = False
            is_failed = False
        
        needs_replan = result.get("needs_replan", False)
        final_result = result.get("final_result", "")
        
        return explicit_status, is_completed, is_failed, needs_replan, final_result

    # ==================== 决策方法 ====================

    def _determine_next_action(
        self,
        state: BrowserAgentState,
        response: Any,
        reflection: str,
        explicit_status: str,
        is_completed: bool,
        is_failed: bool,
        needs_replan: bool,
        final_result: str,
        hard_check_result: Optional[Dict[str, Any]],
        latest_snapshot_text: Optional[str],
        statistics: dict,
        is_step_reflection: bool = False
    ) -> Dict[str, Any]:
        """确定下一步操作
        
        根据任务状态、执行进度和判断结果，决定下一步行动。
        
        决策逻辑（优先级从高到低）：
        1. 如果是任务反思：
           - 如果硬性检查判定完成，直接判定任务完成（跳过LLM判断）
           - 如果硬性检查判定失败，使用LLM进行判断
           - 如果没有硬性检查结果，使用LLM进行判断
        
        Args:
            state: 浏览器Agent状态
            response: LLM响应对象
            reflection: 反思结果
            explicit_status: 明确状态
            is_completed: 是否完成
            is_failed: 是否失败
            needs_replan: 是否需要重规划
            final_result: 最终结果
            hard_check_result: 硬性检查结果
            latest_snapshot_text: 最新快照文本
            statistics: 执行统计数据
            is_step_reflection: 是否为步骤反思（True=步骤反思，False=任务反思）
            
        Returns:
            包含下一步操作的状态更新字典
        """
        # 规范化状态标志
        is_completed, is_failed, final_result = self._normalize_status_flags(
            explicit_status,
            is_completed,
            is_failed,
            final_result,
            statistics
        )
        
        task_completion_judgment = reflection
        
        # ==================== 任务反思逻辑（仅 LLM 判断，无硬性检查）====================
        
        # 处理任务完成的情况（LLM 判断）
        if explicit_status == STATUS_COMPLETED and is_completed:
            return self._build_completed_response(
                response,
                reflection,
                task_completion_judgment,
                final_result,
                hard_check_result,
                latest_snapshot_text,
                state
            )
        
        # 处理任务失败的情况
        if is_failed:
            # 若 LLM 明确认为可换路径/重试（needs_replan=True），且未达重规划上限，优先给一次重规划机会
            max_plan_versions = self.node_impl.config.planning.max_plan_versions
            plan_version = state.get("plan_version", 0)
            if needs_replan and plan_version < max_plan_versions:
                if self.node_impl.verbose:
                    logger.info(
                        "🔄 判断：任务失败但 LLM 建议重试（needs_replan=True），尝试重新规划（plan_version %d -> %d）",
                        plan_version,
                        plan_version + 1,
                    )
                return self._build_replan_response(
                    response,
                    reflection,
                    task_completion_judgment,
                    state,
                )
            return self._build_failed_response(
                response,
                reflection,
                task_completion_judgment,
                state
            )
        
        # 处理LLM判断未完成的情况（需要重规划）
        if explicit_status == STATUS_NOT_COMPLETED:
            return self._build_not_completed_response(
                response,
                reflection,
                task_completion_judgment,
                latest_snapshot_text,
                state
            )
        
        # 处理需要重规划的情况
        if needs_replan:
            return self._build_replan_response(
                response,
                reflection,
                task_completion_judgment,
                state
            )
        
        # 如果LLM判断完成（但explicit_status不是COMPLETED）
        if is_completed:
            return self._build_completed_response(
                response,
                reflection,
                task_completion_judgment,
                final_result,
                hard_check_result,
                latest_snapshot_text,
                state
            )
        
        # 默认：继续执行
        return self._build_continue_execution_response(
            response,
            reflection,
            task_completion_judgment
        )

    def _normalize_status_flags(
        self,
        explicit_status: str,
        is_completed: bool,
        is_failed: bool,
        final_result: str,
        statistics: dict
    ) -> Tuple[bool, bool, str]:
        """规范化状态标志
        
        根据LLM的明确状态和执行情况，规范化状态标志。
        
        Args:
            explicit_status: 明确状态
            is_completed: 是否完成
            is_failed: 是否失败
            final_result: 最终结果
            statistics: 执行统计数据
            
        Returns:
            (规范化后的is_completed, is_failed, final_result) 的元组
        """
        if explicit_status == STATUS_COMPLETED:
            is_completed = True
        elif explicit_status == STATUS_NOT_COMPLETED:
            is_completed = False
        elif explicit_status == STATUS_FAILED:
            is_completed = False
            is_failed = True
        else:
            # LLM没有明确判断，使用执行情况
            if (statistics['completed_count'] == statistics['total_steps'] and
                statistics['failed_count'] == 0 and
                not is_failed):
                is_completed = True
                if not final_result:
                    final_result = f"所有{statistics['total_steps']}个步骤已成功完成"
        
        if self.node_impl.verbose:
            logger.info(
                f"🔍 任务状态判断: explicit_status='{explicit_status}', "
                f"is_completed={is_completed}, "
                f"completed_count={statistics['completed_count']}/{statistics['total_steps']}"
            )
        
        return is_completed, is_failed, final_result

    def _build_continue_execution_response(
        self,
        response: Any,
        reflection: str,
        task_completion_judgment: str
    ) -> Dict[str, Any]:
        """构建继续执行的响应
        
        Args:
            response: LLM响应对象
            reflection: 反思结果
            task_completion_judgment: 任务完成判断
            
        Returns:
            状态更新字典
        """
        if self.node_impl.verbose:
            logger.info("⏳ 判断：任务未完成，继续执行剩余步骤")
        # 显式清除 needs_replan / final_result，避免路由误用上一轮状态而走到 END
        return {
            "messages": [response],
            "reflection": reflection,
            "task_completion_judgment": task_completion_judgment,
            "phase": BrowserAgentPhase.EXECUTING.value,
            "needs_replan": False,
            "final_result": None,
        }

    def _build_completed_response(
        self,
        response: Any,
        reflection: str,
        task_completion_judgment: str,
        final_result: str,
        hard_check_result: Optional[Dict[str, Any]],
        latest_snapshot_text: Optional[str],
        state: BrowserAgentState
    ) -> Dict[str, Any]:
        """构建任务完成的响应
        
        Args:
            response: LLM响应对象
            reflection: 反思结果
            task_completion_judgment: 任务完成判断
            final_result: 最终结果
            hard_check_result: 硬性检查结果
            latest_snapshot_text: 最新快照文本
            state: 浏览器Agent状态
            
        Returns:
            状态更新字典
        """
        # 记录日志
        if hard_check_result:
            if hard_check_result["status"] == HARD_CHECK_POSSIBLE_COMPLETED:
                if self.node_impl.verbose:
                    logger.info(
                        f"✅ 判断：所有步骤已完成，硬性判定和LLM判定都显示任务已完成"
                    )
                    logger.info(f"   硬性判定：{hard_check_result['reason']}")
            elif hard_check_result["status"] == HARD_CHECK_NOT_COMPLETED:
                if self.node_impl.verbose:
                    logger.info(
                        "✅ 判断：所有步骤已完成且任务已完成（LLM判定，硬性判定未找到关键词但LLM基于上下文判断已完成）"
                    )
        else:
            if self.node_impl.verbose:
                logger.info("✅ 判断：所有步骤已完成且任务已完成（仅LLM判定）")
        
        # 添加硬性判定结果到反思中
        final_reflection = reflection
        if hard_check_result:
            if hard_check_result["status"] == HARD_CHECK_NOT_COMPLETED:
                final_reflection += f"\n\n【硬性判定结果（仅供参考）】{hard_check_result['reason']}"
            else:
                final_reflection += f"\n\n【硬性判定结果】{hard_check_result['reason']}"
        
        # 优先使用 Execute 节点写入的 final_result（即本步模型 task_complete.text，含完整「任务完成报告」），
        # 避免被反思 LLM 的短摘要覆盖，确保前端展示 log 中的完整报告内容
        base_result = state.get("final_result") or final_result or final_reflection
        # logger.info(f"------debug---------> base_result: \n{base_result}")
        json_summary = summarize_task_jsons_for_frontend(state.get("messages", []))
        if json_summary:
            base_result = (base_result or "") + json_summary
        
        return {
            "messages": [response],
            "reflection": final_reflection,
            "task_completion_judgment": task_completion_judgment,
            "final_result": base_result,
            "phase": BrowserAgentPhase.COMPLETED.value,
            "needs_replan": False,  # 任务完成时显式清除，避免前端/路由沿用上一轮“未完成”时的 True 误判为未完成
            "page_snapshot": latest_snapshot_text if latest_snapshot_text else state.get("page_snapshot", ""),
        }

    def _build_hard_check_failed_response(
        self,
        response: Any,
        reflection: str,
        task_completion_judgment: str,
        hard_check_result: Dict[str, Any],
        latest_snapshot_text: Optional[str],
        state: BrowserAgentState
    ) -> Dict[str, Any]:
        """构建硬性检查失败的响应
        
        Args:
            response: LLM响应对象
            reflection: 反思结果
            task_completion_judgment: 任务完成判断
            hard_check_result: 硬性检查结果
            latest_snapshot_text: 最新快照文本
            state: 浏览器Agent状态
            
        Returns:
            状态更新字典
        """
        if self.node_impl.verbose:
            logger.info(
                f"❌ 硬性判定未完成，且LLM未明确判断为完成，判定为未完成: {hard_check_result['reason']}"
            )
        
        max_plan_versions = self.node_impl.config.planning.max_plan_versions
        plan_version = state.get("plan_version", 0)
        
        reflection_with_hard_check = reflection + f"\n\n【硬性判定结果】{hard_check_result['reason']}"
        
        if plan_version < max_plan_versions:
            return {
                "messages": [response],
                "reflection": reflection_with_hard_check,
                "task_completion_judgment": task_completion_judgment,
                "needs_replan": True,
                "phase": BrowserAgentPhase.REPLANNING.value,
                "plan_version": plan_version + 1,
                "iteration_count": 0,  # 每次重新规划后重置步数
                "page_snapshot": latest_snapshot_text if latest_snapshot_text else state.get("page_snapshot", ""),
                "last_error": None,  # 重规划后进入执行前清除，避免执行节点被误路由到错误处理
                "last_error_category": None,
            }
        else:
            return {
                "messages": [response],
                "reflection": reflection_with_hard_check,
                "task_completion_judgment": task_completion_judgment,
                "final_result": reflection,
                "phase": BrowserAgentPhase.FAILED.value,
                "page_snapshot": latest_snapshot_text if latest_snapshot_text else state.get("page_snapshot", ""),
            }

    def _build_not_completed_response(
        self,
        response: Any,
        reflection: str,
        task_completion_judgment: str,
        latest_snapshot_text: Optional[str],
        state: BrowserAgentState
    ) -> Dict[str, Any]:
        """构建未完成的响应（需要重规划）
        
        Args:
            response: LLM响应对象
            reflection: 反思结果
            task_completion_judgment: 任务完成判断
            latest_snapshot_text: 最新快照文本
            state: 浏览器Agent状态
            
        Returns:
            状态更新字典
        """
        max_plan_versions = self.node_impl.config.planning.max_plan_versions
        plan_version = state.get("plan_version", 0)
        
        if plan_version < max_plan_versions:
            if self.node_impl.verbose:
                logger.info("🔄 判断：所有步骤已完成但任务未完成，触发重新规划（plan_version %d -> %d）", plan_version, plan_version + 1)
            return {
                "messages": [response],
                "reflection": reflection,
                "task_completion_judgment": task_completion_judgment,
                "needs_replan": True,
                "phase": BrowserAgentPhase.REPLANNING.value,
                "plan_version": plan_version + 1,
                "iteration_count": 0,  # 每次重新规划后重置步数，使新一轮执行有独立 max_iterations 预算
                "page_snapshot": latest_snapshot_text if latest_snapshot_text else state.get("page_snapshot", ""),
                "last_error": None,  # 重规划后进入执行前清除，避免执行节点被误路由到错误处理
                "last_error_category": None,
            }
        else:
            # 已达重规划上限：仍给予一次继续执行机会，避免反思后直接结束
            if self.node_impl.verbose:
                logger.info(
                    "⚠️ 判断：任务未完成且已达最大重规划次数 (%d)，仍给予一次继续执行机会",
                    max_plan_versions,
                )
            return {
                "messages": [response],
                "reflection": reflection,
                "task_completion_judgment": task_completion_judgment,
                "phase": BrowserAgentPhase.EXECUTING.value,
                "needs_replan": False,
                "final_result": None,
                "page_snapshot": latest_snapshot_text if latest_snapshot_text else state.get("page_snapshot", ""),
                "last_error": None,
                "last_error_category": None,
            }

    def _build_failed_response(
        self,
        response: Any,
        reflection: str,
        task_completion_judgment: str,
        state: BrowserAgentState
    ) -> Dict[str, Any]:
        """构建任务失败的响应
        
        Args:
            response: LLM响应对象
            reflection: 反思结果
            task_completion_judgment: 任务完成判断
            state: 浏览器Agent状态
            
        Returns:
            状态更新字典
        """
        # 检查是否包含可恢复的错误类型
        is_recoverable = any(err in reflection for err in RECOVERABLE_ERROR_KEYWORDS)
        
        should_try_replan = (
            is_recoverable or
            any(keyword in reflection for keyword in REPLAN_TRIGGER_KEYWORDS)
        )
        
        max_plan_versions = self.node_impl.config.planning.max_plan_versions
        plan_version = state.get("plan_version", 0)
        
        if should_try_replan and plan_version < max_plan_versions:
            if self.node_impl.verbose:
                logger.info("🔄 判断：任务失败，但错误可恢复，尝试重新规划（plan_version %d -> %d）", plan_version, plan_version + 1)
            return {
                "messages": [response],
                "reflection": reflection,
                "task_completion_judgment": task_completion_judgment,
                "needs_replan": True,
                "phase": BrowserAgentPhase.REPLANNING.value,
                "plan_version": plan_version + 1,
                "iteration_count": 0,  # 每次重新规划后重置步数
            }
        else:
            if self.node_impl.verbose:
                logger.info("❌ 判断：任务失败，无法恢复")
            return {
                "messages": [response],
                "reflection": reflection,
                "task_completion_judgment": task_completion_judgment,
                "final_result": reflection,
                "phase": BrowserAgentPhase.FAILED.value,
            }

    def _build_replan_response(
        self,
        response: Any,
        reflection: str,
        task_completion_judgment: str,
        state: BrowserAgentState
    ) -> Dict[str, Any]:
        """构建重规划的响应
        
        Args:
            response: LLM响应对象
            reflection: 反思结果
            task_completion_judgment: 任务完成判断
            state: 浏览器Agent状态
            
        Returns:
            状态更新字典
        """
        max_plan_versions = self.node_impl.config.planning.max_plan_versions
        plan_version = state.get("plan_version", 0)
        
        if plan_version < max_plan_versions:
            if self.node_impl.verbose:
                logger.info("🔄 判断：需要重新规划（plan_version %d -> %d）", plan_version, plan_version + 1)
            return {
                "messages": [response],
                "reflection": reflection,
                "task_completion_judgment": task_completion_judgment,
                "needs_replan": True,
                "phase": BrowserAgentPhase.REPLANNING.value,
                "plan_version": plan_version + 1,
                "iteration_count": 0,  # 每次重新规划后重置步数
                "last_error": None,  # 重规划后进入执行前清除，避免执行节点被误路由到错误处理
                "last_error_category": None,
            }
        else:
            # 已达重规划上限：仍给予一次继续执行机会
            if self.node_impl.verbose:
                logger.info(
                    "⚠️ 判断：需要重规划但已达最大重规划次数 (%d)，仍给予一次继续执行机会",
                    max_plan_versions,
                )
            return {
                "messages": [response],
                "reflection": reflection,
                "task_completion_judgment": task_completion_judgment,
                "phase": BrowserAgentPhase.EXECUTING.value,
                "needs_replan": False,
                "final_result": None,
                "last_error": None,
                "last_error_category": None,
            }

    # ==================== LLM调用方法 ====================

    # ==================== 主方法 ====================

    def reflect(self, state: BrowserAgentState) -> Dict[str, Any]:
        """反思主方法
        
        评估执行结果并判断任务完成情况。
        
        逻辑：
        1. 如果还有步骤未完成，进行步骤反思（评估当前步骤）
        2. 如果所有步骤都完成，但步骤反思未完成，先进行步骤反思（评估最后一步）
        3. 如果所有步骤都完成且步骤反思已完成，进行任务反思（评估整个任务）
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            包含反思结果和下一步操作的状态更新字典
        """
        # 流程锚点 + 入口日志
        plan_version = state.get("plan_version", 0)
        reflect_round = plan_version + 1
        milestone_label = "第%d次反思" % (reflect_round,)
        log_flow_milestone(milestone_label, plan_version=plan_version, phase=state.get("phase", ""))
        logger.info("▶ [反思] %s | phase=%s", milestone_label, state.get("phase", ""))
        # 收集执行统计数据（无步骤级反思，仅任务反思）
        statistics = self._collect_execution_statistics(state)
        all_steps_done = statistics['all_steps_done']
        is_step_reflection = False  # 已删除步骤级反思，仅做任务反思
        
        if self.node_impl.verbose:
            logger.info("▶ [反思] 根据完整执行记录总结并判断任务是否完成")
        
        # 不再执行硬性检查，仅基于完整 agent_history 做 LLM 总结判断
        hard_check_result = None
        latest_snapshot_text = state.get("page_snapshot") or ""
        
        # 生成反思提示词（含完整执行记录，不截断）
        reflect_prompt = self._generate_reflection_prompt(
            state,
            statistics,
            is_step_reflection=is_step_reflection
        )
        
        # 创建Pydantic输出解析器
        self.output_parser = PydanticOutputParser(
            pydantic_object=TaskCompletionJudgmentOutput
        )
        
        # 使用任务完成判断的提示词格式
        reflector_prompt = get_task_completion_judge_prompt_with_format(
            self.output_parser
        )
        
        # 仅打印摘要，全文留到 DEBUG 便于排查
        logger.info("[REFLECT] 传入 Prompt 摘要: System %d 字 | Human %d 字", len(reflector_prompt), len(reflect_prompt))
        logger.debug("[REFLECT] 传入的 Prompt (Human 全文): %s", reflect_prompt)
        
        # 调用LLM
        messages = [
            SystemMessage(content=reflector_prompt),
            HumanMessage(content=reflect_prompt),
        ]
        
        response = self.node_impl.llm.invoke(messages)
        raw_output = response.content if hasattr(response, 'content') else str(response)
        
        # 使用公共的 parse_with_retry 方法
        result = parse_with_retry(
            text=raw_output,
            output_parser=self.output_parser,
            llm_invoke=self.node_impl.llm.invoke,
            max_retry=3,
            verbose=self.node_impl.verbose
        )
        
        if result:
            # 解析判断结果
            result_dict = result.model_dump()
            explicit_status, is_completed, is_failed, needs_replan, final_result = (
                self._parse_judgment_result_v2(result_dict, state)
            )
            reflection = str(result_dict)
            
            # 确定下一步操作
            return self._determine_next_action(
                state=state,
                response=response,
                reflection=reflection,
                explicit_status=explicit_status,
                is_completed=is_completed,
                is_failed=is_failed,
                needs_replan=needs_replan,
                final_result=final_result,
                hard_check_result=hard_check_result,
                latest_snapshot_text=latest_snapshot_text,
                statistics=statistics,
                is_step_reflection=is_step_reflection
            )
        else:
            # 解析失败，使用简单规则判断
            logger.error(f"反思节点错误: {response}结果解析失败")
            return self._build_fallback_response(statistics, state, response)

    def _build_fallback_response(
        self,
        statistics: dict,
        state: BrowserAgentState,
        response: Any
    ) -> Dict[str, Any]:
        """构建回退响应（当解析失败时）
        
        Args:
            statistics: 执行统计数据
            state: 浏览器Agent状态
            response: LLM响应对象
            
        Returns:
            状态更新字典
        """
        completed_count = statistics['completed_count']
        failed_count = statistics['failed_count']
        total_steps = statistics['total_steps']
        
        if completed_count == total_steps and failed_count == 0:
            return {
                "reflection": f"所有{total_steps}个步骤已成功完成",
                "task_completion_judgment": f"所有{total_steps}个步骤已成功完成",
                "final_result": f"所有{total_steps}个步骤已成功完成",
                "phase": BrowserAgentPhase.COMPLETED.value,
                "needs_replan": False,
            }
        
        return {
            "last_error": f"{response}结果解析失败",
            "error_count": state.get("error_count", 0) + 1,
        }