# -*- coding: utf-8 -*-
"""文档执行后置验证；成功由确定性检查决定，不由模型自报。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence
import uuid

from src.data_prep.document_models import DocumentElement, EvidenceRef

from .document_models import (
    DocumentAction,
    DocumentExecutionResult,
    DocumentPhysicalPlan,
    FindingStatus,
)
from .models import (
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)


def _fingerprint(checks: Sequence[VerificationCheck]) -> str:
    payload = [
        {
            "code": item.code,
            "passed": item.passed,
            "actual": item.actual,
        }
        for item in checks
        if not item.passed
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _evidence_refs(result: DocumentExecutionResult) -> tuple[EvidenceRef, ...]:
    return tuple(
        evidence
        for passage in result.passages
        for evidence in passage.evidence_refs
    ) + tuple(
        evidence
        for difference in result.differences
        for evidence in (
            *difference.before_evidence,
            *difference.after_evidence,
        )
    ) + tuple(
        evidence
        for finding in result.findings
        for evidence in finding.evidence_refs
    ) + tuple(
        evidence
        for derived in result.derived_content
        for evidence in derived.evidence_refs
    )


def _ast_nodes(result: DocumentExecutionResult):
    pending = list(result.ast.root.children)
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.children)


def _scope_respected(
    plan: DocumentPhysicalPlan,
    result: DocumentExecutionResult,
) -> bool:
    """限定提取的正文必须来自绑定选择，不允许整份文档混入。"""

    if plan.whole_document:
        return bool(result.passages)
    if not plan.selections:
        return plan.action != DocumentAction.VERBATIM
    allowed_by_label = {
        selection.label: {
            element_id
            for element_ids in selection.artifact_element_ids.values()
            for element_id in element_ids
        }
        for selection in plan.selections
    }
    return bool(result.passages) and all(
        passage.label in allowed_by_label
        and bool(
            {
                evidence.element_id
                for evidence in passage.evidence_refs
            }
            & allowed_by_label[passage.label]
        )
        for passage in result.passages
    )


def verify_document_execution(
    plan: DocumentPhysicalPlan,
    result: DocumentExecutionResult,
    *,
    source_elements: Mapping[str, tuple[DocumentElement, ...]],
    result_path: Path,
) -> VerificationReport:
    source = {
        element.element_id: element
        for elements in source_elements.values()
        for element in elements
    }
    evidence = _evidence_refs(result)
    evidence_valid = all(
        item.element_id in source
        and item.artifact_id == source[item.element_id].artifact_id
        and item.page == source[item.element_id].page
        and item.quote == (source[item.element_id].text or "").strip()
        and item.quote_sha256
        == hashlib.sha256((item.quote or "").encode("utf-8")).hexdigest()
        for item in evidence
    )
    diff_sides_valid = all(
        (
            item.change_type == "added" or bool(item.before_evidence)
        )
        and (
            item.change_type == "removed" or bool(item.after_evidence)
        )
        for item in result.differences
    )
    finding_evidence_valid = all(
        item.status not in {FindingStatus.PASS, FindingStatus.FAIL}
        or bool(item.evidence_refs)
        for item in result.findings
    )
    ast_nodes = tuple(_ast_nodes(result))
    ast_traceable = all(
        not node.text or bool(node.evidence_refs)
        for node in ast_nodes
    )
    ast_source_elements = {
        evidence_ref.element_id
        for node in ast_nodes
        for evidence_ref in node.evidence_refs
        if node.metadata.get("original") is True
    }
    result_evidence_elements = {item.element_id for item in evidence}
    ast_source_coverage = result_evidence_elements.issubset(ast_source_elements)
    openable = False
    try:
        loaded = json.loads(result_path.read_text(encoding="utf-8"))
        openable = (
            loaded.get("result_id") == result.result_id
            and loaded.get("ast", {}).get("ast_id") == result.ast.ast_id
        )
    except (OSError, json.JSONDecodeError):
        openable = False
    action_output = {
        DocumentAction.VERBATIM: bool(result.passages),
        DocumentAction.COMPARE: bool(result.differences),
        DocumentAction.AUDIT: bool(result.findings),
        DocumentAction.SUMMARIZE: bool(result.derived_content),
        DocumentAction.REWRITE: bool(result.derived_content),
        DocumentAction.TRANSLATE: bool(result.derived_content),
        DocumentAction.COMPOSE: bool(result.derived_content),
    }[plan.action]
    verbatim_pure = not (
        plan.action == DocumentAction.VERBATIM and result.derived_content
    )
    scope_respected = _scope_respected(plan, result)
    checks = (
        VerificationCheck(
            code="action_output_present",
            passed=action_output,
            expected=True,
            actual=action_output,
            message="当前操作必须产生对应结果",
        ),
        VerificationCheck(
            code="evidence_exact_match",
            passed=evidence_valid and bool(evidence),
            expected="全部引用逐字匹配来源元素",
            actual=f"{len(evidence)} 条证据",
            message="引用、页码、制品和 SHA-256 必须回绑不可变来源",
        ),
        VerificationCheck(
            code="comparison_both_sides",
            passed=diff_sides_valid,
            expected=True,
            actual=diff_sides_valid,
            message="修改差异必须同时包含修改前和修改后证据",
        ),
        VerificationCheck(
            code="finding_evidence",
            passed=finding_evidence_valid,
            expected=True,
            actual=finding_evidence_valid,
            message="通过或失败的审查结论必须有证据",
        ),
        VerificationCheck(
            code="verbatim_no_derived_content",
            passed=verbatim_pure,
            expected=True,
            actual=verbatim_pure,
            message="原文模式不得包含总结、改写或补写",
        ),
        VerificationCheck(
            code="document_scope_respected",
            passed=scope_respected,
            expected=True,
            actual=scope_respected,
            message="限定文档任务的正文必须来自已绑定选择，禁止混入全文",
        ),
        VerificationCheck(
            code="ast_evidence_coverage",
            passed=ast_traceable and ast_source_coverage,
            expected=1.0,
            actual=(
                1.0
                if ast_traceable and ast_source_coverage
                else 0.0
            ),
            message="每个带正文的 AST 节点及全部结果证据必须回绑来源元素",
        ),
        VerificationCheck(
            code="result_json_reopen",
            passed=openable,
            expected=True,
            actual=openable,
            message="中间 Document AST JSON 必须可重新打开并保持身份",
        ),
    )
    status = (
        VerificationStatus.PASS
        if all(item.passed for item in checks)
        else VerificationStatus.FAIL
    )
    return VerificationReport(
        report_id=f"docverify_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.logical_plan_id,
        logical_plan_revision=plan.logical_plan_revision,
        logical_plan_hash=plan.logical_plan_hash,
        status=status,
        checks=checks,
        failure_fingerprint=(
            None if status == VerificationStatus.PASS else _fingerprint(checks)
        ),
    )
