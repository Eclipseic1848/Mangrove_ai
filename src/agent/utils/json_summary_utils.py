"""
任务中产生的 JSON 文件摘要：从本任务工具返回中识别「产出」的 JSON 文件，便于前端展示。

根本原因与方案：
- 根因：此前用正则扫描整段 ToolMessage content，同一段 JSON 里会同时出现 file_path、file_name、
  output_file、input_file 等多种形式（如 "downloads/懂车帝/dcd_article.json" 与 "dcd_article.json"），
  被解析成不同绝对路径，导致同一逻辑文件在摘要里重复出现。
- 方案：优先从工具返回的「结构化 JSON」里取「产出路径」单一来源（data.file_path 或 output_file），
  每个工具调用只取一条规范路径；仅当无法解析为约定格式时才回退到正则，并按 (工具名, 文件名) 去重。
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from langchain_core.messages import BaseMessage, ToolMessage

logger = logging.getLogger(__name__)

MAX_JSON_FILE_CHARS = 8000
MAX_SUMMARY_PER_FILE = 1200
MAX_TOTAL_JSON_SUMMARY_CHARS = 4000

# 返回格式为 { success, message, data: { file_path } } 的工具（只取 data.file_path 作为产出）
TOOLS_WITH_DATA_FILE_PATH = frozenset({
    "browser_extract_autohome_post_detail",
    "browser_extract_autohome_chejiahao_info",
    "browser_extract_dcd_post_detail",
    "browser_extract_dcd_video",
})
# 返回格式为扁平 JSON 且用 output_file 表示产出路径的工具（不取 input_file，避免把「输入」当产出）
TOOLS_WITH_OUTPUT_FILE = frozenset({
    "browser_analyze_voc",
    "browser_filter_voc",
    "browser_analyze_video",
})

JSON_PATH_PATTERN = re.compile(r'["\']?([^\s"\']+\.json)["\']?')


def _normalize_and_resolve_path(p: str, cwd: Path) -> Optional[Tuple[Path, str]]:
    """将路径规范为绝对路径并 resolve；返回 (Path, 用于展示的绝对路径字符串)，失败返回 None。"""
    if not p or len(p) > 500:
        return None
    try:
        path = Path(p.strip().strip('"\''))
        if not path.is_absolute():
            path = (cwd / path).resolve()
        else:
            path = path.resolve()
        return (path, str(path))
    except Exception:
        return None


def _is_canonical_display_path(path_str: str) -> bool:
    """是否更推荐作为展示的路径（含 downloads 或 analysis）。"""
    return "downloads" in path_str or "analysis" in path_str


def _structured_output_path(tool_name: str, raw: str, cwd: Path) -> Optional[Tuple[str, str]]:
    """
    从工具返回的 JSON 中解析「本工具产出的文件」规范路径（单一来源，避免重复）。
    返回 (display_path, tool_name) 或 None。
    """
    tool_name = (tool_name or "").strip()
    if not raw or not raw.strip():
        return None
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None
        # 格式一：{ success, message, data: { file_path, ... } }
        if tool_name in TOOLS_WITH_DATA_FILE_PATH:
            data = obj.get("data")
            if isinstance(data, dict):
                path = data.get("file_path")
                if isinstance(path, str) and path.strip().endswith(".json"):
                    t = _normalize_and_resolve_path(path.strip(), cwd)
                    if t:
                        return (t[1], tool_name)
        # 格式二：扁平 JSON，产出在 output_file（不取 input_file）
        if tool_name in TOOLS_WITH_OUTPUT_FILE:
            path = obj.get("output_file")
            if isinstance(path, str) and path.strip().endswith(".json"):
                t = _normalize_and_resolve_path(path.strip(), cwd)
                if t:
                    return (t[1], tool_name)
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _collect_json_paths(messages: List[BaseMessage]) -> List[dict]:
    """
    先按「结构化 JSON」取每条工具返回的单一产出路径；无法解析时再回退到正则，并按 (工具名, 文件名) 去重。
    这样同一工具、同一逻辑文件只对应摘要中的一条。
    """
    # resolved_path -> { "path": 展示路径, "tool_name": 工具名 }，用 resolved 去重
    by_resolved: dict[str, dict] = {}
    cwd = Path.cwd()
    tool_count = 0
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        tool_count += 1
        name = (getattr(m, "name", None) or "").strip() or "工具"
        raw = str(getattr(m, "content", None) or "").strip()
        if not raw:
            continue
        # 1) 优先：从约定 JSON 中取单一产出路径
        structured = _structured_output_path(name, raw, cwd)
        if structured is not None:
            display_path, tool_name = structured
            path_obj = Path(display_path)
            resolved = str(path_obj.resolve())
            if resolved not in by_resolved:
                by_resolved[resolved] = {"path": display_path, "tool_name": tool_name}
                logger.info("[JSON摘要] 从结构化返回解析到产出 JSON: %s -> %s", tool_name, path_obj.name)
            else:
                cur = by_resolved[resolved]["path"]
                if _is_canonical_display_path(display_path) and not _is_canonical_display_path(cur):
                    by_resolved[resolved]["path"] = display_path
            continue
        # 2) 回退：正则匹配 .json，按 (工具名, 文件名) 去重
        for match in JSON_PATH_PATTERN.finditer(raw):
            p = match.group(1).strip().strip('"\'')
            t = _normalize_and_resolve_path(p, cwd)
            if t is None:
                continue
            path_obj, abs_path = t
            resolved = str(path_obj.resolve())
            # 用 resolved 去重；若已存在则只更新展示路径（优先规范路径）
            if resolved not in by_resolved:
                by_resolved[resolved] = {"path": abs_path, "tool_name": name}
                logger.info("[JSON摘要] 从正则匹配到工具产出 JSON: %s -> %s", name, path_obj.name)
            else:
                cur = by_resolved[resolved]["path"]
                if _is_canonical_display_path(abs_path) and not _is_canonical_display_path(cur):
                    by_resolved[resolved]["path"] = abs_path
    result = list(by_resolved.values())
    if result:
        logger.info("[JSON摘要] 共扫描 %d 条工具返回，去重后 %d 个 JSON 文件", tool_count, len(result))
    return result


def _read_preview(path: str) -> str:
    """读 JSON 文件，优先取 summary/message/content 等字段，否则取键名预览。"""
    # 已禁用：不再读取 JSON 文件内容，仅返回路径说明
    return f"（已禁用读取 JSON 文件）{path}"
    # try:
    #     raw = Path(path).read_text(encoding="utf-8", errors="ignore")[:MAX_JSON_FILE_CHARS]
    #     data = json.loads(raw)
    #     if not isinstance(data, dict):
    #         return raw[:MAX_SUMMARY_PER_FILE] + ("..." if len(raw) >= MAX_SUMMARY_PER_FILE else "")
    #     for key in ("summary", "message", "content", "result", "text"):
    #         val = data.get(key)
    #         if val and isinstance(val, str) and val.strip():
    #             s = val.strip()[:MAX_SUMMARY_PER_FILE]
    #             return s + "..." if len(val) > MAX_SUMMARY_PER_FILE else s
    #     return "、".join(list(data.keys())[:8]) + " 等字段"
    # except Exception as e:
    #     logger.debug("读 JSON 摘要失败 %s: %s", path, e)
    #     return ""


def summarize_task_jsons_for_frontend(
    messages: List[BaseMessage],
    max_per_file: int = MAX_SUMMARY_PER_FILE,
    max_total: int = MAX_TOTAL_JSON_SUMMARY_CHARS,
) -> str:
    """从本任务工具返回中检测 .json 路径，读文件做简短摘要，拼到 final_result 便于前端展示。"""
    items = _collect_json_paths(messages)
    if not items:
        logger.debug("[JSON摘要] 未检测到 JSON 文件，跳过摘要")
        return ""
    parts = []
    total = 0
    for it in items:
        path, name = it["path"], it["tool_name"]
        preview = _read_preview(path)
        line = f"- JSON 数据（{name}）：{preview or path}"
        if len(preview) > max_per_file:
            line = f"- JSON 数据（{name}）：{preview[:max_per_file]}..."
        parts.append(line)
        total += len(line) + 1
        if total >= max_total:
            break
    body = "\n".join(parts)
    if len(body) > max_total:
        body = body[: max_total - 20 ] + "\n..."
    out = f"\n\n【本任务产生的数据文件摘要】\n{body}"
    logger.info("[JSON摘要] 已生成数据文件摘要，共 %d 条、%d 字，已拼入 final_result", len(parts), len(out))
    return out
