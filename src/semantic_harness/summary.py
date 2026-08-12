# -*- coding: utf-8 -*-
"""把 STP 确定性渲染成人话；摘要不得再次交给模型改写。"""
from __future__ import annotations

from .models import (
    CombineMode,
    ContentPolicy,
    OperationType,
    PredicateOperator,
    SemanticTaskPlan,
)


_OPERATOR_LABELS = {
    PredicateOperator.EQ: "等于",
    PredicateOperator.NE: "不等于",
    PredicateOperator.GT: "大于",
    PredicateOperator.GTE: "大于等于",
    PredicateOperator.LT: "小于",
    PredicateOperator.LTE: "小于等于",
    PredicateOperator.CONTAINS: "包含",
    PredicateOperator.IN: "属于",
    PredicateOperator.NOT_IN: "不属于",
    PredicateOperator.IS_NULL: "为空",
    PredicateOperator.NOT_NULL: "不为空",
    PredicateOperator.REGEX: "匹配正则",
}

_CONTENT_LABELS = {
    ContentPolicy.VERBATIM: "原文摘录",
    ContentPolicy.NORMALIZED: "确定性规范化",
    ContentPolicy.SUMMARIZED: "总结",
    ContentPolicy.REWRITTEN: "改写",
    ContentPolicy.TRANSLATED: "翻译",
    ContentPolicy.ANALYZED: "分析",
}

_OPERATION_LABELS = {
    OperationType.SORT: "排序",
    OperationType.UNION: "合并",
    OperationType.JOIN: "连接",
    OperationType.GROUP: "分组",
    OperationType.AGGREGATE: "聚合",
    OperationType.DEDUPLICATE: "去重",
    OperationType.NORMALIZE: "规范化",
    OperationType.COMPARE: "比较",
    OperationType.AUDIT: "核查",
}


def render_plan_summary(plan: SemanticTaskPlan) -> str:
    """只消费已经通过校验的计划，保证摘要与 JSON 同源。"""

    lines = [f"目标：{plan.objective.normalized_text}"]
    if plan.source_scope.table_scope:
        lines.append(f"表格范围：{plan.source_scope.table_scope}")
    if plan.source_scope.section_patterns:
        lines.append(f"章节范围：{'、'.join(plan.source_scope.section_patterns)}")
    for item in plan.selection:
        operand = ""
        if item.values:
            operand = "、".join(str(value) for value in item.values)
        elif item.value is not None:
            operand = str(item.value)
        text = f"筛选：{item.field} {_OPERATOR_LABELS[item.operator]}"
        lines.append(f"{text}{f' {operand}' if operand else ''}")
    if plan.projection:
        fields = [item.alias or item.name for item in plan.projection]
        lines.append(f"可见字段：{'、'.join(fields)}")
    if plan.record_grain:
        lines.append(f"结果粒度：{plan.record_grain}")
    if plan.operations:
        operations = [_OPERATION_LABELS[item.operation] for item in plan.operations]
        lines.append(f"处理操作：{' → '.join(operations)}")
    if plan.combine.mode == CombineMode.ONE_TABLE:
        lines.append("结果形态：合并为一张表")
    lines.append(f"内容政策：{_CONTENT_LABELS[plan.content_policy]}")
    lines.append(
        "输出格式："
        + "、".join(item.value.upper() for item in plan.delivery.formats)
    )
    if plan.ambiguities:
        unresolved = [
            item.question
            for item in plan.ambiguities
            if item.material and not item.resolved
        ]
        if unresolved:
            lines.append(f"待确认：{unresolved[0]}")
    return "\n".join(lines)
