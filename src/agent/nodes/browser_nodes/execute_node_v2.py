"""
Execute Node 节点实现（v2 - browser-use 模式）

browser-use 风格执行：LLM 每步自主决定动作，输出 evaluation、memory、next_goal、action。
"""
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.schema.browser_use_models import (
    BrowserUseAgentOutput,
    action_dict_to_tool_call,
    get_uid_dependent_tool_names,
)
from src.agent.prompts.browser_prompt_utils import get_browser_use_system_prompt
from src.agent.prompts.browser_prompts import BROWSER_USE_STATE_MESSAGE_TEMPLATE
from src.services.session_context_service import format_messages_as_context
from src.agent.utils.agent_flow_logger import log_phase_start, log_flow_milestone

logger = logging.getLogger(__name__)

def _is_autohome_search_aggregate_url(url: str) -> bool:
    """汽车之家搜索聚合页（sou.autohome.com.cn/zonghe）不包含单帖正文与评论，需先点入详情页。"""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    return "sou.autohome.com.cn" in u and "/zonghe" in u


def _build_autohome_page_type_hint(current_url: str) -> str:
    """对模型注入强约束：在搜索聚合页禁止直接抽取帖子，必须先点击进入详情页。"""
    if not _is_autohome_search_aggregate_url(current_url):
        return ""
    return (
        "\n<autohome_page_type_hint>\n"
        "【重要】当前页面是汽车之家「搜索结果聚合页」（sou.autohome.com.cn/zonghe），不是具体帖子详情页。\n"
        "- 禁止在该聚合页直接调用 browser_extract_autohome_post_detail（通常会导出空壳 JSON：title/content/comments 为空）\n"
        "- 正确做法：先在列表中点击一条「论坛/帖子/资讯」进入详情页（URL 会变化为具体内容页），再调用 browser_extract_autohome_post_detail 抽取正文与评论\n"
        "- 若步数不足：可直接基于当前快照内容进行总结，并填写 task_complete 交付阶段性结果\n"
        "</autohome_page_type_hint>\n"
    )


def _extract_uid_prefix_from_snapshot(snapshot: str) -> Optional[str]:
    """从快照中提取 uid 前缀（如 37_），用于提示 LLM 仅使用当前快照的 uid"""
    if not snapshot or not isinstance(snapshot, str):
        return None
    m = re.search(r"uid=(\d+)_", snapshot)
    return f"{m.group(1)}_" if m else None


def _build_uid_prefix_hint(snapshot: str) -> str:
    """构建 uid 前缀提示，强调必须从当前快照取 uid"""
    prefix = _extract_uid_prefix_from_snapshot(snapshot)
    if prefix:
        return f"【当前快照 uid 前缀】{prefix}。browser_click/browser_fill 的 uid 必须来自下方快照且以 {prefix} 开头，禁止使用 agent_history 中的历史 uid。\n"
    return "【重要】browser_click/browser_fill 的 uid 必须来自下方「页面快照」，禁止使用 agent_history 中的历史 uid。\n"


# 历史中助手消息里 uid 的脱敏占位，避免模型从历史复制过期 uid
UID_REDACT_PLACEHOLDER = "<来自旧快照>"
_UID_VALUE_PATTERN = re.compile(r'"uid"\s*:\s*"\d+_\d+"')

def _redact_uid_in_ai_messages(messages: List) -> List:
    """对 AIMessage 中的 "uid": "N_M" 脱敏为 "uid": "<来自旧快照>"，避免模型抄袭历史 uid"""
    out: List = []
    for m in messages:
        if isinstance(m, AIMessage):
            raw = str(m.content)
            redacted = _UID_VALUE_PATTERN.sub(
                f'"uid": "{UID_REDACT_PLACEHOLDER}"', raw
            )
            out.append(AIMessage(content=redacted))
        else:
            out.append(m)
    return out


# 上一步工具结果中表示「明确失败」的特征（通用：仅强信号，避免误伤；新增工具无需改此处）
_LAST_TOOL_ERROR_INDICATORS = (
    "错误",
    "失败",
    "exception",
    "traceback",
    '"success": false',
    '"success":false',
)


def _last_tool_result_looks_success(content: str) -> bool:
    """通用判断：上一步工具返回是否视为成功。不依赖具体工具名，新增工具无需改此处。
    规则：含错误特征则失败；JSON 有 success 则依其值、有 status 则依 status；否则默认成功。"""
    if not (content or "").strip():
        return False
    raw = content.strip()
    text_lower = raw.lower()
    for ind in _LAST_TOOL_ERROR_INDICATORS:
        if ind in text_lower or ind in raw:
            return False
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if "success" in data:
                return data.get("success") is True
            status = str(data.get("status", "")).lower()
            if status in ("error", "failed", "failure"):
                return False
            if status:
                return status in ("success", "ok", "true")
        except Exception:
            pass
    return True


def _last_tool_success_but_missing_file_path(last_name: str, raw: str) -> bool:
    """上一步为下载/保存类工具且返回 success 但 file_path/file_name 为空时返回 True，用于提示先重试一次。"""
    if not raw or not last_name:
        return False
    name_lower = last_name.strip().lower()
    is_download_save = any(
        kw in name_lower for kw in ("fetch_and_download", "download_douyin", "download_")
    ) or "download" in name_lower and "douyin" in name_lower
    if not is_download_save:
        return False
    # 检测 "file_path": null 或 "file_name": null（允许少量空格）
    if '"file_path"' not in raw and '"file_name"' not in raw:
        return False
    has_null_path = (
        re.search(r'"file_path"\s*:\s*null', raw) or re.search(r'"file_name"\s*:\s*null', raw)
    )
    if not has_null_path:
        return False
    try:
        data = json.loads(raw)
        if data.get("success") is not True:
            return False
        inner = data.get("data") or data
        fp = inner.get("file_path") if isinstance(inner, dict) else None
        fn = inner.get("file_name") if isinstance(inner, dict) else None
        return (fp is None or fp == "") or (fn is None or fn == "")
    except Exception:
        return True  # 已匹配 null 模式且为下载类工具，保守提示重试


def _build_last_tool_success_hint(messages: List) -> str:
    """上一步有工具返回且结果未报错时：通用提示由模型根据 <user_request> 与上一步结果自行判断是否填 task_complete。"""
    if not messages:
        return ""
    last = messages[-1]
    if not isinstance(last, ToolMessage):
        return ""
    raw = (getattr(last, "content", None) or "") if hasattr(last, "content") else str(last)
    if not raw or not _last_tool_result_looks_success(raw):
        return ""
    no_repeat_hint = (
        "**请根据 <user_request> 与上一步工具返回结果自行判断任务是否已完成**。"
        "若已完成，则**必须**在本步 JSON 中填 task_complete（text、success）并置 action 为 []；若未完成，则执行下一步逻辑，勿以相同参数重复调用上一步已执行过的工具。\n"
    )
    same_action_hint = (
        "**勿因快照/uid 变化而重复同一逻辑操作**：上一步工具已返回后，本步快照可能已刷新。"
        "请依据 agent_history 中上一步工具结果与 <user_request> 判断是否已达成目标；若已达成则填 task_complete。\n"
    )
    name = (getattr(last, "name", None) or "").strip().lower()
    is_content_output = any(kw in name for kw in ("extract", "analyze", "fetch", "download"))
    format_hint = (
        "若上一步为内容提取/分析/下载类工具，请用 **Markdown** 书写 task_complete.text（如 ## 标题、要点列表、保存路径），并汇总关键结果。\n"
        if is_content_output else ""
    )
    # 下载/保存类工具返回 success 但 file_path/file_name 为空时，引导进行下一步而非无限重试
    retry_file_path_hint = ""
    if _last_tool_success_but_missing_file_path(getattr(last, "name", None) or "", raw):
        retry_file_path_hint = (
            "**若上一步为下载/保存类工具且返回 success 但 file_path/file_name 为空**：可视为已保存到指定或默认目录，请**直接进行下一步**（如调用后续工具时使用该目录或任务中指定的路径），"
            "**勿重复调用同一下载工具**。\n"
        )
    return (
        "\n<last_step_success>\n"
        "【上一步工具已返回且未报错】请根据 <user_request> 与上一步工具结果**自行判断**：若任务已满足用户要求则填 task_complete 并置 action 为 []；否则继续下一步。\n"
        f"{no_repeat_hint}"
        f"{same_action_hint}"
        f"{format_hint}"
        f"{retry_file_path_hint}"
        "</last_step_success>\n"
    )


def _build_reflection_section(state: BrowserAgentState) -> str:
    """构建「上次反思结论与建议」区块。仅在存在反思内容且为重新规划后的执行时展示，供本轮按建议重新完成任务。"""
    reflection = (state.get("reflection") or "").strip()
    task_completion_judgment = (state.get("task_completion_judgment") or "").strip()
    content = task_completion_judgment or reflection
    if not content:
        return ""
    plan_version = state.get("plan_version", 0)
    if plan_version == 0:
        return ""
    # 限制长度，避免挤占快照与历史
    max_content = 1200
    if len(content) > max_content:
        content = content[:max_content] + "\n[已截断...]"
    return (
        "\n<reflection_and_suggestions>\n"
        "【上次反思结论与建议】\n"
        f"{content}\n\n"
        "请按上述建议重新尝试完成任务（如尝试车友圈/社区、换关键词等），勿直接再次 task_complete(success=false)。\n"
        "</reflection_and_suggestions>\n"
    )


def _build_saved_results_section(state: BrowserAgentState) -> str:
    """构建「上一任务已保存的结果」区块：来自哪个工具、什么结果类型、保存的绝对路径。供续任务（如继续进行 VOC 分析）时明确引用文件。"""
    prior = state.get("prior_saved_results") or []
    if not prior:
        return ""
    lines = [
        "\n<saved_results_from_prior_task>",
        "【上一任务中已保存的结果】以下文件来自上一任务中工具产生，可直接用于本任务（如 VOC 分析请使用下方 input_file 路径）：",
    ]
    for item in prior:
        tool_name = item.get("tool_name") or "未知工具"
        result_type = item.get("result_type") or "保存的文件"
        path = item.get("path") or ""
        if path:
            lines.append(f"- 工具 {tool_name}：{result_type}；保存路径（绝对）：{path}")
    lines.append("</saved_results_from_prior_task>\n")
    return "\n".join(lines)


def _build_history_section(
    messages: List,
    max_chars: int = 3000,
    head_chars: int = 600,
    tail_chars: int = 2200,
) -> str:
    """构建历史步骤描述，必须包含工具执行结果，否则 LLM 无法感知上一步成功/失败。
    助手消息中的 uid 会脱敏。截断时保留首尾、中间按长度省略，且侧重尾部（tail_chars > head_chars），确保近期步骤与上一步结果在上下文中。"""
    if not messages:
        return ""
    messages = _redact_uid_in_ai_messages(messages)
    ctx = format_messages_as_context(messages, include_tool_results=True)
    if len(ctx) <= max_chars:
        return f"\n<agent_history>\n{ctx}\n</agent_history>\n"
    # 首尾保留，中间省略；尾部保留更多（tail_chars > head_chars），便于根据上一步推理下一步
    middle_marker = "\n[... 中间内容已省略 ...]\n"
    marker_len = len(middle_marker)
    tail_budget = min(tail_chars, max_chars - head_chars - marker_len)  # 总长不超 max_chars
    head = ctx[:head_chars]
    tail = ctx[-tail_budget:]
    ctx = head + middle_marker + tail
    return f"\n<agent_history>\n{ctx}\n</agent_history>\n"


def _build_error_section(last_error: Optional[str], max_chars: int = 500) -> str:
    """构建「上一步结果」区块：错误原文 + 一句通用原则。与 agent_history 末尾呼应，不按错误类型再分支。"""
    if not last_error or not last_error.strip():
        return ""
    err = last_error.strip()[:max_chars]
    if len(last_error) > max_chars:
        err += "..."
    return (
        f"\n<last_error>\n"
        f"⚠️ 上一步执行失败：\n{err}\n"
        "请根据上述错误调整策略（如先 browser_snapshot 再选新 uid、换元素或换页面），勿用相同参数重试。\n"
        "</last_error>\n"
    )


def _is_blank_page_url(url: Optional[str]) -> bool:
    """是否为浏览器空白页 URL（about:blank 或 about:blank/），不区分大小写、忽略末尾斜杠。"""
    if url is None:
        return False
    u = str(url).strip().rstrip("/").lower()
    return u == "about:blank"


def _build_state_override_hint(state: BrowserAgentState) -> str:
    """构建状态覆盖提示，减少陈旧 memory 对决策的干扰"""
    current_url = (state.get("current_url") or "").strip()
    if not current_url or current_url == "未知" or _is_blank_page_url(current_url):
        return ""
    messages = state.get("messages", [])
    if not messages or len(messages) < 4:
        return ""
    return (
        "\n<state_override_hint>\n"
        "【重要】browser_state 为当前最新状态。若上文 agent_history 中的 memory 与当前 URL/页面不符（如历史写「空白页」而当前 URL 已变化），以 browser_state 为准，勿受陈旧 memory 影响。\n"
        "</state_override_hint>\n"
    )


def _get_current_selected_page_id(state: BrowserAgentState) -> Optional[int]:
    """从 state 的 pages_info 中取当前已选中的 page_id（selected=True）。"""
    pages_info = state.get("pages_info") or {}
    for pid, info in pages_info.items():
        if info.get("selected") is True:
            try:
                return int(pid)
            except (ValueError, TypeError):
                return None
    return None


def _build_active_page_id_hint(state: BrowserAgentState) -> str:
    """构建「当前已选页面」提示，让 LLM 明确知道当前在哪一页，避免重复调用 browser_select_page 选择同一页。"""
    page_id = _get_current_selected_page_id(state)
    if page_id is None:
        return ""
    return (
        f"当前已选页面: page_id={page_id}。"
        "若你本步要操作的正是该页，**无需再次调用 browser_select_page**，直接进行后续操作（如 browser_snapshot、browser_click 等）。\n"
    )


def _build_observation_result(state: BrowserAgentState) -> str:
    """构建观察结果文案，供 <observation_result> 使用。
    从 state 读取 Observe 节点已写入的检测结果（新页面、弹窗等），不在此执行检测。
    """
    parts = []

    # 新页面检测（Observe 节点写入 new_page_id / new_page_url）
    new_page_id = state.get("new_page_id")
    new_page_url = (state.get("new_page_url") or "").strip()
    if new_page_id and new_page_url and not _is_blank_page_url(new_page_url):
        url_preview = str(new_page_url)[:80] + "..." if len(str(new_page_url)) > 80 else str(new_page_url)
        parts.append(
            "【本步必须执行·最高优先级】\n"
            "检测到新页面已打开（通常是点击搜索或链接后）。\n"
            f"- 新页面: page_id={new_page_id}, URL={url_preview}\n"
            f"- **本步 action 有且仅有**：browser_select_page(pageId={new_page_id})\n"
            f"- **本步 next_goal 应写**：切换至新页面 page_id={new_page_id}，下一轮再执行其他操作（如提取、点击等）\n"
            "- **禁止**本步输出：browser_click、browser_fill、browser_snapshot、browser_extract_*（当前快照为旧页，切换后下一轮才有新快照）\n"
            "- 违反将导致重复点击同一按钮的循环。下一轮切换成功后，你将看到新页快照，再继续任务。\n"
        )

    # 弹窗/广告检测结果（Observe 节点写入 popup_hint）
    # 若已检测到新页面，说明点击已生效（如搜索打开新 tab），不再注入 popup_hint，避免误导 agent 重复点击
    # 若上一步 browser_click_by_vision 已成功，则弹窗应已关闭；快照中仍含 popup 等词多为残余 DOM 或页面内容，
    # 此时抑制 popup_hint，避免误导 agent 重复关弹窗
    popup_hint = state.get("popup_hint")
    last_success = _get_last_successful_tool_name_and_args(state.get("messages", []))
    suppress_popup_for_vision_click = (
        last_success is not None
        and (last_success[0] or "").strip() == "browser_click_by_vision"
    )
    if popup_hint and not (new_page_id and new_page_url):
        if suppress_popup_for_vision_click:
            logger.info("[Execute] observation_result: 上一步 browser_click_by_vision 已成功，抑制 popup_hint 避免误导")
        else:
            logger.info("[Execute] observation_result: 包含 popup_hint (弹窗检测到)，将注入提示")
            parts.append(popup_hint)
    elif popup_hint and (new_page_id and new_page_url):
        logger.info("[Execute] observation_result: 检测到新页面，跳过 popup_hint 避免误导 agent 重复点击")
    elif not popup_hint:
        logger.info("[Execute] observation_result: popup_hint 为空，不注入弹窗提示")

    if parts:
        return "\n".join(parts)
    return "本轮观察：未检测到新页面；当前快照与上方「当前URL」对应。"


def _build_iteration_hint(iteration_count: int, max_iterations: int) -> str:
    """与 browser-use 一致：75% 步数预算提醒（仅当非最后一步时）；剩余步数提醒"""
    if max_iterations <= 0:
        return ""
    remaining = max_iterations - iteration_count
    if remaining <= 0:
        return ""
    is_last = remaining == 1
    parts = []
    # browser-use: 75% 且非最后一步时注入 BUDGET WARNING（prioritize consolidate + call done）
    if iteration_count >= 0.75 * max_iterations and not is_last:
        pct = int(100 * iteration_count / max_iterations) if max_iterations else 0
        parts.append(
            f"\n<iteration_hint>\n"
            f"BUDGET WARNING: 你已使用 {iteration_count}/{max_iterations} 步（约 {pct}%），剩余 {remaining} 步。"
            f"若无法在剩余步数内完成用户请求，请优先：(1) 整合已有结果（若有文件则保存），(2) 填写 task_complete 交付当前结果。"
            f"部分结果远优于耗尽步数却无交付。\n"
            f"</iteration_hint>\n"
        )
    if remaining <= 3:
        parts.append(
            f"\n<iteration_hint>\n"
            f"⚠️ 剩余迭代次数仅 {remaining} 次，请尽快完成关键操作或填写 task_complete 结束任务。\n"
            f"</iteration_hint>\n"
        )
    elif remaining <= 5 and not parts:
        parts.append(
            f"\n<iteration_hint>\n"
            f"当前已执行 {iteration_count}/{max_iterations} 步，剩余 {remaining} 次，请推进任务。\n"
            f"</iteration_hint>\n"
        )
    return "".join(parts) if parts else ""


def _is_last_iteration(iteration_count: int, max_iterations: int) -> bool:
    """与 browser-use is_last_step 一致：当前步是否为最后允许的一步"""
    return max_iterations > 0 and iteration_count >= max_iterations - 1


def _build_last_step_force_done_hint(iteration_count: int, max_iterations: int) -> str:
    """最后一步时注入：必须填 task_complete 结束，勿再调用工具。"""
    if not _is_last_iteration(iteration_count, max_iterations):
        return ""
    return (
        "\n<last_step_force_done>\n"
        "你已到达 max_steps，这是最后一步。请填写 task_complete（text、success），并将 action 置为 []，勿再调用任何工具。\n"
        "若任务尚未按用户要求完全完成，请将 success 设为 false；否则设为 true。将你为本次任务所获得的全部信息写入 text。\n"
        "</last_step_force_done>\n"
    )


def _build_anti_repetitive_loop_hint(state: BrowserAgentState) -> str:
    """构建防重复执行循环提示。
    当 last_error 含 stale snapshot 时，不禁止 snapshot，优先提示先快照再操作。
    """
    last_error = (state.get("last_error") or "").lower()
    if any(kw in last_error for kw in ["stale snapshot", "过期的快照", "take_snapshot"]):
        return (
            "\n<stale_snapshot_hint>\n"
            "⚠️ 上一步 fill/click 因快照过期失败。**必须先** browser_snapshot 获取新快照，再从新快照取 uid 执行 fill/click。\n"
            "</stale_snapshot_hint>\n"
        )

    tool_records = state.get("tool_call_records", [])
    if not tool_records:
        return ""
    try:
        from src.agent.config.agent_config import BrowserAgentConfig
        cfg = BrowserAgentConfig.from_settings()
        preparatory = set(cfg.tool_classification.preparatory_tools or ["browser_snapshot"])
        action_types = set(cfg.tool_classification.action_tools_need_uid or ["fill", "click", "hover", "drag"])
    except Exception:
        preparatory = {"browser_snapshot"}
        action_types = {"fill", "click", "hover", "drag"}

    def _tool_name(rec) -> str:
        return getattr(rec, "tool_name", None) or (rec.get("tool_name", "") if isinstance(rec, dict) else "")

    repeated_tool = None
    consecutive_count = 0
    for rec in reversed(tool_records):
        name = _tool_name(rec)
        if name in preparatory:
            repeated_tool = name
            consecutive_count += 1
        else:
            break

    if consecutive_count < 1 or not repeated_tool:
        return ""

    if consecutive_count >= 2:
        return (
            "\n<anti_repetitive_loop>\n"
            f"⚠️ 已连续 {consecutive_count} 次执行 {repeated_tool}，可能陷入循环。"
            "请勿再次调用该工具，转而执行能推进任务的操作（如 browser_click、browser_fill），"
            "或尝试 browser_list_pages → browser_select_page 切换页面后继续。\n"
            "</anti_repetitive_loop>\n"
        )
    return (
        "\n<anti_repetitive_loop>\n"
        f"⚠️ 上一步已执行 {repeated_tool}，状态已更新。"
        f"请直接执行下一步操作（如 browser_click、browser_fill），禁止再次调用 {repeated_tool}。\n"
        "</anti_repetitive_loop>\n"
    )


# 合法顶层字段（BrowserUseAgentOutput），非此则可能是「单 action」形态
_AGENT_OUTPUT_TOP_KEYS = frozenset(
    {"evaluation_previous_goal", "memory", "next_goal", "action", "task_complete"}
)


def _normalize_agent_output_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """解析前规范化：根级或误放在 action 内的 task_complete 提到根并清空 action；LLM 只返回单 action 时包成完整结构。"""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    if data.get("task_complete") is not None:
        data["action"] = []
        return data
    # LLM 常将 task_complete 误放在 action 内（如 action: [{"task_complete": {...}}]），提到根并清空 action
    action = data.get("action")
    if isinstance(action, list):
        for item in action:
            if isinstance(item, dict) and "task_complete" in item and len(item) == 1:
                data["task_complete"] = item["task_complete"]
                data["action"] = []
                return data
    # LLM 有时只返回单个 action 对象（如单一 tool 调用），缺少顶层字段，包成完整结构
    keys = set(data.keys())
    if len(keys) == 1:
        (only_key,) = keys
        if only_key.startswith("browser_") and only_key not in _AGENT_OUTPUT_TOP_KEYS:
            data = {
                "evaluation_previous_goal": "",
                "memory": "",
                "next_goal": "",
                "action": [data],
            }
    return data


def _parse_agent_output(text: str) -> Optional[BrowserUseAgentOutput]:
    """从 LLM 输出解析 JSON，支持 ```json 包裹。解析前规范化 task_complete（含误放在 action 内的情况）并清空 action。"""
    
    logger.info(f"------debug---------> text: \n{text}")
    if not text or not text.strip():
        return None
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
        data = _normalize_agent_output_data(data)
        return BrowserUseAgentOutput(**data)
    except Exception as e:
        logger.warning(f"[BrowserUse] 解析 AgentOutput 失败: {e}")
        return None


def _norm_url_for_compare(url: str) -> str:
    """将 URL 规范化为可比较形式：协议统一为 https、去除末尾斜杠，避免 http/https 或尾斜杠导致同帖被判为不同参数。"""
    if not url or not isinstance(url, str):
        return url or ""
    s = url.strip()
    if s.startswith("http://"):
        s = "https://" + s[7:]
    if s.endswith("/"):
        s = s[:-1]
    return s


def _summarize_snapshot_for_user_query(
    llm: Any,
    user_task: str,
    page_snapshot: str,
    current_url: str,
    max_snapshot_input: int = 120000,
) -> Optional[str]:
    """无可用工具时：用 LLM 按用户任务对页面快照做整理性输出；失败返回 None。"""
    snap = (page_snapshot or "").strip()
    if not snap:
        return None
    if len(snap) > max_snapshot_input:
        snap = snap[:max_snapshot_input] + f"\n\n[快照已截断供整理，原长度 {len(page_snapshot)} 字符]"
    system = (
        "你是助手。当前没有可用的浏览器/MCP 工具继续自动完成任务；你只根据下方「页面快照」文本，围绕「用户任务」进行整理与作答。\n"
        "要求：\n"
        "- 使用中文 Markdown，结构清晰（小标题、列表、表格等按需使用）。\n"
        "- 紧扣用户任务提炼快照中的相关信息；若快照不足以完成任务，明确说明局限与缺失项。\n"
        "- 严禁编造快照中不存在的内容。\n"
        "- 不要输出 JSON，不要复述本系统说明。"
    )
    human = (
        f"用户任务：\n{user_task or '（无）'}\n\n"
        f"当前页面 URL：{current_url or '未知'}\n\n"
        f"页面快照：\n{snap}"
    )
    try:
        resp = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=human),
        ])
        out = (resp.content or "").strip()
        if not out:
            return None
        if out.startswith("```"):
            lines = out.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            out = "\n".join(lines).strip()
        return out or None
    except Exception as e:
        logger.warning("[BrowserUse] 无工具兜底：按任务整理快照失败: %s", e)
        return None


def _build_fallback_result_no_suitable_tool(
    page_snapshot: str,
    current_url: str,
    user_task: str,
    *,
    llm: Optional[Any] = None,
    max_snapshot_for_llm: int = 120000,
    max_raw_fallback_chars: int = 16000,
) -> str:
    """无可用工具时：按用户任务整理最新快照并注明无对应工具；整理失败则附快照节选。"""
    head_lines = [
        "【说明】**未找到可完成当前请求的工具**；以下为根据**当前最新页面快照**、**围绕用户任务整理**的结果。",
        "",
        f"**当前 URL**：{current_url or '未知'}",
        f"**用户任务**：{user_task or '（无）'}",
        "",
    ]
    raw = (page_snapshot or "").strip()
    if not raw:
        head_lines.append(
            "**页面内容**：当前无可用快照。若需基于页面作答，请在前序步骤中执行 `browser_snapshot` 获取页面结构后再试。"
        )
        return "\n".join(head_lines)

    organized: Optional[str] = None
    if llm is not None:
        organized = _summarize_snapshot_for_user_query(
            llm, user_task, raw, current_url, max_snapshot_input=max_snapshot_for_llm
        )
    if organized:
        logger.info("[BrowserUse] 无工具兜底：已按用户任务对快照做 LLM 整理")
        return "\n".join(head_lines) + "\n**整理结果**：\n\n" + organized

    logger.info("[BrowserUse] 无工具兜底：使用快照原文节选（整理不可用或未配置 LLM）")
    body = (
        raw
        if len(raw) <= max_raw_fallback_chars
        else raw[:max_raw_fallback_chars] + f"\n\n[快照已截断，原长度 {len(page_snapshot)} 字符]"
    )
    head_lines.append("**说明**：无法自动生成围绕任务的整理时，附页面快照原文节选如下。\n")
    head_lines.append("**页面快照（节选）**：\n")
    head_lines.append(body)
    return "\n".join(head_lines)


def _norm_args_for_compare(args: Any) -> Optional[str]:
    """将参数规范化为可比较的字符串，用于判断两次调用是否一致。
    对含 url 的参数字典会先规范化 url（http→https、去尾斜杠），再序列化，避免同帖因协议或尾斜杠不同而漏判重复。"""
    if args is None:
        return None
    if isinstance(args, dict):
        d = dict(args)
        if "url" in d and isinstance(d["url"], str):
            d["url"] = _norm_url_for_compare(d["url"])
        return json.dumps(d, sort_keys=True, ensure_ascii=False)
    return str(args)


def _get_last_successful_tool_name_and_args(messages: List) -> Optional[tuple]:
    """若上一条消息是 ToolMessage 且结果表示成功，返回 (工具名, 规范化后的参数字符串)；否则返回 None。
    通过上一条 AIMessage 的 tool_calls 与 ToolMessage 的 tool_call_id 对齐得到上一步的调用参数。"""
    if not messages or len(messages) < 2:
        return None
    last = messages[-1]
    if not isinstance(last, ToolMessage):
        return None
    name = (getattr(last, "name", None) or "").strip()
    if name == "browser_done":
        return None  # 保留以兼容历史消息，browser_done 已不再作为工具使用
    raw = (getattr(last, "content", None) or "") if hasattr(last, "content") else str(last)
    if not raw or not _last_tool_result_looks_success(raw):
        return None
    tool_call_id = getattr(last, "tool_call_id", None)
    if not tool_call_id:
        return (name, None)
    for m in reversed(messages[:-1]):
        if not isinstance(m, AIMessage):
            continue
        for tc in getattr(m, "tool_calls", []) or []:
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if tc_id != tool_call_id:
                continue
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            return (name, _norm_args_for_compare(args))
        break
    return (name, None)


def _replace_repeated_successful_tool_with_done(messages: List, tool_calls: List[Dict]) -> Tuple[List[Dict], Optional[Dict[str, Any]]]:
    """仅当「上一步某工具已成功」且「本步再次调用同一工具且参数完全一致」时，返回空 tool_calls 与 force_done，
    由调用方设置 phase/final_result 结束任务（不再使用 browser_done 工具）。"""
    last_info = _get_last_successful_tool_name_and_args(messages)
    if not last_info or not tool_calls:
        return (tool_calls, None)
    last_name, last_args_norm = last_info
    for i, tc in enumerate(tool_calls):
        if (tc.get("name") or "").strip() != last_name:
            continue
        current_norm = _norm_args_for_compare(tc.get("args"))
        if last_args_norm is not None and current_norm != last_args_norm:
            continue
        if last_args_norm is None:
            continue
        done_text = "上一步已成功执行 " + last_name + "（相同参数），当前状态已满足需求，任务完成。"
        if messages:
            last_msg = messages[-1]
            raw = (getattr(last_msg, "content", None) or "") if hasattr(last_msg, "content") else str(last_msg)
            if raw and raw.strip().startswith("{"):
                try:
                    data = json.loads(raw)
                    for key in ("summary", "message", "text", "result"):
                        val = (data.get(key) or "").strip()
                        if val and isinstance(val, str):
                            done_text = "任务已完成（由上一步工具结果自动结束）。\n\n" + key + "：\n" + (val[:3000] + "..." if len(val) > 3000 else val)
                            break
                except Exception:
                    pass
        logger.info(
            "[BrowserUse] 上一步 %s 已成功且本步再次以相同参数调用，以 task_complete 结束，避免重复循环",
            last_name,
        )
        return ([], {"text": done_text, "success": True})
    return (tool_calls, None)


def _limit_uid_dependent_tools_per_step(tool_calls: List[Dict]) -> List[Dict]:
    """每步最多执行一个依赖 uid 的工具（browser_click、browser_fill 等）。填值/点击只能串行，不能并行：
    任意操作后快照会变，同轮内第二个的 uid 会失效，故只保留第一个，其余留到下一轮基于新快照再执行。"""
    if not tool_calls:
        return tool_calls
    uid_tools = get_uid_dependent_tool_names()
    if not uid_tools:
        return tool_calls
    kept = []
    seen_uid_tool = False
    skipped_names: List[str] = []
    for tc in tool_calls:
        name = (tc.get("name") or "").strip()
        if name in uid_tools:
            if seen_uid_tool:
                skipped_names.append(name)
                continue
            seen_uid_tool = True
        kept.append(tc)
    if skipped_names:
        logger.info(
            "[BrowserUse] 填值/点击仅串行执行：本步已只保留第一个 uid 依赖工具，忽略后续 %s，下一轮将基于新快照再执行",
            skipped_names,
        )
    return kept


def _convert_actions_to_tool_calls(actions: List[Dict[str, Any]]) -> List[Dict]:
    """将 action 列表转换为 LangChain tool_call 格式"""
    result = []
    for i, action in enumerate(actions or []):
        tc = action_dict_to_tool_call(action)
        if tc:
            tc["id"] = f"browser_use_action_{i}"
            result.append(tc)
    return result


def _get_browser_use_max_iterations(state: BrowserAgentState, node_impl) -> int:
    """获取 browser_use 模式的最大迭代次数"""
    from src.config import settings
    browser_use_max = getattr(settings, "browser_use_max_iterations", 20)
    if browser_use_max > 0:
        return browser_use_max
    max_from_state = state.get("max_iterations")
    if max_from_state is not None:
        return max_from_state
    return getattr(settings, "agent_max_iterations", 50)


class ExecuteNode:
    """执行节点：browser-use 风格，LLM 每步自主决定动作"""

    def __init__(self, implementation):
        self.node_impl = implementation

    def execute(self, state: BrowserAgentState) -> Dict[str, Any]:
        """browser-use 风格执行：LLM 结构化输出 -> 转换为 tool_calls -> 返回"""
        user_task = state.get("user_task", "") or ""
        messages = state.get("messages", [])
        current_url = state.get("current_url") or "未知"
        page_snapshot = state.get("page_snapshot") or ""
        iteration_count = state.get("iteration_count", 0)
        max_iterations = _get_browser_use_max_iterations(state, self.node_impl)

        if iteration_count >= max_iterations:
            logger.warning(f"[BrowserUse] 达到最大迭代次数 {iteration_count}/{max_iterations}，终止执行")
            return {
                "phase": BrowserAgentPhase.FAILED.value,
                "last_error": f"已达到最大迭代次数 ({iteration_count}/{max_iterations})，请简化任务或分步执行",
            }

        plan_version = state.get("plan_version", 0)
        plan_round_label = "首次规划执行" if plan_version == 0 else f"第{plan_version + 1}次规划执行"
        log_flow_milestone(plan_round_label, plan_version=plan_version, task=user_task[:50])
        if self.node_impl.verbose:
            log_phase_start(
                "execute",
                extra={"模式": "browser_use", "任务": user_task[:40]},
                plan_round_label=plan_round_label,
            )

        max_snapshot = 180000
        snapshot_section = ""
        if page_snapshot:
            if len(page_snapshot) > max_snapshot:
                snapshot_section = f"页面快照（前{max_snapshot}字符）:\n{page_snapshot[:max_snapshot]}\n[已截断...]"
            else:
                snapshot_section = f"页面快照:\n{page_snapshot}"
        else:
            snapshot_section = "（无快照，请先 browser_snapshot）"

        # Debug：模型本步看到的 URL/快照摘要，用于排查「点击后仍认为要再点搜索」等重复决策
        snapshot_preview = (page_snapshot[:350] + "...").replace("\n", " ") if page_snapshot else "(无)"
        logger.info(
            "[DEBUG] Execute 本步 state: current_url=%s, page_snapshot_len=%s, 快照前350字: %s",
            current_url[:100] if current_url else "未知",
            len(page_snapshot) if page_snapshot else 0,
            snapshot_preview,
        )

        history_section = _build_history_section(messages)
        saved_results_section = _build_saved_results_section(state)
        reflection_section = _build_reflection_section(state)
        last_error = state.get("last_error") or ""
        error_section = _build_error_section(last_error)
        iteration_hint = _build_iteration_hint(iteration_count, max_iterations)
        error_replan_hint = ""  # 已合并进 error_section 的通用原则，不再单独重复
        new_page_hint = _build_observation_result(state)
        state_override_hint = _build_state_override_hint(state)
        active_page_id_hint = _build_active_page_id_hint(state)
        uid_prefix_hint = _build_uid_prefix_hint(page_snapshot)
        last_tool_success_hint = _build_last_tool_success_hint(messages)
        autohome_hint = _build_autohome_page_type_hint(current_url)

        state_message = BROWSER_USE_STATE_MESSAGE_TEMPLATE.format(
            user_task=user_task,
            current_url=current_url,
            active_page_id_hint=active_page_id_hint,
            uid_prefix_hint=uid_prefix_hint,
            snapshot_section=snapshot_section,
            state_override_hint=state_override_hint,
            new_page_hint=new_page_hint,
            reflection_section=reflection_section,
            history_section=history_section,
            saved_results_section=saved_results_section,
            error_section=error_section,
            iteration_hint=iteration_hint,
            error_replan_hint=error_replan_hint,
        )

        if autohome_hint:
            state_message = state_message + autohome_hint

        if last_tool_success_hint:
            state_message = state_message + last_tool_success_hint
            if self.node_impl.verbose:
                logger.info("[BrowserUse] 已添加上一步工具成功提示（通用），引导满足请求时填 task_complete")
        last_step_hint = _build_last_step_force_done_hint(iteration_count, max_iterations)
        if last_step_hint:
            state_message = state_message + last_step_hint
            if self.node_impl.verbose:
                logger.info("[BrowserUse] 已添加最后一步强制 task_complete 提示")
        anti_loop_hint = _build_anti_repetitive_loop_hint(state)
        if anti_loop_hint:
            state_message = state_message + anti_loop_hint
            if self.node_impl.verbose:
                logger.info("[BrowserUse] 已添加防重复执行循环提示")

        llm = self.node_impl.llm_provider.llm if hasattr(self.node_impl, 'llm_provider') else None
        if not llm:
            from src.services import get_llm_provider
            llm = get_llm_provider().llm

        tools = getattr(self.node_impl, "tools", None)
        # Prompt Caching：System 内容每轮严格一致，推理端（vLLM/SGLang/Claude 等）可缓存前缀，只算动态 Human
        system_content = state.get("execute_system_prompt_cache")
        if not system_content:
            system_content = (
                get_browser_use_system_prompt(tools=tools)
                + "\n\n你必须输出有效的 JSON，不要输出其他内容。"
            )
        logger.info(f"------debug---------> 执行SystemMessage的system_content: \n{system_content}")
        logger.info(f"------debug---------> 执行HumanMessage的state_message: \n{state_message}")
        try:
            # 统一使用 invoke + _parse_agent_output：部分 LLM 会返回 ```json ... ``` 包裹，
            # with_structured_output 无法解析，_parse_agent_output 会先剥离 markdown 再解析
            response = llm.invoke([
                SystemMessage(content=system_content),
                HumanMessage(content=state_message),
            ])
            text = (response.content or "").strip()
            output = _parse_agent_output(text)
        except Exception as e:
            logger.error(f"[BrowserUse] LLM 调用失败: {e}")
            out = {
                "last_error": str(e),
                "error_count": state.get("error_count", 0) + 1,
                "phase": BrowserAgentPhase.FAILED.value,
            }
            if system_content and not state.get("execute_system_prompt_cache"):
                out["execute_system_prompt_cache"] = system_content
            return out
        if not output:
            out = {
                "last_error": "无法解析 LLM 输出的 JSON",
                "error_count": state.get("error_count", 0) + 1,
                "phase": BrowserAgentPhase.FAILED.value,
            }
            if system_content and not state.get("execute_system_prompt_cache"):
                out["execute_system_prompt_cache"] = system_content
            return out

        tool_calls = _convert_actions_to_tool_calls(output.action or [])
        tool_calls = _limit_uid_dependent_tools_per_step(tool_calls)
        force_phase = None
        force_final_result = None
        if output.task_complete:
            tool_calls = []
            force_phase = BrowserAgentPhase.COMPLETED.value
            force_final_result = (
                output.task_complete.text
                if hasattr(output.task_complete, "text")
                else (output.task_complete.get("text", "任务完成。") if isinstance(output.task_complete, dict) else "任务完成。")
            )
        elif not tool_calls:
            # 无 action / 无可映射工具：按最新快照整理输出并结束，避免在 execute↔reflect 间空转
            force_phase = BrowserAgentPhase.COMPLETED.value
            prefix = ""
            if _is_last_iteration(iteration_count, max_iterations):
                prefix = "（本步为允许执行步数内的最后一步。）\n\n"
            force_final_result = prefix + _build_fallback_result_no_suitable_tool(
                page_snapshot, current_url, user_task, llm=llm
            )
            logger.info("[BrowserUse] 本步无可用工具调用，按页面快照整理结果并结束")
        # 最后一步：若仍有工具调用则优先执行工具；仅当未触发上方分支时需兼容（理论上无 tool_calls 已结束）
        elif _is_last_iteration(iteration_count, max_iterations):
            if self.node_impl.verbose:
                logger.info("[BrowserUse] 最后一步但本步有工具调用，优先执行工具，不强制结束")

        tool_call_objs = []
        for i, tc in enumerate(tool_calls):
            tool_call_objs.append({
                "name": tc["name"],
                "args": tc.get("args", {}),
                "id": tc.get("id", f"call_{i}"),
            })

        ai_message = AIMessage(
            content=json.dumps({
                "evaluation_previous_goal": output.evaluation_previous_goal,
                "memory": output.memory,
                "next_goal": output.next_goal,
            }, ensure_ascii=False),
            tool_calls=tool_call_objs,
        )

        result = {
            "messages": [ai_message],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
        result["execute_system_prompt_cache"] = system_content
        if force_phase:
            result["phase"] = force_phase
            result["final_result"] = force_final_result or "任务完成。"
            # 将本步模型完整输出（含 evaluation_previous_goal/memory/next_goal/action/task_complete）一并写入状态，供任务完成时返回
            result["last_step_raw_json"] = text

        return result
