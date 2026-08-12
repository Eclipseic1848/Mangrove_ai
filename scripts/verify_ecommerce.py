#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""电商采集器联网冒烟测试（真实访问京东免登录接口）。

运行：python scripts/verify_ecommerce.py
说明：京东可能因风控/区域对免登录接口返回空或验证页，本脚本如实打印结果，
不通过不代表代码错误，可能是接口被风控；此时路由会自动降级到 search/firecrawl。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.ecommerce_collector import EcommerceCollector
from src.conductor.task_spec import TaskSpec


async def main():
    c = EcommerceCollector()

    # 场景1：关键词搜索 → 取 SKU → 拉评论
    spec_kw = TaskSpec(intent="京东上 brooks 甘油23 跑鞋的用户评价",
                       platforms=["京东"], keywords=["brooks 甘油23"], max_items=10)
    print("[场景1] 关键词路径：京东 + 'brooks 甘油23'")
    res = await c.collect(spec_kw)
    print(f"  success={res.success}  count={len(res.items)}  msg={res.message}")
    for it in res.items[:3]:
        print(f"    - [{it.metadata.get('score')}星] {it.content[:50]} ({it.metadata.get('spec')})")

    print("=" * 50)
    print("说明：success=False 多为京东风控/区域限制（非代码错误），届时路由自动降级到全网搜索。")


if __name__ == "__main__":
    asyncio.run(main())
