# -*- coding: utf-8 -*-
"""数据准备核心契约测试（plan Phase 0 退出标准）。

验证 Pydantic 模型校验、凭证脱敏、Schema 导出。
双模式：可 `python tests/test_data_prep_contracts.py` 或 pytest 运行。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_prep.models import (  # noqa: E402
    DataPrepTaskSpec,
    OutputFormat,
    QualityPolicy,
    QualityResult,
    Recipe,
    RecipeRule,
    RecipeStage,
    SourceSpec,
    SourceType,
    TargetSchema,
    TargetSchemaField,
)


def test_source_spec_credential_redaction():
    """credential_ref 在 to_public_dict 中脱敏（ADR-0002/0004）。"""
    s = SourceSpec(source_id="s1", source_type=SourceType.WEB, locator="http://x", credential_ref="secret-abc")
    pub = s.to_public_dict()
    assert pub["credential_ref"] == "····", f"凭证应脱敏，实际: {pub['credential_ref']}"
    assert "secret-abc" not in json.dumps(pub)


def test_task_spec_defaults():
    """默认 mode=data_prep，默认输出 JSONL+Parquet（7C/ADR-0005）。"""
    spec = DataPrepTaskSpec(intent="测试")
    assert spec.mode.value == "data_prep"
    assert OutputFormat.JSONL in spec.outputs
    assert OutputFormat.PARQUET in spec.outputs


def test_recipe_rule_stage_order():
    """RecipeStage 枚举完整（plan 8.2 九阶段）。"""
    stages = [s.value for s in RecipeStage]
    assert len(stages) == 9
    assert "input_validation" in stages and "anomaly_isolation" in stages


def test_quality_policy_thresholds():
    """质量策略默认阈值合理（plan 9）。"""
    p = QualityPolicy()
    assert 0 < p.max_reject_rate <= 1
    assert p.min_completeness >= 0.9


def test_target_schema_unique_keys():
    """目标 Schema 支持复合唯一键。"""
    schema = TargetSchema(
        fields=[TargetSchemaField(name="id", dtype="integer", required=True, unique=True)],
        primary_key=["id"],
        unique_keys=[["id"], ["url", "date"]],
    )
    assert schema.fields[0].unique is True
    assert len(schema.unique_keys) == 2


def test_quality_result_enum():
    """质量结论三态（plan 9：pass/warn/fail）。"""
    assert QualityResult.PASS.value == "pass"
    assert QualityResult.WARN.value == "warn"
    assert QualityResult.FAIL.value == "fail"


def test_json_schema_exported():
    """JSON Schema 文件已导出到 docs/schemas/（Phase 0 产物）。"""
    schema_dir = PROJECT_ROOT / "docs" / "schemas"
    assert (schema_dir / "DataPrepTaskSpec.json").exists(), "DataPrepTaskSpec.json 未导出"
    assert (schema_dir / "RawArtifact.json").exists()
    assert (schema_dir / "ResultContract.json").exists()
    assert (schema_dir / "ExtractedRecord.json").exists()
    assert (schema_dir / "ExtractedTable.json").exists()
    assert (schema_dir / "ExtractedDocument.json").exists()
    assert (schema_dir / "ExtractedAggregate.json").exists()
    assert (schema_dir / "index.json").exists()
    data = json.loads((schema_dir / "DataPrepTaskSpec.json").read_text(encoding="utf-8"))
    assert "properties" in data
    index = json.loads((schema_dir / "index.json").read_text(encoding="utf-8"))
    assert {
        "ResultContract",
        "ExtractedRecord",
        "ExtractedTable",
        "ExtractedDocument",
        "ExtractedAggregate",
    }.issubset(
        set(index["models"])
    )


TESTS = [
    test_source_spec_credential_redaction,
    test_task_spec_defaults,
    test_recipe_rule_stage_order,
    test_quality_policy_thresholds,
    test_target_schema_unique_keys,
    test_quality_result_enum,
    test_json_schema_exported,
]


def main():
    failed = 0
    for t in TESTS:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(TESTS) - failed}/{len(TESTS)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
