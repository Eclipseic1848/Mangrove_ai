#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mangrove 端到端验证（直接走 astream_conductor 流式入口）。

用一个普通网址跑通整链路：intent→路由→crawl4ai/scrapling 降级→clean→分析→产出，
并打印节点级进度（验证流式）、采集引擎、分析结果、产出文件路径。
使用 .env 中的默认模型供应商（deepseek）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.conductor.graph import astream_conductor  # noqa: E402

TASK = "抓取这个网页的正文并生成一份中文摘要报告：https://baike.baidu.com/item/小米SU7"


async def main():
    print(f"[任务] {TASK}\n" + "=" * 60, flush=True)
    final = {}
    async for kind, payload in astream_conductor(
        user_input=TASK,
        messages=[{"role": "user", "content": TASK}],
        provider=None,  # 用 .env 默认（deepseek）
    ):
        if kind == "node":
            print(f"  ✓ 节点完成: {payload}", flush=True)
        elif kind == "final":
            final = payload

    print("=" * 60, flush=True)
    if final.get("needs_clarification"):
        print("[结果] 触发澄清提问:", final.get("clarification_question"))
        return
    if final.get("error") and not final.get("outputs"):
        print("[结果] 出错:", final.get("error"))
        return

    spec = final.get("task_spec")
    print("[采集引擎]", final.get("collector_used"))
    print("[清洗后条数]", len(final.get("cleaned_dataset", [])))
    print("[分析类型]", getattr(spec, "analysis_type", None))
    analysis = final.get("analysis") or ""
    print("\n[分析结果(前 600 字)]\n" + analysis[:600])
    outputs = final.get("outputs", {})
    print("\n[产出文件]")
    print("  report.md:", outputs.get("report_md"))
    print("  data.json:", outputs.get("json"))
    rp = outputs.get("report_md")
    if rp and Path(rp).exists():
        print("  report.md 大小:", Path(rp).stat().st_size, "字节  ✓ 已落盘")


if __name__ == "__main__":
    asyncio.run(main())
