# -*- coding: utf-8 -*-
"""Phase 4B 批次 4 文档 PhysicalPlan 与用户隔离测试 API。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

import src.api.auth as auth_mod
from src.api.auth import get_current_user
from src.api.routes import (
    semantic_bindings,
    semantic_documents,
    semantic_plans,
)
from src.config.settings import settings
from src.semantic_harness.compiler_models import CompileRequest, PlanSemanticsDraft
from src.semantic_harness.models import (
    ContentPolicy,
    DeliveryFormat,
    DeliverySpec,
    TaskFamily,
)
from src.services.upload_store import UploadStore
from tests.database_migration_helpers import migrated_webui_database


class DocumentFakeGenerator:
    provider = "local"
    model = "document-fixture-model"
    prompt_version = "stp-v1-test"
    prompt_sha256 = "3" * 64

    async def generate(
        self,
        request: CompileRequest,
        *,
        diagnostics,
        attempt: int,
    ) -> PlanSemanticsDraft:
        del request, diagnostics, attempt
        return PlanSemanticsDraft(
            task_family=TaskFamily.EXTRACT,
            normalized_objective="逐字摘录付款条款",
            section_patterns=("付款条款",),
            content_policy=ContentPolicy.VERBATIM,
            delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
        )


def _client(tmp_path, monkeypatch, *, user_id="user-a"):
    database = migrated_webui_database(tmp_path / "runs.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(
        settings, "data_prep_upload_root", str(tmp_path / "uploads")
    )
    monkeypatch.setattr(
        settings,
        "semantic_execution_root",
        str(tmp_path / "executions"),
    )
    auth_mod._store = None
    monkeypatch.setattr(
        semantic_plans,
        "_build_generator",
        lambda **_: DocumentFakeGenerator(),
    )
    app = FastAPI()
    app.include_router(semantic_plans.router)
    app.include_router(semantic_bindings.router)
    app.include_router(semantic_documents.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    return TestClient(app)


def test_prepare_execute_verify_document_and_hide_paths(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    upload_store = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    )
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "semantic_harness"
        / "public"
        / "batch0"
        / "documents"
        / "contract.docx"
    )
    upload = upload_store.save_bytes(
        "user-a",
        "contract.docx",
        fixture.read_bytes(),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    compiled = client.post(
        "/api/semantic-plans/compile",
        json={
            "task_id": "task-document-api",
            "objective_text": "逐字摘录付款条款，不要总结",
            "artifact_ids": [upload.upload_id],
            "accepted_formats": ["docx"],
            "provider": "local",
            "model": "document-fixture-model",
        },
    )
    assert compiled.status_code == 200, compiled.text
    plan_id = compiled.json()["plan_id"]
    bound = client.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["status"] == "ready"
    prepared = client.post(
        f"/api/semantic-plans/{plan_id}/document-plans",
        json={"runtime_profile": "windows_local"},
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["status"] == "ready"
    physical_id = prepared.json()["physical_plan_id"]

    executed = client.post(
        f"/api/semantic-plans/{plan_id}/document-plans/"
        f"{physical_id}/execute"
    )

    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "pass"
    assert body["verification"]["status"] == "pass"
    assert body["result"]["action"] == "verbatim"
    assert "百分之六十" in body["result"]["passages"][0]["text"]
    assert body["result"]["derived_content"] == []
    assert "artifact_paths" not in body
    assert str(tmp_path) not in executed.text
    fetched = client.get(
        f"/api/semantic-plans/{plan_id}/document-runs/{body['run_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_other_user_cannot_read_document_plan_or_run(
    tmp_path,
    monkeypatch,
) -> None:
    client_a = _client(tmp_path, monkeypatch, user_id="user-a")
    assert client_a.get(
        "/api/semantic-plans/not-owned/document-plans"
    ).status_code == 404

    client_b = _client(tmp_path, monkeypatch, user_id="user-b")
    assert client_b.get(
        "/api/semantic-plans/not-owned/document-runs/not-owned"
    ).status_code == 404
