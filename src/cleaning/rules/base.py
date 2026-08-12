"""清洗规则抽象基类（plan.md 第 8 节）。

每条规则：输入一批记录，输出（保留记录, 隔离记录, 统计）。
规则只做确定性转换/过滤；同样的输入 + 规则版本必须产生一致输出（plan 8.1）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

from src.data_prep.models import RecordEnvelope, RecipeStage

from ..models import RejectRecord, RuleStats


class Rule(ABC):
    """规则基类。子类实现 apply。

    约定：
    - rule_id/stage 与契约 RecipeRule 对应
    - apply 返回 (保留记录, 隔离记录)；统计由引擎从输入/输出差值计算
    - 高影响规则（high_impact）在执行前由图预览并等待用户确认
    """

    rule_id: str = "base"
    stage: RecipeStage = RecipeStage.CONTENT_CLEAN
    high_impact: bool = False
    reversible: bool = True

    @abstractmethod
    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        """执行规则。返回 (保留, 隔离)。"""
        raise NotImplementedError

    def make_stats(
        self, input_count: int, kept: List[RecordEnvelope], rejects: List[RejectRecord],
        merged: int = 0, reason: str = "",
    ) -> RuleStats:
        from ..models import RuleStats  # 局部导入避免循环
        return RuleStats(
            rule_id=self.rule_id,
            stage=self.stage,
            input_count=input_count,
            output_count=len(kept),
            isolated_count=len(rejects),
            merged_count=merged,
            reason=reason,
        )
