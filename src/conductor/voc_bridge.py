"""
VOC 分析桥接：把清洗后的数据交给 src/services/voc_processor 引擎处理。

voc_processor 面向单文档（读取 post/topic 的 summary/content），依赖其本地模型
（见 src/services/voc_processor/llm/settings.py 的 VLLM_CFG）。本桥接把多条数据合并为
一份文档喂入，读取解析结果并格式化为文本。

设计为"尽力而为"：未开启开关、依赖缺失、本地模型不可达等任何情况都返回 None，
由 analyze 节点回退到可切换模型(deepseek/qwen)的 LLM VOC 分析。
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)


def _run_sync(data: List[Dict[str, Any]]) -> Optional[str]:
    """同步执行 voc_processor，返回格式化文本；失败返回 None。"""
    # 延迟导入，避免未使用时引入其本地模型依赖
    from src.services.voc_processor.processor import process_voc_file

    # 合并所有清洗后的正文为一份文档（voc_processor 按单文档处理）
    merged = "\n\n".join(
        (it.get("content") or "").strip() for it in data if (it.get("content") or "").strip()
    )
    if not merged:
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="voc_"))
    in_file = tmp_dir / "input.json"
    out_file = tmp_dir / "output.json"
    # voc_processor 兼容 data["post"].content 结构
    in_file.write_text(
        json.dumps({"post": {"content": merged}}, ensure_ascii=False), encoding="utf-8"
    )

    result = process_voc_file(str(in_file), str(out_file))
    if not result.get("success") or not result.get("output_file"):
        logger.info("voc_processor 未成功：%s", result.get("message"))
        return None

    try:
        analyzed = json.loads(Path(result["output_file"]).read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取 voc_processor 输出失败：%s", e)
        return None

    # 把结构化解析结果格式化为可读 Markdown
    body = json.dumps(analyzed, ensure_ascii=False, indent=2)
    return f"> 本节由 voc_processor 引擎生成。\n\n```json\n{body}\n```"


async def run_voc_processor(data: List[Dict[str, Any]]) -> Optional[str]:
    """异步入口：在线程池中运行 voc_processor。任何异常都返回 None（触发回退）。"""
    if not settings.voc_use_processor:
        return None
    if not data:
        return None
    try:
        return await asyncio.to_thread(_run_sync, data)
    except Exception as e:
        logger.info("voc_processor 桥接失败，回退 LLM VOC：%s", e)
        return None
