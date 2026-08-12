"""
会话上下文管理服务

基于 LangChain InMemoryChatMessageHistory 实现任务间的上下文存储。
只保存当前窗口中的历史对话，作为下一任务的上下文，由模型自主思考利用。

适配 browser-use 上下文管理机制：
- max_history_items 风格：保留首轮 + 最近 N 轮，中间省略
- 单条内容上限：Human/AI 消息分别截断
- URL 压缩：长 URL 压缩为域名形式
- LLM 压缩（Message Compaction）：历史超阈值时用 LLM 摘要中间轮次
- 上一任务已保存结果：记录工具产生的 json/mp4 等文件路径，供下一任务历史信息补充
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from langchain_core.chat_history import BaseChatMessageHistory, InMemoryChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

# 单例存储：{connection_id: InMemoryChatMessageHistory}
_store: dict[str, BaseChatMessageHistory] = {}

# LLM 压缩摘要缓存：{connection_id: 摘要文本}，add_task_result 时清空
_compacted_memory: dict[str, str] = {}

# 上一任务已保存结果：{connection_id: [{"tool_name": str, "result_type": str, "path": str}, ...]}
# 用于在 agent_history 中补充「来自哪个工具、什么结果、绝对路径」
_saved_results_store: dict[str, List[Dict[str, str]]] = {}

# ========== browser-use 风格配置 ==========
# 最多保留最近 N 轮对话（1 轮 = 1 Human + 1 AI）
MAX_CONVERSATION_ROUNDS = 6
# 是否保留首轮（browser-use 风格：保留第 1 步 + 最近 N 步）
KEEP_FIRST_ROUND = True
# 单条消息最大字符数（超则截断）
MAX_HUMAN_MESSAGE_CHARS = 500
MAX_AI_MESSAGE_CHARS = 300
# URL 压缩：超过此长度的 URL 压缩为域名形式
MAX_URL_CHARS = 80
# 上下文截断时为本任务数据文件摘要保留的字符数（含标题「【本任务产生的数据文件摘要】」），该块不得省略。单条约 180 字（含长路径），预留 1200 可容纳约 6 条
DATA_FILE_SUMMARY_RESERVED_CHARS = 1200
DATA_FILE_SUMMARY_MARKER = "【本任务产生的数据文件摘要】"

# ========== LLM 压缩配置（browser-use Message Compaction） ==========
COMPACTION_ENABLED = True
COMPACTION_TRIGGER_CHAR_COUNT = 40000  # 历史总字符数超过此值才触发压缩
COMPACTION_KEEP_LAST_ROUNDS = 6        # 压缩后保留最近几轮
COMPACTION_SUMMARY_MAX_CHARS = 6000   # 压缩摘要最大字符数


def get_session_history(connection_id: str) -> BaseChatMessageHistory:
    """
    获取或创建指定连接/会话的消息历史。
    基于 LangChain InMemoryChatMessageHistory 实现。

    Args:
        connection_id: 连接/会话标识（WebSocket 下为 connection_id，CLI 下为 agent 的 connection_id）

    Returns:
        该会话的聊天历史实例
    """
    if connection_id not in _store:
        _store[connection_id] = InMemoryChatMessageHistory()
        logger.info(f"[SessionContext] 为 connection_id={connection_id[:8]}... 创建新历史")
    return _store[connection_id]


def extract_saved_results_from_messages(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    """
    从消息列表中解析工具产生的已保存文件（json、mp4 等），用于补充历史信息。
    扫描 ToolMessage 的 content，提取 file_path、output_file 或文本中的保存路径。

    Returns:
        [{"tool_name": str, "result_type": str, "path": str}, ...]，path 为绝对路径
    """
    result: List[Dict[str, str]] = []
    seen_paths: set[str] = set()

    def _add(tool_name: str, result_type: str, path: str) -> None:
        if not path or not path.strip():
            return
        try:
            p = Path(path.strip())
            abs_path = str(p.resolve()) if not p.is_absolute() else path.strip()
            if abs_path in seen_paths:
                return
            seen_paths.add(abs_path)
            result.append({"tool_name": tool_name, "result_type": result_type, "path": abs_path})
        except Exception:
            pass

    # 工具名 -> 结果类型描述
    tool_result_types: Dict[str, str] = {
        "browser_extract_autohome_post_detail": "汽车之家帖子详情 JSON 数据",
        "browser_extract_autohome_chejiahao_info": "汽车之家车家号页面 JSON 数据",
        "browser_extract_dcd_post_detail": "懂车帝帖子详情 JSON 数据",
        "browser_extract_dcd_video": "懂车帝视频页 JSON 数据",
        "browser_filter_voc": "VOC 内容筛选结果 JSON",
        "browser_analyze_voc": "VOC 内容分析结果 JSON",
        "browser_analyze_video": "抖音视频分析 JSON",
        "browser_fetch_and_download_douyin_video": "抖音视频文件",
    }

    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        tool_name = (getattr(m, "name", None) or "").strip()
        content = getattr(m, "content", None) or ""
        raw = str(content).strip()
        if not raw:
            continue

        # 1) 尝试 JSON 解析：file_path、output_file
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                path = data.get("file_path") or data.get("output_file")
                if path and isinstance(path, str):
                    result_type = tool_result_types.get(tool_name) or "保存的文件"
                    _add(tool_name, result_type, path)
        except (json.JSONDecodeError, TypeError):
            pass

        # 2) 文本正则：JSON file saved / JSON文件已保存 / output_file / file_path
        for pattern, default_type in [
            (r"JSON file saved:\s*([^\s\n]+)", "帖子/数据 JSON 文件"),
            (r"JSON文件已保存:\s*([^\s\n]+)", "帖子/数据 JSON 文件"),
            (r"保存路径[：:]\s*([^\s\n]+)", "保存的文件"),
            (r'"file_path"\s*:\s*"([^"]+)"', None),
            (r'"output_file"\s*:\s*"([^"]+)"', "分析/输出 JSON 文件"),
        ]:
            for match in re.finditer(pattern, raw):
                path = match.group(1).strip()
                if path.endswith(".json") or path.endswith(".mp4") or "/" in path or "\\" in path:
                    result_type = default_type or tool_result_types.get(tool_name) or "保存的文件"
                    _add(tool_name, result_type, path)

    return result


def add_task_result(
    connection_id: str,
    task: str,
    result_summary: str,
    final_url: Optional[str] = None,
    saved_results: Optional[List[Dict[str, str]]] = None,
) -> None:
    """
    将任务及其结果追加到会话历史。
    窗口不关闭期间持续记录。
    新增消息后清除 LLM 压缩缓存（摘要可能过期）。
    若提供 saved_results，会写入会话供下一任务在历史中展示「上一任务已保存的结果」。

    Args:
        connection_id: 连接/会话标识
        task: 用户任务描述
        result_summary: 任务结果摘要（成功/失败、final_result 等）
        final_url: 可选，任务结束时的当前 URL
        saved_results: 可选，本任务中工具产生的保存结果 [{"tool_name","result_type","path"}]
    """
    if connection_id in _compacted_memory:
        del _compacted_memory[connection_id]
        logger.info(
            f"[SessionContext] connection_id={connection_id[:8]}... LLM 压缩缓存已清除 "
            f"（新任务结果已加入，摘要可能过期）"
        )
    if saved_results:
        _saved_results_store[connection_id] = saved_results
        logger.info(
            f"[SessionContext] 已保存本任务产出结果 {len(saved_results)} 条，供下一任务历史补充"
        )
    history = get_session_history(connection_id)
    history.add_user_message(task)
    summary = result_summary
    if final_url:
        summary = f"{result_summary}\n（当前 URL: {final_url}）"
    history.add_ai_message(summary)
    logger.info(f"[SessionContext] 已追加任务结果到 connection_id={connection_id[:8]}...")


def get_prior_saved_results(connection_id: str) -> List[Dict[str, str]]:
    """
    获取上一任务中工具产生的已保存结果（json、mp4 等），用于在 agent_history 中补充说明。

    Returns:
        [{"tool_name": str, "result_type": str, "path": str}, ...]，无则返回 []
    """
    return list(_saved_results_store.get(connection_id, []))


def get_prior_messages(connection_id: str) -> List[BaseMessage]:
    """
    获取该会话的完整历史消息列表。

    Args:
        connection_id: 连接/会话标识

    Returns:
        历史消息列表（HumanMessage, AIMessage 交替），无历史时返回空列表
    """
    if connection_id not in _store:
        return []
    history = _store[connection_id]
    return history.messages


def _format_messages_for_compaction(messages: List[BaseMessage]) -> str:
    """将消息列表格式化为供 LLM 摘要的文本（不截断、不压缩 URL）"""
    lines = []
    for m in messages:
        raw = str(m.content)
        if isinstance(m, HumanMessage):
            lines.append(f"用户: {raw}")
        elif isinstance(m, AIMessage):
            lines.append(f"助手: {raw}")
    return "\n".join(lines)


def _compact_messages_with_llm(
    connection_id: str,
    middle_messages: List[BaseMessage],
) -> str:
    """
    调用 LLM 对中间轮次对话做摘要，返回摘要文本。
    browser-use Message Compaction 风格。
    """
    try:
        from src.services import get_llm_provider
        llm = get_llm_provider().llm
    except Exception as e:
        logger.warning(f"[SessionContext] 获取 LLM 失败，跳过压缩: {e}")
        return ""

    text = _format_messages_for_compaction(middle_messages)
    if len(text) > COMPACTION_TRIGGER_CHAR_COUNT:
        text = text[:COMPACTION_TRIGGER_CHAR_COUNT] + "\n[... 已截断 ...]"

    prompt = f"""请将以下浏览器操作会话的中间部分概括成一段简短摘要（不超过 {COMPACTION_SUMMARY_MAX_CHARS} 字）。
摘要应保留：用户的主要任务、助手的执行结果、关键 URL 或页面状态。
不要逐条复述，用连贯的段落概括。

【待摘要的对话】
{text}

【摘要】"""
    try:
        response = llm.invoke([SystemMessage(content=prompt)])
        summary = str(response.content).strip()
        if len(summary) > COMPACTION_SUMMARY_MAX_CHARS:
            summary = summary[:COMPACTION_SUMMARY_MAX_CHARS] + "..."
        return summary
    except Exception as e:
        logger.warning(f"[SessionContext] LLM 压缩失败: {e}")
        return ""


def _maybe_compact_messages(
    connection_id: str,
    messages: List[BaseMessage],
    n: int,
    round_size: int,
) -> Optional[str]:
    """
    当历史超阈值时，调用 LLM 对中间轮次做摘要。
    返回摘要文本，若未触发或失败则返回 None。
    """
    if not COMPACTION_ENABLED:
        return None
    total_rounds = len(messages) // round_size
    if total_rounds <= n + 1:
        return None
    if connection_id in _compacted_memory:
        logger.info(
            f"[SessionContext] connection_id={connection_id[:8]}... 使用已缓存的 LLM 压缩摘要 "
            f"（避免重复调用 LLM）"
        )
        return _compacted_memory[connection_id]

    total_chars = sum(len(str(m.content)) for m in messages)
    if total_chars < COMPACTION_TRIGGER_CHAR_COUNT:
        return None

    first_round = messages[:round_size]
    last_n_rounds = messages[-(n * round_size):]
    middle_messages = messages[round_size : -(n * round_size)]
    if not middle_messages:
        return None

    logger.info(
        f"[SessionContext] 触发 LLM 压缩（browser-use Message Compaction）: "
        f"connection_id={connection_id[:8]}..., 中间 {len(middle_messages)//2} 轮待摘要, "
        f"历史总字符 {total_chars} > 阈值 {COMPACTION_TRIGGER_CHAR_COUNT}"
    )
    summary = _compact_messages_with_llm(connection_id, middle_messages)
    if summary:
        _compacted_memory[connection_id] = summary
        logger.info(
            f"[SessionContext] LLM 压缩完成: 摘要 {len(summary)} 字, "
            f"下次将用摘要替代中间 {len(middle_messages)//2} 轮对话"
        )
        return summary
    return None


def get_recent_messages(
    connection_id: str,
    max_rounds: Optional[int] = None,
    keep_first_round: Optional[bool] = None,
) -> List[BaseMessage]:
    """
    获取该会话的最近 N 轮对话，用于注入到下一任务的 messages 中。
    适配 browser-use max_history_items 风格：保留首轮 + 最近 N 轮，中间省略。

    Args:
        connection_id: 连接/会话标识
        max_rounds: 最多保留几轮（1 轮 = 1 个 Human + 1 个 AI），None 表示使用默认 MAX_CONVERSATION_ROUNDS
        keep_first_round: 是否保留首轮，None 表示使用默认 KEEP_FIRST_ROUND

    Returns:
        最近 N 轮的消息列表，无历史时返回空列表
    """
    messages = get_prior_messages(connection_id)
    if not messages:
        return []
    n = max_rounds if max_rounds is not None else MAX_CONVERSATION_ROUNDS
    if n <= 0:
        return []
    keep_first = keep_first_round if keep_first_round is not None else KEEP_FIRST_ROUND

    total = len(messages)
    round_size = 2  # 1 轮 = Human + AI
    total_rounds = total // round_size

    if total_rounds <= n:
        logger.info(
            f"[SessionContext] 上下文历史: 共 {total_rounds} 轮, 全量返回（未超过 max_rounds={n}）"
        )
        return list(messages)

    # browser-use 风格：保留首轮 + 最近 n 轮，中间用省略占位或 LLM 压缩摘要
    # 对齐 browser-use agent_history_description：total_items > max_history_items 即触发
    # 原条件 total_rounds > n+1 导致 7 轮时误走「仅保留最近 n 轮」，丢失首轮
    if keep_first and total_rounds > n:
        first_round = messages[:round_size]
        last_n_rounds = messages[-(n * round_size):]
        omitted_count = total_rounds - 1 - n
        if omitted_count <= 0:
            # 首轮与最近 n 轮无重叠或仅差 1 轮，直接拼接（无中间省略）
            logger.info(
                f"[SessionContext] 上下文历史: browser-use 风格, 共 {total_rounds} 轮 -> "
                f"首轮 + 最近 {n} 轮（无中间省略）"
            )
            return first_round + last_n_rounds
        compacted = _maybe_compact_messages(connection_id, messages, n, round_size)
        if compacted:
            placeholder = HumanMessage(
                content=f"<compacted_memory>{compacted}</compacted_memory>"
            )
            logger.info(
                f"[SessionContext] 上下文历史: browser-use 风格截断, 共 {total_rounds} 轮 -> "
                f"首轮 + LLM 压缩摘要 + 最近 {n} 轮"
            )
        else:
            placeholder = HumanMessage(
                content=f"[... 省略中间 {omitted_count} 轮对话 ...]"
            )
            logger.info(
                f"[SessionContext] 上下文历史: browser-use 风格截断, 共 {total_rounds} 轮 -> "
                f"首轮 + 中间 {omitted_count} 轮省略 + 最近 {n} 轮（历史未超 {COMPACTION_TRIGGER_CHAR_COUNT} 字，未触发 LLM 压缩）"
            )
        return first_round + [placeholder] + last_n_rounds

    # 不保留首轮：仅取最后 n 轮（browser-use 当 keep_first=False 时的行为）
    keep = min(total, n * round_size)
    logger.info(
        f"[SessionContext] 上下文历史: 共 {total_rounds} 轮 -> 仅保留最近 {n} 轮 "
        f"（keep_first_round=False）"
    )
    return list(messages[-keep:])


def _compress_urls_in_text(text: str, max_url_chars: int = MAX_URL_CHARS) -> str:
    """
    将文本中的长 URL 压缩为域名形式，减少 token 占用。
    适配 browser-use 单块内容上限思路。
    """
    if not text or max_url_chars <= 0:
        return text
    # 匹配 http/https URL
    url_pattern = re.compile(
        r'https?://[^\s\u4e00-\u9fff，,。！!？?；;：\)\]\}]+',
        re.IGNORECASE
    )

    def replace_url(match: re.Match) -> str:
        url = match.group(0)
        if len(url) <= max_url_chars:
            return url
        try:
            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else url[:max_url_chars]
            if len(base) <= max_url_chars:
                return base
            return base[:max_url_chars] + "..."
        except Exception:
            return url[:max_url_chars] + "..."

    return url_pattern.sub(replace_url, text)


def format_messages_as_context(
    messages: List[BaseMessage],
    max_human_chars: Optional[int] = None,
    max_ai_chars: Optional[int] = None,
    max_tool_chars: Optional[int] = None,
    compress_urls: bool = True,
    add_round_labels: bool = True,
    include_tool_results: bool = False,
    log_content: bool = True,
) -> str:
    """
    将消息列表格式化为可供模型阅读的上下文字符串。
    供 Intent、Plan、Reflect 等节点在 prompt 中使用，由模型自主理解。
    适配 browser-use：单条长度上限、URL 压缩。
    支持时间线：每轮对话前加 [第N轮] 便于理解先后顺序。

    Args:
        messages: HumanMessage / AIMessage 列表，include_tool_results=True 时也处理 ToolMessage
        max_human_chars: 用户消息最大字符数，None 使用默认
        max_ai_chars: 助手消息最大字符数，None 使用默认
        max_tool_chars: 工具结果最大字符数，None 时与 max_ai_chars 一致；反思等场景可设较小值（如 5000）避免超长
        compress_urls: 是否压缩长 URL
        add_round_labels: 是否在每轮前加 [第N轮] 时间线标签
        include_tool_results: 是否包含 ToolMessage（工具执行结果），browser-use 执行节点需为 True
        log_content: 是否在日志中输出完整内容；False 时仅输出长度与短预览（供反思等长上下文场景）

    Returns:
        格式化的对话文本，如 "[第1轮] 用户: xxx\n助手: xxx\n工具结果: xxx\n..."
    """
    if not messages:
        return ""
    max_h = max_human_chars if max_human_chars is not None else MAX_HUMAN_MESSAGE_CHARS
    max_a = max_ai_chars if max_ai_chars is not None else MAX_AI_MESSAGE_CHARS
    max_t = max_tool_chars if max_tool_chars is not None else max_a
    has_omitted = False
    has_compacted = False
    lines = []
    round_num = 1
    for i, m in enumerate(messages):
        raw = str(m.content)
        # 省略占位符：直接输出，不加前缀
        if raw.startswith("[... 省略"):
            has_omitted = True
            lines.append(raw)
            continue
        # LLM 压缩摘要（browser-use Message Compaction）：直接输出，不加前缀
        if raw.startswith("<compacted_memory>"):
            has_compacted = True
            lines.append(raw)
            continue
        # 每轮首条（HumanMessage）前加时间线标签
        prefix = ""
        if add_round_labels and isinstance(m, HumanMessage):
            prefix = f"[第{round_num}轮] "
        if isinstance(m, HumanMessage):
            content = _compress_urls_in_text(raw) if compress_urls else raw
            content = content[:max_h] + ("..." if len(content) > max_h else "")
            lines.append(f"{prefix}用户: {content}")
        elif isinstance(m, AIMessage):
            content = _compress_urls_in_text(raw) if compress_urls else raw
            if DATA_FILE_SUMMARY_MARKER in content and len(content) > max_a:
                # 本任务数据文件摘要不得省略：保留摘要块，前面部分截断
                idx = content.find(DATA_FILE_SUMMARY_MARKER)
                before = content[:idx]
                summary_block = content[idx:]
                reserve = min(len(summary_block), DATA_FILE_SUMMARY_RESERVED_CHARS)
                head_budget = max(0, max_a - reserve - 10)
                if head_budget > 0 and len(before) > head_budget:
                    content = before[:head_budget] + "...\n\n" + (
                        summary_block if len(summary_block) <= DATA_FILE_SUMMARY_RESERVED_CHARS
                        else summary_block[:DATA_FILE_SUMMARY_RESERVED_CHARS] + "..."
                    )
                else:
                    content = summary_block if len(summary_block) <= DATA_FILE_SUMMARY_RESERVED_CHARS else summary_block[:DATA_FILE_SUMMARY_RESERVED_CHARS] + "..."
            else:
                content = content[:max_a] + ("..." if len(content) > max_a else "")
            lines.append(f"{prefix}助手: {content}")
            round_num += 1
        elif include_tool_results and isinstance(m, ToolMessage):
            content = _compress_urls_in_text(raw) if compress_urls else raw
            if len(content) > max_t:
                content = content[:max_t] + f"... [已截断，原长 {len(content)} 字]"
            lines.append(f"工具结果: {content}")
    result = "\n".join(lines)

    # 追加「本任务产生的数据文件摘要」：仅在包含工具结果时附加，便于后续节点快速引用产出文件路径。
    # 具体“是否读取 JSON 内容”的策略由 json_summary_utils 控制；当前默认不读取文件内容，仅展示路径。
    if include_tool_results and DATA_FILE_SUMMARY_MARKER not in result:
        try:
            from src.agent.utils.json_summary_utils import summarize_task_jsons_for_frontend
            summary = summarize_task_jsons_for_frontend(messages)
            if summary:
                result = result + summary
        except Exception as e:
            logger.debug("[SessionContext] 生成数据文件摘要失败: %s", e)
    # 上下文格式化完成时打日志，便于小白理解机制
    ctx_parts = [f"单条截断 Human≤{max_h}/AI≤{max_a}字"]
    if include_tool_results:
        ctx_parts.append(f"工具结果≤{max_t}字")
        ctx_parts.append("含工具结果")
    if add_round_labels:
        ctx_parts.append("时间线[第N轮]")
    if compress_urls:
        ctx_parts.append("URL压缩")
    if has_compacted:
        ctx_parts.append("含LLM压缩摘要")
    if has_omitted:
        ctx_parts.append("含省略占位")
    if log_content:
        logger.info(f"[SessionContext] 上下文格式化完成: {', '.join(ctx_parts)}, 输出 {len(result)} 字，内容为：{result}")
    else:
        preview = (result[:200] + "...") if len(result) > 200 else result
        logger.info(f"[SessionContext] 上下文格式化完成: {', '.join(ctx_parts)}, 输出 {len(result)} 字，预览: {preview}")
    return result


def clear_session(connection_id: str) -> None:
    """
    清除指定会话的历史（例如连接断开、Agent stop 时调用）。

    Args:
        connection_id: 连接/会话标识
    """
    if connection_id in _compacted_memory:
        del _compacted_memory[connection_id]
    if connection_id in _saved_results_store:
        del _saved_results_store[connection_id]
    if connection_id in _store:
        _store[connection_id].clear()
        del _store[connection_id]
        logger.info(f"[SessionContext] 已清除 connection_id={connection_id[:8]}... 的历史")
