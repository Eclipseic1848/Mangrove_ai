# -*- coding: utf-8 -*-
"""对已经独立安装的依赖组执行代表性 import smoke。"""
from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 脚本以文件路径执行时 sys.path[0] 是 scripts/ci，必须显式暴露产品导入 seam。
sys.path.insert(0, str(PROJECT_ROOT))
GROUP_IMPORTS: dict[str, tuple[str, ...]] = {
    "runtime": (
        "fastapi",
        "langgraph",
        "pydantic",
        "sqlalchemy",
        "uvicorn",
    ),
    "collectors": (
        "crawl4ai",
        "ddgs",
        "firecrawl",
        "msgspec",
        "patchright",
        "playwright",
        "yt_dlp",
    ),
    "dev": (
        "hypothesis",
        "pytest",
        "testcontainers",
    ),
    "evaluation": (
        "docxtpl",
        "jiwer",
        "markitdown",
    ),
    "gpu": (),
}


def _verify_scrapling_runtime_seams() -> list[str]:
    # 不能只导入 fetchers 包：这些符号和 Mangrove 可用性 seam 会触发惰性依赖解析。
    from scrapling.fetchers import Fetcher, StealthyFetcher

    if Fetcher is None or StealthyFetcher is None:
        raise RuntimeError("Scrapling fetchers 未完整加载")
    collector_module = import_module("src.collectors.scrapling_collector")
    collector = collector_module.ScraplingCollector()
    if not collector.is_available():
        raise RuntimeError("Mangrove ScraplingCollector 不可用")
    return [
        "scrapling.fetchers.Fetcher",
        "scrapling.fetchers.StealthyFetcher",
        "src.collectors.scrapling_collector:available",
    ]


def _verify_empty_gpu_overlay() -> None:
    content = (PROJECT_ROOT / "requirements-gpu.txt").read_text(encoding="utf-8")
    declarations = [
        raw_line.split("#", 1)[0].strip()
        for raw_line in content.splitlines()
        if raw_line.split("#", 1)[0].strip()
    ]
    if declarations:
        raise RuntimeError(f"GPU overlay 应保持为空，实际为: {declarations}")


def main() -> int:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--group", choices=sorted(GROUP_IMPORTS))
    selection.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        print("\n".join(sorted(GROUP_IMPORTS)))
        return 0

    group = str(args.group)
    if group == "gpu":
        _verify_empty_gpu_overlay()
    imported = []
    for module_name in GROUP_IMPORTS[group]:
        import_module(module_name)
        imported.append(module_name)
    if group == "collectors":
        imported.extend(_verify_scrapling_runtime_seams())
    print(json.dumps({"group": group, "imports": imported, "status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
