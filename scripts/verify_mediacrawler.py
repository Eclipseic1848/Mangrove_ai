#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
验证 mediacrawler 采集器（直连代码路径，不走 LLM）。

构造一个 VOC 抖音任务，调用 SocialMediaCollector().collect(spec)，
这会执行与 Agent 完全一致的子进程命令（含 --get_comment yes）、登录、
JSON 读取与"评论优先"解析。首次运行会弹出二维码窗口，需用抖音 App 扫码。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.conductor.task_spec import AnalysisType, DataType, TaskSpec  # noqa: E402
from src.collectors.social_media_collector import SocialMediaCollector  # noqa: E402


async def main():
    spec = TaskSpec(
        intent="抖音上小米SU7的用户槽点",
        platforms=["抖音"],
        keywords=["小米SU7"],
        data_type=DataType.COMMENT,
        analysis_type=AnalysisType.VOC,
        max_items=10,
    )
    print("[验证] 开始调用 mediacrawler 采集器（会弹二维码，请扫码）...", flush=True)
    col = SocialMediaCollector()
    print("[验证] is_available =", col.is_available(), flush=True)
    result = await col.collect(spec)
    print("=" * 60, flush=True)
    print("success =", result.success, flush=True)
    print("message =", result.message, flush=True)
    print("items   =", len(result.items), flush=True)
    for i, it in enumerate(result.items[:5], 1):
        kind = it.metadata.get("kind")
        sample = (it.content or "").replace("\n", " ")[:80]
        print(f"  [{i}] kind={kind} title={it.title!r} content={sample!r}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
