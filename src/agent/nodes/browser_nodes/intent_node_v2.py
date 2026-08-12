"""
任务意图节点（v2）

在 Observe 之前运行，分析当前任务与此前任务是否存在关联性：
- 有关联：保留已打开的浏览器窗口（不清理标签页）
- 无关联：关闭多余标签页，只保留一个，确保干净环境

该节点作为 graph 的入口节点：START -> intent -> observe -> ...
"""
import logging
import re
from typing import Dict, Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.types.state_types import BrowserAgentState
from src.agent.prompts.browser_prompts import INTENT_TASK_RELATION_PROMPT_TEMPLATE
from src.services import get_llm_provider
from src.services.session_context_service import format_messages_as_context
from src.agent.utils.agent_flow_logger import log_phase_start, log_phase_result

logger = logging.getLogger(__name__)


MAX_BROWSER_STATE_TABS = 8
MAX_URL_LEN = 80


def _fetch_browser_state_for_intent(mcp_client) -> str:
    """获取当前浏览器状态，供意图分析使用。

    在 Intent 节点内调用，用于让 LLM 感知当前打开的标签页和 URL，辅助判断任务关联性。

    Returns:
        格式化的浏览器状态字符串，如 "2 个标签: 1: https://www.baidu.com/ [选中], 2: https://www.dongchedi.com/"
        失败或未连接时返回空字符串
    """
    if not mcp_client or not mcp_client.is_connected():
        return ""
    try:
        result = mcp_client.list_pages()
        pages: list = []
        if isinstance(result, dict):
            if "pages" in result:
                pages = result["pages"]
            elif "content" in result:
                for item in result.get("content", []):
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text", "")
                        if "## Pages" in text or "Pages" in text:
                            for line in text.split("\n"):
                                match = re.search(r"(\d+):\s+([^\s\]]+)(?:\s+\[(selected)\])?", line.strip())
                                if match:
                                    pid, url, selected = match.group(1), match.group(2), bool(match.group(3))
                                    url_short = url[:MAX_URL_LEN] + "..." if len(url) > MAX_URL_LEN else url
                                    pages.append({"pageId": pid, "url": url_short, "selected": selected})
                            break
        if not pages:
            return ""
        lines = []
        for i, p in enumerate(pages[:MAX_BROWSER_STATE_TABS]):
            if isinstance(p, dict):
                pid = p.get("pageId", p.get("pageIdx", i + 1))
                url = p.get("url", "")
                if len(url) > MAX_URL_LEN:
                    url = url[:MAX_URL_LEN] + "..."
                sel = " [选中]" if p.get("selected", False) else ""
                lines.append(f"{pid}: {url}{sel}")
            else:
                lines.append(str(p))
        return f"{len(pages)} 个标签: " + ", ".join(lines)
    except Exception as e:
        logger.debug(f"[Intent] 获取浏览器状态失败: {e}")
        return ""


def _parse_page_ids_from_list_pages_result(result: dict) -> list:
    """从 list_pages 的返回结果中解析出所有 pageId 列表"""
    ids = []
    content = result.get("content", [])
    if not isinstance(content, list):
        return ids
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text", "")
        in_pages_section = False
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("## Pages"):
                in_pages_section = True
                continue
            if in_pages_section and re.match(r"^\d+:\s", line):
                try:
                    pid = int(line.split(":", 1)[0].strip())
                    ids.append(pid)
                except ValueError:
                    pass
    return ids


def _ensure_single_browser_tab_sync(mcp_client) -> None:
    """同步关闭多余标签页，只保留第一个（用于 graph 节点内调用）"""
    try:
        if not mcp_client or not mcp_client.is_connected():
            return
        result = mcp_client.list_pages()
        page_ids = _parse_page_ids_from_list_pages_result(result)
        if len(page_ids) <= 1:
            return
        to_close = sorted(page_ids)[1:]
        for page_id in to_close:
            try:
                mcp_client.close_page(page_id)
                logger.info(f"[Intent] 已关闭多余标签页: {page_id}")
            except Exception as e:
                logger.warning(f"[Intent] 关闭标签页 {page_id} 失败: {e}")
    except Exception as e:
        logger.warning(f"[Intent] 清理多余标签页时出错: {e}")


def _navigate_remaining_tab_to_blank(mcp_client) -> None:
    """将当前保留的唯一标签页导航到 about:blank，避免无关联任务从错误站点（如汽车之家）起步执行懂车帝等任务。"""
    try:
        if not mcp_client or not mcp_client.is_connected():
            return
        mcp_client.navigate("about:blank")
        logger.info("[Intent] 已将被保留的标签页重置为 about:blank，便于新任务从空白页开始导航。")
    except Exception as e:
        logger.warning(f"[Intent] 将保留标签页导航至 about:blank 失败: {e}")


def _analyze_task_relation(
    current_task: str,
    conversation_context: Optional[str],
    browser_state: Optional[str] = None,
) -> bool:
    """分析当前任务与此前对话是否有关联。True=有关联保留窗口，False=无关联可清理

    Args:
        current_task: 当前用户任务
        conversation_context: 此前对话历史（用户+助手）
        browser_state: 当前浏览器状态（已打开标签页及 URL），可选
    """
    if not (conversation_context and conversation_context.strip()):
        return False
    try:
        llm = get_llm_provider().llm
        browser_part = (browser_state or "").strip() or "（无法获取或暂无标签页）"
        prompt = INTENT_TASK_RELATION_PROMPT_TEMPLATE.format(
            prior_conversation=conversation_context.strip(),
            browser_state=browser_part,
            current_task=current_task.strip(),
        )
        logger.info(f"------debug---------> 意图分析 prompt: {prompt}")
        response = llm.invoke([
            SystemMessage(content="你只输出一个字：是 或 否，不要输出其他内容。"),
            HumanMessage(content=prompt),
        ])
        text = (response.content or "").strip()
        head = text[:3] if len(text) >= 3 else text
        is_related = head.startswith("是") or ("是" in text and not head.startswith("否"))
        return is_related
    except Exception as e:
        logger.warning(f"[Intent] 意图分析失败，默认无关联: {e}")
        return False


class IntentNode:
    """任务意图节点
    
    职责：
    - 分析当前任务与此前任务是否有关联
    - 无关联时关闭多余标签页，只保留一个
    - 有关联时保留已打开的窗口
    """
    
    def __init__(self, implementation):
        """初始化
        
        Args:
            implementation: 节点实现对象（BrowserNodeManager 实例）
        """
        self.node_impl = implementation
    
    def intent(self, state: BrowserAgentState) -> Dict[str, Any]:
        """意图分析节点：判断任务关联性，必要时清理标签页
        
        Args:
            state: 当前状态
            
        Returns:
            状态更新（本节点一般只产生副作用，返回空或最小更新）
        """
        if self.node_impl.verbose:
            log_phase_start("intent", extra={"任务": state.get("user_task") or ""})
        user_task = state.get("user_task", "") or ""
        messages = state.get("messages", [])
        conversation_context = format_messages_as_context(messages)
        browser_state = _fetch_browser_state_for_intent(self.node_impl.mcp_client)

        is_related = _analyze_task_relation(
            user_task, conversation_context, browser_state
        )
        
        if is_related:
            if self.node_impl.verbose:
                log_phase_result("intent", "任务与此前有关联，保留浏览器窗口")
        else:
            if self.node_impl.verbose:
                log_phase_result("intent", "任务与此前无关联，清理多余标签页")
            _ensure_single_browser_tab_sync(self.node_impl.mcp_client)
            # 将保留的唯一样签页重置为 about:blank，避免任务为懂车帝等而当前页为汽车之家时从错误站点起步
            _navigate_remaining_tab_to_blank(self.node_impl.mcp_client)
        
        # 将意图分析结果写入 state，供规划节点作为上下文使用
        return {"intent_task_related": is_related}
