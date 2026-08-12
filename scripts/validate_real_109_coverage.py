# -*- coding: utf-8 -*-
"""在用户原始 109 页 PDF 上验证覆盖感知检索的四个核心场景。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agentic_runtime.document_retrieval import (
    DocumentRetrievalError,
    DocumentRetrievalModule,
)
from src.agentic_runtime.document_tools import DocumentToolBroker
from src.agentic_runtime.models import SourceInput


class Page40FailureModule(DocumentRetrievalModule):
    """只在验收中注入第 40 页发现超时，其他页仍走真实缓存/Adapter。"""

    def _discover_scanned_page(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        page: int,
    ) -> tuple[str, str, str, bool]:
        if page == 40:
            raise DocumentRetrievalError("验收注入：第 40 页发现超时")
        return super()._discover_scanned_page(
            source,
            owner_key=owner_key,
            page=page,
        )


def _source(path: Path, source_id: str) -> SourceInput:
    return SourceInput(
        upload_id=source_id,
        original_name=path.name,
        host_path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/pdf",
    )


async def _inspect_and_freeze(
    broker: DocumentToolBroker,
    source: SourceInput,
    *,
    owner: str,
    task_id: str,
    scope_units: list[str],
    cardinality: str,
    required_fields: list[str],
    interpretation: str,
) -> tuple[object, object]:
    grant = broker.issue_grant(
        owner_user_id=owner,
        task_id=task_id,
        revision=1,
        run_id=f"run-{task_id}",
        sources=(source,),
    )
    source_map = await broker.call(
        grant_token=grant.token,
        operation="inspect_source",
        payload={"source_id": source.upload_id},
    )
    await broker.call(
        grant_token=grant.token,
        operation="freeze_coverage",
        payload={
            "authorized_scope": {
                "source_ids": [source.upload_id],
                "unit_ids": scope_units,
            },
            "result_cardinality": cardinality,
            "completeness": "strict",
            "ordering": "PDF 页码升序",
            "required_fields": required_fields,
            "object_boundary": "每页一张完整报销审批单",
            "stop_semantics": "冻结范围满足覆盖且每个结果均绑定权威证据",
            "interpretation": interpretation,
            "confidence": "high",
        },
    )
    return grant, source_map


def _result(page: int, source_id: str, evidence_ref: str, fields: list[str]) -> dict[str, object]:
    unit_id = f"{source_id}:page:{page}"
    return {
        "result_id": f"expense-page-{page}",
        "unit_ids": [unit_id],
        "evidence_refs": [evidence_ref],
        "boundary_evidence_refs": [evidence_ref],
        "required_field_evidence": {
            field: [evidence_ref] for field in fields
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    pdf = Path(args.pdf).resolve()
    source = _source(pdf, args.source_id)
    module = DocumentRetrievalModule(execution_root=Path(args.cache_root))
    broker = DocumentToolBroker(retriever=module)
    report: dict[str, object] = {
        "source_sha256": source.sha256,
        "baseline_elapsed_ms": args.baseline_ms,
        "scenarios": {},
    }

    grant, source_map = await _inspect_and_freeze(
        broker,
        source,
        owner=args.owner,
        task_id="real-109-explicit",
        scope_units=[f"{source.upload_id}:page:20"],
        cardinality="all",
        required_fields=["结算金额"],
        interpretation="只读取第 20 页的结算金额",
    )
    started = time.perf_counter()
    read = await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": source.upload_id,
            "unit_ids": [f"{source.upload_id}:page:20"],
            "needs": ["text", "layout"],
        },
    )
    proposal = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "指定页已权威读取",
            "results": [_result(20, source.upload_id, read["evidence_refs"][0], ["结算金额"])],
        },
    )
    report["scenarios"]["explicit_page"] = {
        "elapsed_seconds": time.perf_counter() - started,
        "unit_count": source_map["unit_count"],
        "high_quality_reads": 1,
        "cache_hits": read["cache_hits"],
        "decision": proposal["decision"],
    }

    grant, _ = await _inspect_and_freeze(
        broker,
        source,
        owner=args.owner,
        task_id="real-109-first",
        scope_units=[],
        cardinality="first",
        required_fields=["报销人", "每行出差信息", "小计", "合计发票金额", "结算金额"],
        interpretation="返回按页码顺序的第一个完整报销审批单",
    )
    started = time.perf_counter()
    read = await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": source.upload_id,
            "unit_ids": [f"{source.upload_id}:page:1"],
            "needs": ["text", "table", "layout"],
        },
    )
    first_fields = ["报销人", "每行出差信息", "小计", "合计发票金额", "结算金额"]
    proposal = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={
            "summary": "第 1 页是首个完整对象",
            "ordering_proof": ["获准范围按页码升序，第 1 页没有前序页"],
            "results": [_result(1, source.upload_id, read["evidence_refs"][0], first_fields)],
        },
    )
    report["scenarios"]["first_complete"] = {
        "elapsed_seconds": time.perf_counter() - started,
        "high_quality_reads": 1,
        "cache_hits": read["cache_hits"],
        "decision": proposal["decision"],
    }

    grant, _ = await _inspect_and_freeze(
        broker,
        source,
        owner=args.owner,
        task_id="real-109-all",
        scope_units=[],
        cardinality="all",
        required_fields=[],
        interpretation="返回整份文件中所有提到都江堰的报销审批单",
    )
    started = time.perf_counter()
    discovery = await broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={"source_id": source.upload_id, "query": "都江堰"},
    )
    read = await broker.call(
        grant_token=grant.token,
        operation="read_evidence",
        payload={
            "source_id": source.upload_id,
            "unit_ids": discovery["candidate_unit_ids"],
            "needs": ["text", "table", "layout"],
        },
    )
    evidence_by_unit = {
        item["unit_id"]: item["evidence_ref"] for item in read["items"]
    }
    results = []
    for unit_id in discovery["candidate_unit_ids"]:
        page = int(unit_id.rsplit(":", 1)[1])
        results.append(_result(page, source.upload_id, evidence_by_unit[unit_id], []))
    proposal = await broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={"summary": "全部页面已发现且候选已精读", "results": results},
    )
    report["scenarios"]["all_scattered"] = {
        "elapsed_seconds": time.perf_counter() - started,
        "observed": len(discovery["observed_unit_ids"]),
        "candidate_pages": [int(value.rsplit(":", 1)[1]) for value in discovery["candidate_unit_ids"]],
        "high_quality_reads": len(read["source_unit_ids"]),
        "discovery_cache_hits": discovery["cache_hits"],
        "evidence_cache_hits": read["cache_hits"],
        "decision": proposal["decision"],
    }

    failure_broker = DocumentToolBroker(
        retriever=Page40FailureModule(execution_root=Path(args.cache_root))
    )
    grant, _ = await _inspect_and_freeze(
        failure_broker,
        source,
        owner=args.owner,
        task_id="real-109-failure",
        scope_units=[],
        cardinality="all",
        required_fields=[],
        interpretation="返回整份文件中所有提到都江堰的报销审批单",
    )
    discovery = await failure_broker.call(
        grant_token=grant.token,
        operation="discover_content",
        payload={"source_id": source.upload_id, "query": "都江堰"},
    )
    proposal = await failure_broker.call(
        grant_token=grant.token,
        operation="propose_completion",
        payload={"summary": "尝试提前完成", "results": []},
    )
    report["scenarios"]["page_40_failure"] = {
        "observed": len(discovery["observed_unit_ids"]),
        "unknown_units": discovery["unknown_units"],
        "decision": proposal["decision"],
    }
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--cache-root", default=".pytest-tmp/real-109-acceptance")
    parser.add_argument("--baseline-ms", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    decisions = [
        item["decision"]["passed"]
        for name, item in report["scenarios"].items()
        if name != "page_40_failure"
    ]
    failure_closed = not report["scenarios"]["page_40_failure"]["decision"]["passed"]
    return 0 if all(decisions) and failure_closed else 1


if __name__ == "__main__":
    raise SystemExit(main())
