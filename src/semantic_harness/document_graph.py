# -*- coding: utf-8 -*-
"""批次 4 compile → execute → verify 文档 Graph。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from .document_executor import (
    DocumentExecutionBundle,
    DocumentSemanticProvider,
    execute_document_plan,
)
from .document_models import DocumentPhysicalPlan
from .document_planner import compile_document_plan
from .document_verifier import verify_document_execution
from .inspection_models import SourceInspectionReport
from .models import BoundPlan, SemanticTaskPlan
from .physical_models import RuntimeProfileName


class _DocumentState(TypedDict, total=False):
    logical_plan: SemanticTaskPlan
    bound_plan: BoundPlan
    reports: tuple[SourceInspectionReport, ...]
    profile: RuntimeProfileName
    artifact_paths: Mapping[str, Path]
    output_dir: Path
    semantic_provider: DocumentSemanticProvider | None
    physical_plan: DocumentPhysicalPlan
    bundle: DocumentExecutionBundle
    verification: object


def _build_graph():
    async def compile_node(state: _DocumentState) -> dict:
        if "physical_plan" in state:
            return {"physical_plan": state["physical_plan"]}
        physical = await asyncio.to_thread(
            compile_document_plan,
            state["logical_plan"],
            state["bound_plan"],
            state["reports"],
            profile=state["profile"],
        )
        return {"physical_plan": physical}

    async def execute_node(state: _DocumentState) -> dict:
        bundle = await execute_document_plan(
            state["physical_plan"],
            artifact_paths=state["artifact_paths"],
            output_dir=state["output_dir"],
            semantic_provider=state.get("semantic_provider"),
        )
        return {"bundle": bundle}

    async def verify_node(state: _DocumentState) -> dict:
        bundle = state["bundle"]
        report = await asyncio.to_thread(
            verify_document_execution,
            state["physical_plan"],
            bundle.result,
            source_elements=bundle.source_elements,
            result_path=bundle.result_path,
        )
        return {"verification": report}

    builder = StateGraph(_DocumentState)
    builder.add_node("compile_document_plan", compile_node)
    builder.add_node("execute_document_plan", execute_node)
    builder.add_node("verify_document_result", verify_node)
    builder.add_edge(START, "compile_document_plan")
    builder.add_edge("compile_document_plan", "execute_document_plan")
    builder.add_edge("execute_document_plan", "verify_document_result")
    builder.add_edge("verify_document_result", END)
    return builder.compile()


_GRAPH = _build_graph()


async def run_document_execution_graph(
    logical_plan: SemanticTaskPlan,
    bound_plan: BoundPlan,
    reports: Sequence[SourceInspectionReport],
    *,
    profile: RuntimeProfileName,
    artifact_paths: Mapping[str, Path],
    output_dir: Path,
    semantic_provider: DocumentSemanticProvider | None = None,
    physical_plan: DocumentPhysicalPlan | None = None,
) -> tuple[DocumentPhysicalPlan, DocumentExecutionBundle, object]:
    initial: _DocumentState = {
        "logical_plan": logical_plan,
        "bound_plan": bound_plan,
        "reports": tuple(reports),
        "profile": profile,
        "artifact_paths": artifact_paths,
        "output_dir": output_dir,
        "semantic_provider": semantic_provider,
    }
    if physical_plan is not None:
        initial["physical_plan"] = physical_plan
    state = await _GRAPH.ainvoke(initial, config={"recursion_limit": 8})
    return state["physical_plan"], state["bundle"], state["verification"]
