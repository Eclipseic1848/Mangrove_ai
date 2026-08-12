"""清洗内部模型：规则执行结果、批次账本、剖析结果（plan.md 第 8 节）。

与 src/data_prep/models.py 的 Recipe/RecipeRule（契约）区分：
- 契约模型描述"要做什么"（rule_id/stage/params/version）
- 本模块描述"做完后的事实"（输入数/输出数/隔离数/变更原因）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.data_prep.models import RecordEnvelope, RecipeStage


@dataclass
class RejectRecord:
    """被隔离的记录 + 原因 + 原始定位（plan 8.1：保留原始定位）。"""
    record: RecordEnvelope
    reason: str                       # 如 "验证页"/"乱码"/"样板噪声"/"重复"
    rule_id: str
    stage: RecipeStage


@dataclass
class RuleStats:
    """单条规则的执行账本（plan 8.1：每条规则记录输入/输出/隔离/合并数）。"""
    rule_id: str
    stage: RecipeStage
    input_count: int = 0
    output_count: int = 0
    isolated_count: int = 0
    merged_count: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "stage": self.stage.value,
            "input": self.input_count,
            "output": self.output_count,
            "isolated": self.isolated_count,
            "merged": self.merged_count,
            "reason": self.reason,
        }


@dataclass
class CleanResult:
    """一次 Recipe 执行的汇总。"""
    clean: List[RecordEnvelope] = field(default_factory=list)
    rejects: List[RejectRecord] = field(default_factory=list)
    rule_stats: List[RuleStats] = field(default_factory=list)

    @property
    def total_isolated(self) -> int:
        return sum(r.isolated_count for r in self.rule_stats)

    def ledger(self) -> Dict[str, int]:
        """记录账本（plan 9 清洗完整性：解析记录 = 输出 + 隔离 + 合并）。"""
        return {
            "clean": len(self.clean),
            "rejects": len(self.rejects),
            "merged": sum(r.merged_count for r in self.rule_stats),
        }


@dataclass
class ProfileResult:
    """清洗前数据剖析（plan 5.3 第 11 步：作为清洗影响基线）。"""
    record_count: int = 0
    fields: List[str] = field(default_factory=list)
    null_rates: Dict[str, float] = field(default_factory=dict)
    dup_count: int = 0
    content_avg_len: float = 0.0
    encoding_issues: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_count": self.record_count,
            "fields": self.fields,
            "null_rates": self.null_rates,
            "dup_count": self.dup_count,
            "content_avg_len": round(self.content_avg_len, 1),
            "encoding_issues": self.encoding_issues,
        }
