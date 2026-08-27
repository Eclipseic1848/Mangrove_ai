# -*- coding: utf-8 -*-
"""精确网页来源获取 API 的鉴权、恢复与零下游副作用。"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from src.api.auth import get_current_user
from src.api.routes import source_acquisition as source_routes
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import (
    AnonymousWebFetcher,
    SourceAcquisitionRepository,
    SourceAcquisitionService,
)
from tests.database_migration_helpers import migrated_webui_database


def _client(tmp_path: Path, monkeypatch, handler):
    database = migrated_webui_database(tmp_path / "source-api.db")
    service = SourceAcquisitionService(
        SourceAcquisitionRepository(database),
        AnonymousWebFetcher(
            security_guard=HttpSecurityGuard(
                resolver=lambda _host: ["93.184.216.34"]
            ),
            transport=httpx.MockTransport(handler),
            max_bytes=1024,
        ),
    )
    monkeypatch.setattr(
        source_routes,
        "get_source_acquisition_service",
        lambda: service,
    )
    owner = {"value": "owner-a"}
    app = FastAPI()
    app.include_router(source_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": owner["value"],
        "role": "user",
    }
    return TestClient(app), database, owner


def test_api_persists_success_and_recovers_same_attempt(tmp_path, monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>产品资料</title></head><body>公开说明</body></html>",
            request=request,
        )

    client, _database, _owner = _client(tmp_path, monkeypatch, handler)
    payload = {
        "url": "https://example.com/product#overview",
        "purpose": "读取公开产品说明，供当前任务分析",
        "allowed_scope": "current_page",
    }
    first = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "api-success"},
        json=payload,
    )
    second = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "api-success"},
        json=payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert calls == 1
    body = first.json()
    assert second.json()["attempt_id"] == body["attempt_id"]
    assert body["status"] == "succeeded"
    assert body["normalized_url"] == "https://example.com/product"
    assert body["snapshot"]["artifacts"][0]["title"] == "产品资料"

    restored = client.get(
        f"/api/semantic-workspace/source-acquisitions/{body['attempt_id']}"
    )
    assert restored.status_code == 200
    assert restored.json() == body

    artifact_id = body["snapshot"]["artifacts"][0]["artifact_id"]
    artifact = client.get(
        f"/api/semantic-workspace/source-artifacts/{artifact_id}"
    )
    assert artifact.status_code == 200
    assert artifact.json()["text_preview"] == "产品资料 公开说明"
    assert "content_blob" not in artifact.json()


def test_api_failure_creates_no_task_runtime_or_delivery(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF",
            request=request,
        )

    client, database, _owner = _client(tmp_path, monkeypatch, handler)
    response = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "api-failure"},
        json={
            "url": "https://example.com/file.pdf",
            "purpose": "读取公开页面",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "non_html"
    with sqlite3.connect(database) as connection:
        for table in (
            "semantic_workspace_tasks",
            "semantic_workspace_revisions",
            "agentic_runtime_runs",
            "formal_delivery_runs",
            "model_provider_usage",
        ):
            assert connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM source_snapshots"
        ).fetchone()[0] == 0


def test_api_owner_isolation_conflict_validation_and_cancel(
    tmp_path,
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>正文</body></html>",
            request=request,
        )

    client, _database, owner = _client(tmp_path, monkeypatch, handler)
    created = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "owner-key"},
        json={
            "url": "https://example.com/page",
            "purpose": "读取公开页面",
        },
    ).json()

    conflict = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "owner-key"},
        json={
            "url": "https://example.com/other",
            "purpose": "读取公开页面",
        },
    )
    assert conflict.status_code == 409

    invalid = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "invalid"},
        json={"url": "file:///secret", "purpose": "读取公开页面"},
    )
    assert invalid.status_code == 422

    snapshot_id = created["snapshot_id"]
    artifact_id = created["snapshot"]["artifacts"][0]["artifact_id"]

    owner["value"] = "owner-b"
    assert client.get(
        f"/api/semantic-workspace/source-acquisitions/{created['attempt_id']}"
    ).status_code == 404
    assert client.post(
        f"/api/semantic-workspace/source-acquisitions/{created['attempt_id']}/cancel"
    ).status_code == 404
    assert client.get(
        f"/api/semantic-workspace/source-snapshots/{snapshot_id}"
    ).status_code == 404
    assert client.get(
        f"/api/semantic-workspace/source-artifacts/{artifact_id}"
    ).status_code == 404

    owner_b = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "owner-key"},
        json={
            "url": "https://example.com/page",
            "purpose": "读取公开页面",
        },
    )
    assert owner_b.status_code == 202
    assert owner_b.json()["attempt_id"] != created["attempt_id"]
