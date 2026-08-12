# -*- coding: utf-8 -*-
"""批次 0 工具赛马 Graph；状态只传制品引用和小型摘要。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from src.semantic_harness.models import (
    ArtifactRef,
    ExecutionLedger,
    LineageEvent,
    ResourceUsage,
    ToolResult,
    ToolStatus,
)

from .adapters import get_table_adapter
from .fixtures import Batch0Case, load_batch0_manifest
from .scoring import score_table_result


class BenchmarkState(TypedDict, total=False):
    manifest_path: str
    case_id: str
    candidate_ids: tuple[str, ...]
    output_dir: str
    case_summary: dict[str, Any]
    candidate_refs: list[dict[str, Any]]
    verified_refs: list[dict[str, Any]]
    report: dict[str, Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_case(state: BenchmarkState) -> tuple[Any, Batch0Case]:
    manifest = load_batch0_manifest(Path(state["manifest_path"]))
    return manifest, manifest.case(state["case_id"])


def _prepare(state: BenchmarkState) -> dict[str, Any]:
    manifest, case = _load_case(state)
    source_path = manifest.resolve(case.canonical_input)
    return {
        "case_summary": {
            "case_id": case.case_id,
            "source_path": str(source_path),
            "source_sha256": _sha256(source_path),
        }
    }


def _execute(state: BenchmarkState) -> dict[str, Any]:
    manifest, case = _load_case(state)
    source_path = manifest.resolve(case.canonical_input)
    output_dir = Path(state["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    refs: list[dict[str, Any]] = []
    for capability_id in state["candidate_ids"]:
        adapter = get_table_adapter(capability_id)
        capability = adapter.manifest()
        adapter_output = adapter.run(case, source_path)
        candidate_dir = output_dir / capability_id.replace(".", "_")
        candidate_dir.mkdir(parents=True, exist_ok=True)
        result_path = candidate_dir / "result.json"
        result_path.write_text(
            json.dumps(adapter_output.payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifact = ArtifactRef(
            artifact_id=f"{case.case_id}_{capability_id}",
            kind="benchmark_result",
            media_type="application/json",
            sha256=_sha256(result_path),
            size_bytes=result_path.stat().st_size,
        )
        tool_result = ToolResult(
            call_id=f"{case.case_id}_{capability_id}",
            capability_id=capability.capability_id,
            capability_version=capability.version,
            status=ToolStatus.SUCCEEDED,
            output_artifacts=(artifact,),
            ledger=ExecutionLedger(
                input_records=None,
                output_records=len(adapter_output.payload["records"]),
                input_bytes=source_path.stat().st_size,
                output_bytes=result_path.stat().st_size,
            ),
            lineage=(
                LineageEvent(
                    event="benchmark_filter_project",
                    input_artifact_ids=(f"fixture_{case.case_id}",),
                    output_artifact_ids=(artifact.artifact_id,),
                    details={"source_sha256": _sha256(source_path)},
                ),
            ),
            facts={
                "table_count": adapter_output.payload["table_count"],
                "rows": len(adapter_output.payload["records"]),
                "visible_columns": adapter_output.payload["visible_columns"],
            },
            tool_config_summary={"selection": case.selection, "projection": case.projection},
            resource_usage=ResourceUsage(duration_ms=adapter_output.duration_ms),
        )
        tool_result_path = candidate_dir / "tool-result.json"
        tool_result_path.write_text(
            tool_result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        manifest_path = candidate_dir / "capability-manifest.json"
        manifest_path.write_text(
            capability.model_dump_json(indent=2),
            encoding="utf-8",
        )
        refs.append(
            {
                "capability_id": capability_id,
                "result_path": str(result_path),
                "tool_result_path": str(tool_result_path),
                "manifest_path": str(manifest_path),
                "duration_ms": adapter_output.duration_ms,
            }
        )
    return {"candidate_refs": refs}


def _verify(state: BenchmarkState) -> dict[str, Any]:
    manifest, case = _load_case(state)
    expected_records = json.loads(
        manifest.resolve(case.expected.records_path).read_text(encoding="utf-8")
    )
    logical_plan_hash = hashlib.sha256(
        json.dumps(
            {
                "case_id": case.case_id,
                "selection": case.selection,
                "projection": case.projection,
                "expected": case.expected.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    refs: list[dict[str, Any]] = []
    for candidate in state["candidate_refs"]:
        result_path = Path(candidate["result_path"])
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        report = score_table_result(
            case,
            {**payload, "_expected_records": expected_records},
            logical_plan_hash=logical_plan_hash,
        )
        verification_path = result_path.parent / "verification.json"
        verification_path.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        passed = sum(1 for check in report.checks if check.passed)
        refs.append(
            {
                **candidate,
                "verification_path": str(verification_path),
                "verification_status": report.status.value,
                "quality_score": round(passed / len(report.checks) * 100, 4),
            }
        )
    return {"verified_refs": refs}


def _summarize(state: BenchmarkState) -> dict[str, Any]:
    candidates = sorted(
        state["verified_refs"],
        key=lambda item: (
            item["verification_status"] == "pass",
            item["quality_score"],
            -item["duration_ms"],
        ),
        reverse=True,
    )
    passing = [item for item in candidates if item["verification_status"] == "pass"]
    winner = passing[0]["capability_id"] if passing else None
    report = {
        "case_id": state["case_id"],
        "status": "pass" if winner else "fail",
        "winner": winner,
        "candidates": candidates,
    }
    report_path = Path(state["output_dir"]).resolve() / "benchmark-summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"report": report}


def build_table_benchmark_graph():
    graph = StateGraph(BenchmarkState)
    graph.add_node("prepare", _prepare)
    graph.add_node("execute", _execute)
    graph.add_node("verify", _verify)
    graph.add_node("summarize", _summarize)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "execute")
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


def run_table_benchmark(
    *,
    manifest_path: Path,
    case_id: str,
    candidate_ids: tuple[str, ...],
    output_dir: Path,
) -> dict[str, Any]:
    result = build_table_benchmark_graph().invoke(
        {
            "manifest_path": str(manifest_path.resolve()),
            "case_id": case_id,
            "candidate_ids": candidate_ids,
            "output_dir": str(output_dir.resolve()),
        }
    )
    return dict(result["report"])
