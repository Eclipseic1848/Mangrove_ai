"""定时任务节点：当任务带 schedule 且非调度器自身触发时，短路为"安排定时任务"回执。

本节点不执行采集/分析，仅把检测到的 schedule 原始串透传给前端（schedule_request），
由前端/网关完成注册。这样保持依赖单向：scheduler 依赖 conductor，conductor 不反依赖。
"""
from __future__ import annotations

from typing import Any, Dict

from ..state import ConductorState


async def schedule_node(state: ConductorState) -> Dict[str, Any]:
    spec = state["task_spec"]
    schedule = (spec.schedule or "").strip()
    reply = (
        f"已识别为定时任务：「{spec.intent}」\n"
        f"调度计划：{schedule}\n"
        "请确认创建。创建后将按计划自动执行，结果可在产出目录查看；可随时取消。"
    )
    return {"schedule_request": schedule, "reply": reply}
