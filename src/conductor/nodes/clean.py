"""清洗节点：噪声剥离 + 质量门（验证页/乱码检测）+ 去重 + 丢空，保留至多 max_items 条。

逐条截断已移至 analyze._build_blob（仅影响 LLM 上下文，不影响 data.json 落盘）。
质量门（P0-2）：确定性剔除反爬验证页、乱码内容，剔除原因统计进 clean_stats 供白盒透出。"""
from __future__ import annotations

import re
from typing import Any, Dict

from src.collectors._common import is_challenge_page as _is_captcha_page

from ..state import ConductorState
from ..targets import is_direct_video_manifest

# 超短片段（疑似导航/页脚样板）阈值：短于此且不含句读的片段丢弃
_MIN_CONTENT_LEN = 12

_TAG_RE = re.compile(r"<[^>]+>")          # HTML 标签
_WS_RE = re.compile(r"[ \t　]+")          # 行内连续空白（含全角空格）
_BLANK_RE = re.compile(r"\n{3,}")         # 3+ 连续空行 → 2

# 乱码检测：�（解码替换符）或不可打印字符占比超过此比例 → 判为乱码
_GARBLED_RATIO = 0.15


def _denoise(text: str) -> str:
    """去 HTML 标签、折叠空白、清理 markdown 残留。"""
    if not text:
        return ""
    t = _TAG_RE.sub(" ", text)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = _WS_RE.sub(" ", t)
    t = _BLANK_RE.sub("\n\n", t)
    # 去掉每行首尾空白后再拼回，丢弃纯空行造成的前后空白
    t = "\n".join(line.strip() for line in t.splitlines())
    return t.strip()


def _is_boilerplate(text: str) -> bool:
    """超短且无句读的片段视为导航/页脚样板。"""
    if len(text) >= _MIN_CONTENT_LEN:
        return False
    return not any(p in text for p in "。！？.!?，,")


def _is_garbled(text: str) -> bool:
    """乱码检测：解码替换符/不可打印字符占比过高。"""
    if not text:
        return False
    bad = sum(1 for ch in text if ch == "�" or (ord(ch) < 32 and ch not in "\n\r\t"))
    return bad / len(text) > _GARBLED_RATIO


async def clean_node(state: ConductorState) -> Dict[str, Any]:
    raw = state.get("raw_dataset", [])
    spec = state["task_spec"]
    if state.get("project_result"):
        # 项目字段已绑定逐字原文，通用去HTML会改坏JSON中的业务值。
        return {"cleaned_dataset": raw, "clean_stats": {}}
    seen = set()
    cleaned: list[Dict[str, Any]] = []
    # 剔除原因统计（白盒卡片透出，让"为什么少了 N 条"可解释）
    stats: Dict[str, int] = {"空内容": 0, "样板噪声": 0, "验证页": 0, "乱码": 0, "重复": 0, "目标不符": 0}
    direct_video = is_direct_video_manifest(state.get("target_manifest") or [])
    for item in raw:
        if direct_video and not (item.get("metadata") or {}).get("identity_verified"):
            stats["目标不符"] += 1
            continue
        content = _denoise((item.get("content") or "").strip())
        if not content:
            stats["空内容"] += 1
            continue
        if _is_boilerplate(content):
            stats["样板噪声"] += 1
            continue
        if _is_captcha_page(content):
            stats["验证页"] += 1
            continue
        if _is_garbled(content):
            stats["乱码"] += 1
            continue
        # 以 url + 正文前缀 去重
        key = (item.get("url", ""), content[:200])
        if key in seen:
            stats["重复"] += 1
            continue
        seen.add(key)
        # 完整内容保留到 cleaned_dataset（最终写入 data.json），
        # 喂给 LLM 时才由 analyze._build_blob 做逐条截断控 token
        cleaned.append({**item, "content": content})
        if len(cleaned) >= spec.max_items:
            break

    return {
        "cleaned_dataset": cleaned,
        # 只保留非零项，白盒展示更干净
        "clean_stats": {k: v for k, v in stats.items() if v > 0},
    }
