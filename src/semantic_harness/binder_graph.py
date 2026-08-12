# -*- coding: utf-8 -*-
"""批次 2 inspect → bind Graph；不包含执行或交付节点。"""
from __future__ import annotations

import asyncio
from typing import Mapping, Protocol, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from .binder import LocalRerankScoreProvider, bind_semantic_plan
from .inspection_models import BindResult, SourceInspectionReport
from .models import SemanticTaskPlan


class SourceInspector(Protocol):
    def inspect(self, plan: SemanticTaskPlan) -> Sequence[SourceInspectionReport]:
        """检查计划范围内的可信来源。"""


class _BindingState(TypedDict, total=False):
    plan: SemanticTaskPlan
    inspector: SourceInspector
    reports: tuple[SourceInspectionReport, ...]
    binding_revision: int
    resolutions: Mapping[str, str]
    use_local_semantics: bool
    result: BindResult


def _build_graph():
    async def inspect_sources(state: _BindingState) -> dict:
        reports = await asyncio.to_thread(
            state["inspector"].inspect,
            state["plan"],
        )
        return {"reports": tuple(reports)}

    async def bind_sources(state: _BindingState) -> dict:
        provider = (
            LocalRerankScoreProvider()
            if state.get("use_local_semantics", True)
            else None
        )
        result = await asyncio.to_thread(
            bind_semantic_plan,
            state["plan"],
            state["reports"],
            binding_revision=state.get("binding_revision", 1),
            semantic_provider=provider,
            resolutions=state.get("resolutions", {}),
        )
        return {"result": result}

    builder = StateGraph(_BindingState)
    builder.add_node("inspect_sources", inspect_sources)
    builder.add_node("bind_sources", bind_sources)
    builder.add_edge(START, "inspect_sources")
    builder.add_edge("inspect_sources", "bind_sources")
    builder.add_edge("bind_sources", END)
    return builder.compile()


_GRAPH = _build_graph()


async def run_inspect_bind_graph(
    plan: SemanticTaskPlan,
    *,
    inspector: SourceInspector,
    binding_revision: int = 1,
    resolutions: Mapping[str, str] | None = None,
    use_local_semantics: bool = True,
) -> tuple[tuple[SourceInspectionReport, ...], BindResult]:
    """检查并绑定一个逻辑计划；返回报告与不可变绑定结果。"""

    state = await _GRAPH.ainvoke(
        {
            "plan": plan,
            "inspector": inspector,
            "reports": (),
            "binding_revision": binding_revision,
            "resolutions": dict(resolutions or {}),
            "use_local_semantics": use_local_semantics,
        },
        config={"recursion_limit": 8},
    )
    return state["reports"], state["result"]
