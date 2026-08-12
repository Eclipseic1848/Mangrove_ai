#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""意图理解/规划评测集（P0-3）：黄金样例回归，改 intent/planner prompt 必须先过本评测。

与确定性单测不同，本脚本真实调用 LLM（走 .env 默认供应商），评估意图→规划链路的
端到端判断质量：数据类型、分析类型、平台识别、澄清行为是否符合预期。

运行：python scripts/eval_intent.py [provider]   # provider 可选：deepseek/qwen/local
通过标准：准确率 ≥ 80%（LLM 有随机性，个别波动可接受；大面积失败=prompt 回归）。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.nodes.intent import intent_node
from src.conductor.nodes.planner import planner_node

# ---------------------------------------------------------------
# 黄金样例：input → 期望（字段缺省表示不检查该项）
#   clarify:   期望在 intent 阶段追问澄清（模糊输入）
#   data_type: 期望的 TaskSpec.data_type 值（str）
#   voc:       期望是否走 VOC（True=analysis_type 应为 voc；False=不应为 voc）
#   platform:  期望 platforms 中包含的子串
#   keyword:   期望 keywords 中包含的子串
# 注：输入里带上数量（如"采集20条"），避免 planner 的数量追问打断评测。
# ---------------------------------------------------------------
CASES = [
    # --- 真 VOC（口碑/槽点） ---
    {"input": "帮我采集20条小米SU7的用户口碑评论，做槽点分析", "data_type": "comment", "voc": True, "keyword": "SU7"},
    {"input": "去小红书搜理想L6的用户吐槽，采30条，看看大家主要不满意什么", "platform": "小红书", "voc": True},
    {"input": "分析一下问界M7的差评，采集50条，总结用户投诉的核心问题", "voc": True},
    # --- 伪 VOC（历史误判 case：含"评论"二字但不是口碑分析） ---
    {"input": "采集20条关于昨晚欧冠比赛的赛事评论文章并总结要点", "voc": False},
    {"input": "找10篇解读最新房地产政策的评论文章，梳理专家观点", "voc": False},
    # --- 招投标 ---
    {"input": "帮我扫一下最近一周的储能相关招标公告，采集30条", "data_type": "bid", "voc": False},
    {"input": "采集20条医疗设备采购标讯，整理成扫标表", "data_type": "bid"},
    # --- 新闻/文章 ---
    {"input": "全网搜20篇关于固态电池量产的最新报道并汇总", "data_type": "article", "voc": False},
    {"input": "采集15篇分析2026世界杯夺冠热门的文章", "data_type": "article"},
    # --- 商品 ---
    {"input": "采集20条京东上华为MateBook的商品信息，对比参数和卖点", "data_type": "product", "platform": "京东"},
    # --- 社媒平台识别 ---
    {"input": "去抖音搜刘畊宏健身，采20条视频信息做个汇总", "platform": "抖音"},
    {"input": "帮我在B站搜黑神话悟空的视频，采15条看看热度", "platform": "b站"},
    # --- 指定站点 ---
    {"input": "去汽车之家搜比亚迪海豹的资讯，采20条", "platform": "汽车之家"},
    # --- URL 任务 ---
    {"input": "抓取这个网页的正文并总结：https://example.com/news/123", "clarify": False},
    # --- 模糊输入应澄清 ---
    {"input": "帮我查点数据", "clarify": True},
    {"input": "分析一下", "clarify": True},
]

_PASS_THRESHOLD = 0.8


async def _run_case(case: dict, provider: str | None) -> tuple[bool, str]:
    """跑单条样例：intent → (planner)，与期望比对。返回 (是否通过, 说明)。"""
    text = case["input"]
    state = {"user_input": text, "messages": [{"role": "user", "content": text}], "provider": provider}
    out = await intent_node(state)

    if out.get("error"):
        return False, f"模型调用失败：{out['error']}"

    want_clarify = case.get("clarify")
    if out.get("needs_clarification"):
        if want_clarify is True:
            return True, "按预期追问澄清"
        return False, f"不该澄清却追问了：{out.get('clarification_question')}"
    if want_clarify is True:
        return False, "模糊输入未追问澄清"

    # 进规划：拿 TaskSpec 比对字段
    state.update(out)
    pout = await planner_node(state)
    spec = pout.get("task_spec")
    if spec is None:
        return False, "planner 未产出 TaskSpec"

    problems = []
    if "data_type" in case and spec.data_type.value != case["data_type"]:
        problems.append(f"data_type={spec.data_type.value}（期望 {case['data_type']}）")
    if case.get("voc") is True and spec.analysis_type.value != "voc":
        problems.append(f"analysis_type={spec.analysis_type.value}（期望 voc）")
    if case.get("voc") is False and spec.analysis_type.value == "voc":
        problems.append("analysis_type=voc（期望非 voc，历史误判 case）")
    if "platform" in case:
        plats = " ".join(spec.platforms).lower()
        if case["platform"].lower() not in plats:
            problems.append(f"platforms={spec.platforms}（期望含 {case['platform']}）")
    if "keyword" in case:
        kws = " ".join(spec.keywords).lower()
        if case["keyword"].lower() not in kws:
            problems.append(f"keywords={spec.keywords}（期望含 {case['keyword']}）")

    if problems:
        return False, "；".join(problems)
    return True, f"data_type={spec.data_type.value} analysis={spec.analysis_type.value} platforms={spec.platforms}"


async def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"意图评测集：{len(CASES)} 条样例，供应商={provider or '默认(.env)'}\n" + "=" * 60)
    passed = 0
    for i, case in enumerate(CASES, 1):
        try:
            ok, msg = await _run_case(case, provider)
        except Exception as e:
            ok, msg = False, f"异常：{e}"
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"[{mark}] #{i} {case['input'][:36]}\n       → {msg}")
    acc = passed / len(CASES)
    print("=" * 60 + f"\n准确率：{passed}/{len(CASES)} = {acc:.0%}（通过线 {_PASS_THRESHOLD:.0%}）")
    return 0 if acc >= _PASS_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
