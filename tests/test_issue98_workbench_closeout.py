# -*- coding: utf-8 -*-
"""Issue #98：统一工作台收口所需的最小集成证明。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import io
from pathlib import Path
import threading
import time
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from src.api.auth import get_current_user
from src.api import semantic_workspace_runtime as runtime_mod
from src.api.routes import semantic_deliveries, semantic_workspace, source_acquisition
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.delivery_publishing.service import DeliveryPublisher
from src.source_acquisition import (
    AnonymousWebFetcher,
    SourceAcquisitionRepository,
    SourceAcquisitionService,
)
from tests.test_pi_runtime_workspace_api import _wait_for_delivery
from tests.test_web_source_delivery_api import (
    CoverageAwareWebPiRuntime,
    _client,
    _seed_snapshot,
)


def _task_payload(snapshot_id: str) -> dict:
    return {
        "objective_text": "根据公开网页生成产品摘要",
        "upload_ids": [],
        "source_snapshot_id": snapshot_id,
        "must_include": ["产品名称", "公开说明"],
        "explicit_exclusions": ["不得推测未公开价格"],
        "quantity_requirement": "当前页面中有证据的全部内容",
        "completeness_requirement": "仅对当前精确页面负责",
        "output_formats": ["json"],
        "runtime_version": "pi",
        "permission_profile": "standard",
        "provider": "local",
    }


def _restart_client(monkeypatch, runtime, candidate_verification) -> TestClient:
    manager = SemanticWorkspaceManager(
        pi_runtime=runtime,
        candidate_verification=candidate_verification,
    )
    monkeypatch.setattr(runtime_mod, "_manager", manager)

    @asynccontextmanager
    async def lifespan(_app):
        manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(semantic_workspace.router)
    app.include_router(semantic_deliveries.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "admin",
    }
    return TestClient(app)


def test_controlled_web_page_reaches_one_formal_delivery_preview_and_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fetch_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_calls
        fetch_calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title>Mangrove</title></head>"
                "<body>产品名称：Mangrove；公开说明：统一数据任务平台。</body></html>"
            ),
            request=request,
        )

    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    service = SourceAcquisitionService(
        SourceAcquisitionRepository(settings.webui_db_path),
        AnonymousWebFetcher(
            security_guard=HttpSecurityGuard(
                resolver=lambda _host: ["93.184.216.34"]
            ),
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.setattr(
        source_acquisition,
        "get_source_acquisition_service",
        lambda: service,
    )
    client.app.include_router(source_acquisition.router)

    with client:
        acquired = client.post(
            "/api/semantic-workspace/source-acquisitions",
            headers={"Idempotency-Key": "issue98-source"},
            json={
                "url": "https://example.com/product",
                "purpose": "生成公开产品摘要",
                "allowed_scope": "current_page",
            },
        )
        assert acquired.status_code == 202, acquired.text
        snapshot_id = acquired.json()["snapshot_id"]
        created = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "issue98-task"},
            json=_task_payload(snapshot_id),
        )
        assert created.status_code == 202, created.text
        completed = _wait_for_delivery(client, created.json()["task_id"])

        preview = client.get(
            f"/api/semantic-workspace/tasks/{completed['task_id']}/preview"
        )
        bundle = client.get(
            f"/api/semantic-workspace/tasks/{completed['task_id']}/bundle"
        )
        output = client.get(completed["delivery"]["outputs"][0]["download_url"])

    assert fetch_calls == 1
    assert runtime.start_calls == 1
    assert preview.status_code == 200, preview.text
    assert output.status_code == 200, output.text
    assert bundle.status_code == 200, bundle.text
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert {"manifest.json", "qa.json", "trace.json"} <= set(
            archive.namelist()
        )


def test_pending_publication_recovers_after_manager_restart_without_rerunning_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = CoverageAwareWebPiRuntime()
    allow_publish = False
    publish_failed = threading.Event()
    successful_publish_calls = 0
    original_publish = DeliveryPublisher.publish

    def fail_until_restart(self, command, *, actor_id):
        nonlocal successful_publish_calls
        if not allow_publish:
            publish_failed.set()
            raise OSError("模拟发布进程中断")
        successful_publish_calls += 1
        return original_publish(self, command, actor_id=actor_id)

    monkeypatch.setattr(DeliveryPublisher, "publish", fail_until_restart)
    first_client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    with first_client:
        created = first_client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "issue98-restart-task"},
            json=_task_payload(snapshot_id),
        )
        assert created.status_code == 202, created.text
        assert publish_failed.wait(timeout=5)
        task_id = created.json()["task_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pending = first_client.get(
                f"/api/semantic-workspace/tasks/{task_id}"
            ).json()
            if (
                pending["status"] == "running"
                and pending["delivery"] is None
                and pending["agentic_runtime"]["status"] == "candidate_ready"
            ):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"Publisher 失败未留下可恢复状态：{pending}")
        candidate_verification = runtime_mod._manager._candidate_verification

    allow_publish = True
    restarted_runtime = CoverageAwareWebPiRuntime()
    restarted = _restart_client(
        monkeypatch,
        restarted_runtime,
        candidate_verification,
    )
    with restarted:
        completed = _wait_for_delivery(restarted, task_id)
        latest = restarted.get(
            "/api/semantic-deliveries/runs/"
            f"{completed['delivery']['run_id']}/latest"
        )

    assert runtime.start_calls == 1
    assert runtime.resume_calls == []
    assert restarted_runtime.start_calls == 0
    assert restarted_runtime.resume_calls == []
    assert successful_publish_calls == 1
    assert latest.status_code == 200, latest.text
    assert latest.json()["delivery_id"] == completed["delivery"]["delivery_id"]


def test_other_owner_cannot_operate_or_reuse_web_task_facts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json=_task_payload(snapshot_id),
        )
        completed = _wait_for_delivery(client, created.json()["task_id"])
        task_id = completed["task_id"]
        runtime = completed["agentic_runtime"]
        attempt_id = runtime["latest_verification_attempt"]["attempt_id"]
        candidate_id = runtime["candidates"][0]["artifact_id"]
        output_id = completed["delivery"]["outputs"][0]["output_id"]

        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-b",
            "role": "user",
        }
        denied = (
            (client.get(f"/api/semantic-workspace/tasks/{task_id}"), {404}),
            (client.get(f"/api/semantic-workspace/tasks/{task_id}/events"), {404}),
            (client.post(
                f"/api/semantic-workspace/tasks/{task_id}/answer",
                json={"answer": "继续"},
            ), {404}),
            (client.post(f"/api/semantic-workspace/tasks/{task_id}/cancel"), {404}),
            (client.post(
                f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
                headers={"Idempotency-Key": "owner-b-retry"},
                json={
                    "expected_revision": 1,
                    "expected_previous_attempt_id": attempt_id,
                    "external_api_confirmed": False,
                },
            ), {404}),
            (client.post(
                f"/api/semantic-workspace/tasks/{task_id}/"
                f"candidate-verifications/{attempt_id}/publish",
                headers={"Idempotency-Key": "owner-b-publish"},
                json={"expected_revision": 1},
            ), {404}),
            (client.get(f"/api/semantic-workspace/tasks/{task_id}/preview"), {404}),
            (client.get(f"/api/semantic-workspace/tasks/{task_id}/bundle"), {404}),
            (client.get(
                f"/api/semantic-workspace/tasks/{task_id}/candidates/{candidate_id}"
            ), {404}),
            (client.get(f"/api/semantic-deliveries/outputs/{output_id}"), {404}),
            (client.post(
                f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
                headers={"Idempotency-Key": "owner-b-refresh"},
                json={"expected_active_revision": 1},
            ), {404}),
            (client.post(f"/api/semantic-workspace/tasks/{task_id}/restore"), {404}),
            (client.post(
                "/api/semantic-workspace/tasks",
                headers={"Idempotency-Key": "owner-b-reuse"},
                json=_task_payload(snapshot_id),
            ), {403, 404}),
        )

    for response, expected_statuses in denied:
        assert response.status_code in expected_statuses, response.text
        assert task_id not in response.text
        assert attempt_id not in response.text
        assert candidate_id not in response.text
        assert output_id not in response.text
