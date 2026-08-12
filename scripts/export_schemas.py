#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导出数据准备核心契约的 JSON Schema（plan Phase 0：定义 JSON Schema）。

把 src/data_prep/models.py 的 Pydantic 模型导出为 JSON Schema 文件，
供前后端联调、测试断言与外部消费者共用，避免契约漂移。

用法：
    python scripts/export_schemas.py
    输出到 docs/schemas/*.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_prep.models import (  # noqa: E402
    DataPrepTaskSpec,
    DatasetManifest,
    QualityReport,
    RawArtifact,
    RecordEnvelope,
    Recipe,
    SourceSpec,
    TargetSchema,
)
from src.data_prep.document_models import (  # noqa: E402
    DiscoverySpec,
    DocumentElement,
    EvidenceRef,
    ExtractedAggregate,
    ExtractedDocument,
    ExtractedField,
    ExtractedRecord,
    ExtractedTable,
    ExtractionSpec,
    ResultContract,
    ReviewPolicy,
    ReviewTask,
    TaskGoal,
)
from src.semantic_harness.models import (  # noqa: E402
    BoundPlan,
    CapabilityManifest,
    SemanticTaskPlan,
    ToolResult,
    VerificationReport,
)
from src.semantic_harness.compiler_models import (  # noqa: E402
    CompileRequest,
    CompileResult,
    PlanSemanticsDraft,
)
from src.semantic_harness.inspection_models import (  # noqa: E402
    BindResult,
    BindingCandidate,
    SourceInspectionReport,
)
from src.semantic_harness.physical_models import PhysicalPlan  # noqa: E402
from src.semantic_harness.document_models import (  # noqa: E402
    AuditRule,
    DocumentAST,
    DocumentExecutionResult,
    DocumentPhysicalPlan,
)
from src.semantic_harness.harness_models import (  # noqa: E402
    HarnessLoopPolicy,
    HarnessQuestion,
    HarnessResume,
    HarnessRun,
    RepairDecision,
    RepairProposal,
)

# 模型 -> 导出文件名
MODELS = [
    ("DataPrepTaskSpec", DataPrepTaskSpec),
    ("SourceSpec", SourceSpec),
    ("RawArtifact", RawArtifact),
    ("RecordEnvelope", RecordEnvelope),
    ("Recipe", Recipe),
    ("TargetSchema", TargetSchema),
    ("QualityReport", QualityReport),
    ("DatasetManifest", DatasetManifest),
    ("TaskGoal", TaskGoal),
    ("DiscoverySpec", DiscoverySpec),
    ("ExtractionSpec", ExtractionSpec),
    ("DocumentElement", DocumentElement),
    ("EvidenceRef", EvidenceRef),
    ("ExtractedField", ExtractedField),
    ("ExtractedRecord", ExtractedRecord),
    ("ExtractedTable", ExtractedTable),
    ("ExtractedDocument", ExtractedDocument),
    ("ExtractedAggregate", ExtractedAggregate),
    ("ResultContract", ResultContract),
    ("ReviewPolicy", ReviewPolicy),
    ("ReviewTask", ReviewTask),
    ("SemanticTaskPlan", SemanticTaskPlan),
    ("BoundPlan", BoundPlan),
    ("CapabilityManifest", CapabilityManifest),
    ("ToolResult", ToolResult),
    ("VerificationReport", VerificationReport),
    ("CompileRequest", CompileRequest),
    ("PlanSemanticsDraft", PlanSemanticsDraft),
    ("CompileResult", CompileResult),
    ("SourceInspectionReport", SourceInspectionReport),
    ("BindingCandidate", BindingCandidate),
    ("BindResult", BindResult),
    ("PhysicalPlan", PhysicalPlan),
    ("AuditRule", AuditRule),
    ("DocumentPhysicalPlan", DocumentPhysicalPlan),
    ("DocumentAST", DocumentAST),
    ("DocumentExecutionResult", DocumentExecutionResult),
    ("HarnessLoopPolicy", HarnessLoopPolicy),
    ("HarnessRun", HarnessRun),
    ("HarnessQuestion", HarnessQuestion),
    ("HarnessResume", HarnessResume),
    ("RepairProposal", RepairProposal),
    ("RepairDecision", RepairDecision),
]

OUT_DIR = PROJECT_ROOT / "docs" / "schemas"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"导出 JSON Schema 到 {OUT_DIR}")
    for name, model in MODELS:
        schema = model.model_json_schema()
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ {name}.json ({len(schema.get('properties', {}))} properties)")
    # 汇总索引
    index = {"version": "8", "models": [name for name, _ in MODELS]}
    (OUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ index.json")
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
