# -*- coding: utf-8 -*-
"""批次 3 compile → execute → verify Graph；确认后全程零 LLM。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from .inspection_models import SourceInspectionReport
from .models import BoundPlan, SemanticTaskPlan
from .physical_models import PhysicalPlan, RuntimeProfileName
from .physical_planner import compile_physical_plan
from .table_executor import ExecutionBundle, execute_physical_plan
from .table_verifier import verify_table_execution


class _ExecutionState(TypedDict, total=False):
    logical_plan: SemanticTaskPlan
    bound_plan: BoundPlan
    reports: tuple[SourceInspectionReport, ...]
    profile: RuntimeProfileName
    artifact_paths: Mapping[str, Path]
    output_dir: Path
    physical_plan: PhysicalPlan
    bundle: ExecutionBundle
    verification: object


def _build_graph():
    async def compile_node(state: _ExecutionState) -> dict:
        if "physical_plan" in state:
            return {"physical_plan": state["physical_plan"]}
        physical = await asyncio.to_thread(
            compile_physical_plan,
            state["logical_plan"],
            state["bound_plan"],
            state["reports"],
            profile=state["profile"],
        )
        return {"physical_plan": physical}

    async def execute_node(state: _ExecutionState) -> dict:
        bundle = await asyncio.to_thread(
            execute_physical_plan,
            state["physical_plan"],
            artifact_paths=state["artifact_paths"],
            output_dir=state["output_dir"],
        )
        return {"bundle": bundle}

    async def verify_node(state: _ExecutionState) -> dict:
        report = await asyncio.to_thread(
            verify_table_execution,
            state["logical_plan"],
            state["bundle"],
        )
        return {"verification": report}

    builder = StateGraph(_ExecutionState)
    builder.add_node("compile_physical_plan", compile_node)
    builder.add_node("execute_table_plan", execute_node)
    builder.add_node("verify_table_result", verify_node)
    builder.add_edge(START, "compile_physical_plan")
    builder.add_edge("compile_physical_plan", "execute_table_plan")
    builder.add_edge("execute_table_plan", "verify_table_result")
    builder.add_edge("verify_table_result", END)
    return builder.compile()


_GRAPH = _build_graph()


async def run_table_execution_graph(
    logical_plan: SemanticTaskPlan,
    bound_plan: BoundPlan,
    reports: Sequence[SourceInspectionReport],
    *,
    profile: RuntimeProfileName,
    artifact_paths: Mapping[str, Path],
    output_dir: Path,
    physical_plan: PhysicalPlan | None = None,
) -> tuple[PhysicalPlan, ExecutionBundle, object]:
    initial: _ExecutionState = {
            "logical_plan": logical_plan,
            "bound_plan": bound_plan,
            "reports": tuple(reports),
            "profile": profile,
            "artifact_paths": artifact_paths,
            "output_dir": output_dir,
    }
    if physical_plan is not None:
        initial["physical_plan"] = physical_plan
    state = await _GRAPH.ainvoke(
        initial,
        config={"recursion_limit": 8},
    )
    return (
        state["physical_plan"],
        state["bundle"],
        state["verification"],
    )
