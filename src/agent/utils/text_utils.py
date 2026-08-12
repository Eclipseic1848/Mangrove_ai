"""
文本工具函数

提供智能化的文本截取和格式化功能，避免关键信息被截断。
按功能模块组织：URL 提取、快照解析、快照截断、快照关键词提取、页面信息格式化。
"""
import re
from typing import Optional, List, Union, Any

from src.agent.config.text_processing_config import TextProcessingConfig, default_text_config


# ==================== 1. 配置与常量 ====================

UID_PATTERN = re.compile(r'\buid=(\d+)_(\d+)\b')


def _get_config(config: Optional[TextProcessingConfig]) -> TextProcessingConfig:
    """获取配置对象，若为 None 则返回默认配置"""
    return config if config is not None else default_text_config


# ==================== 2. URL 提取 ====================

def _is_valid_url(url: str) -> bool:
    """判断 URL 是否有效（含 http:// 或 https://）"""
    return 'http://' in url or 'https://' in url


def _extract_urls_from_text(text: str, max_count: Optional[int] = None) -> List[str]:
    """从文本中提取有效 URL 列表"""
    matches = re.findall(r'url=["\']?([^"\'>\s]+)["\']?', text)
    valid_urls = []
    seen = set()
    for url in matches:
        if url not in seen and _is_valid_url(url):
            seen.add(url)
            valid_urls.append(url)
            if max_count and len(valid_urls) >= max_count:
                break
    return valid_urls


def _extract_first_url_from_text(text: str) -> Optional[str]:
    """从文本中提取第一个有效 URL"""
    urls = _extract_urls_from_text(text, max_count=1)
    return urls[0] if urls else None


# ==================== 3. 快照格式与解析 ====================

def normalize_snapshot_to_str(snapshot: Any) -> str:
    """
    将快照统一转为字符串。
    支持 str、dict（content/text，MCP 常见格式）、list 等格式。
    避免将 dict 的 repr（如 {'content': [{'type': 'text', 'text': '...'}]}）当作快照内容传入 LLM。
    """
    if snapshot is None:
        return ""
    if isinstance(snapshot, str):
        return snapshot
    if isinstance(snapshot, dict):
        content = snapshot.get("content")
        if isinstance(content, list) and content:
            texts = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item.get("text", ""))
            if texts:
                return "\n".join(t for t in texts if t)
        if "text" in snapshot:
            return snapshot.get("text", "")
        return str(snapshot)
    if isinstance(snapshot, list) and snapshot:
        texts = []
        for item in snapshot:
            if isinstance(item, dict) and "text" in item:
                texts.append(item.get("text", ""))
        if texts:
            return "\n".join(t for t in texts if t)
    return str(snapshot)


def extract_title_from_snapshot(snapshot: str) -> Optional[str]:
    """从页面快照中提取标题（RootWebArea 后的文本）"""
    matches = re.findall(r'RootWebArea\s+"([^"]+)"', snapshot)
    return matches[0] if matches else None


def extract_first_line(snapshot: str, max_length: int) -> str:
    """提取快照第一行，截断到 max_length"""
    if '\n' in snapshot:
        first_line = snapshot.split('\n')[0]
        return first_line[:max_length] if len(first_line) > max_length else first_line
    return snapshot[:max_length] if len(snapshot) > max_length else snapshot


# ==================== 4. 快照关键信息 ====================

def _build_key_info_from_snapshot(snapshot: str, config: TextProcessingConfig) -> str:
    """从快照中提取关键信息（URL、标题、第一行）"""
    parts = []
    urls = _extract_urls_from_text(snapshot, max_count=config.max_urls_to_extract)
    for url in urls[:config.max_urls_to_extract]:
        parts.append(f"URL: {url}")
    title = extract_title_from_snapshot(snapshot)
    if title:
        parts.append(f"标题: {title}")
    first_line = extract_first_line(snapshot, config.first_line_max_length)
    if first_line:
        parts.append(f"第一行: {first_line}")
    return '\n'.join(parts)


# ==================== 4.5 browser-use 风格：单行截断与可交互过滤 ====================

# 可交互元素类型（MCP 快照格式，用于过滤 StaticText）
_INTERACTIVE_TYPES = frozenset({
    "link", "button", "textbox", "combobox", "checkbox", "radio",
    "heading", "Video", "image", "RootWebArea", "banner",
})


def cap_line_length(line: str, max_length: int) -> str:
    """
    单行文本截断（参考 browser-use cap_text_length）。
    避免单行过长挤占 token。
    """
    if not line or len(line) <= max_length:
        return line
    return line[:max_length] + "..."


def _is_interactive_line(line: str) -> bool:
    """判断快照行是否为可交互元素（link/button/textbox 等，非 StaticText）"""
    strip_line = line.lstrip()
    for t in _INTERACTIVE_TYPES:
        if f" {t} " in strip_line or f" {t}\"" in strip_line or strip_line.endswith(f" {t}"):
            return True
    return False


def _filter_to_interactive_elements(
    lines: List[str],
    keep_header: bool = True,
) -> List[str]:
    """
    大快照时仅保留可交互元素及其父级链（参考 browser-use 只索引可交互元素）。
    过滤掉纯 StaticText 行，减少 LLM 噪音。
    """
    header_lines = []
    line_infos = []  # (idx, line, indent)
    found_first_uid = False

    for i, line in enumerate(lines):
        has_uid = bool(UID_PATTERN.search(line))
        if not found_first_uid:
            if not has_uid:
                if keep_header:
                    header_lines.append(line)
                continue
            found_first_uid = True

        strip_line = line.lstrip()
        indent = len(line) - len(strip_line)
        line_infos.append((i, line, indent))

    # 收集可交互行及其祖先
    idx_to_info = {info[0]: info for info in line_infos}
    interactive_indices = {info[0] for info in line_infos if _is_interactive_line(info[1])}

    # 为可交互行添加父级链
    for idx in list(interactive_indices):
        info = idx_to_info.get(idx)
        if not info:
            continue
        _, _, indent = info
        for j in range(idx - 1, -1, -1):
            j_info = idx_to_info.get(j)
            if j_info and j_info[2] < indent:
                interactive_indices.add(j)
                indent = j_info[2]
                if indent == 0:
                    break

    # 按原始顺序输出
    result = list(header_lines)
    for info in line_infos:
        if info[0] in interactive_indices:
            result.append(info[1])
    return result


def apply_snapshot_browser_use_optimizations(
    snapshot: str,
    max_line_length: Optional[int] = None,
    interactive_filter_threshold: Optional[int] = None,
    keywords: Optional[List[str]] = None,
    config: Optional[TextProcessingConfig] = None,
) -> str:
    """
    应用 browser-use 风格优化：单行截断 + 大快照时过滤为可交互元素。
    - 有关键词时：不做可交互过滤（关键词抽取已处理）
    - 无关键词且快照超阈值：过滤为可交互元素
    - 所有输出行统一做单行截断
    """
    config = _get_config(config)
    max_line = max_line_length or config.max_line_length
    threshold = interactive_filter_threshold or config.interactive_filter_threshold

    if not snapshot:
        return ""

    lines = snapshot.split("\n")
    if not lines:
        return snapshot

    # 大快照且无关键词时，过滤为可交互元素
    if len(snapshot) > threshold and not keywords:
        lines = _filter_to_interactive_elements(lines, keep_header=True)
        if not lines:
            return snapshot  # 过滤后为空则回退

    # 单行截断
    capped = [cap_line_length(line, max_line) for line in lines]
    return "\n".join(capped)


# ==================== 5. 快照截断 ====================

def _build_truncated_snapshot_parts(
    snapshot: str,
    key_info: str,
    max_length: int,
    config: TextProcessingConfig
) -> List[str]:
    """构建截取后的快照各部分（关键信息 + 开头 + 结尾）"""
    key_info_len = len(key_info)
    remaining = max_length - key_info_len - config.separator_reserve_length

    if remaining < config.min_key_info_length:
        return [
            key_info,
            f"\n[内容过长，已截断，完整长度: {len(snapshot)} 字符]"
        ]

    head_len = int(remaining * config.head_tail_ratio)
    tail_len = remaining - head_len
    head = snapshot[:head_len] if len(snapshot) > head_len else snapshot
    tail = snapshot[-tail_len:] if len(snapshot) > tail_len else snapshot

    parts = []
    if key_info:
        parts.append(f"【关键信息】\n{key_info}")
    if head:
        parts.append(f"【开头部分】\n{head}")
    if tail and tail != head:
        parts.append(f"【结尾部分】\n{tail}")
    parts.append(f"\n[内容过长，已截断，完整长度: {len(snapshot)} 字符]")
    return parts


# ==================== 6. 快照关键词提取 ====================

def _parse_snapshot_lines(
    lines: List[str],
    keywords: Optional[List[str]],
    case_sensitive: bool
) -> tuple:
    """
    解析快照行，返回 (header_lines, line_infos, uid_order)。
    header: 第一个 uid 出现前的所有行；line_infos: 含 uid 行的 idx、line、indent、uid、keywords_matched。
    """
    header_lines = []
    line_infos = []
    uid_order = []
    seen_uids = set()
    found_first_uid = False

    for i, line in enumerate(lines):
        has_uid = bool(UID_PATTERN.search(line))
        if not found_first_uid:
            if not has_uid:
                header_lines.append(line)
                continue
            found_first_uid = True

        strip_line = line.lstrip()
        indent = len(line) - len(strip_line)
        uid_match = UID_PATTERN.search(line)
        uid_val = uid_match.group(0) if uid_match else None

        if uid_val and uid_val not in seen_uids:
            seen_uids.add(uid_val)
            uid_order.append((i, uid_val))

        keywords_matched = []
        if keywords:
            text = strip_line if case_sensitive else strip_line.lower()
            for kw in keywords:
                if not kw:
                    continue
                check_kw = kw if case_sensitive else kw.lower()
                # 强大的模糊匹配策略（支持大模型预测的关键词与页面实际文本不完全一致的情况）
                matched = False
                
                # 策略1：完整关键词匹配（最高优先级）
                if check_kw in text:
                    matched = True
                else:
                    # 策略2：智能分词匹配（适用于中文和混合文本）
                    # 提取关键词中的核心部分进行匹配
                    kw_parts = _extract_keyword_parts(check_kw)
                    for part in kw_parts:
                        if len(part) >= 2 and part in text:
                            matched = True
                            break
                    
                    # 策略3：字符级匹配（如果分词匹配失败）
                    # 对于短关键词，尝试匹配关键词中的每个字符
                    if not matched and len(check_kw) >= 2:
                        # 计算匹配的字符比例
                        matched_chars = sum(1 for char in check_kw if char in text)
                        match_ratio = matched_chars / len(check_kw)
                        # 如果匹配比例>=0.5，认为匹配成功
                        if match_ratio >= 0.5:
                            matched = True
                
                if matched:
                    keywords_matched.append(kw)
                    break

        line_infos.append({
            "idx": i,
            "line": line,
            "indent": indent,
            "uid": uid_val,
            "keywords_matched": keywords_matched,
        })

    return header_lines, line_infos, uid_order


def _extract_keyword_parts(keyword: str) -> List[str]:
    """
    提取关键词中的核心部分，用于模糊匹配
    
    策略：
    1. 对于中文关键词，按字符分割并提取有意义的片段
    2. 对于混合文本（中英文），分别处理中文和英文部分
    3. 提取长度>=2的片段
    
    示例：
    - "搜索框" -> ["搜索", "框", "搜索框"]
    - "小米SU7车友圈" -> ["小米", "SU7", "车友圈", "小米SU7", "SU7车友圈"]
    - "搜索按钮" -> ["搜索", "按钮", "搜索按钮"]
    """
    if not keyword or len(keyword) < 2:
        return [keyword] if keyword else []
    
    parts = []
    
    # 添加完整关键词
    parts.append(keyword)
    
    # 提取中文部分（连续的中文字符）
    chinese_parts = []
    current_chinese = ""
    for char in keyword:
        if '\u4e00' <= char <= '\u9fff':  # 中文字符范围
            current_chinese += char
        else:
            if len(current_chinese) >= 2:
                chinese_parts.append(current_chinese)
            current_chinese = ""
            # 非中文字符也作为独立部分
            if char.isalnum():
                parts.append(char)
    if len(current_chinese) >= 2:
        chinese_parts.append(current_chinese)
    
    # 提取中文部分的子串
    for chinese_part in chinese_parts:
        if len(chinese_part) >= 2:
            # 添加完整的中文部分
            if chinese_part not in parts:
                parts.append(chinese_part)
            # 对于长度>=4的中文部分，提取前2个和后2个字符
            if len(chinese_part) >= 4:
                prefix = chinese_part[:2]
                suffix = chinese_part[-2:]
                if prefix not in parts:
                    parts.append(prefix)
                if suffix not in parts:
                    parts.append(suffix)
            # 对于长度>=3的中文部分，提取前2个字符
            elif len(chinese_part) >= 3:
                prefix = chinese_part[:2]
                if prefix not in parts:
                    parts.append(prefix)
    
    # 提取英文/数字部分（连续的字母数字）
    import re
    alnum_parts = re.findall(r'[a-zA-Z0-9]+', keyword)
    for alnum_part in alnum_parts:
        if len(alnum_part) >= 2 and alnum_part not in parts:
            parts.append(alnum_part)
        # 对于长度>=4的英文部分，提取前3个字符
        if len(alnum_part) >= 4:
            prefix = alnum_part[:3]
            if prefix not in parts:
                parts.append(prefix)
    
    # 去重并过滤空值
    parts = [p for p in parts if p and len(p) >= 2]
    return list(set(parts))

    return header_lines, line_infos, uid_order


def _add_ancestor_chain(idx_to_info: dict, target_indices: set) -> None:
    """为 target_indices 中的行添加其父级链。idx_to_info: idx -> {indent, ...}"""
    for idx in list(target_indices):
        info = idx_to_info.get(idx)
        if not info:
            continue
        indent = info["indent"]
        for j in range(idx - 1, -1, -1):
            j_info = idx_to_info.get(j)
            if j_info and j_info["indent"] < indent:
                target_indices.add(j)
                indent = j_info["indent"]
                if indent == 0:
                    break


def _add_children_until_sibling(start_idx: int, idx_to_info: dict, lines_len: int, target_set: set) -> None:
    """为 start_idx 行添加子节点，直到遇到同级或更浅缩进"""
    start_info = idx_to_info.get(start_idx)
    if not start_info:
        return
    start_indent = start_info["indent"]
    for j in range(start_idx + 1, lines_len):
        j_info = idx_to_info.get(j)
        if not j_info:
            continue
        if j_info["indent"] <= start_indent:
            break
        target_set.add(j)


def _select_snapshot_indices(
    line_infos: list,
    uid_order: list,
    lines_count: int,
    keywords: Optional[List[str]],
    include_interactive_ancestors: bool
) -> set:
    """选定需要保留的行索引
    
    策略：
    1. 关键词匹配行及其父级链、子节点
    2. 按原始顺序去重输出
    """
    idx_to_info = {info["idx"]: info for info in line_infos}

    # 关键词匹配行
    keyword_indices = {info["idx"] for info in line_infos if info["keywords_matched"]}
    if include_interactive_ancestors and keyword_indices:
        _add_ancestor_chain(idx_to_info, keyword_indices)

    # 为关键词行添加子节点
    selected = keyword_indices
    for idx in list(keyword_indices):
        _add_children_until_sibling(idx, idx_to_info, lines_count, selected)

    return selected


def extract_keyword_relevant_snapshot(
    snapshot: Union[str, dict, list, Any],
    keywords: Optional[List[str]] = None,
    max_output_length: Optional[int] = None,
    include_interactive_ancestors: bool = True,
    case_sensitive: bool = False,
) -> str:
    """
    从页面快照中提取与关键词相关的内容，减轻提示词过长。
    专用于 click、fill、hover 等需要元素定位的场景。

    策略：
    1. 关键词匹配行及其父级链、子节点
    2. 按原始顺序去重输出
    3. 如果超过长度限制，优先保留关键词匹配行及其直接上下文
    
    注意：快照的【开头部分】和【结尾部分】由调用方通过 smart_format_page_info 等函数处理，
    本函数只负责提取与关键词相关的行。
    """
    raw = normalize_snapshot_to_str(snapshot)
    if not raw:
        return ""

    lines = raw.split("\n")
    if not lines:
        return raw

    header_lines, line_infos, uid_order = _parse_snapshot_lines(
        lines, keywords, case_sensitive
    )
    selected = _select_snapshot_indices(
        line_infos, uid_order, len(lines), keywords, include_interactive_ancestors
    )

    # 识别关键词匹配的行索引（最高优先级）
    keyword_indices = {info["idx"] for info in line_infos if info["keywords_matched"]}
    
    result_lines = list(header_lines)
    # 记录每行对应的原始索引，用于后续优先保留
    line_to_original_idx = {}
    for info in line_infos:
        if info["idx"] in selected:
            result_lines.append(info["line"])
            line_to_original_idx[len(result_lines) - 1] = info["idx"]

    out = "\n".join(result_lines)
    
    # 如果超过长度限制，智能截断：优先保留关键词匹配行
    if max_output_length and len(out) > max_output_length:
        if keyword_indices:
            # 找到关键词匹配行在 result_lines 中的位置
            keyword_line_positions = []
            header_len = len(header_lines)
            for i in range(header_len, len(result_lines)):
                if line_to_original_idx.get(i) in keyword_indices:
                    keyword_line_positions.append(i)
            
            if keyword_line_positions:
                # 策略：优先保留关键词行及其前后各10行的上下文
                context_lines = 10
                keyword_context_indices = set()
                for pos in keyword_line_positions:
                    start = max(header_len, pos - context_lines)
                    end = min(len(result_lines), pos + context_lines + 1)
                    keyword_context_indices.update(range(start, end))
                
                # 按优先级顺序构建内容：
                # 1. 首先：关键词匹配行及其上下文（最重要）
                keyword_context_lines = [result_lines[i] for i in sorted(keyword_context_indices)]
                keyword_text = "\n".join(keyword_context_lines)
                
                # 2. 然后：头部快照（header_lines）
                header_text = "\n".join(header_lines)
                
                # 3. 最后：其他行的开头部分（如果有空间）
                truncate_marker = f"\n[已截断，完整长度: {len(out)} 字符]"
                
                # 按顺序计算各部分需要的空间
                final_parts = []
                used_length = len(truncate_marker)
                
                # 1. 首先添加关键词上下文（必须保留）
                final_parts.append(keyword_text)
                used_length += len(keyword_text)
                
                # 2. 然后添加 header（如果还有空间）
                remaining_length = max_output_length - used_length
                if remaining_length > len(header_text) + 10:  # 至少预留10字符
                    final_parts.append(header_text)
                    used_length += len(header_text) + 1  # +1 for newline
                    remaining_length = max_output_length - used_length
                elif remaining_length > 50:  # 如果空间不够，至少添加部分 header
                    header_partial = header_text[:remaining_length - 10]
                    final_parts.append(header_partial)
                    used_length += len(header_partial) + 1
                    remaining_length = max_output_length - used_length
                
                # 3. 最后添加其他行的开头部分（如果还有空间）
                if remaining_length > 200:
                    other_indices = sorted(set(range(len(result_lines))) - keyword_context_indices - set(range(header_len)))
                    if other_indices:
                        other_lines = []
                        other_text_len = 0
                        for i in other_indices:
                            line = result_lines[i]
                            line_len = len(line) + 1  # +1 for newline
                            if other_text_len + line_len <= remaining_length - 10:  # 预留10字符
                                other_lines.append(line)
                                other_text_len += line_len
                            else:
                                break
                        if other_lines:
                            final_parts.append("\n".join(other_lines))
                
                out = "\n".join(final_parts) + truncate_marker
            else:
                # 如果找不到关键词行位置，使用简单截断
                out = out[:max_output_length] + f"\n[已截断，完整长度: {len(out)} 字符]"
        else:
            # 没有关键词匹配行，使用简单截断
            out = out[:max_output_length] + f"\n[已截断，完整长度: {len(out)} 字符]"
    
    return out


# ==================== 7. 通用文本截断 ====================

def smart_truncate_text(
    text: str,
    max_length: Optional[int] = None,
    preserve_start: bool = True,
    preserve_end: bool = False,
    config: Optional[TextProcessingConfig] = None
) -> str:
    """智能截取文本，优先保留开头或结尾"""
    config = _get_config(config)
    if max_length is None:
        max_length = config.text_max_length_reflection
    if not text or len(text) <= max_length:
        return text

    trunc_info = f"... [已截断，完整长度: {len(text)} 字符]"
    if preserve_start and not preserve_end:
        return text[:max_length] + trunc_info
    elif preserve_end and not preserve_start:
        return f"[已截断，完整长度: {len(text)} 字符] ..." + text[-max_length:]
    elif preserve_start and preserve_end:
        head_len = int(max_length * config.head_tail_ratio)
        tail_len = max_length - head_len
        return f"{text[:head_len]}{trunc_info}{text[-tail_len:]}"
    return text[:max_length] + "..."


# ==================== 8. 公共 API ====================

def smart_truncate_snapshot(
    snapshot: str,
    max_length: Optional[int] = None,
    config: Optional[TextProcessingConfig] = None
) -> str:
    """
    智能截取页面快照，优先保留关键信息（URL、标题）+ 开头 + 结尾。
    """
    if not snapshot:
        return "无"

    config = _get_config(config)
    if max_length is None:
        max_length = config.snapshot_max_length_reflection
    if len(snapshot) <= max_length:
        return snapshot

    key_info = _build_key_info_from_snapshot(snapshot, config)
    parts = _build_truncated_snapshot_parts(snapshot, key_info, max_length, config)
    return '\n\n'.join(parts)


def extract_url_from_snapshot(
    snapshot: str,
    config: Optional[TextProcessingConfig] = None
) -> Optional[str]:
    """从页面快照中提取 URL，优先在第一行查找"""
    if not snapshot:
        return None
    first_line = snapshot.split('\n')[0] if '\n' in snapshot else snapshot
    url = _extract_first_url_from_text(first_line)
    return url or _extract_first_url_from_text(snapshot)


def smart_format_page_info(
    page_snapshot: Optional[str] = None,
    current_url: Optional[str] = None,
    max_snapshot_length: Optional[int] = None,
    context: str = "reflection",
    config: Optional[TextProcessingConfig] = None,
    skip_truncate: bool = False
) -> str:
    """
    智能格式化页面信息：URL + 快照。
    context 可选: 'reflection', 'judge', 'execution', 'replan'
    skip_truncate: 如果为 True，跳过截断（用于已经提取过关键词相关内容的快照）
    """
    config = _get_config(config)
    if max_snapshot_length is None:
        max_snapshot_length = config.get_snapshot_max_length(context)

    parts = []
    snapshot_url = extract_url_from_snapshot(page_snapshot, config=config) if page_snapshot else None
    url = snapshot_url or current_url

    if url:
        if snapshot_url and current_url and snapshot_url != current_url:
            parts.append(f"**当前URL（重要，来自页面快照）**: {snapshot_url}")
            parts.append(f"**状态中记录的URL（可能已过时）**: {current_url}")
        else:
            parts.append(f"**当前URL（重要）**: {url}")

    if page_snapshot:
        # browser-use 风格：单行截断 + 大快照可交互元素过滤（仅 execution 时）
        # skip_truncate 表示已做关键词提取，不触发可交互过滤，但仍做单行截断
        if context == "execution":
            page_snapshot = apply_snapshot_browser_use_optimizations(
                page_snapshot,
                keywords=["_"] if skip_truncate else None,  # 有关键词时跳过可交互过滤
                config=config,
            )
        # 若超过最大长度，必须截断以控制 token（150000 上限）。关键词内容优先保留
        if len(page_snapshot) <= max_snapshot_length:
            smart_snapshot = page_snapshot
        elif "【关键词相关内容】" in page_snapshot:
            # 关键词格式：保留【关键信息】+【关键词相关内容】，仅截断超长部分
            marker = "【关键词相关内容】\n"
            idx = page_snapshot.find(marker)
            if idx >= 0:
                header_part = page_snapshot[: idx + len(marker)]
                keyword_part = page_snapshot[idx + len(marker) :]
                remain = max_snapshot_length - len(header_part) - 50  # 预留截断标记
                if remain > 0 and len(keyword_part) > remain:
                    smart_snapshot = header_part + keyword_part[:remain] + f"\n[已截断，完整长度: {len(page_snapshot)} 字符]"
                else:
                    smart_snapshot = page_snapshot[:max_snapshot_length] + f"\n[已截断，完整长度: {len(page_snapshot)} 字符]"
            else:
                smart_snapshot = smart_truncate_snapshot(
                    page_snapshot, max_length=max_snapshot_length, config=config
                )
        else:
            smart_snapshot = smart_truncate_snapshot(
                page_snapshot, max_length=max_snapshot_length, config=config
            )
        if context == 'execution':
            parts.append(f"页面快照（用于元素定位）:\n{smart_snapshot}")
            parts.append("\n**元素定位说明**:")
            parts.append("- 使用 'uid=X_Y' 格式，**必须来自上方当前快照**")
            parts.append("- click 链接/入口：选 link 类型；click 按钮：选 button 类型；fill 输入：选 textbox 类型")
            parts.append("- 禁止选 StaticText 类型执行 click 或 fill（StaticText 不可点击、不可输入）")
            parts.append("- 按元素类型+文本双重匹配，避免同页相似元素混淆")
            parts.append("- 如果元素有预填内容，fill操作会自动替换")
        else:
            parts.append(f"页面快照:\n{smart_snapshot}")
    elif not url:
        parts.append("页面快照: 无")

    return "\n".join(parts) if parts else "页面信息: 无"
