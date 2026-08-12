# -*- coding: utf-8 -*-
"""ZIP 文档边界接入统一证据元素的测试。"""
from __future__ import annotations

import io
import zipfile

from docx import Document

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
