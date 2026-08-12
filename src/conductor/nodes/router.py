"""路由节点：按 TaskSpec 选出可用且适用的采集器候选（按优先级排序）。"""
from __future__ import annotations

from typing import Any, Dict

from src.collectors import select_collectors

from ..state import ConductorState


async def router_node(state: ConductorState) -> Dict[str, Any]:
    spec = state["task_spec"]
    candidates = [c.name for c in select_collectors(spec)]
    return {"collector_candidates": candidates}
