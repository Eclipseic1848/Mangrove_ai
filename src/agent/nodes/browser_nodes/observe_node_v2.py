"""
观察节点（v2 - 模块化版本）

负责观察页面状态，包括：
- 捕获页面截图
- 获取页面快照
- 收集控制台消息
- 更新状态信息
"""
import logging
import json
import re
import time
from typing import Dict, Any, Optional, List

from src.agent.types.state_types import BrowserAgentState, BrowserAgentPhase
from src.agent.utils.screenshot_utils import capture_screenshot
from src.tools.mcp.chrome_devtools import create_browser_tools
from src.agent.utils.text_utils import extract_url_from_snapshot

logger = logging.getLogger(__name__)


# ==================== 常量定义 ====================

# 默认值
DEFAULT_PAGE_SNAPSHOT = "无页面快照"
DEFAULT_CONSOLE_MESSAGES = "无控制台消息"

# 控制台消息长度限制
MAX_CONSOLE_MESSAGE_LENGTH = 500

# 工具执行后、调用 list_pages 前的延迟（秒），用于等待页面稳定（如弹窗关闭动画、DOM 更新）。0 表示不延迟。
LIST_PAGES_DELAY_AFTER_TOOLS_SEC = 2.5
# 当上一步为 browser_click_by_vision（关闭弹窗）时，额外延迟（秒），弹窗关闭常伴动画需更长时间
LIST_PAGES_EXTRA_DELAY_AFTER_VISION_CLICK_SEC = 1.0


# ==================== 观察节点类 ====================

class ObserveNode:
    """观察节点（v2 - 模块化版本）
    
    负责观察页面状态，收集截图、快照和控制台消息等信息。
    """
    
    def __init__(self, implementation):
        """初始化观察节点
        
        Args:
            implementation: 节点实现对象，包含MCP客户端等依赖
        """
        self.node_impl = implementation

    # ==================== 截图相关方法 ====================

    def _capture_screenshot(self, state: BrowserAgentState) -> tuple[str, str]:
        """捕获截图并保存到临时文件夹（委托给 screenshot_utils 实现）
        
        Args:
            state: 浏览器Agent状态，用于获取截图计数器
            
        Returns:
            (base64编码的截图字符串, 截图文件相对路径)，失败返回 ("", "")
        """
        return capture_screenshot(
            self.node_impl.mcp_client,
            state,
            verbose=bool(self.node_impl.verbose),
        )

    # ==================== 快照相关方法 ====================

    def _fetch_snapshot_from_mcp(self) -> str:
        """从MCP客户端获取快照
        
        Returns:
            页面快照字符串，失败返回错误消息
        """
        try:
            from src.agent.utils.text_utils import normalize_snapshot_to_str
            snapshot_result = self.node_impl.mcp_client.take_snapshot()
            logger.info(f"debug-----1------snapshot_result: {snapshot_result}")
            page_snapshot = normalize_snapshot_to_str(snapshot_result)
            self._log_snapshot_success(page_snapshot)
            return page_snapshot
        except Exception as e:
            error_msg = f"获取快照失败: {e}"
            self._log_snapshot_error(error_msg)
            return error_msg

    def _parse_list_pages_result(self, result_str: str) -> List[Dict[str, Any]]:
        """从 browser_list_pages 的返回中解析出页面列表。
        支持：1) 包装 JSON data.content 中的 Markdown；2) 直接 JSON 的 pages/list；3) 纯文本 Markdown。
        """
        pages: List[Dict[str, Any]] = []
        content_to_parse = result_str
        try:
            result_json = json.loads(result_str)
            if isinstance(result_json, dict):
                if "pages" in result_json:
                    return result_json["pages"] if isinstance(result_json["pages"], list) else []
                if "data" in result_json and isinstance(result_json["data"], dict) and "content" in result_json["data"]:
                    content_to_parse = result_json["data"]["content"] or ""
            elif isinstance(result_json, list):
                return result_json
        except json.JSONDecodeError:
            pass
        if not content_to_parse or "## Pages" not in content_to_parse:
            return pages
        lines = content_to_parse.split("\n")
        for line in lines:
            match = re.search(r"(\d+):\s+([^\s\]]+)(?:\s+\[(selected)\])?", line)
            if match:
                page_id, url, selected = match.group(1), match.group(2), bool(match.group(3))
                pages.append({
                    "pageId": page_id,
                    "url": url,
                    "title": url,
                    "selected": selected,
                })
        return pages

    def _update_pages_info(self, state: BrowserAgentState) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        """更新页面信息并返回（pages_info 字典, 按 list_pages 顺序的页面列表）。
        
        调用 browser_list_pages 工具获取当前所有页面；列表顺序即 API 返回顺序，
        用于判断 [selected] 当前页及其下方的「新打开页面」。
        """
        if not self.node_impl.mcp_client.is_connected():
            return state.get("pages_info", {}), []

        try:
            tools = create_browser_tools(self.node_impl.mcp_client)
            browser_list_pages = next(t for t in tools if t.name == "browser_list_pages")
            result_str = browser_list_pages.invoke({})
            pages = self._parse_list_pages_result(result_str)
            if not pages:
                logger.warning(f"解析页面列表失败: {result_str[:200]}")
                return state.get("pages_info", {}), []

            current_pages_info = state.get("pages_info", {}).copy()
            now = time.time()

            for page in pages:
                page_id = str(page.get("pageId") or page.get("page_id") or "").strip()
                if not page_id:
                    continue
                url = page.get("url") or page.get("pageUrl")
                if page_id not in current_pages_info:
                    current_pages_info[page_id] = {
                        "url": url,
                        "title": page.get("title") or url,
                        "open_time": now,
                        "last_active": now,
                        "selected": bool(page.get("selected", False))
                    }
                else:
                    current_pages_info[page_id]["url"] = url
                    current_pages_info[page_id]["title"] = page.get("title") or url
                    current_pages_info[page_id]["selected"] = bool(page.get("selected", False))

            active_ids = {str(p.get("pageId") or p.get("page_id") or "").strip() for p in pages}
            active_ids = {x for x in active_ids if x}
            current_pages_info = {pid: info for pid, info in current_pages_info.items() if pid in active_ids}
            return current_pages_info, pages
        except Exception as e:
            logger.warning(f"更新页面信息失败: {e}")
            return state.get("pages_info", {}), []

    # ==================== 操作记录上下文（page_records） ====================

    def _fetch_list_pages_from_mcp(self) -> List[Dict[str, Any]]:
        """从 MCP 获取当前打开的页面列表。

        Returns:
            页面列表，每项包含 pageId、url 等
        """
        if not self.node_impl.mcp_client.is_connected():
            return []
        try:
            tools = create_browser_tools(self.node_impl.mcp_client)
            browser_list_pages = next(t for t in tools if t.name == "browser_list_pages")
            result_str = browser_list_pages.invoke({})
            return self._parse_list_pages_result(result_str)
        except Exception as e:
            logger.warning(f"获取页面列表失败: {e}")
            return []

    def _build_initial_page_records(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """初始观察时构建 page_records。

        为每个页面生成一条 PageRecord，step_name=\"初始观察\"，tool_name=\"list_pages\"。
        """
        if not pages:
            return []
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        records = []
        for p in pages:
            url = p.get("url") or p.get("pageUrl") or ""
            if not url:
                continue
            records.append({
                "url": url,
                "open_time": now,
                "step_name": "初始观察",
                "tool_name": "list_pages",
                "page_id": str(p.get("pageId", p.get("pageIdx", ""))),
            })
        return records

    def _update_page_records_after_tools(
        self,
        state: BrowserAgentState,
        pages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """工具执行后更新 page_records。

        若最近一次工具为 browser_navigate、browser_new_page 等导航类工具，
        将新出现且尚未记录的页面追加到 page_records，并填入当前步骤的 description 与工具名。
        """
        from src.agent.config.agent_config import ToolClassificationConfig

        config = ToolClassificationConfig()
        page_opening_tools = set(config.page_opening_tools)

        existing_records: List[Dict[str, Any]] = list(state.get("page_records") or [])
        if isinstance(existing_records, dict):
            existing_records = list(existing_records.values()) if existing_records else []
        existing_page_ids = {str(r.get("page_id", "")) for r in existing_records}

        tool_call_records = state.get("tool_call_records") or []
        last_tool_name = ""
        if tool_call_records:
            last_rec = tool_call_records[-1]
            if hasattr(last_rec, "tool_name"):
                last_tool_name = getattr(last_rec, "tool_name", "") or ""
            elif isinstance(last_rec, dict):
                last_tool_name = last_rec.get("tool_name", "") or ""

        if last_tool_name not in page_opening_tools:
            return existing_records

        step_name = "工具执行后观察"

        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        new_records = list(existing_records)
        for p in pages:
            url = p.get("url") or p.get("pageUrl") or ""
            page_id = str(p.get("pageId", p.get("pageIdx", "")))
            if not url or not page_id:
                continue
            if page_id not in existing_page_ids:
                new_records.append({
                    "url": url,
                    "open_time": now,
                    "step_name": step_name,
                    "tool_name": last_tool_name or "list_pages",
                    "page_id": page_id,
                })
                existing_page_ids.add(page_id)

        return new_records

    def _select_latest_page(self, pages_info: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """选择最新的页面ID"""
        if not pages_info:
            return None
        
        # 按 open_time 降序排序
        sorted_pages = sorted(
            pages_info.items(), 
            key=lambda x: x[1].get("open_time", 0), 
            reverse=True
        )
        logger.info(f"------sorted_pages: {sorted_pages}")
        return sorted_pages[0][0] if sorted_pages else None


    def _detect_new_pages_info(self, state: BrowserAgentState) -> tuple[Dict[str, Dict[str, Any]], Optional[str], Optional[str]]:
        """检测新页面信息（不自动切换）。

        规则（与 list_pages 返回格式约定一致）：
        - 列表顺序以 list_pages 返回为准（Markdown 行序或 JSON pages 数组序）。
        - 带 [selected] 的项为当前页；若有多个则取第一个。
        - 紧挨在当前页**下一项**的页面视为「新打开的页面」，取其 pageId 与 url 上报。
        - 若当前页已是最后一项、或下一项缺少 pageId/url，则不报新页面。
        """
        pages_info, ordered_pages = self._update_pages_info(state)
        new_page_id: Optional[str] = None
        new_page_url: Optional[str] = None
        selected_idx: Optional[int] = None
        for i, p in enumerate(ordered_pages):
            if p.get("selected"):
                selected_idx = i
                break
        if selected_idx is not None and selected_idx + 1 < len(ordered_pages):
            next_page = ordered_pages[selected_idx + 1]
            pid = next_page.get("pageId") or next_page.get("page_id")
            url = next_page.get("url") or next_page.get("pageUrl") or ""
            pid_str = str(pid).strip() if pid is not None else ""
            url_str = (url.strip() if isinstance(url, str) else str(url or "").strip()) or ""
            if pid_str and url_str:
                new_page_id = pid_str
                new_page_url = url_str
                if self.node_impl.verbose:
                    logger.info(f"🔍 检测到新页面（[selected] 下方）: {new_page_id}, URL: {new_page_url}")
        return pages_info, new_page_id or None, new_page_url or None

    def _log_snapshot_success(self, page_snapshot: str) -> None:
        """记录快照成功日志
        
        Args:
            page_snapshot: 页面快照字符串
        """
        if self.node_impl.verbose:
            length = len(page_snapshot) if page_snapshot is not None else 0
            logger.info(f"📸 获取页面快照 ({length} 字符)")

    def _log_snapshot_error(self, error_msg: str) -> None:
        """记录快照错误日志
        
        Args:
            error_msg: 错误消息
        """
        if self.node_impl.verbose:
            logger.info(f"⚠️ {error_msg}")

    def _log_mcp_not_connected(self) -> None:
        """记录MCP未连接日志"""
        if self.node_impl.verbose:
            logger.info("⚠️ MCP 服务器未连接")

    # ==================== 控制台消息方法 ====================

    def _get_console_messages(self) -> str:
        """获取控制台消息
        
        通过MCP客户端获取浏览器控制台消息。
        
        Returns:
            控制台消息字符串，失败返回错误消息
        """
        if not self.node_impl.mcp_client.is_connected():
            self._log_mcp_not_connected()
            return DEFAULT_CONSOLE_MESSAGES
        
        try:
            console_result = self.node_impl.mcp_client.list_console_messages()
            console_messages = str(console_result)[:MAX_CONSOLE_MESSAGE_LENGTH]
            return console_messages
        except Exception as e:
            error_msg = f"获取控制台消息失败: {e}"
            logger.error(error_msg)
            return error_msg

    # ==================== 主方法 ====================

    def observe(self, state: BrowserAgentState) -> Dict[str, Any]:
        """观察节点主入口：观察页面状态
        
        收集页面状态信息，包括截图、快照和控制台消息。
        
        重要：工具操作完成后，先检查页面是否有增加以及跳转，先更新信息，然后再获取快照。
        
        Args:
            state: 浏览器Agent状态
            
        Returns:
            包含观察结果的状态更新字典
        """
        if self.node_impl.verbose:
            from src.agent.utils.agent_flow_logger import log_phase_start, log_observe_summary
            iteration = state.get("iteration_count", 0)
            log_phase_start("observe", iteration=iteration, extra={"当前URL": state.get("current_url") or "未知"})
        
        # 1) 先从状态中获取当前的 URL（上一轮认知）
        current_url = state.get("current_url")
        
        # 2) 检查是否刚执行完工具（本轮 observe 是否为“工具之后”的观察）
        tool_call_records = state.get("tool_call_records") or []
        just_after_tools = len(tool_call_records) > 0
        
        # 2.5) 工具执行后先 sleep 再 list_pages，等待页面稳定（弹窗关闭、DOM 更新等）
        if just_after_tools and LIST_PAGES_DELAY_AFTER_TOOLS_SEC > 0:
            delay_sec = LIST_PAGES_DELAY_AFTER_TOOLS_SEC
            # 上一步为 browser_click_by_vision（关闭弹窗）时额外延迟，弹窗关闭动画需更长时间
            if tool_call_records:
                last_rec = tool_call_records[-1]
                last_tool = (last_rec.get("tool_name") or "") if isinstance(last_rec, dict) else getattr(last_rec, "tool_name", "")
                if last_tool == "browser_click_by_vision" and LIST_PAGES_EXTRA_DELAY_AFTER_VISION_CLICK_SEC > 0:
                    delay_sec += LIST_PAGES_EXTRA_DELAY_AFTER_VISION_CLICK_SEC
                    logger.info(f"⏱️ [OBSERVE] 上一步为 browser_click_by_vision，额外延迟 {LIST_PAGES_EXTRA_DELAY_AFTER_VISION_CLICK_SEC}s，共 {delay_sec}s")
            logger.info(f"⏱️ [OBSERVE] 工具执行后 sleep {delay_sec}s 再 list_pages")
            time.sleep(delay_sec)
        
        # 3) 使用 list_pages 检查当前打开的页面，并检测是否有新页面
        #    观察节点只检测新页面信息，不自动切换，页面切换由执行节点负责
        pages_info, new_page_id, new_page_url = self._detect_new_pages_info(state)

        # 4) 确定当前活跃页面（selected 优先，其次按 open_time 最新），用于更新当前 URL
        active_page_id = None
        for pid, info in pages_info.items():
            if info.get("selected") is True:
                active_page_id = pid
                break
        if not active_page_id:
            active_page_id = self._select_latest_page(pages_info)
        
        # 5) 根据当前活跃页面更新 URL（如果能获取到）
        new_url = None
        if active_page_id and pages_info.get(active_page_id, {}).get("url"):
            new_url = pages_info[active_page_id]["url"]
        current_url = new_url or current_url
        
        # 6) 决定是否需要刷新快照（根本不变式：page_snapshot 必须与 current_url 所指当前页一致，
        #    否则执行节点会看到「URL=新页、快照=旧页」而误判，导致重复导航或错误操作）
        #    刷新条件（满足任一即刷新，且不削弱原有逻辑）：
        #    A. 尚无有效快照 → 必须拉一份（原有：初始观察）
        #    B. 工具执行后且检测到新页面（[selected] 下有下一项）→ 刷新（原有：新开标签后刷新）
        #    C. 工具执行后且 current_url 相对上一轮发生变化 → 刷新（同页 navigate/跳转后快照必过期，否则会反复导航）
        #    D. 工具执行后 → 由观察节点统一更新快照（click/fill/navigate 等不再用工具返回的瞬间快照写 state，此处补一次刷新）
        page_snapshot = state.get("page_snapshot", DEFAULT_PAGE_SNAPSHOT)
        old_url = (state.get("current_url") or "").strip()
        new_url = (current_url or "").strip()
        url_changed = bool(old_url and new_url and old_url.rstrip("/") != new_url.rstrip("/"))

        need_initial_snapshot = (not page_snapshot) or (page_snapshot == DEFAULT_PAGE_SNAPSHOT)
        need_refresh_new_page_after_tools = bool(just_after_tools and new_page_id and new_page_url)
        need_refresh_url_changed_after_tools = bool(just_after_tools and url_changed)
        need_refresh_after_tools = bool(just_after_tools)  # 工具执行后统一由观察节点更新快照，不依赖工具返回的瞬间快照

        needs_refresh = need_initial_snapshot or need_refresh_new_page_after_tools or need_refresh_url_changed_after_tools or need_refresh_after_tools

        # 记录快照追踪日志（仅关注长度变化，避免冗余信息）
        old_snapshot = state.get("page_snapshot")
        old_snapshot_len = len(old_snapshot) if old_snapshot else 0
        
        if needs_refresh:
            reasons = []
            if need_initial_snapshot:
                reasons.append("初始无快照或为默认值")
            if need_refresh_new_page_after_tools:
                reasons.append("工具执行后检测到新页面")
            if need_refresh_url_changed_after_tools:
                reasons.append("工具执行后URL变化(同页导航)")
            if need_refresh_after_tools and not (need_refresh_new_page_after_tools or need_refresh_url_changed_after_tools):
                reasons.append("工具执行后由观察节点统一更新快照")
            logger.info(f"📸 [OBSERVE] 快照追踪 - 需要刷新快照，原因: {', '.join(reasons)}")
            page_snapshot = self._fetch_snapshot_from_mcp()
            new_snapshot_len = len(page_snapshot) if page_snapshot else 0
            logger.info(f"📸 [OBSERVE] 快照追踪 - 已刷新快照，新快照长度: {new_snapshot_len} 字符")
        else:
            logger.info(f"📸 [OBSERVE] 快照追踪 - 不刷新快照，沿用 state 中的快照 ({old_snapshot_len} 字符)")
        
        # 如果URL仍未更新，尝试从快照提取
        if not current_url:
            current_url = extract_url_from_snapshot(page_snapshot)
            if self.node_impl.verbose:
                logger.info(f"🔗 从快照提取 URL: {current_url}")
        
        # 识别是否为标准 Web 页面
        is_web_page = bool(current_url and (current_url.startswith("http://") or current_url.startswith("https://")))
        
        if self.node_impl.verbose:
            if not is_web_page:
                logger.info(f"ℹ️ 当前处于非 Web 页面 ({current_url or '空白页'})，将简化观察逻辑")
            else:
                logger.info(f"🌐 当前处于 Web 页面: {current_url}")

        # 获取当前截图计数器
        screenshot_count = state.get("screenshot_count", 0)
        
        # 执行观察动作
        screenshot_path = state.get("current_screenshot")  # 保持当前截图路径（由工具调用后更新）
        screenshot_base64 = None  # 视觉弹窗检测用，仅捕获时可直接传入避免重复读文件
        
        # 仅在首次观察时（iteration_count==0）截图；后续观察由 tools 节点后截图
        is_initial = state.get("iteration_count", 0) == 0
        if is_initial and is_web_page:
            # 初始观察时，如果还没有截图，则捕获一张
            if not screenshot_path:
                screenshot_count += 1
                temp_state = {**state, "screenshot_count": screenshot_count}
                screenshot_base64, screenshot_path = self._capture_screenshot(temp_state)
                if screenshot_path:
                    if self.node_impl.verbose:
                        logger.info(f"📸 初始观察截图: {screenshot_path} (计数器: {screenshot_count})")
                else:
                    screenshot_count -= 1
                    screenshot_base64 = None
        
        # 获取控制台消息
        if is_web_page:
            console_messages = self._get_console_messages()
        else:
            console_messages = "非 Web 页面，跳过控制台收集"
        
        # 弹窗检测：优先视觉模型分析截图，其次控制台 alert/confirm/prompt
        from src.agent.utils.popup_detection_utils import detect_popup
        console_for_detect = [console_messages] if isinstance(console_messages, str) else console_messages
        popup_hint = detect_popup(
            page_snapshot,
            console_for_detect,
            screenshot_path=screenshot_path,
            screenshot_base64=screenshot_base64,
        )
        if popup_hint:
            logger.info("[OBSERVE] 弹窗检测: popup_hint 已设置，将写入 state 供 Execute 使用")
        else:
            logger.info("[OBSERVE] 弹窗检测: popup_hint=None")
        
        next_phase = BrowserAgentPhase.REFLECTING.value

        # 更新操作记录上下文 page_records（步骤名称、使用工具）
        pages_raw = self._fetch_list_pages_from_mcp()
        if is_initial:
            page_records = self._build_initial_page_records(pages_raw)
        else:
            page_records = self._update_page_records_after_tools(state, pages_raw)
        if self.node_impl.verbose and page_records:
            logger.info(f"👁️ [OBSERVE] 📋 更新 page_records: {len(page_records)} 条")

        # logger.info(f"------debug---------> 更新后的current_url: {current_url}")
        
        # 🔧 确保状态更新：将所有更新的信息都包含在返回的字典中
        # LangGraph 会自动将这些更新合并到 state 中，供下一个节点使用
        update_dict = {
            "pages_info": pages_info,  # 已更新的页面信息（包括新页面和URL变化）
            "page_snapshot": page_snapshot,  # 已更新的快照
            "current_url": current_url,  # 已更新的URL
            "is_web_page": is_web_page,
            "console_messages": [console_messages],
            "popup_hint": popup_hint,  # 弹窗/广告检测结果，供 Execute 节点构建 observation_result
            "phase": next_phase,
            "current_screenshot": screenshot_path if screenshot_path else state.get("current_screenshot"),  # 保留之前的截图如果未捕获新截图
            "screenshot_count": screenshot_count if screenshot_path else state.get("screenshot_count", 0),  # 仅在捕获新截图时更新计数器
            "page_records": page_records,  # 已更新的页面记录
        }
        
        # 🔧 新增：传递新页面信息给执行节点
        # 重要：即使current_url已更新，如果检测到新页面，也要传递新页面URL
        # 这样路由函数可以对比新页面URL和当前URL，决定是否需要导航
        logger.info(f"🔍 [OBSERVE] 检查新页面信息: new_page_id={new_page_id}, new_page_url={new_page_url}")
        if new_page_id and new_page_url:
            update_dict["new_page_id"] = new_page_id
            update_dict["new_page_url"] = new_page_url
            logger.info(f"   📤 传递新页面信息给执行节点: new_page_url={new_page_url}, current_url={current_url}")
        else:
            # 清理之前可能存在的new_page_url（如果这次没有检测到新页面）
            if "new_page_url" in state:
                update_dict["new_page_url"] = None
                update_dict["new_page_id"] = None
                logger.info(f"   🧹 清理之前的新页面信息")
        
        # 添加调试日志，确保状态更新被正确记录
        final_snapshot_len = len(page_snapshot) if page_snapshot else 0
        logger.info(f"📸 [OBSERVE] 快照追踪 - 最终更新到state的快照长度: {final_snapshot_len} 字符")
        if old_snapshot_len != final_snapshot_len:
            logger.info(f"📸 [OBSERVE] 快照追踪 - 快照已更新: {old_snapshot_len} -> {final_snapshot_len} 字符")
        
        if self.node_impl.verbose:
            new_page_info = f"page_id={new_page_id}, URL={new_page_url}" if (new_page_id and new_page_url) else None
            log_observe_summary(
                current_url=current_url,
                page_count=len(pages_info),
                snapshot_len=final_snapshot_len,
                new_page_info=new_page_info,
            )

        return update_dict
