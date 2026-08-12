"""数据准备内核（plan.md 第 5/6/13 节）。

控制面继续用 LangGraph，但 state 只保存引用、计数和摘要；
数据面用任务目录中的批次文件传递，不把大数据集塞进 state。

核心契约见 models.py；ArtifactStore/新图/checkpoint 在本包内实现。
"""
from __future__ import annotations

# 契约模型统一从这里导出，便于外部 `from src.data_prep.models import ...`
from .models import (
    ConnectorCapability,
    DataPrepTaskSpec,
    DatasetManifest,
    ManifestArtifactEntry,
    ManifestOutputEntry,
    QualityDimensionResult,
    QualityPolicy,
    QualityReport,
    QualityResult,
    RawArtifact,
    Recipe,
    RecipeRule,
    RecipeStage,
    RecordEnvelope,
    RecordPosition,
    RetentionPolicy,
    SelectionSpec,
    SourceLimits,
    SourceSpec,
    SourceType,
    TargetSchema,
    TargetSchemaField,
    TaskMode,
    OutputFormat,
    IncrementalSpec,
)

__all__ = [
    "ConnectorCapability",
    "DataPrepTaskSpec",
    "DatasetManifest",
    "ManifestArtifactEntry",
    "ManifestOutputEntry",
    "OutputFormat",
    "QualityDimensionResult",
    "QualityPolicy",
    "QualityReport",
    "QualityResult",
    "RawArtifact",
    "Recipe",
    "RecipeRule",
    "RecipeStage",
    "RecordEnvelope",
    "RecordPosition",
    "RetentionPolicy",
    "SelectionSpec",
    "SourceLimits",
    "SourceSpec",
    "SourceType",
    "TargetSchema",
    "TargetSchemaField",
    "TaskMode",
    "IncrementalSpec",
]
