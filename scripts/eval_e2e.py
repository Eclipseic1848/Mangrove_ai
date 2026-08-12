#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端评测（P1-4）：黄金任务集真实跑完整流水线，输出采集成功率/模板命中率报表。

与单测不同，本脚本真实联网采集 + 真实调 LLM（意图/规划/分析/质检全链路），
用于定期量化 Agent 的核心能力基线：
- 采集成功率：清洗后数据量 ≥ 任务要求的最低条数；
- 模板命中率：analysis_source 命中该任务预期的模板来源集合；
- 质量均分：Checker 打分的平均值（仅统计有产出的任务）。

运行：python scripts/eval_e2e.py [provider]   # provider 可选：deepseek/qwen/local，缺省用 .env
报表：控制台表格 + data/eval/e2e_<时间戳>.json（定期跑可比对历史趋势）。
定期跑：可用 Windows 任务计划程序或调度器定时执行本脚本。
注意：全链路真实执行，单任务超时 600s，整轮约 5~20 分钟；社媒/电商深度采集
依赖 cookie（MC_COOKIE_*/JD_COOKIE），未配时由采集器级联自动降级到全网检索。
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows 管道/重定向下 stdout 默认 GBK，✓/✗ 等符号会 UnicodeEncodeError——显式 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.conductor.graph import run_conductor

# ---------------------------------------------------------------
# 黄金任务集：覆盖 新闻/招投标/商品/VOC/文章/站定向 六类典型场景。
# prompt 必须写明数量（避免 planner 数量追问打断评测）。
#   min_items:      采集成功线（清洗后 ≥ 该数即算采集成功）
#   expect_sources: 预期 analysis_source 集合（builtin=内置领域模板，voc=口碑引擎/模板）
# ---------------------------------------------------------------
GOLDEN_TASKS = [
    {"name": "新闻-新能源汽车", "prompt": "采集3条关于新能源汽车销量的最新新闻，输出新闻汇总报告",
     "min_items": 1, "expect_sources": {"builtin"}},
    {"name": "招投标-医疗设备", "prompt": "搜集3条最近的医疗设备招标公告，整理成标讯报告",
     "min_items": 1, "expect_sources": {"builtin"}},
    {"name": "商品-华为手机", "prompt": "采集3条华为Mate70的商品信息，对比参数和卖点",
     "min_items": 1, "expect_sources": {"builtin"}},
    {"name": "VOC-小米SU7口碑", "prompt": "采集5条小米SU7的用户评价，分析用户口碑和主要槽点",
     "min_items": 1, "expect_sources": {"voc", "builtin"}},
    {"name": "文章-AI Agent趋势", "prompt": "搜集3篇关于AI Agent发展趋势的文章，总结核心要点",
     "min_items": 1, "expect_sources": {"builtin"}},
    {"name": "站定向-懂车帝", "prompt": "去懂车帝搜集3条小米SU7的资讯并总结要点",
     "min_items": 1, "expect_sources": {"builtin"}},
]

_TASK_TIMEOUT = 600  # 单任务超时（秒）：全链路含采集级联+LLM，给足余量


async def _run_task(task: dict, provider: str | None) -> dict:
    """跑单个黄金任务，返回评测记录。"""
    t0 = time.time()
    rec = {"name": task["name"], "prompt": task["prompt"], "collect_ok": False,
           "template_hit": False, "items": 0, "collector": "", "source": "",
           "score": None, "error": "", "ms": 0}
    try:
        state = await asyncio.wait_for(
            run_conductor(task["prompt"], provider=provider, session_id="eval_e2e"),
            timeout=_TASK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        rec["error"] = f"超时(>{_TASK_TIMEOUT}s)"
        rec["ms"] = round((time.time() - t0) * 1000)
        return rec
    except Exception as e:
        rec["error"] = f"执行异常: {e}"
        rec["ms"] = round((time.time() - t0) * 1000)
        return rec

    rec["ms"] = round((time.time() - t0) * 1000)
    if state.get("needs_clarification"):
        rec["error"] = f"被追问澄清: {state.get('clarification_question')}"
        return rec
    if state.get("error"):
        rec["error"] = str(state["error"])

    cleaned = state.get("cleaned_dataset") or []
    rec["items"] = len(cleaned)
    rec["collector"] = state.get("collector_used") or ""
    rec["collect_ok"] = len(cleaned) >= task["min_items"]
    rec["source"] = state.get("analysis_source") or ""
    rec["template_hit"] = rec["source"] in task["expect_sources"]
    q = state.get("quality") or {}
    if q.get("score") is not None:
        rec["score"] = q["score"]
    rec["reruns"] = state.get("analyze_reruns", 0)  # 质检闭环触发情况（P1-2 观测）
    return rec


async def main() -> None:
    provider = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"端到端评测开始：{len(GOLDEN_TASKS)} 个黄金任务，provider={provider or '(.env 默认)'}\n")

    records = []
    for i, task in enumerate(GOLDEN_TASKS, 1):
        print(f"[{i}/{len(GOLDEN_TASKS)}] {task['name']} …", flush=True)
        rec = await _run_task(task, provider)
        status = "✓" if rec["collect_ok"] else "✗"
        tpl = "✓" if rec["template_hit"] else "✗"
        print(f"    采集{status} {rec['items']}条({rec['collector'] or '—'})  "
              f"模板{tpl}({rec['source'] or '—'})  质量分={rec['score'] if rec['score'] is not None else '—'}  "
              f"{rec['ms'] / 1000:.0f}s"
              + (f"  错误: {rec['error']}" if rec["error"] else ""), flush=True)
        records.append(rec)

    # ---- 汇总报表 ----
    n = len(records)
    collect_ok = sum(1 for r in records if r["collect_ok"])
    tpl_hit = sum(1 for r in records if r["template_hit"])
    scores = [r["score"] for r in records if r["score"] is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    reruns = sum(1 for r in records if r.get("reruns"))

    print("\n" + "=" * 60)
    print(f"采集成功率：{collect_ok}/{n} = {collect_ok / n:.0%}")
    print(f"模板命中率：{tpl_hit}/{n} = {tpl_hit / n:.0%}")
    print(f"质量均分：{avg_score if avg_score is not None else '—'}（{len(scores)} 个任务有质检分）")
    print(f"质检闭环触发重跑：{reruns} 个任务")
    print("=" * 60)

    # ---- JSON 报表落盘（定期跑可比对历史趋势）----
    out_dir = PROJECT_ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "provider": provider or "(default)",
        "summary": {"total": n, "collect_ok": collect_ok, "template_hit": tpl_hit,
                    "avg_score": avg_score, "reruns": reruns},
        "tasks": records,
    }
    out_path = out_dir / f"e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报表已保存：{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
