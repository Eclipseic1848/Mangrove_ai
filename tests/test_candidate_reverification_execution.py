# -*- coding: utf-8 -*-
"""CV-05 完整重验的真实 Verifier 与只读 Candidate 回归。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openpyxl import Workbook
import pytest
from reportlab.pdfgen import canvas

from src.agentic_runtime.candidate_qa import inspect_candidates
from src.agentic_runtime.candidate_verifier import CandidateVerifier
from src.agentic_runtime.models import (
    PermissionProfile,
    PiRuntimeRequest,
    SemanticDecision,
    SourceInput,
    TableOutputContract,
    VerificationStatus,
)


class _PassingLocalJudge:
    async def judge(self, **_kwargs) -> SemanticDecision:
        return SemanticDecision(
            passed=True,
            contains_unrequested_content=False,
            reason="候选满足冻结目标",
        )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output_format", "filename"),
    (("csv", "result.csv"), ("json", "result.json"), ("xlsx", "result.xlsx")),
)
async def test_complete_local_reverification_reopens_all_gates_without_writing_candidate(
    tmp_path: Path,
    output_format: str,
    filename: str,
) -> None:
    source = tmp_path / "source.pdf"
    document = canvas.Canvas(str(source))
    document.drawString(72, 760, "Service Fee Details - Alice - 100")
    document.save()
    output = tmp_path / "output"
    output.mkdir()
    candidate = output / filename
    if output_format == "csv":
        candidate.write_text("name,fee\nAlice,100\n", encoding="utf-8-sig")
    elif output_format == "json":
        candidate.write_text(
            json.dumps([{"name": "Alice", "fee": 100}]),
            encoding="utf-8",
        )
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(("name", "fee"))
        sheet.append(("Alice", 100))
        workbook.save(candidate)
    (output / "candidate-manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "artifacts": [
                    {
                        "filename": filename,
                        "format": output_format,
                        "description": "Only the requested fee table",
                        "evidence": [
                            {
                                "source": "source.pdf",
                                "locator": "page:1",
                                "quote": "Service Fee Details - Alice - 100",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    request = PiRuntimeRequest(
        user_id="owner-a",
        task_id="task-a",
        revision=1,
        objective_text="Extract one fee table and no other content.",
        requested_output_formats=(output_format,),
        table_output_contracts=(
            TableOutputContract(
                format=output_format,
                exact_columns=("name", "fee"),
                json_shape="records" if output_format == "json" else None,
            ),
        ),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.pdf",
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                media_type="application/pdf",
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model="local-model",
        base_url="http://127.0.0.1:18080/v1",
        api_key="local-runtime",
    )
    before = _tree_hashes(output)

    report = await CandidateVerifier(
        semantic_judge=_PassingLocalJudge()
    ).verify(
        request=request,
        candidates=inspect_candidates(output, (output_format,)),
        manifest_path=output / "candidate-manifest.json",
    )

    assert report.status is VerificationStatus.PASSED
    assert {check.code for check in report.checks if check.passed} >= {
        "artifact_set",
        "artifact_count",
        "table_output_contract",
        "source_grounding",
        "semantic_goal",
    }
    assert _tree_hashes(output) == before
