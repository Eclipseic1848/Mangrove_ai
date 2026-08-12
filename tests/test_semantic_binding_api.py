# -*- coding: utf-8 -*-
"""Phase 4B 批次 2：绑定 API 的不可变 revision 与用户隔离。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
from src.api.auth import get_current_user
from src.api.routes import semantic_bindings, semantic_plans
from src.config.settings import settings
from src.services.upload_store import UploadStore
from tests.test_semantic_plan_api import ApiFakeGenerator


def _make_client(tmp_path, monkeypatch, *, user_id: str):
    monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "binding.db"))
    monkeypatch.setattr(
        settings,
        "data_prep_upload_root",
        str(tmp_path / "uploads"),
    )
    auth_mod._store = None
    generator = ApiFakeGenerator()
    monkeypatch.setattr(
        semantic_plans,
        "_build_generator",
        lambda **_: generator,
    )
    app = FastAPI()
    app.include_router(semantic_plans.router)
    app.include_router(semantic_bindings.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    return TestClient(app), generator


def _upload(tmp_path, user_id: str, content: str):
    store = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    )
    return store.save_bytes(
        user_id,
        "workload.csv",
        content.encode("utf-8"),
        media_type="text/csv",
    )


def _compile(client: TestClient, upload_id: str) -> str:
    response = client.post(
        "/api/semantic-plans/compile",
        json={
            "task_id": "task-binding-api",
            "objective_text": "只提取谢超群的数据，只保留核销工作量天数和工作量费用",
            "artifact_ids": [upload_id],
            "accepted_formats": ["csv"],
            "provider": "local",
            "model": "api-fixture-model",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["plan_id"]


def test_inspect_bind_persists_ready_revision_and_forbids_overwrite(
    tmp_path,
    monkeypatch,
):
    client, _ = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload = _upload(
        tmp_path,
        "user-a",
        "姓名,核销工作量天数,工作量费用\n谢超群,0.5,1200\n",
    )
    plan_id = _compile(client, upload.upload_id)

    created = client.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "ready"
    assert body["binding_revision"] == 1
    assert body["bound_plan_hash"]
    assert body["reports"][0]["artifact_id"] == upload.upload_id
    assert client.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    ).status_code == 409


def test_ambiguous_binding_is_resolved_by_appending_revision(
    tmp_path,
    monkeypatch,
):
    client, _ = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload = _upload(
        tmp_path,
        "user-a",
        "姓名,核销工作量天数,核销工作量天数,工作量费用\n"
        "谢超群,0.5,0.75,1200\n",
    )
    plan_id = _compile(client, upload.upload_id)
    first = client.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "needs_user"
    result = first.json()["result"]
    question = result["clarification"]
    semantic_ref = question["ambiguity_id"].split("|", 1)[0]
    candidate = next(
        item
        for item in result["candidates"]
        if item["semantic_ref"] == semantic_ref
    )
    revised = client.post(
        f"/api/semantic-plans/{plan_id}/bound-revisions",
        json={
            "ambiguity_id": question["ambiguity_id"],
            "physical_ref": candidate["physical_ref"],
            "use_local_semantics": False,
        },
    )

    assert revised.status_code == 200, revised.text
    assert revised.json()["binding_revision"] == 2
    assert revised.json()["status"] == "ready"
    assert (
        revised.json()["reports"][0]["inspection_id"]
        == first.json()["reports"][0]["inspection_id"]
    )
    revisions = client.get(
        f"/api/semantic-plans/{plan_id}/bound-revisions"
    ).json()
    assert [item["binding_revision"] for item in revisions] == [2, 1]


def test_resolution_rejects_invented_physical_ref(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload = _upload(
        tmp_path,
        "user-a",
        "姓名,核销工作量天数,核销工作量天数,工作量费用\n"
        "谢超群,0.5,0.75,1200\n",
    )
    plan_id = _compile(client, upload.upload_id)
    first = client.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    ).json()

    response = client.post(
        f"/api/semantic-plans/{plan_id}/bound-revisions",
        json={
            "ambiguity_id": first["result"]["clarification"]["ambiguity_id"],
            "physical_ref": "artifact://invented/table/0/column/0",
            "use_local_semantics": False,
        },
    )

    assert response.status_code == 409
    revisions = client.get(
        f"/api/semantic-plans/{plan_id}/bound-revisions"
    ).json()
    assert [item["binding_revision"] for item in revisions] == [1]


def test_other_user_cannot_read_plan_binding_or_uploaded_source(
    tmp_path,
    monkeypatch,
):
    client_a, _ = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload = _upload(
        tmp_path,
        "user-a",
        "姓名,核销工作量天数,工作量费用\n谢超群,0.5,1200\n",
    )
    plan_id = _compile(client_a, upload.upload_id)
    assert client_a.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    ).status_code == 200

    client_b, _ = _make_client(tmp_path, monkeypatch, user_id="user-b")
    assert client_b.get(
        f"/api/semantic-plans/{plan_id}/bound-revisions"
    ).status_code == 404
    assert client_b.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    ).status_code == 404
