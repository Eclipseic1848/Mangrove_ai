# -*- coding: utf-8 -*-
"""沙箱候选清单 CLI 的公共行为测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agentic_runtime.candidate_verifier import load_result_items
from src.agentic_runtime.candidate_manifest_tool import (
    ManifestToolError,
    add_evidence,
    add_qualified_omission,
    add_result_item,
    initialize_manifest,
    mark_result_search_complete,
    remove_evidence,
)


def test_manifest_tool_builds_strict_json_incrementally(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "result.txt").write_text("商务条款", encoding="utf-8")

    initialize_manifest(
        output_dir=output,
        filename="result.txt",
        output_format="txt",
        description="商务条款汇总",
    )
    add_evidence(
        output_dir=output,
        filename="result.txt",
        source="source.docx",
        locator="paragraph:验收",
        quote="验收标准",
    )

    payload = json.loads(
        (output / "candidate-manifest.json").read_text(encoding="utf-8")
    )
    assert payload["version"] == 2
    assert payload["artifacts"][0]["filename"] == "result.txt"
    assert payload["artifacts"][0]["evidence"] == [
        {
            "source": "source.docx",
            "locator": "paragraph:验收",
            "quote": "验收标准",
        }
    ]
    assert load_result_items(output / "candidate-manifest.json") == ()

    remove_evidence(
        output_dir=output,
        filename="result.txt",
        locator="paragraph:验收",
    )
    payload = json.loads(
        (output / "candidate-manifest.json").read_text(encoding="utf-8")
    )
    assert payload["artifacts"][0]["evidence"] == []


def test_manifest_tool_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ManifestToolError):
        initialize_manifest(
            output_dir=tmp_path,
            filename="../result.txt",
            output_format="txt",
            description="非法候选",
        )


def test_manifest_tool_registers_each_result_with_its_own_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "companies.json").write_text("[]", encoding="utf-8")
    initialize_manifest(
        output_dir=output,
        filename="companies.json",
        output_format="json",
        description="公司名单",
    )

    add_result_item(
        output_dir=output,
        result_id="company-1",
        label="甲公司",
        source="directory.html",
        locator="company:甲公司",
        quote="甲公司",
    )
    add_evidence(
        output_dir=output,
        filename="companies.json",
        source="directory.html",
        locator="company:甲公司",
        quote="甲公司",
    )

    payload = json.loads(
        (output / "candidate-manifest.json").read_text(encoding="utf-8")
    )
    assert payload["result_items"] == [
        {
            "result_id": "company-1",
            "label": "甲公司",
            "evidence": [
                {
                    "source": "directory.html",
                    "locator": "company:甲公司",
                    "quote": "甲公司",
                }
            ],
        }
    ]
    result_items = load_result_items(output / "candidate-manifest.json")
    assert result_items[0].result_id == "company-1"
    assert result_items[0].evidence_refs[0].startswith("evidence_")


def test_manifest_records_qualified_omission_and_search_completion(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "companies.json").write_text("[]", encoding="utf-8")
    initialize_manifest(
        output_dir=output,
        filename="companies.json",
        output_format="json",
        description="公司名单",
    )
    add_evidence(
        output_dir=output,
        filename="companies.json",
        source="directory.html",
        locator="company:乙公司",
        quote="乙公司",
    )
    add_qualified_omission(
        output_dir=output,
        result_id="company-2",
        label="乙公司",
        source="directory.html",
        locator="company:乙公司",
        quote="乙公司",
    )
    mark_result_search_complete(output_dir=output)

    payload = json.loads(
        (output / "candidate-manifest.json").read_text(encoding="utf-8")
    )
    assert payload["qualified_omissions"][0]["result_id"] == "company-2"
    assert payload["result_search_complete"] is True

    add_result_item(
        output_dir=output,
        result_id="company-2",
        label="乙公司",
        source="directory.html",
        locator="company:乙公司",
        quote="乙公司",
    )
    repaired = json.loads(
        (output / "candidate-manifest.json").read_text(encoding="utf-8")
    )
    assert repaired["qualified_omissions"] == []
    assert repaired["result_items"][0]["result_id"] == "company-2"


def test_result_item_cannot_reference_unverified_artifact_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "companies.json").write_text("[]", encoding="utf-8")
    initialize_manifest(
        output_dir=output,
        filename="companies.json",
        output_format="json",
        description="公司名单",
    )
    add_evidence(
        output_dir=output,
        filename="companies.json",
        source="directory.html",
        locator="company:甲公司",
        quote="甲公司",
    )
    add_result_item(
        output_dir=output,
        result_id="company-2",
        label="乙公司",
        source="directory.html",
        locator="company:乙公司",
        quote="乙公司",
    )

    with pytest.raises(ValueError, match="未经过候选文件验证"):
        load_result_items(output / "candidate-manifest.json")
