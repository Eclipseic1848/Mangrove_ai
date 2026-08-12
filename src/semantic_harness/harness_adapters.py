# -*- coding: utf-8 -*-
"""把批次 3/4 执行 Graph 包装为统一 Harness 能力适配器。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .document_executor import LocalDocumentSemanticProvider
from .document_executor import execute_document_plan
from .document_models import AuditOperator, DocumentAction, DocumentPhysicalPlan
from .document_planner import compile_document_plan
from .document_verifier import verify_document_execution
from .inspection_models import SourceInspectionReport
from .models import BoundPlan, SemanticTaskPlan, ToolResult, VerificationReport
from .physical_models import PhysicalPlan, RuntimeProfileName
from .physical_planner import compile_physical_plan
from .table_executor import execute_physical_plan
from .table_verifier import verify_table_execution


@dataclass(frozen=True)
class HarnessAdapterOutcome:
    physical_plan: PhysicalPlan | DocumentPhysicalPlan
    tool_result: ToolResult
    verification: VerificationReport
    artifact_paths: Mapping[str, Path]


class HarnessCapabilityAdapter(Protocol):
    capability_id: str

    def compile_plan(
        self,
        logical_plan: SemanticTaskPlan,
        bound_plan: BoundPlan,
        reports: Sequence[SourceInspectionReport],
        *,
        profile: RuntimeProfileName,
    ) -> PhysicalPlan | DocumentPhysicalPlan:
        ...

    async def execute(
        self,
        logical_plan: SemanticTaskPlan,
        bound_plan: BoundPlan,
        reports: Sequence[SourceInspectionReport],
        *,
        profile: RuntimeProfileName,
        artifact_paths: Mapping[str, Path],
        output_dir: Path,
        physical_plan: PhysicalPlan | DocumentPhysicalPlan,
    ) -> HarnessAdapterOutcome:
        ...


class TableHarnessAdapter:
    capability_id = "table.duckdb"

    def compile_plan(
        self,
        logical_plan: SemanticTaskPlan,
        bound_plan: BoundPlan,
        reports: Sequence[SourceInspectionReport],
        *,
        profile: RuntimeProfileName,
    ) -> PhysicalPlan:
        return compile_physical_plan(
            logical_plan,
            bound_plan,
            reports,
            profile=profile,
        )

    async def execute(
        self,
        logical_plan: SemanticTaskPlan,
        bound_plan: BoundPlan,
        reports: Sequence[SourceInspectionReport],
        *,
        profile: RuntimeProfileName,
        artifact_paths: Mapping[str, Path],
        output_dir: Path,
        physical_plan: PhysicalPlan | DocumentPhysicalPlan,
    ) -> HarnessAdapterOutcome:
        physical = PhysicalPlan.model_validate(physical_plan)
        del bound_plan, reports, profile
        bundle = await asyncio.to_thread(
            execute_physical_plan,
            physical,
            artifact_paths=artifact_paths,
            output_dir=output_dir,
        )
        verification = await asyncio.to_thread(
            verify_table_execution,
            logical_plan,
            bundle,
        )
        return HarnessAdapterOutcome(
            physical_plan=physical,
            tool_result=bundle.tool_result,
            verification=verification,
            artifact_paths={
                key: value
                for key, value in {
                    "result": bundle.result_path,
                    "lineage": bundle.lineage_path,
                }.items()
                if value is not None
            },
        )


class DocumentHarnessAdapter:
    capability_id = "document.evidence"

    @staticmethod
    def _semantic_provider(physical: DocumentPhysicalPlan):
        needs_semantics = physical.action in {
            DocumentAction.COMPARE,
            DocumentAction.SUMMARIZE,
            DocumentAction.REWRITE,
            DocumentAction.TRANSLATE,
            DocumentAction.COMPOSE,
        } or any(
            rule.operator == AuditOperator.SEMANTIC
            for rule in physical.audit_rules
        )
        return LocalDocumentSemanticProvider() if needs_semantics else None

    def compile_plan(
        self,
        logical_plan: SemanticTaskPlan,
        bound_plan: BoundPlan,
        reports: Sequence[SourceInspectionReport],
        *,
        profile: RuntimeProfileName,
    ) -> DocumentPhysicalPlan:
        return compile_document_plan(
            logical_plan,
            bound_plan,
            reports,
            profile=profile,
        )

    async def execute(
        self,
        logical_plan: SemanticTaskPlan,
        bound_plan: BoundPlan,
        reports: Sequence[SourceInspectionReport],
        *,
        profile: RuntimeProfileName,
        artifact_paths: Mapping[str, Path],
        output_dir: Path,
        physical_plan: PhysicalPlan | DocumentPhysicalPlan,
    ) -> HarnessAdapterOutcome:
        physical = DocumentPhysicalPlan.model_validate(physical_plan)
        del logical_plan, bound_plan, reports, profile
        bundle = await execute_document_plan(
            physical,
            artifact_paths=artifact_paths,
            output_dir=output_dir,
            semantic_provider=self._semantic_provider(physical),
        )
        verification = await asyncio.to_thread(
            verify_document_execution,
            physical,
            bundle.result,
            source_elements=bundle.source_elements,
            result_path=bundle.result_path,
        )
        return HarnessAdapterOutcome(
            physical_plan=physical,
            tool_result=bundle.tool_result,
            verification=verification,
            artifact_paths={"result": bundle.result_path},
        )


_ADAPTERS: dict[str, HarnessCapabilityAdapter] = {
    "table.duckdb": TableHarnessAdapter(),
    "document.evidence": DocumentHarnessAdapter(),
}


def get_harness_adapter(capability_id: str) -> HarnessCapabilityAdapter:
    try:
        return _ADAPTERS[capability_id]
    except KeyError as exc:
        raise KeyError(f"未登记 Harness 能力适配器：{capability_id}") from exc


def register_harness_adapter_for_test(
    capability_id: str,
    adapter: HarnessCapabilityAdapter,
) -> Any:
    """仅供确定性测试注入故障适配器，返回原适配器以便恢复。"""

    previous = _ADAPTERS.get(capability_id)
    _ADAPTERS[capability_id] = adapter
    return previous
