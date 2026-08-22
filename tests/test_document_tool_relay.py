# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agentic_runtime.document_tools import (
    DocumentToolBroker,
    DocumentToolClaims,
    DocumentToolError,
    DocumentToolGrant,
)
from src.agentic_runtime.models import SourceInput
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.routes import document_tools as document_tool_routes


class InspectAdapter:
    async def inspect(
        self,
        source: SourceInput,
        *,
        owner_key: str = "anonymous",
    ) -> dict[str, object]:
        del owner_key
        return {
            "source_id": source.upload_id,
            "name": source.original_name,
            "unit_count": 3,
            "units": [
                {"unit_id": f"{source.upload_id}:page:{page}"}
                for page in range(1, 4)
            ],
        }

    async def discover(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        query: str,
        unit_ids: tuple[str, ...],
    ) -> dict[str, object]:
        del owner_key, query
        selected = unit_ids or tuple(
            f"{source.upload_id}:page:{page}" for page in range(1, 4)
        )
        return {
            "source_id": source.upload_id,
            "observed_unit_ids": list(selected),
            "candidate_unit_ids": [
                unit_id for unit_id in selected if unit_id.endswith(":page:1")
            ],
            "low_quality_units": [],
            "unknown_units": [],
            "hits": [],
            "cache_hits": 0,
            "parser_versions": ["test-discovery-v1"],
        }

    async def read(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        unit_ids: tuple[str, ...],
        needs: tuple[str, ...],
    ) -> dict[str, object]:
        del owner_key, needs
        return {
            "source_id": source.upload_id,
            "source_unit_ids": list(unit_ids),
            "evidence_refs": [f"evidence:{unit_id}" for unit_id in unit_ids],
            "quality_status": "trusted",
            "authoritative_parser_versions": ["test-read-v1"],
            "items": [
                {
                    "evidence_ref": f"evidence:{unit_id}",
                    "unit_id": unit_id,
                    "quality_status": "trusted",
                }
                for unit_id in unit_ids
            ],
            "cache_hits": 0,
        }


@pytest.mark.asyncio
async def test_ordinal_result_does_not_require_units_after_target(
    tmp_path: Path,
) -> None:
    """第 N 个对象只需证明前序和目标，不应被后续低质量页阻断。"""

    class OrdinalAdapter(InspectAdapter):
        async def discover(
            self,
            source: SourceInput,
            *,
            owner_key: str,
            query: str,
            unit_ids: tuple[str, ...],
        ) -> dict[str, object]:
            del owner_key, query, unit_ids
            return {
                "source_id": source.upload_id,
                "observed_unit_ids": [
                    f"{source.upload_id}:page:1",
                    f"{source.upload_id}:page:2",
                    f"{source.upload_id}:page:3",
                ],
                "candidate_unit_ids": [
                    f"{source.upload_id}:page:1",
                    f"{source.upload_id}:page:2",
                ],
                "low_quality_units": [f"{source.upload_id}:page:3"],
                "unknown_units": [],
                "hits": [],
                "cache_hits": 0,
                "parser_versions": ["test-discovery-v1"],
            }

    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=OrdinalAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-ordinal",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "ordinal",
            "result_ordinal": 2,
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": ["姓名"],
            "object_boundary": "一张完整审批单",
            "stop_semantics": "第 2 张审批单边界与字段已证明后停止",
            "interpretation": "提取第 2 张审批单",
            "confidence": "high",
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={"source_id": "upload-a", "query": "审批单"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": "upload-a",
            "unit_ids": ["upload-a:page:1", "upload-a:page:2"],
        },
    )

    proposal = {
        "summary": "第 2 张审批单位于第 2 页",
        "ordering_proof": [
            "第 1 张审批单：upload-a:page:1",
        ],
        "results": [{
            "result_id": "expense-2",
            "unit_ids": ["upload-a:page:2"],
            "evidence_refs": ["evidence:upload-a:page:2"],
            "boundary_evidence_refs": ["evidence:upload-a:page:2"],
            "required_field_evidence": {
                "姓名": ["evidence:upload-a:page:2"],
            },
        }],
        "rejected_candidates": [{
            "unit_id": "upload-a:page:1",
            "evidence_refs": ["evidence:upload-a:page:1"],
        }],
    }
    missing_order = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload=proposal,
    )
    assert missing_order["decision"]["passed"] is False
    assert any(
        "缺少足够的稳定顺序证明" in gap
        for gap in missing_order["decision"]["gaps"]
    )

    proposal["ordering_proof"].append(
        "第 2 张审批单：upload-a:page:2"
    )
    accepted = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload=proposal,
    )

    assert accepted["decision"]["passed"] is True


@pytest.mark.asyncio
async def test_inspect_accepts_runtime_visible_source_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )

    result = await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "/workspace/input/source.pdf"},
    )

    assert result["source_id"] == "upload-a"
    frozen = await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {
                "source_ids": ["/workspace/input/source.pdf"],
            },
            "result_cardinality": "first",
            "completeness": "strict",
            "ordering": "按页码升序",
            "required_fields": ["姓名"],
            "object_boundary": "一张完整审批单",
            "stop_semantics": "首个完整对象字段齐全后停止",
            "interpretation": "提取第一个完整审批单",
            "confidence": "high",
        },
    )

    assert frozen["contract"]["authorized_scope"]["source_ids"] == [
        "upload-a",
    ]


@pytest.mark.asyncio
async def test_sequential_trusted_reads_do_not_downgrade_previous_units(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "all",
            "completeness": "strict",
            "ordering": "按页码升序",
            "required_fields": [],
            "object_boundary": "每页一条记录",
            "stop_semantics": "全部页面可信读取后停止",
            "interpretation": "返回全部记录",
            "confidence": "high",
        },
    )

    first = await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={"source_id": "upload-a", "unit_ids": ["upload-a:page:1"]},
    )
    second = await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={"source_id": "upload-a", "unit_ids": ["upload-a:page:3"]},
    )

    assert first["coverage"]["low_quality"] == 0
    assert second["coverage"]["authoritatively_read"] == 2
    assert second["coverage"]["low_quality"] == 0


@pytest.mark.asyncio
async def test_all_results_must_resolve_every_discovered_candidate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "all",
            "completeness": "strict",
            "ordering": "按页码升序",
            "required_fields": [],
            "object_boundary": "每条记录",
            "stop_semantics": "全部候选处理完成",
            "interpretation": "返回全部匹配记录",
            "confidence": "high",
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={"source_id": "upload-a", "query": "目标"},
    )

    for invalid_confirmation in ("false", "true", 1):
        with pytest.raises(DocumentToolError, match="只有明确确认结果为空"):
            await broker.call(
                grant_token=grant.token,
                operation="propose_completion",
                payload={
                    "summary": "没有结果",
                    "results": [],
                    "result_empty_confirmed": invalid_confirmation,
                },
            )

    unresolved = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "没有结果",
            "results": [],
            "result_empty_confirmed": True,
        },
    )
    assert unresolved["decision"]["passed"] is False
    assert any("发现候选未形成结果" in gap for gap in unresolved["decision"]["gaps"])

    await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={"source_id": "upload-a", "unit_ids": ["upload-a:page:1"]},
    )
    rejected = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "候选经精读确认不匹配",
            "results": [],
            "result_empty_confirmed": True,
            "rejected_candidates": [{
                "unit_id": "upload-a:page:1",
                "evidence_refs": ["evidence:upload-a:page:1"],
            }],
        },
    )
    assert rejected["decision"]["passed"] is True


@pytest.mark.asyncio
async def test_document_tool_grant_can_inspect_only_bound_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )

    result = await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )

    assert result == {
        "source_id": "upload-a",
        "name": "source.pdf",
        "unit_count": 3,
        "units": [
            {"unit_id": "upload-a:page:1"},
            {"unit_id": "upload-a:page:2"},
            {"unit_id": "upload-a:page:3"},
        ],
    }

    with pytest.raises(DocumentToolError, match="不存在或不属于"):
        await broker.call(
            grant_token=grant.token,
            operation="inspect_source",
            payload={"source_id": "upload-other"},
        )


def _relay_headers(grant: DocumentToolGrant) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {grant.token}",
        "X-Mangrove-Grant-ID": grant.grant_id,
        "X-Mangrove-Owner-Binding": grant.owner_binding,
        "X-Mangrove-Task-ID": grant.task_id,
        "X-Mangrove-Revision": str(grant.revision),
        "X-Mangrove-Run-ID": grant.run_id,
        "X-Mangrove-Purpose": grant.purpose,
    }


def _claims(grant: DocumentToolGrant, **changes: object) -> DocumentToolClaims:
    values = {
        "grant_id": grant.grant_id,
        "owner_binding": grant.owner_binding,
        "task_id": grant.task_id,
        "revision": grant.revision,
        "run_id": grant.run_id,
        "purpose": grant.purpose,
    }
    values.update(changes)
    return DocumentToolClaims.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_change",
    (
        {"run_id": "run-other"},
        {"owner_binding": "0" * 64},
        {"revision": 2},
        {"purpose": "model_relay"},
    ),
)
async def test_document_tool_grant_rejects_each_mismatched_binding(
    tmp_path: Path,
    claim_change: dict[str, object],
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )

    with pytest.raises(DocumentToolError, match="绑定不一致"):
        await broker.call(
            grant_token=grant.token,
            operation="inspect_source",
            payload={"source_id": "upload-a"},
            claims=_claims(grant, **claim_change),
        )


def test_internal_document_tool_relay_uses_bearer_grant(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )
    app = FastAPI()
    app.include_router(document_tool_routes.router)
    app.dependency_overrides[
        document_tool_routes.get_document_tool_broker
    ] = lambda: broker
    client = TestClient(app)

    response = client.post(
        "/internal/document-tools/inspect_source",
        headers=_relay_headers(grant),
        json={"source_id": "upload-a"},
    )

    assert response.status_code == 200
    assert response.json()["unit_count"] == 3
    wrong_run_headers = _relay_headers(grant)
    wrong_run_headers["X-Mangrove-Run-ID"] = "run-other"
    wrong_run = client.post(
        "/internal/document-tools/inspect_source",
        headers=wrong_run_headers,
        json={"source_id": "upload-a"},
    )
    assert wrong_run.status_code == 403
    assert "绑定不一致" in wrong_run.json()["detail"]
    missing_claims = client.post(
        "/internal/document-tools/inspect_source",
        headers={"Authorization": f"Bearer {grant.token}"},
        json={"source_id": "upload-a"},
    )
    assert missing_claims.status_code == 403
    assert "/internal/document-tools/{operation}" not in app.openapi()[
        "paths"
    ]
    oversized = client.post(
        "/internal/document-tools/inspect_source",
        headers=_relay_headers(grant),
        content=b"x" * (1024 * 1024 + 1),
    )
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_document_tool_grant_fails_closed_after_revoke_or_expiry(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    clock = lambda: now
    broker = DocumentToolBroker(
        retriever=InspectAdapter(),
        clock=clock,
    )
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )

    broker.revoke_grant(grant.grant_id, "任务取消")
    with pytest.raises(DocumentToolError, match="无效或已撤销"):
        await broker.call(
            grant_token=grant.token,
            operation="inspect_source",
            payload={"source_id": "upload-a"},
        )

    expiring = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
        ttl_seconds=1,
    )
    now += timedelta(seconds=2)
    with pytest.raises(DocumentToolError, match="已过期"):
        await broker.call(
            grant_token=expiring.token,
            operation="inspect_source",
            payload={"source_id": "upload-a"},
        )


@pytest.mark.asyncio
async def test_all_matches_cannot_complete_until_every_unit_is_observed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "all",
            "completeness": "strict",
            "ordering": "按页码升序",
            "required_fields": [],
            "object_boundary": "每条匹配记录",
            "stop_semantics": "全部获准页面完成可信发现且候选已精读",
            "interpretation": "返回整份文件中的全部匹配项",
            "confidence": "high",
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={
            "source_id": "upload-a",
            "query": "目标",
            "unit_ids": ["upload-a:page:1"],
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": "upload-a",
            "unit_ids": ["upload-a:page:1"],
            "needs": ["text"],
        },
    )

    incomplete = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "已找到目标",
            "results": [{
                "result_id": "result-1",
                "unit_ids": ["upload-a:page:1"],
                "evidence_refs": ["evidence:upload-a:page:1"],
                "boundary_evidence_refs": ["evidence:upload-a:page:1"],
                "required_field_evidence": {},
            }],
        },
    )

    assert incomplete["decision"]["passed"] is False
    assert any("未参与可信发现" in gap for gap in incomplete["decision"]["gaps"])

    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={
            "source_id": "upload-a",
            "query": "目标",
            "unit_ids": ["upload-a:page:2", "upload-a:page:3"],
        },
    )
    complete = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "全部获准页面已经检查",
            "results": [{
                "result_id": "result-1",
                "unit_ids": ["upload-a:page:1"],
                "evidence_refs": ["evidence:upload-a:page:1"],
                "boundary_evidence_refs": ["evidence:upload-a:page:1"],
                "required_field_evidence": {},
            }],
        },
    )

    assert complete["decision"] == {
        "passed": True,
        "decision": "passed",
        "gaps": [],
    }


@pytest.mark.asyncio
async def test_coverage_contract_and_ledger_resume_in_same_owner_run(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    repository = AgenticRuntimeRepository(tmp_path / "runtime.db")
    broker = DocumentToolBroker(
        retriever=InspectAdapter(),
        state_store=repository,
    )
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {
                "source_ids": ["upload-a"],
                "unit_ids": ["upload-a:page:2"],
            },
            "result_cardinality": "first",
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": ["姓名", "金额"],
            "object_boundary": "完整单据",
            "stop_semantics": "首个完整单据已读且字段齐全",
            "interpretation": "读取第 2 页的首个完整单据",
            "confidence": "high",
        },
    )

    resumed = DocumentToolBroker(
        retriever=InspectAdapter(),
        state_store=repository,
    )
    resumed_grant = resumed.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )
    read = await resumed.call(
        grant_token=resumed_grant.token,
        operation="read_evidence",
        payload={
            "source_id": "upload-a",
            "unit_ids": ["upload-a:page:2"],
            "needs": ["text"],
        },
    )

    assert read["coverage"]["authoritatively_read"] == 1
    assert repository.get_coverage(
        user_id="user-b",
        task_id="task-a",
        revision=1,
        run_id="run-a",
    ) is None


@pytest.mark.asyncio
async def test_explicit_page_can_complete_after_direct_authoritative_read(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-explicit-page",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {
                "source_ids": ["upload-a"],
                "unit_ids": ["upload-a:page:2"],
            },
            "result_cardinality": "all",
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": ["金额"],
            "object_boundary": "第 2 页",
            "stop_semantics": "指定页已权威读取",
            "interpretation": "只读取第 2 页金额",
            "confidence": "high",
        },
    )
    read = await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": "upload-a",
            "unit_ids": ["upload-a:page:2"],
            "needs": ["text"],
        },
    )
    assert read["coverage"]["observed"] == 1
    accepted = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "指定页已读取",
            "results": [{
                "result_id": "page-2",
                "unit_ids": ["upload-a:page:2"],
                "evidence_refs": ["evidence:upload-a:page:2"],
                "boundary_evidence_refs": ["evidence:upload-a:page:2"],
                "required_field_evidence": {
                    "金额": ["evidence:upload-a:page:2"],
                },
            }],
        },
    )
    assert accepted["decision"]["passed"] is True


@pytest.mark.asyncio
async def test_first_complete_object_requires_preceding_order_and_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "first",
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": ["姓名", "金额"],
            "object_boundary": "完整报销审批单",
            "stop_semantics": "找到并精读首个字段齐全的完整单据",
            "interpretation": "提取第一个完整报销审批单",
            "confidence": "high",
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={
            "source_id": "upload-a",
            "query": "报销审批单",
            "unit_ids": ["upload-a:page:2"],
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": "upload-a",
            "unit_ids": ["upload-a:page:2"],
            "needs": ["text", "layout"],
        },
    )
    proposal = {
        "summary": "第 2 页是首个完整单据",
        "ordering_proof": ["已按页码升序检查前序页"],
        "results": [{
            "result_id": "expense-1",
            "unit_ids": ["upload-a:page:2"],
            "evidence_refs": ["evidence:upload-a:page:2"],
            "boundary_evidence_refs": ["evidence:upload-a:page:2"],
            "required_field_evidence": {
                "姓名": ["evidence:upload-a:page:2"],
                "金额": ["evidence:upload-a:page:2"],
            },
        }],
    }

    premature = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload=proposal,
    )
    assert premature["decision"]["passed"] is False
    assert any("之前仍有" in gap for gap in premature["decision"]["gaps"])

    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={
            "source_id": "upload-a",
            "query": "报销审批单",
            "unit_ids": ["upload-a:page:1"],
        },
    )
    accepted = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload=proposal,
    )
    assert accepted["decision"]["passed"] is True


@pytest.mark.asyncio
async def test_first_result_can_span_pages_but_each_page_needs_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-cross-page",
        revision=1,
        run_id="run-a",
        sources=(SourceInput(
            upload_id="upload-a",
            original_name="source.pdf",
            host_path=source,
            sha256="a" * 64,
            media_type="application/pdf",
        ),),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "first",
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": ["姓名", "金额"],
            "object_boundary": "跨页完整单据",
            "stop_semantics": "第一个完整单据的结束边界已确认",
            "interpretation": "返回第一个跨页完整单据",
            "confidence": "high",
        },
    )
    await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={"source_id": "upload-a", "query": "单据"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": "upload-a",
            "unit_ids": ["upload-a:page:1", "upload-a:page:2"],
            "needs": ["text", "layout"],
        },
    )
    proposal = {
        "summary": "首个单据跨第 1–2 页",
        "ordering_proof": ["已从第 1 页按序检查"],
        "results": [{
            "result_id": "expense-1",
            "unit_ids": ["upload-a:page:1", "upload-a:page:2"],
            "evidence_refs": ["evidence:upload-a:page:1"],
            "boundary_evidence_refs": [
                "evidence:upload-a:page:1",
                "evidence:upload-a:page:2",
            ],
            "required_field_evidence": {
                "姓名": ["evidence:upload-a:page:1"],
                "金额": ["evidence:upload-a:page:2"],
            },
        }],
    }

    missing_binding = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload=proposal,
    )
    assert missing_binding["decision"]["passed"] is False
    assert any(
        "每个内容单元都有权威证据" in gap
        for gap in missing_binding["decision"]["gaps"]
    )

    proposal["results"][0]["evidence_refs"].append(
        "evidence:upload-a:page:2"
    )
    accepted = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload=proposal,
    )
    assert accepted["decision"]["passed"] is True


@pytest.mark.asyncio
async def test_revoking_grant_cancels_inflight_document_tool(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    class BlockingAdapter(InspectAdapter):
        async def discover(
            self,
            source: SourceInput,
            *,
            owner_key: str,
            query: str,
            unit_ids: tuple[str, ...],
        ) -> dict[str, object]:
            del source, owner_key, query, unit_ids
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("撤销后不应继续返回工具结果")

    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=BlockingAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )
    await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": "upload-a"},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {"source_ids": ["upload-a"]},
            "result_cardinality": "all",
            "completeness": "strict",
            "ordering": "页码升序",
            "required_fields": [],
            "object_boundary": "匹配记录",
            "stop_semantics": "全部页面已发现",
            "interpretation": "返回全部匹配",
            "confidence": "high",
        },
    )
    pending = asyncio.create_task(
        broker.call(
            grant_token=grant.token,
            operation="discover_content",
            payload={"source_id": "upload-a", "query": "目标"},
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    broker.revoke_grant(grant.grant_id, "任务取消")

    with pytest.raises(asyncio.CancelledError):
        await pending


@pytest.mark.asyncio
async def test_material_ambiguity_requests_one_question_before_freeze(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = broker.issue_grant(
        owner_user_id="user-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )

    result = await broker.call(
        grant_token=grant.token,
        operation="request_clarification",
        payload={
            "question": "你需要第一条记录，还是文件中的全部记录？",
            "reason": "两种解释会改变结果数量和扫描范围",
        },
    )

    assert result["status"] == "needs_input"
    assert broker.clarification_state(grant.grant_id) == {
        "question": "你需要第一条记录，还是文件中的全部记录？",
        "reason": "两种解释会改变结果数量和扫描范围",
    }
