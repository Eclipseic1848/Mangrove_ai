"""Recipe 清洗引擎（plan.md 第 8 节）。

职责：按 stage 顺序逐规则执行 Recipe，记录每条规则的输入/输出/隔离/合并账本。
- 同样的 RawArtifact + Recipe + 引擎版本 -> 一致输出（plan 8.1 幂等）
- 高影响规则（high_impact）执行前由图预览，确认后才执行（本引擎只负责执行，不负责确认）
- LLM 不参与执行（ADR-0003）
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.data_prep.models import Recipe, RecipeRule, RecipeStage, RecordEnvelope

from .models import CleanResult, RejectRecord, RuleStats
from .rules.base import Rule

logger = logging.getLogger(__name__)

# 全局规则注册表：rule_id -> Rule 类。web_rules 在导入时注册，后续格式在各自 Phase 注册。
_RULE_REGISTRY: Dict[str, type] = {}


def register_rule(rule_cls: type) -> type:
    """装饰器：注册规则类。Phase 2+ 的新规则用此登记。"""
    rid = getattr(rule_cls, "rule_id", None)
    if rid:
        _RULE_REGISTRY[rid] = rule_cls
    return rule_cls


def _ensure_web_rules_registered() -> None:
    """惰性导入网页规则并注册（避免循环导入）。"""
    if _RULE_REGISTRY:
        return
    from .rules.web_rules import WEB_RULE_REGISTRY
    _RULE_REGISTRY.update(WEB_RULE_REGISTRY)


def resolve_rule(recipe_rule: RecipeRule) -> Optional[Rule]:
    """把契约 RecipeRule 解析为 Rule 实例。未知 rule_id 返回 None（引擎告警跳过）。"""
    _ensure_web_rules_registered()
    cls = _RULE_REGISTRY.get(recipe_rule.rule_id)
    if cls is None:
        logger.warning("未知清洗规则 %s，已跳过", recipe_rule.rule_id)
        return None
    return cls()


# RecipeStage 执行顺序（plan 8.2）
_STAGE_ORDER = [
    RecipeStage.INPUT_VALIDATION,
    RecipeStage.BASIC_NORMALIZE,
    RecipeStage.FIELD_NORMALIZE,
    RecipeStage.VALUE_NORMALIZE,
    RecipeStage.CONTENT_CLEAN,
    RecipeStage.DEDUP,
    RecipeStage.QUALITY_CONSTRAINT,
    RecipeStage.SENSITIVE_INFO,
    RecipeStage.ANOMALY_ISOLATION,
]


class RecipeEngine:
    """按 Recipe 逐规则执行清洗。"""

    def execute(
        self,
        records: List[RecordEnvelope],
        recipe: Recipe,
        *,
        rule_params: Optional[Dict[str, Dict]] = None,
        use_web_defaults: bool = True,
    ) -> CleanResult:
        """执行 Recipe。rule_params: rule_id -> 额外参数（如 max_items）。

        use_web_defaults：空 Recipe 时是否加载默认网页规则（兼容旧 clean.py）。
        非 web 链路（upload_file/http_api）应传 False，避免网页规则误隔离结构化数据。
        """
        _ensure_web_rules_registered()
        rule_params = rule_params or {}

        # 解析 Recipe 规则；空 Recipe 按需用默认网页规则（兼容旧 clean.py 行为）
        if recipe.rules:
            rules: List[tuple[RecipeRule, Rule]] = []
            for rr in recipe.rules:
                r = resolve_rule(rr)
                if r is not None:
                    rules.append((rr, r))
        elif use_web_defaults:
            from .rules.web_rules import DEFAULT_WEB_RULES
            rules = [
                (RecipeRule(rule_id=r.rule_id, stage=r.stage, name=r.rule_id), r)
                for r in DEFAULT_WEB_RULES
            ]
        else:
            rules = []

        # 按 stage 排序（同 stage 内保持声明顺序）
        stage_rank = {s: i for i, s in enumerate(_STAGE_ORDER)}
        rules.sort(key=lambda x: stage_rank.get(x[0].stage, 99))

        current: List[RecordEnvelope] = list(records)
        all_rejects: List[RejectRecord] = []
        all_stats: List[RuleStats] = []

        for rr, rule in rules:
            params = {**(rr.params or {}), **(rule_params.get(rr.rule_id) or {})}
            input_count = len(current)
            kept, rejects = rule.apply(current, params)
            stats = rule.make_stats(input_count, kept, rejects)
            all_stats.append(stats)
            all_rejects.extend(rejects)
            current = kept
            logger.debug("规则 %s: 输入 %d -> 输出 %d, 隔离 %d", rr.rule_id, input_count, len(kept), len(rejects))

        return CleanResult(clean=current, rejects=all_rejects, rule_stats=all_stats)

    def execute_batch(
        self,
        records: List[RecordEnvelope],
        recipe: Recipe,
        *,
        rule_params: Optional[Dict[str, Dict]] = None,
        use_web_defaults: bool = True,
    ) -> CleanResult:
        """执行单个批次的清洗（不跨批持有数据，plan Phase 2 Task 2）。

        精确去重需要跨批状态时，调用方负责保存稳定键集合，不在此方法内累积。
        """
        return self.execute(records, recipe, rule_params=rule_params, use_web_defaults=use_web_defaults)
