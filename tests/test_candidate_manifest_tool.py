# -*- coding: utf-8 -*-
"""沙箱候选清单 CLI 的公共行为测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agentic_runtime.candidate_manifest_tool import (
    ManifestToolError,
    add_evidence,
    initialize_manifest,
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
    assert payload["artifacts"][0]["filename"] == "result.txt"
    assert payload["artifacts"][0]["evidence"] == [
        {
            "source": "source.docx",
            "locator": "paragraph:验收",
            "quote": "验收标准",
        }
    ]

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
