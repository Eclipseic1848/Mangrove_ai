# -*- coding: utf-8 -*-
"""Phase 4A ExtractionSpec 到 STP 的无损、诚实适配。"""
from __future__ import annotations

from src.data_prep.document_models import (
    ExtractionSpec,
    ResultShape,
)

from .models import (
    Ambiguity,
    CombineMode,
    CombineSpec,
    ContentPolicy,
    DeliveryFormat,
    DeliverySpec,
    ObjectiveSpec,
    PostconditionSpec,
    ProjectionField,
    SemanticTaskPlan,
    SourceScope,
    TaskFamily,
)


_FAMILY_BY_SHAPE = {
    ResultShape.FIELDS: TaskFamily.EXTRACT,
    ResultShape.RECORDS: TaskFamily.EXTRACT,
    ResultShape.TABLES: TaskFamily.TABULAR_TRANSFORM,
    ResultShape.DOCUMENT: TaskFamily.EXTRACT,
    ResultShape.AGGREGATE: TaskFamily.TABULAR_TRANSFORM,
}


def _delivery_formats(spec: ExtractionSpec) -> tuple[DeliveryFormat, ...]:
    formats = []
    for item in spec.result_contract.output_formats:
        try:
            value = DeliveryFormat(item.lower())
        except ValueError:
            continue
        if value not in formats:
            formats.append(value)
    return tuple(formats) or (DeliveryFormat.JSON,)


def extraction_spec_to_stp(
    spec: ExtractionSpec,
    *,
    task_id: str,
    plan_id: str,
    revision: int = 1,
) -> SemanticTaskPlan:
    """只迁移旧契约明确保存的语义，缺失信息必须进入歧义而非猜测。"""

    shape = spec.result_contract.shape
    projection = tuple(ProjectionField(name=item.name) for item in spec.fields)
    ambiguities = []
    record_grain = spec.result_contract.record_grain
    if shape in {ResultShape.TABLES, ResultShape.AGGREGATE}:
        if not spec.result_contract.exhaustive:
            ambiguities.append(
                Ambiguity(
                    ambiguity_id="legacy.selection",
                    question="旧规格没有保存结构化筛选条件，需要处理全部记录还是指定范围？",
                    candidates=("全部记录", "指定筛选范围"),
                )
            )
        if not record_grain:
            record_grain = "unresolved_legacy_grain"
            ambiguities.append(
                Ambiguity(
                    ambiguity_id="legacy.record_grain",
                    question="旧规格没有明确每一行代表什么，请确认结果粒度。",
                    candidates=("保留源明细", "按业务对象汇总"),
                )
            )
    if shape == ResultShape.AGGREGATE:
        ambiguities.append(
            Ambiguity(
                ambiguity_id="legacy.aggregate",
                question="旧规格没有保存聚合函数，请确认需要求和、平均、计数还是其他方式。",
                candidates=("求和", "平均", "计数"),
            )
        )

    one_table = (
        shape == ResultShape.TABLES and spec.result_contract.merge_tables
    )
    return SemanticTaskPlan(
        plan_id=plan_id,
        task_id=task_id,
        revision=revision,
        task_family=_FAMILY_BY_SHAPE[shape],
        objective=ObjectiveSpec(
            original_text=spec.goal.objective,
            normalized_text=spec.goal.objective,
        ),
        source_scope=SourceScope(
            artifact_ids=tuple(spec.discovery.artifact_ids),
            pages={
                key: tuple(value)
                for key, value in spec.discovery.pages.items()
            },
            section_patterns=tuple(spec.discovery.section_patterns),
            whole_document=(
                shape
                in {
                    ResultShape.FIELDS,
                    ResultShape.RECORDS,
                    ResultShape.DOCUMENT,
                }
                and not bool(
                    spec.discovery.section_patterns or spec.discovery.pages
                )
            ),
        ),
        projection=projection,
        record_grain=record_grain,
        combine=CombineSpec(
            mode=(
                CombineMode.ONE_TABLE if one_table else CombineMode.PRESERVE
            )
        ),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=_delivery_formats(spec)),
        postconditions=PostconditionSpec(
            table_count=1 if one_table else None,
            exact_visible_columns=tuple(item.name for item in projection),
            minimum_evidence_coverage=1.0,
        ),
        ambiguities=tuple(ambiguities),
    )
