"""清洗前数据剖析（plan.md 第 5.3 第 11 步 / 第 8.2 节）。

计算记录数、字段、空值率、重复、平均正文长度、编码问题，作为清洗影响基线。
清洗后可再跑一次对比，量化规则影响（plan 8.1：每条规则记录影响数量）。

ProfileAccumulator（Phase 2 Task 2.5）：逐批累计，不持有完整记录集，
峰值内存不随总记录数线性增长（plan 退出门禁）。
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, List

from src.data_prep.models import RecordEnvelope

from .models import ProfileResult


def profile(records: List[RecordEnvelope]) -> ProfileResult:
    """对记录集合做清洗前剖析（全量，保留供小规模/测试用）。"""
    if not records:
        return ProfileResult()
    acc = ProfileAccumulator()
    acc.add_records(records)
    return acc.finalize()


class ProfileAccumulator:
    """逐批累计剖析指标，不保存完整记录（plan Phase 2 Task 2.5）。

    内存占用：字段计数器（字段数有限）+ record_id 去重集合 + 正文长度求和。
    不存记录本身，百万行只存百万个短 record_id（内容哈希派生）。
    """

    def __init__(self) -> None:
        self._field_counter: Counter = Counter()
        self._null_counter: Counter = Counter()
        self._content_sum: float = 0.0
        self._content_count: int = 0
        self._dup_keys: set = set()
        self._dup_count: int = 0
        self._encoding_issues: int = 0
        self._n: int = 0

    def add_records(self, records: Iterable[RecordEnvelope]) -> None:
        """累计一批记录（可传生成器，逐条消费不一次性物化）。"""
        for r in records:
            self._n += 1
            for k, v in (r.data or {}).items():
                self._field_counter[k] += 1
                if v is None or (isinstance(v, str) and not v.strip()):
                    self._null_counter[k] += 1
                if k == "content":
                    self._content_sum += float(len(v or ""))
                    self._content_count += 1
                    if "�" in (v or ""):
                        self._encoding_issues += 1
            rid = r.record_id
            if rid in self._dup_keys:
                self._dup_count += 1
            else:
                self._dup_keys.add(rid)

    def finalize(self) -> ProfileResult:
        if self._n == 0:
            return ProfileResult()
        null_rates = {
            k: round(self._null_counter[k] / self._n, 4) for k in self._field_counter
        }
        avg_len = (
            self._content_sum / self._content_count if self._content_count else 0.0
        )
        return ProfileResult(
            record_count=self._n,
            fields=list(self._field_counter.keys()),
            null_rates=null_rates,
            dup_count=self._dup_count,
            content_avg_len=avg_len,
            encoding_issues=self._encoding_issues,
        )
