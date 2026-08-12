# -*- coding: utf-8 -*-
"""Phase 4B 批次 4：文档原文、比较、审查、派生内容与证据门。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.semantic_harness.document_executor import execute_document_plan
from src.semantic_harness.capabilities import get_capability_registry
from src.semantic_harness.document_models import (
    AuditOperator,
    AuditRule,
    DocumentAction,
    DocumentPhysicalPlan,
    DocumentPlanStatus,
    DocumentSelection,
    DocumentSource,
    FindingStatus,
)
from src.semantic_harness.document_verifier import verify_document_execution
from src.semantic_harness.models import ContentPolicy
from src.semantic_harness.physical_models import RuntimeProfileName
from src.semantic_harness.physical_planner import runtime_policy


ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "semantic_harness"
    / "public"
    / "batch0"
    / "documents"
)


def test_document_capability_is_explicitly_registered() -> None:
    manifest = get_capability_registry().manifest("document.evidence")

    assert set(manifest.accepts) == {
        "pdf", "docx", "pptx", "html", "markdown", "txt", "xml",
    }
    assert {"verbatim", "compare", "audit", "translate"}.issubset(
        manifest.operations
    )
    assert manifest.evidence_preserving is True
    assert get_capability_registry().is_healthy("document.evidence") is True


def _source(index: int, path: Path) -> DocumentSource:
    return DocumentSource(
        source_id=f"source_{index}",
        artifact_id=f"artifact_{index}",
        artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        detected_format=path.suffix.lower().lstrip("."),
        original_name=path.name,
    )


def _plan(
    action: DocumentAction,
    paths: tuple[Path, ...],
    *,
    content_policy: ContentPolicy = ContentPolicy.VERBATIM,
    audit_rules: tuple[AuditRule, ...] = (),
    whole_document: bool = True,
    selections: tuple[DocumentSelection, ...] = (),
) -> DocumentPhysicalPlan:
    return DocumentPhysicalPlan(
        physical_plan_id=f"docphysical_{action.value}",
        logical_plan_id=f"plan_{action.value}",
        logical_plan_revision=1,
        logical_plan_hash="1" * 64,
        bound_plan_id="bound_test",
        bound_plan_hash="2" * 64,
        binding_revision=1,
        status=DocumentPlanStatus.READY,
        action=action,
        content_policy=content_policy,
        sources=tuple(_source(index, path) for index, path in enumerate(paths)),
        whole_document=whole_document,
        selections=selections,
        audit_rules=audit_rules,
        instruction="测试文档能力",
        runtime_policy=runtime_policy(RuntimeProfileName.WINDOWS_LOCAL),
    )


def _paths(plan: DocumentPhysicalPlan, source_paths: tuple[Path, ...]):
    return {
        source.artifact_id: path
        for source, path in zip(plan.sources, source_paths, strict=True)
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    (
        "contract.pdf",
        "contract.docx",
        "contract.pptx",
        "contract.html",
        "contract.md",
        "contract.txt",
        "contract.xml",
    ),
)
async def test_verbatim_supports_seven_document_formats(
    filename: str,
    tmp_path: Path,
) -> None:
    source_path = ROOT / filename
    plan = _plan(DocumentAction.VERBATIM, (source_path,))

    bundle = await execute_document_plan(
        plan,
        artifact_paths=_paths(plan, (source_path,)),
        output_dir=tmp_path / source_path.stem,
    )
    verification = verify_document_execution(
        plan,
        bundle.result,
        source_elements=bundle.source_elements,
        result_path=bundle.result_path,
    )
    combined = "\n".join(item.text for item in bundle.result.passages)

    assert "付款条款" in combined
    assert "百分之六十" in combined
    assert verification.authoritative_output_allowed is True
    assert bundle.result.derived_content == ()
    source_sections = [
        node
        for node in bundle.result.ast.root.children
        if node.metadata.get("role") == "source_document"
    ]
    assert len(source_sections) == 1
    assert source_sections[0].children
    assert all(
        node.text and len(node.evidence_refs) == 1
        for node in source_sections[0].children
    )


@pytest.mark.asyncio
async def test_scoped_verbatim_only_returns_bound_payment_clause(
    tmp_path: Path,
) -> None:
    source_path = ROOT / "contract.docx"
    full_plan = _plan(DocumentAction.VERBATIM, (source_path,))
    full_bundle = await execute_document_plan(
        full_plan,
        artifact_paths=_paths(full_plan, (source_path,)),
        output_dir=tmp_path / "full",
    )
    heading = next(
        element
        for element in full_bundle.source_elements["artifact_0"]
        if (element.text or "").strip() == "付款条款"
    )
    scoped_plan = _plan(
        DocumentAction.VERBATIM,
        (source_path,),
        whole_document=False,
        selections=(
            DocumentSelection(
                semantic_ref="concept:付款条款",
                label="付款条款",
                artifact_element_ids={
                    "artifact_0": (heading.element_id,),
                },
            ),
        ),
    )

    scoped_bundle = await execute_document_plan(
        scoped_plan,
        artifact_paths=_paths(scoped_plan, (source_path,)),
        output_dir=tmp_path / "scoped",
    )
    verification = verify_document_execution(
        scoped_plan,
        scoped_bundle.result,
        source_elements=scoped_bundle.source_elements,
        result_path=scoped_bundle.result_path,
    )
    combined = "\n".join(
        passage.text for passage in scoped_bundle.result.passages
    )

    assert "付款条款" in combined
    assert "百分之六十" in combined
    assert "交付条款" not in combined
    assert "九月三十" not in combined
    assert "违约责任" not in combined
    assert verification.authoritative_output_allowed is True
    assert next(
        item
        for item in verification.checks
        if item.code == "document_scope_respected"
    ).passed is True


@pytest.mark.asyncio
async def test_verifier_rejects_whole_document_mixed_into_scoped_result(
    tmp_path: Path,
) -> None:
    source_path = ROOT / "contract.docx"
    full_plan = _plan(DocumentAction.VERBATIM, (source_path,))
    full_bundle = await execute_document_plan(
        full_plan,
        artifact_paths=_paths(full_plan, (source_path,)),
        output_dir=tmp_path / "full",
    )
    heading = next(
        element
        for element in full_bundle.source_elements["artifact_0"]
        if (element.text or "").strip() == "付款条款"
    )
    scoped_plan = _plan(
        DocumentAction.VERBATIM,
        (source_path,),
        whole_document=False,
        selections=(
            DocumentSelection(
                semantic_ref="concept:付款条款",
                label="付款条款",
                artifact_element_ids={
                    "artifact_0": (heading.element_id,),
                },
            ),
        ),
    )

    verification = verify_document_execution(
        scoped_plan,
        full_bundle.result,
        source_elements=full_bundle.source_elements,
        result_path=full_bundle.result_path,
    )

    scope_check = next(
        item
        for item in verification.checks
        if item.code == "document_scope_respected"
    )
    assert scope_check.passed is False
    assert verification.authoritative_output_allowed is False


class _FakeSemanticProvider:
    name = "fake-local"
    model = "fake-model"

    async def derive(self, *, action, instruction, passages, target_language):
        evidence_id = passages[0].evidence_refs[0].element_id
        return "基于原文生成的派生内容。", (evidence_id,)

    async def assess_impact(self, difference):
        evidence_ids = tuple(
            item.element_id
            for item in (*difference.before_evidence, *difference.after_evidence)
        )
        return "付款、日期或违约责任发生实质变化。", evidence_ids

    async def evaluate_rule(self, rule, passages):
        evidence_id = passages[0].evidence_refs[0].element_id
        return FindingStatus.PASS, "语义规则满足", (evidence_id,)


@pytest.mark.asyncio
async def test_compare_has_both_side_evidence_and_changed_clauses(
    tmp_path: Path,
) -> None:
    source_paths = (ROOT / "contract.docx", ROOT / "contract-v2.docx")
    plan = _plan(
        DocumentAction.COMPARE,
        source_paths,
        whole_document=False,
    )

    bundle = await execute_document_plan(
        plan,
        artifact_paths=_paths(plan, source_paths),
        output_dir=tmp_path,
        semantic_provider=_FakeSemanticProvider(),
    )
    verification = verify_document_execution(
        plan,
        bundle.result,
        source_elements=bundle.source_elements,
        result_path=bundle.result_path,
    )

    changed = {item.label for item in bundle.result.differences}
    assert {"付款条款", "交付条款", "违约责任"}.issubset(changed)
    assert all(
        item.before_evidence and item.after_evidence
        for item in bundle.result.differences
        if item.change_type == "modified"
    )
    assert verification.authoritative_output_allowed is True


@pytest.mark.asyncio
async def test_deterministic_audit_supports_chinese_number_and_date(
    tmp_path: Path,
) -> None:
    source_path = ROOT / "contract.docx"
    rules = (
        AuditRule(
            rule_id="rule_payment_days",
            label="付款周期不超过20个工作日",
            query="付款条款",
            operator=AuditOperator.NUMERIC_LTE,
            value=20,
        ),
        AuditRule(
            rule_id="rule_delivery_date",
            label="交付日期不晚于2026-09-30",
            query="交付条款",
            operator=AuditOperator.DATE_LTE,
            value="2026-09-30",
        ),
    )
    plan = _plan(
        DocumentAction.AUDIT,
        (source_path,),
        audit_rules=rules,
        whole_document=False,
    )

    bundle = await execute_document_plan(
        plan,
        artifact_paths=_paths(plan, (source_path,)),
        output_dir=tmp_path,
    )
    verification = verify_document_execution(
        plan,
        bundle.result,
        source_elements=bundle.source_elements,
        result_path=bundle.result_path,
    )

    assert [item.status for item in bundle.result.findings] == [
        FindingStatus.PASS,
        FindingStatus.PASS,
    ]
    assert all(item.evidence_refs for item in bundle.result.findings)
    assert verification.authoritative_output_allowed is True


@pytest.mark.asyncio
async def test_explicit_summary_keeps_original_and_derived_separate(
    tmp_path: Path,
) -> None:
    source_path = ROOT / "contract.docx"
    plan = _plan(
        DocumentAction.SUMMARIZE,
        (source_path,),
        content_policy=ContentPolicy.SUMMARIZED,
    )

    bundle = await execute_document_plan(
        plan,
        artifact_paths=_paths(plan, (source_path,)),
        output_dir=tmp_path,
        semantic_provider=_FakeSemanticProvider(),
    )
    verification = verify_document_execution(
        plan,
        bundle.result,
        source_elements=bundle.source_elements,
        result_path=bundle.result_path,
    )

    assert bundle.result.passages
    assert bundle.result.derived_content[0].content == "基于原文生成的派生内容。"
    assert bundle.result.ast.root.children[-1].metadata["original"] is False
    assert verification.authoritative_output_allowed is True


@pytest.mark.asyncio
async def test_derived_content_rejects_unknown_evidence_id(tmp_path: Path) -> None:
    class InvalidProvider(_FakeSemanticProvider):
        async def derive(self, **kwargs):
            return "没有合法证据。", ("made-up-evidence",)

    source_path = ROOT / "contract.docx"
    plan = _plan(
        DocumentAction.SUMMARIZE,
        (source_path,),
        content_policy=ContentPolicy.SUMMARIZED,
    )

    with pytest.raises(ValueError, match="缺少有效来源证据"):
        await execute_document_plan(
            plan,
            artifact_paths=_paths(plan, (source_path,)),
            output_dir=tmp_path,
            semantic_provider=InvalidProvider(),
        )
