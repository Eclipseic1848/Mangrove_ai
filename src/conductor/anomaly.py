"""
执行异常检测（期3 可观测性补完）。

基于已有的 trace / quality / 采集结果，用确定性规则标出本次运行的异常信号，
帮助快速发现"看似成功、实则有问题"的运行（如采集 0 条、质量不达标、走到末端兜底、节点超时）。
纯函数、零依赖；由 output 节点调用，结果进回执与 state.grade。
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.config.settings import settings

# 末端兜底采集器：实际用到它们说明前面更优的引擎都失败/无数据了
_FALLBACK_COLLECTORS = {"simple_http", "browser"}
# 单节点耗时绝对告警阈值（毫秒）
_SLOW_NODE_MS = 60000


def detect_anomalies(state: Dict[str, Any]) -> List[str]:
    """检查本次运行状态，返回异常信号列表（无异常则空列表）。"""
    out: List[str] = []
    spec = state.get("task_spec")
    data = state.get("cleaned_dataset") or []
    n = len(data)

    # 1) 采集条数异常：0 条，或请求较多却只拿到很少
    max_items = getattr(spec, "max_items", 0) or 0
    if n == 0:
        out.append("采集结果为 0 条")
    elif max_items >= 10 and n < max(3, int(max_items * 0.2)):
        out.append(f"采集条数偏少：仅 {n} 条（请求上限 {max_items}）")

    # 2) 质量评估未通过
    q = state.get("quality") or {}
    score = q.get("score")
    if score is not None and not q.get("passed"):
        out.append(f"质量评估未通过（{score} 分 < 阈值 {settings.checker_pass_threshold}）")

    # 3) 流程报错
    if state.get("error"):
        out.append(f"流程报错：{state['error']}")

    # 4) 采集降级到末端兜底引擎（补采时 collector_used 为 "a+b" 多引擎串，逐个检查）
    used = state.get("collector_used") or ""
    hit = sorted(set(used.split("+")) & _FALLBACK_COLLECTORS)
    if hit:
        out.append(f"采集降级到末端兜底引擎（{'+'.join(hit)}），优选引擎均未取得数据")

    # 5) 单节点耗时异常高
    for t in state.get("trace") or []:
        ms = t.get("ms") or 0
        if ms >= _SLOW_NODE_MS:
            out.append(f"节点 {t.get('node')} 耗时异常：{ms}ms")

    return out
