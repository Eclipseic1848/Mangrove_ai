"""数据质量门确定性校验（plan.md 第 9 节，ADR-0003）。

"正确"拆成可计算维度：来源正确性/采集完整性/解析完整性/清洗完整性/
字段完整性/有效性/唯一性/一致性/新鲜度/可追溯性。
质量门由确定性规则计算；LLM 不参与通过判定。

QualityAccumulator（Phase 2 Task 2.5 阶段2）：逐批累计字段非空计数与
主键去重集合，不持有完整记录集，峰值内存不随总记录数线性增长。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from src.data_prep.models import (
    QualityDimensionResult,
    QualityPolicy,
    QualityReport,
    QualityResult,
    RawArtifact,
    RecordEnvelope,
    TargetSchema,
)


def _is_non_null(v: Any) -> bool:
    """判定业务值非空（None 或纯空白视为空，plan 9 字段完整性）。"""
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


class QualityAccumulator:
    """逐批累计质量统计，不保存完整记录（plan Phase 2 Task 2.5 阶段2）。

    内存占用：必填字段非空计数器（字段数有限）+ 主键去重集合（主键元组数有限）。
    不存记录本身。百万行仅存百万个短主键元组（业务键派生）。
    """

    def __init__(
        self,
        required_fields: Optional[List[str]] = None,
        primary_key: Optional[List[str]] = None,
    ) -> None:
        self._required: List[str] = list(required_fields or [])
        self._primary_key: List[str] = list(primary_key or [])
        self.total: int = 0
        self.non_null_counts: Dict[str, int] = {f: 0 for f in self._required}
        self.duplicates: int = 0
        self._seen_keys: set = set()

    def add_batch(self, records: Iterable[RecordEnvelope]) -> None:
        """累计一批记录（可传生成器，逐条消费不一次性物化）。"""
        for r in records:
            self.total += 1
            data = r.data or {}
            for f in self._required:
                if _is_non_null(data.get(f)):
                    self.non_null_counts[f] += 1
            if self._primary_key:
                key = tuple(str(data.get(k, "")) for k in self._primary_key)
                if key in self._seen_keys:
                    self.duplicates += 1
                else:
                    self._seen_keys.add(key)

    def non_null_rates(self) -> Dict[str, float]:
        if self.total == 0:
            return {f: 1.0 for f in self._required}
        return {f: self.non_null_counts[f] / self.total for f in self._required}

    @property
    def uniqueness(self) -> float:
        if self.total == 0:
            return 1.0
        return 1.0 - (self.duplicates / self.total)


def _dim(name: str, value: float, threshold: Optional[float], passed: bool, **details) -> QualityDimensionResult:
    return QualityDimensionResult(
        name=name, value=value, threshold=threshold, passed=passed, details=details or {}
    )


def validate(
    *,
    clean_records: Optional[List[RecordEnvelope]] = None,
    record_counts: Dict[str, int],
    artifacts: List[RawArtifact],
    policy: QualityPolicy,
    target_schema: Optional[TargetSchema] = None,
    source_count_expected: Optional[int] = None,
    lineage_coverage: Optional[float] = None,
    accumulated: Optional[QualityAccumulator] = None,
) -> QualityReport:
    """对清洗后数据做确定性质量校验，返回 QualityReport。

    逐批模式（plan Phase 2 Task 2.5 阶段2）：传 ``accumulated`` 时字段完整性与
    唯一性用累计统计，``clean_records`` 可不传，避免全量物化。
    旧模式：传 ``clean_records``，现场计算（向后兼容）。

    lineage_coverage：若提供（由调用方从 lineage/records.jsonl 计算），
    用它作为可追溯性维度值；否则从 clean_records.meta.artifact_id 推断
    （clean 输出 JSONL 不含 meta 时应传此参数，plan 6.3 业务/系统分离）。
    """
    dims: List[QualityDimensionResult] = []
    issues: List[str] = []

    raw_n = record_counts.get("raw", 0)
    parsed_n = record_counts.get("parsed", 0)
    clean_n = record_counts.get("clean", len(clean_records) if clean_records is not None else (accumulated.total if accumulated else 0))
    rejects_parse = record_counts.get("rejects_parse", 0)
    rejects_clean = record_counts.get("rejects_clean", 0)
    merged = record_counts.get("merged", 0)

    # 1. 解析完整性：原始 = 解析成功 + 解析隔离
    # raw 为 artifact 数，parsed 为记录数；文件链路 1 artifact 可产出 N 记录，
    # 故 parse_loss <= 0 属正常（非损失），仅 loss > 0（丢数据）判 fail。
    parse_loss = raw_n - parsed_n - rejects_parse
    parse_complete = parse_loss <= 0
    parse_rate = (parsed_n / raw_n) if raw_n else 1.0
    dims.append(_dim("解析完整性", parse_rate, 1.0, parse_complete,
                     raw=raw_n, parsed=parsed_n, rejects=rejects_parse, unexplained_loss=max(parse_loss, 0)))
    if parse_loss > 0:
        issues.append(f"解析存在未解释记录损失 {parse_loss} 条（原始{raw_n} ≠ 解析{parsed_n}+隔离{rejects_parse}）")

    # 2. 清洗完整性：解析 = 输出 + 清洗隔离 + 合并
    clean_loss = parsed_n - clean_n - rejects_clean - merged
    clean_complete = clean_loss == 0
    clean_rate = (clean_n / parsed_n) if parsed_n else 1.0
    dims.append(_dim("清洗完整性", clean_rate, 1.0, clean_complete,
                     parsed=parsed_n, clean=clean_n, rejects=rejects_clean, merged=merged, unexplained_loss=clean_loss))
    if not clean_complete:
        issues.append(f"清洗存在未解释记录损失 {clean_loss} 条")

    # 3. 字段完整性：必填字段非空率
    if target_schema and target_schema.fields:
        required = [f.name for f in target_schema.fields if f.required]
        if required and (accumulated is not None or clean_records):
            if accumulated is not None and accumulated.total > 0:
                non_null_rates = accumulated.non_null_rates()
                denom = accumulated.total
            elif clean_records:
                non_null_rates = {
                    field: sum(1 for r in clean_records if _is_non_null(r.data.get(field))) / len(clean_records)
                    for field in required
                }
                denom = len(clean_records)
            else:
                non_null_rates = {f: 1.0 for f in required}
                denom = 0
            min_rate = min(non_null_rates.values()) if non_null_rates else 1.0
            field_pass = min_rate >= policy.min_completeness
            dims.append(_dim("字段完整性", min_rate, policy.min_completeness, field_pass, per_field=non_null_rates))
            if not field_pass:
                issues.append(f"必填字段非空率 {min_rate:.2%} 低于阈值 {policy.min_completeness:.2%}")
        else:
            dims.append(_dim("字段完整性", 1.0, policy.min_completeness, True))
    else:
        dims.append(_dim("字段完整性", 1.0, policy.min_completeness, True, note="未声明 target_schema，跳过"))

    # 4. 唯一性：主键/业务键重复率
    if target_schema and target_schema.primary_key and (accumulated is not None or clean_records):
        if accumulated is not None and accumulated.total > 0:
            uniqueness = accumulated.uniqueness
            dup = accumulated.duplicates
        elif clean_records:
            seen = set()
            dup = 0
            for r in clean_records:
                key = tuple(str(r.data.get(k, "")) for k in target_schema.primary_key)
                if key in seen:
                    dup += 1
                else:
                    seen.add(key)
            uniqueness = 1.0 - (dup / len(clean_records)) if clean_records else 1.0
        else:
            uniqueness = 1.0
            dup = 0
        uniq_pass = uniqueness >= policy.min_uniqueness
        dims.append(_dim("唯一性", uniqueness, policy.min_uniqueness, uniq_pass, duplicates=dup, key=target_schema.primary_key))
        if not uniq_pass:
            issues.append(f"主键 {target_schema.primary_key} 重复 {dup} 条")
    else:
        dims.append(_dim("唯一性", 1.0, policy.min_uniqueness, True, note="未声明主键，跳过"))

    # 5. 可追溯性：输出记录到原始制品的血缘覆盖率（plan 15.3：100%）
    # clean 输出 JSONL 不含 meta（plan 6.3），故优先用调用方从 lineage 文件计算的覆盖率
    if lineage_coverage is not None:
        lineage_rate = lineage_coverage
    elif clean_records:
        with_lineage = sum(1 for r in clean_records if r.meta.get("artifact_id"))
        lineage_rate = with_lineage / len(clean_records)
    else:
        lineage_rate = 1.0
    lineage_pass = (not policy.require_lineage) or lineage_rate >= 1.0
    dims.append(_dim("可追溯性", lineage_rate, 1.0 if policy.require_lineage else None, lineage_pass,
                     source="lineage_file" if lineage_coverage is not None else "record_meta"))
    if not lineage_pass:
        issues.append(f"血缘覆盖率 {lineage_rate:.2%}，未达 100%")

    # 6. 异常隔离率：超过 max_reject_rate 则 fail，超 warn 则 warn
    # 分母用解析阶段总记录数（parsed + rejects_parse），文件 1:N 链路 raw=artifact 数不适用
    total_rejects = rejects_parse + rejects_clean
    reject_denom = parsed_n + rejects_parse
    reject_rate = (total_rejects / reject_denom) if reject_denom else 0.0
    if reject_rate > policy.max_reject_rate:
        reject_pass = False
        issues.append(f"异常隔离率 {reject_rate:.2%} 超过硬上限 {policy.max_reject_rate:.2%}")
    else:
        reject_pass = True
    dims.append(_dim("异常隔离率", reject_rate, policy.max_reject_rate, reject_pass,
                     parse_rejects=rejects_parse, clean_rejects=rejects_clean, total=total_rejects))

    # ---- 汇总结论 ----
    hard_fail = any(not d.passed for d in dims if d.name in ("解析完整性", "清洗完整性", "可追溯性", "异常隔离率"))
    has_warn = any(not d.passed for d in dims) and not hard_fail
    if hard_fail:
        overall = QualityResult.FAIL
    elif has_warn or reject_rate > policy.warn_reject_rate:
        overall = QualityResult.WARN
    else:
        overall = QualityResult.PASS

    return QualityReport(
        task_id="",  # 由调用方填充
        overall=overall,
        dimensions=dims,
        issues=issues,
        counts=record_counts,
    )
