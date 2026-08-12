# -*- coding: utf-8 -*-
"""Phase 4B 批次 5：有界 Harness 灰度 API 与恢复门禁。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

import src.api.auth as auth_mod
from src.api.auth import get_current_user
from src.api.routes import (
    semantic_bindings,
    semantic_deliveries,
    semantic_harness,
    semantic_plans,
)
from src.config.settings import settings
from src.semantic_harness.harness_adapters import (
    get_harness_adapter,
    register_harness_adapter_for_test,
)
from src.services.upload_store import UploadStore
from tests.test_semantic_document_api import DocumentFakeGenerator
from tests.test_semantic_plan_api import ApiFakeGenerator


def _client(
    tmp_path,
    monkeypatch,
    *,
    user_id: str = "user-a",
    generator_factory=ApiFakeGenerator,
):
    monkeypatch.setattr(
        settings, "webui_db_path", str(tmp_path / "harness.db")
    )
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
        lambda **_: generator_factory(),
    )
    app = FastAPI()
    app.include_router(semantic_plans.router)
    app.include_router(semantic_bindings.router)
    app.include_router(semantic_harness.router)
    app.include_router(semantic_deliveries.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user_id
    }
    return TestClient(app)


def _prepare_table_plan(
    client: TestClient,
    tmp_path,
    *,
    provider: str = "local",
) -> str:
    uploads = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    )
    rows = ["姓名,核销工作量天数,工作量费用"]
    rows.extend(
        f"谢超群,{index / 2:.1f},{index * 100:.2f}"
        for index in range(1, 12)
    )
    upload = uploads.save_bytes(
        "user-a",
        "workload.csv",
        ("\n".join(rows) + "\n").encode("utf-8"),
        media_type="text/csv",
    )
    compiled = client.post(
        "/api/semantic-plans/compile",
        json={
            "task_id": "task-harness",
            "objective_text": (
                "只提取谢超群的数据，只保留核销工作量天数和工作量费用"
            ),
            "artifact_ids": [upload.upload_id],
            "accepted_formats": ["csv"],
            "provider": provider,
            "model": "api-fixture-model",
            "external_api_confirmed": provider != "local",
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
    return plan_id


def _prepare_document_plan(client: TestClient, tmp_path) -> str:
    uploads = UploadStore(
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
    upload = uploads.save_bytes(
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
            "task_id": "task-document-harness",
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
    return plan_id


class _FaultAdapter:
    capability_id = "table.duckdb"

    def __init__(self, *, failures: int, exception: Exception) -> None:
        self.delegate = get_harness_adapter(self.capability_id)
        self.failures = failures
        self.exception = exception
        self.calls = 0

    def compile_plan(self, *args, **kwargs):
        return self.delegate.compile_plan(*args, **kwargs)

    async def execute(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exception
        return await self.delegate.execute(*args, **kwargs)


def test_harness_executes_table_and_exposes_only_audited_state(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    plan_id = _prepare_table_plan(client, tmp_path)

    created = client.post(
        "/api/semantic-harness/runs",
        json={"plan_id": plan_id, "runtime_profile": "windows_local"},
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["current_node"] == "deliver"
    assert body["eligible_for_delivery"] is True
    assert body["final_verification"]["status"] == "pass"
    assert body["policy"]["max_total_repair_rounds"] == 5
    assert str(tmp_path) not in created.text
    run_id = body["run_id"]

    attempts = client.get(
        f"/api/semantic-harness/runs/{run_id}/attempts"
    )
    assert attempts.status_code == 200
    assert len(attempts.json()) == 1
    assert attempts.json()[0]["status"] == "succeeded"
    assert "artifact_paths" not in attempts.json()[0]
    private_attempt = auth_mod.get_store().get_semantic_harness_attempt_by_key(
        "user-a", attempts.json()[0]["idempotency_key"]
    )
    assert private_attempt is not None
    assert Path(private_attempt["artifact_paths"]["result"]).is_file()
    events = client.get(
        f"/api/semantic-harness/runs/{run_id}/events"
    )
    assert events.status_code == 200
    assert [item["node"] for item in events.json()] == [
        "interpret",
        "inspect",
        "bind",
        "plan",
        "execute",
        "verify",
        "deliver",
    ]
    delivery_event = events.json()[-1]
    assert delivery_event["event_type"] == "delivery_published"
    assert delivery_event["details"]["formal_download_created"] is True
    delivery_id = delivery_event["details"]["delivery_id"]
    delivery = client.get(f"/api/semantic-deliveries/{delivery_id}")
    assert delivery.status_code == 200
    assert delivery.json()["status"] == "succeeded"
    assert "user-a" not in delivery.text
    assert str(tmp_path) not in delivery.text
    output = delivery.json()["outputs"][0]
    downloaded = client.get(output["download_url"])
    assert downloaded.status_code == 200
    assert len(downloaded.content) == output["size_bytes"]
    other_client = _client(
        tmp_path,
        monkeypatch,
        user_id="user-b",
    )
    assert other_client.get(output["download_url"]).status_code == 404
    stored_output = auth_mod.get_store().get_semantic_delivery_output(
        "user-a", output["output_id"]
    )
    assert stored_output is not None
    Path(stored_output["file_path"]).write_bytes(b"tampered")
    assert client.get(output["download_url"]).status_code == 409
    assert client.post(
        f"/api/semantic-harness/runs/{run_id}/resume",
        json={
            "question_id": "stale",
            "resume_token": "0" * 32,
            "answer": "retry",
        },
    ).status_code == 409


def test_external_boundary_interrupts_and_resumes_from_sqlite_checkpoint(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    plan_id = _prepare_table_plan(
        client,
        tmp_path,
        provider="deepseek",
    )
    created = client.post(
        "/api/semantic-harness/runs",
        json={"plan_id": plan_id},
    )
    assert created.status_code == 200, created.text
    waiting = created.json()
    assert waiting["status"] == "needs_user"
    assert waiting["current_node"] == "needs_user"
    question = waiting["question"]
    assert question["external_service"]
    assert "数据将离开" in question["risk"]
    assert question["allow_free_text"] is False
    assert client.post(
        f"/api/semantic-harness/runs/{waiting['run_id']}/resume",
        json={
            "question_id": question["question_id"],
            "resume_token": question["resume_token"],
            "answer": "随便继续",
        },
    ).status_code == 409

    resumed = client.post(
        f"/api/semantic-harness/runs/{waiting['run_id']}/resume",
        json={
            "question_id": question["question_id"],
            "resume_token": question["resume_token"],
            "answer": "confirm_external",
        },
    )

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "succeeded"
    assert resumed.json()["eligible_for_delivery"] is True
    assert client.post(
        f"/api/semantic-harness/runs/{waiting['run_id']}/resume",
        json={
            "question_id": question["question_id"],
            "resume_token": question["resume_token"],
            "answer": "confirm_external",
        },
    ).status_code == 409


def test_transient_failure_is_retried_once_with_append_only_audit(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    plan_id = _prepare_table_plan(client, tmp_path)
    adapter = _FaultAdapter(
        failures=1,
        exception=TimeoutError("局域网能力暂时超时"),
    )
    previous = register_harness_adapter_for_test(
        "table.duckdb", adapter
    )
    try:
        created = client.post(
            "/api/semantic-harness/runs",
            json={"plan_id": plan_id},
        )
    finally:
        register_harness_adapter_for_test("table.duckdb", previous)

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["repair_rounds"] == 1
    assert body["transient_retries"] == 1
    assert adapter.calls == 2
    attempts = client.get(
        f"/api/semantic-harness/runs/{body['run_id']}/attempts"
    ).json()
    assert [item["status"] for item in attempts] == [
        "failed",
        "approved",
        "succeeded",
    ]


def test_same_invalid_failure_stops_after_two_fingerprints(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    plan_id = _prepare_table_plan(client, tmp_path)
    adapter = _FaultAdapter(
        failures=20,
        exception=ValueError("固定的物理计划错误"),
    )
    previous = register_harness_adapter_for_test(
        "table.duckdb", adapter
    )
    try:
        created = client.post(
            "/api/semantic-harness/runs",
            json={"plan_id": plan_id},
        )
    finally:
        register_harness_adapter_for_test("table.duckdb", previous)

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "failed"
    assert body["eligible_for_delivery"] is False
    assert body["same_failure_count"] == 2
    assert adapter.calls == 2
    attempts = client.get(
        f"/api/semantic-harness/runs/{body['run_id']}/attempts"
    ).json()
    repair_actions = [
        item["repair_decision"]["proposal"]["action"]
        for item in attempts
        if item["repair_decision"] is not None
    ]
    assert repair_actions == ["recompile_physical_plan", "stop"]


def test_document_capability_runs_through_same_harness(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        generator_factory=DocumentFakeGenerator,
    )
    plan_id = _prepare_document_plan(client, tmp_path)
    created = client.post(
        "/api/semantic-harness/runs",
        json={"plan_id": plan_id},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["capability_id"] == "document.evidence"
    assert body["final_verification"]["status"] == "pass"
    assert body["eligible_for_delivery"] is True


def test_client_cannot_submit_sql_paths_or_loop_policy(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/semantic-harness/runs",
        json={
            "plan_id": "any",
            "sql": "SELECT * FROM read_parquet('C:/secret.parquet')",
            "output_path": "C:/secret.xlsx",
            "policy": {"max_total_repair_rounds": 999},
        },
    )
    assert response.status_code == 422


def test_harness_run_events_attempts_and_resume_are_user_isolated(
    tmp_path,
    monkeypatch,
) -> None:
    client_a = _client(tmp_path, monkeypatch, user_id="user-a")
    plan_id = _prepare_table_plan(client_a, tmp_path)
    created = client_a.post(
        "/api/semantic-harness/runs",
        json={"plan_id": plan_id},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    client_b = _client(tmp_path, monkeypatch, user_id="user-b")
    assert client_b.get(
        f"/api/semantic-harness/runs/{run_id}"
    ).status_code == 404
    assert client_b.get(
        f"/api/semantic-harness/runs/{run_id}/events"
    ).status_code == 404
    assert client_b.get(
        f"/api/semantic-harness/runs/{run_id}/attempts"
    ).status_code == 404
    assert client_b.post(
        f"/api/semantic-harness/runs/{run_id}/resume",
        json={
            "question_id": "not-owned",
            "resume_token": "0" * 32,
            "answer": "retry",
        },
    ).status_code == 404
