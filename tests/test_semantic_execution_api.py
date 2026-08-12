# -*- coding: utf-8 -*-
"""Phase 4B 批次 3：PhysicalPlan 与执行记录 API。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
from src.api.auth import get_current_user
from src.api.routes import (
    semantic_bindings,
    semantic_executions,
    semantic_plans,
)
from src.config.settings import settings
from src.services.upload_store import UploadStore
from tests.test_semantic_plan_api import ApiFakeGenerator


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "runs.db"))
    monkeypatch.setattr(
        settings, "data_prep_upload_root", str(tmp_path / "uploads")
    )
    monkeypatch.setattr(
        settings,
        "semantic_execution_root",
        str(tmp_path / "executions"),
    )
    auth_mod._store = None
    generator = ApiFakeGenerator()
    monkeypatch.setattr(
        semantic_plans, "_build_generator", lambda **_: generator
    )
    app = FastAPI()
    app.include_router(semantic_plans.router)
    app.include_router(semantic_bindings.router)
    app.include_router(semantic_executions.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a"
    }
    return TestClient(app)


def test_prepare_execute_verify_and_hide_server_paths(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    upload_store = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    )
    rows = ["姓名,核销工作量天数,工作量费用"]
    rows.extend(
        f"谢超群,{index / 2:.1f},{index * 100:.2f}"
        for index in range(1, 12)
    )
    upload = upload_store.save_bytes(
        "user-a",
        "workload.csv",
        ("\n".join(rows) + "\n").encode("utf-8"),
        media_type="text/csv",
    )
    compiled = client.post(
        "/api/semantic-plans/compile",
        json={
            "task_id": "task-execution-api",
            "objective_text": "只提取谢超群的数据，只保留核销工作量天数和工作量费用",
            "artifact_ids": [upload.upload_id],
            "accepted_formats": ["csv"],
            "provider": "local",
            "model": "api-fixture-model",
        },
    )
    assert compiled.status_code == 200, compiled.text
    plan_id = compiled.json()["plan_id"]
    bound = client.post(
        f"/api/semantic-plans/{plan_id}/inspect-bind",
        json={"use_local_semantics": False},
    )
    assert bound.status_code == 200, bound.text
    prepared = client.post(
        f"/api/semantic-plans/{plan_id}/physical-plans",
        json={"runtime_profile": "windows_local"},
    )
    assert prepared.status_code == 200, prepared.text
    physical_id = prepared.json()["physical_plan_id"]

    executed = client.post(
        f"/api/semantic-plans/{plan_id}/physical-plans/"
        f"{physical_id}/execute"
    )

    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "pass"
    assert body["tool_result"]["ledger"]["output_records"] == 11
    assert body["verification"]["status"] == "pass"
    assert "artifact_paths" not in body
    assert str(tmp_path) not in executed.text
    fetched = client.get(
        f"/api/semantic-plans/{plan_id}/execution-runs/{body['run_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json() == body
