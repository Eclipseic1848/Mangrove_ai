"""质量报告生成（plan.md 第 6.5.3 / 第 9 节）。

机器读取的 quality_report.json 由 ArtifactStore.write_quality 落盘；
本模块提供从 QualityReport 生成人可读摘要的能力（供前端/日志）。
"""
from __future__ import annotations

from typing import List

from src.data_prep.models import QualityReport, QualityResult


_RESULT_LABEL = {
    QualityResult.PASS: "通过",
    QualityResult.WARN: "告警",
    QualityResult.FAIL: "失败",
}


def to_human_summary(report: QualityReport) -> str:
    """生成人可读的质量摘要（plan 5.4：LLM 可生成解释性摘要，但通过判定由确定性规则）。"""
    lines: List[str] = []
    lines.append(f"质量结论：{_RESULT_LABEL.get(report.overall, report.overall.value)}")
    lines.append("维度明细：")
    for d in report.dimensions:
        flag = "✓" if d.passed else "✗"
        thr = f"（阈值 {d.threshold:.2%}）" if d.threshold is not None else ""
        lines.append(f"  {flag} {d.name}: {d.value:.2%}{thr}")
    if report.issues:
        lines.append("问题清单：")
        for i, issue in enumerate(report.issues, 1):
            lines.append(f"  {i}. {issue}")
    if report.counts:
        lines.append(f"记录账本：{report.counts}")
    return "\n".join(lines)
