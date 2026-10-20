# -*- coding: utf-8 -*-
"""ZIP 文档边界接入统一证据元素的测试。"""
from __future__ import annotations

import io
import zipfile

from docx import Document
import pytest

from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.parsers.archive import ArchiveParser
from src.parsers.office import OfficeParser
from src.parsers.registry import ParserRegistry
from src.services.document_ingest import ingest_document_artifact


def _docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("ZIP 内合同编号：HT-ZIP-001")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_zip_document_members_keep_parent_boundary_and_produce_elements(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("contracts/contract.docx", _docx_bytes())
        archive.writestr("notes/readme.txt", "不属于 Phase 4A 文档类型")
    store = ArtifactStore(str(tmp_path))
    raw = store.write_raw(
        "zip-task",
        "upload:u1",
        buffer.getvalue(),
        uri="bundle.zip",
        media_type="application/zip",
        ext="zip",
    )
    registry = ParserRegistry()
    registry.register(ArchiveParser())
    registry.register(OfficeParser())

    result = ingest_document_artifact(
        raw,
        buffer.getvalue(),
        registry=registry,
        store=store,
    )

    assert len(result.raw_artifacts) == 3
    children = [
        artifact for artifact in result.raw_artifacts
        if artifact.parent_artifact_id == raw.artifact_id
    ]
    assert len(children) == 2
    assert result.artifact_ids == [children[0].artifact_id]
    assert result.elements[0].artifact_id == children[0].artifact_id
    assert result.elements[0].text == "ZIP 内合同编号：HT-ZIP-001"
    assert result.elements[0].metadata["location"]["kind"] == "docx_paragraph"
    assert any(
        reject["reason"] == "zip_member_unsupported"
        for reject in result.rejects
    )


@pytest.mark.parametrize("exceeds_limit", [True, False], ids=["over-limit", "at-limit"])
def test_nested_zip_shares_expansion_budget_and_keeps_originals(tmp_path, monkeypatch, exceeds_limit):
    document = _docx_bytes()
    inner_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("contract.docx", document)
    inner = inner_buffer.getvalue()
    note = b"unsupported member still occupies the expansion budget"
    outer_buffer = io.BytesIO()
    with zipfile.ZipFile(outer_buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("inner.zip", inner)
        archive.writestr("notes.txt", note)
    outer = outer_buffer.getvalue()
    # 容器和不支持的成员都占额度，等限时不能重复扣内层 ZIP 的字节。
    monkeypatch.setattr(
        settings, "data_prep_max_task_bytes", len(inner) + len(note) + len(document) - int(exceeds_limit),
    )
    store = ArtifactStore(str(tmp_path))
    raw = store.write_raw(
        "nested-task", "upload:nested", outer,
        uri="outer.zip", media_type="application/zip", ext="zip",
    )
    registry = ParserRegistry()
    registry.register(ArchiveParser())
    registry.register(OfficeParser())

    result = ingest_document_artifact(raw, outer, registry=registry, store=store)

    assert sum(reject["reason"] == "zip_total_exceeded" for reject in result.rejects) == int(exceeds_limit)
    if exceeds_limit:
        assert result.artifact_ids == []
        assert result.elements == []
    else:
        inner_artifact = next(item for item in result.raw_artifacts if item.uri.endswith("/inner.zip"))
        document_artifact = next(item for item in result.raw_artifacts if item.uri.endswith("/contract.docx"))
        assert document_artifact.parent_artifact_id == inner_artifact.artifact_id
        assert result.artifact_ids == [document_artifact.artifact_id]
        assert result.elements[0].text == "ZIP 内合同编号：HT-ZIP-001"
    expected_count = 3 if exceeds_limit else 4
    assert len(result.raw_artifacts) == expected_count
    children = [item for item in result.raw_artifacts if item.parent_artifact_id == raw.artifact_id]
    assert len(children) == 2
    assert sorted(store.read_raw_bytes(item.task_id, item.storage_path) for item in children) == sorted([inner, note])
    assert store.read_raw_bytes(raw.task_id, raw.storage_path) == outer
    assert len(list(store.task_dir(raw.task_id).joinpath("raw").iterdir())) == expected_count
