# -*- coding: utf-8 -*-
"""Phase 4B 私有样本到脱敏 fixture 的边界测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.prepare_semantic_fixture import prepare_fixture


def test_prepare_fixture_replaces_literals_and_writes_manifest(tmp_path: Path):
    fixture_root = tmp_path / "semantic_harness"
    source = fixture_root / "private" / "workload.csv"
    output = fixture_root / "public" / "workload-filter.csv"
    source.parent.mkdir(parents=True)
    source.write_text(
        "姓名,项目,核销工作量天数\n真实姓名,真实项目,1.5\n",
        encoding="utf-8",
    )

    manifest_path = prepare_fixture(
        source=source,
        output=output,
        replacements={"真实姓名": "示例人员甲", "真实项目": "示例项目A"},
        confirmed=True,
        fixture_root=fixture_root,
    )

    text = output.read_text(encoding="utf-8")
    assert "真实姓名" not in text
    assert "真实项目" not in text
    assert "示例人员甲" in text
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["replacement_count"] == 2
    assert manifest["manual_review_still_required_before_commit"] is True


def test_prepare_fixture_rejects_paths_outside_private_and_public(tmp_path: Path):
    fixture_root = tmp_path / "semantic_harness"
    outside = tmp_path / "outside.csv"
    outside.write_text("姓名\n真实姓名\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source 必须位于"):
        prepare_fixture(
            source=outside,
            output=fixture_root / "public" / "result.csv",
            replacements={"真实姓名": "示例人员甲"},
            confirmed=True,
            fixture_root=fixture_root,
        )


def test_prepare_fixture_requires_confirmation_and_rejects_binary(tmp_path: Path):
    fixture_root = tmp_path / "semantic_harness"
    source = fixture_root / "private" / "document.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not-a-real-docx")

    with pytest.raises(ValueError, match="人工复核"):
        prepare_fixture(
            source=source,
            output=fixture_root / "public" / "document.docx",
            replacements={"真实": "示例"},
            confirmed=False,
            fixture_root=fixture_root,
        )

    with pytest.raises(ValueError, match="暂不支持"):
        prepare_fixture(
            source=source,
            output=fixture_root / "public" / "document.docx",
            replacements={"真实": "示例"},
            confirmed=True,
            fixture_root=fixture_root,
        )
