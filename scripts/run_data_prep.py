#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据准备 e2e CLI（plan Phase 1 退出标准验证）。

用法：
    python -X utf8 scripts/run_data_prep.py --url https://example.com
    python -X utf8 scripts/run_data_prep.py --keywords 小米SU7 --max-items 10

产出：downloads/<task_id>/{manifest.json, raw/, parsed/, clean/, rejects/, lineage/, quality_report.json, schema.json, trace.json}
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
import uuid

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# UTF-8 基线（ADR-0006）
for _s in (sys.stdout, sys.stderr):
    _enc = (getattr(_s, "encoding", "") or "").lower().replace("-", "")
    if _enc != "utf8":
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

from src.data_prep.models import (  # noqa: E402
    DataPrepTaskSpec,
    OutputFormat,
    QualityPolicy,
    Recipe,
    SourceLimits,
    SourceSpec,
    SourceType,
    SelectionSpec,
)
from src.data_prep.graph import run_data_prep  # noqa: E402


def build_spec(args) -> DataPrepTaskSpec:
    if args.url:
        source = SourceSpec(
            source_id="web-1",
            source_type=SourceType.WEB,
            locator=args.url,
            limits=SourceLimits(max_records=args.max_items) if args.max_items else None,
            options={"max_items": args.max_items or 30},
        )
        keywords = []
    else:
        source = SourceSpec(
            source_id="web-search-1",
            source_type=SourceType.WEB,
            locator="",  # 关键词发现型
            limits=SourceLimits(max_records=args.max_items) if args.max_items else None,
            options={"max_items": args.max_items or 30, "keywords": args.keywords},
        )
        keywords = args.keywords

    return DataPrepTaskSpec(
        intent=args.intent or (f"采集 {args.url}" if args.url else f"搜索 {keywords}"),
        sources=[source],
        selection=SelectionSpec(keywords=keywords),
        cleaning_recipe=Recipe(),  # 空 Recipe -> 用默认网页规则
        quality_policy=QualityPolicy(),
        outputs=[OutputFormat.JSONL, OutputFormat.PARQUET, OutputFormat.CSV],
    )


async def main_async(args) -> int:
    spec = build_spec(args)
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    print(f"任务 ID: {task_id}")
    print(f"意图: {spec.intent}")
    print(f"数据源: {spec.sources[0].source_type.value} | 定位: {spec.sources[0].locator or '(关键词发现)'}")
    print("-" * 60)

    result = await run_data_prep(spec, task_id)

    print("=" * 60)
    if result.get("error"):
        print(f"❌ 失败: {result['error']}")
        return 1

    counts = result.get("record_counts") or {}
    print(f"状态: {result.get('status', '?')}")
    print(f"记录账本: {counts}")
    print(f"清洗规则统计:")
    for s in result.get("rule_stats") or []:
        print(f"  {s['rule_id']}: 输入{s['input']} -> 输出{s['output']}, 隔离{s['isolated']}, 合并{s['merged']}")

    quality = result.get("quality")
    if quality:
        from src.quality.report import to_human_summary
        print("-" * 60)
        print(to_human_summary(quality))

    print("-" * 60)
    print(f"Manifest: downloads/{result.get('manifest_path', '?')}")
    print(f"产出文件: {len(result.get('outputs') or [])} 个")
    for o in result.get("outputs") or []:
        print(f"  - {o['format']}: downloads/{o['path']} ({o['records']} 条)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="数据准备 e2e CLI")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="目标 URL")
    g.add_argument("--keywords", nargs="+", help="搜索关键词")
    p.add_argument("--max-items", type=int, default=30, help="最大采集条数")
    p.add_argument("--intent", help="意图描述")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
