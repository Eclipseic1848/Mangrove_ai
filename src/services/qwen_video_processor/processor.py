#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen 视频文字提取适配层

通过调用 src/services/qwen_video 的 MCP 客户端能力，提供同步接口供 Agent 工具使用。
当 MCP 返回 success 但 summary 为空时，使用直接 OpenAI API 调用兜底，保证集成到项目时也能解析出视频内容。
当调用方传入 output_file 时，成功后将完整结果保存到该路径（与 browser_analyze_voc 一致，通常为 analysis/抖音/xxx_analysis.json）。
"""

import asyncio
import base64
import json
import logging
import os
import sys
import httpx
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 将 src/services/qwen_video 加入 path，以便导入其 mcp_video_client（不修改该目录下代码）
_project_root = Path(__file__).resolve().parent.parent.parent.parent
_qwen_video_dir = _project_root / "src" / "services" / "qwen_video"
if _qwen_video_dir.exists() and str(_qwen_video_dir) not in sys.path:
    sys.path.insert(0, str(_qwen_video_dir))

# 先检测 mcp 是否可用，再导入 mcp_video_client，避免未安装 mcp 时该模块内部 sys.exit(1) 导致进程退出
_analyze_video_mcp = None
_import_error: Optional[str] = None
try:
    import mcp  # noqa: F401
except ImportError:
    _import_error = "需要安装 mcp 库: pip install mcp"
else:
    try:
        from mcp_video_client import analyze_video_mcp as _analyze_video_mcp  # type: ignore[import-not-found]
    except Exception as e:
        _analyze_video_mcp = None
        _import_error = str(e)

# 直接 API 兜底：在同一进程内调用 OpenAI，避免 MCP 子进程里 content 为空。
_DEFAULT_QUESTION = (
    "请先在内部识别并理解整个视频画面中出现的所有文字内容（包括字幕、标牌、界面文字、弹幕等），"
    "然后基于这些文字内容，用自然、连贯的中文写出一篇完整的讲解稿/文章，"
    "大致复现原视频的讲述逻辑和信息点。\n"
    "要求：\n"
    "1. 输出为一整篇连续的中文文章，不要带时间戳、不要用项目符号列表；\n"
    "2. 内容可以适当合并相近句子，但不要凭空杜撰与画面文字无关的信息；\n"
    "3. 如果有品牌名、车型名、数字等关键信息，请尽量原样保留；\n"
    "4. 语气可以口语化，类似主持人口播文案。"
)


def _analyze_video_direct(video_path: str, question: Optional[str] = None) -> dict:
    """在主进程内直接调用 OpenAI，用于 MCP 返回 success 但 summary 为空时兜底。"""
    try:
        from openai import OpenAI
    except ImportError:
        return {"success": False, "summary": None, "usage": None, "error": "未安装 openai", "error_type": "ImportError"}
    base_url = os.getenv("QWEN_VL_BASE_URL", os.getenv("LLM_BASE_URL", "http://192.168.1.20:6012/v1"))
    model = os.getenv("QWEN_VL_MODEL", os.getenv("LLM_MODEL_NAME", "Qwen3.6-35B-A3B"))
    api_key = os.getenv("QWEN_VL_API_KEY", "not-needed")
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=httpx.Client(trust_env=False, timeout=600),
    )
    video_path = os.path.abspath(os.path.expanduser(video_path))
    if not os.path.isfile(video_path):
        return {"success": False, "summary": None, "usage": None, "error": "文件不存在", "error_type": "ValueError"}
    try:
        with open(video_path, "rb") as f:
            video_data = f.read()
        base64_video = base64.b64encode(video_data).decode("utf-8")
    except Exception as e:
        return {"success": False, "summary": None, "usage": None, "error": str(e), "error_type": type(e).__name__}
    prompt = (question or "").strip() or _DEFAULT_QUESTION
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{base64_video}"}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=2048,
            temperature=0.7,
            top_p=0.9,
            presence_penalty=1.0,
        )
    except Exception as e:
        return {"success": False, "summary": None, "usage": None, "error": str(e), "error_type": type(e).__name__}
    raw = response.choices[0].message.content
    summary = None
    if raw is not None:
        if isinstance(raw, str) and raw.strip():
            summary = raw.strip()
        elif isinstance(raw, list):
            for part in raw:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content") or part.get("value")
                    if t and isinstance(t, str) and t.strip():
                        summary = t.strip()
                        break
    usage = None
    if response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
            "total_tokens": getattr(response.usage, "total_tokens", None),
        }
    return {
        "success": True,
        "summary": summary,
        "usage": usage,
        "error": None,
        "error_type": None,
    }


def analyze_video_file(
    video_path: str,
    question: Optional[str] = None,
    slice_seconds: int = 0,
    output_file: Optional[str] = None,
) -> dict:
    """
    从视频画面中提取文字（调用 external/qwen-video 的 MCP 客户端，内部启动 MCP 服务器）。

    Args:
        video_path: 视频文件路径（本地）
        question: 对视频文字提取和整合方式的说明，可选；默认由 Qwen 使用内置提示输出连贯文章
        slice_seconds: 按多少秒切片处理；0 表示不切片，整体一次性处理
        output_file: 分析结果 JSON 保存路径（可选）。由调用方按 browser_analyze_voc 规则解析，通常为 analysis/抖音/xxx_analysis.json

    Returns:
        dict: 包含 success、message、summary、usage、output_file、file_name 等，风格与 voc_processor 一致
    """
    if _analyze_video_mcp is None:
        return {
            "success": False,
            "message": "视频分析不可用：无法加载 qwen-video 客户端",
            "summary": None,
            "usage": None,
            "error": _import_error or "mcp_video_client 未找到",
            "error_type": "ImportError",
        }

    video_path = os.path.abspath(os.path.expanduser((video_path or "").strip()))
    if not video_path or not os.path.isfile(video_path):
        return {
            "success": False,
            "message": "视频文件路径无效或文件不存在",
            "summary": None,
            "usage": None,
            "error": f"无效路径或非文件: {video_path}",
            "error_type": "ValueError",
        }

    try:
        result = asyncio.run(_analyze_video_mcp(video_path, question, slice_seconds))
    except Exception as e:
        return {
            "success": False,
            "message": f"视频文字提取失败: {e}",
            "summary": None,
            "usage": None,
            "error": str(e),
            "error_type": type(e).__name__,
        }

    if not result.get("success"):
        return {
            "success": False,
            "message": result.get("error", "未知错误"),
            "summary": None,
            "usage": result.get("usage"),
            "error": result.get("error"),
            "error_type": result.get("error_type", "Unknown"),
        }

    # summary 优先用 result["summary"]；部分 MCP/API 可能把正文放在 content/text，做兜底
    summary = result.get("summary")
    if (summary is None or (isinstance(summary, str) and not summary.strip())) and result.get("success"):
        summary = result.get("content") or result.get("text")
    if isinstance(summary, str) and not summary.strip():
        summary = None
    # 集成时 MCP 子进程常返回 success 但 summary 为空；与 test.py 同逻辑的直接 API 兜底，保证能解析出内容
    if summary is None or (isinstance(summary, str) and not summary.strip()):
        logger.info("[qwen_video_processor] MCP 返回 summary 为空，使用与 test.py 相同的直接 API 兜底")
        direct = _analyze_video_direct(video_path, question)
        if direct.get("success") and direct.get("summary"):
            summary = direct["summary"]
            if direct.get("usage"):
                result["usage"] = direct["usage"]
    out_dict = {
        "success": True,
        "message": "视频文字提取完成",
        "summary": summary,
        "usage": result.get("usage"),
        "error": None,
        "error_type": None,
    }
    # 当调用方传入 output_file 时，保存到该路径（与 browser_analyze_voc 一致，通常为 analysis/抖音/xxx_analysis.json）
    output_path_str = (output_file or "").strip()
    if output_path_str:
        try:
            out_path = Path(output_path_str)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(out_dict, f, ensure_ascii=False, indent=2)
            out_dict["output_file"] = str(out_path.resolve())
            out_dict["file_name"] = out_path.name
        except Exception:
            pass
    return out_dict
