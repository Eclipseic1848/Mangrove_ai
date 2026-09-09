"""混合来源的真实持久与发布接缝；运行生成和语义验证为明确替身。"""
import hashlib
import io
import json
import zipfile

import httpx

from src.api.routes import source_acquisition as source_routes
from src.api.routes import semantic_workspace as workspace_routes
from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.services.upload_store import UploadStore
from src.source_acquisition import (
    AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionService,
)
from tests.test_pi_runtime_workspace_api import _wait_for_delivery
from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime, _client


def _original_bytes(client, task_id, revision, expected_count=3):
    response = client.get(
        f"/api/semantic-workspace/tasks/{task_id}/source-bundle",
        params={"revision": revision},
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["sources"]) == expected_count
        contents = []
        for source in manifest["sources"]:
            original = next(item for item in source["files"] if item["role"] == "original")
            content = archive.read(original["path"])
            assert hashlib.sha256(content).hexdigest() == original["sha256"]
            contents.append(content)
        return contents


def test_mixed_originals_survive_delivery_and_targeted_refresh(tmp_path, monkeypatch):
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    client.app.include_router(source_routes.router)
    table = "实体ID,名称,公开规模\nE-101,青林,\n".encode("utf-8")
    upload = UploadStore(root=settings.data_prep_upload_root, max_bytes=1024 * 1024).save_bytes(
        "user-a", "table-a.csv", table, media_type="text/csv",
    )
    pages = {
        "b.example.com": b"<html><body>Entity E-101; scale 200; source B</body></html>",
        "c.example.com": b"<html><body>Entity E-101; scale 220; source C</body></html>",
    }
    original_b = pages["b.example.com"]
    calls = []

    def handler(request):
        host = request.headers["host"]
        calls.append(host)
        return httpx.Response(200, content=pages[host], headers={"content-type": "text/html"})

    service = SourceAcquisitionService(
        SourceAcquisitionRepository(settings.webui_db_path),
        AnonymousWebFetcher(
            security_guard=HttpSecurityGuard(resolver=lambda _: ["93.184.216.34"]),
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.setattr(source_routes, "get_source_acquisition_service", lambda: service)
    monkeypatch.setattr(workspace_routes, "_source_acquisition_service", lambda: service)
    with client:
        snapshots = []
        for host in pages:
            response = client.post(
                "/api/semantic-workspace/source-acquisitions",
                headers={"Idempotency-Key": f"mixed-{host}"},
                json={"url": f"https://{host}/entity", "purpose": "取得已选实体资料"},
            )
            assert response.status_code == 202, response.text
            snapshots.append(response.json()["snapshot_id"])
        response = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "mixed-delivery"},
            json={
                "objective_text": "按实体ID汇总资料，冲突以C为准，保留来源依据",
                "upload_ids": [upload.upload_id], "source_snapshot_ids": snapshots,
                "quantity_requirement": "尽可能多", "completeness_requirement": "允许披露缺口后交付",
                "output_formats": ["json"], "runtime_version": "pi", "provider": "local",
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        assert _wait_for_delivery(client, task_id)["delivery"] is not None
        first_sources = runtime.requests[0].sources
        assert len(first_sources) == 3
        assert {item.host_path.read_bytes() for item in first_sources} == {table, *pages.values()}
        assert set(_original_bytes(client, task_id, 1)) == {table, *pages.values()}
        assert calls == ["b.example.com", "c.example.com"]

        pages["b.example.com"] = b"<html><body>Entity E-101; scale 201; source B refreshed</body></html>"
        refreshed = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
            headers={"Idempotency-Key": "refresh-only-b"},
            json={"expected_active_revision": 1, "target_source_snapshot_id": snapshots[0]},
        )
        assert refreshed.status_code == 202, refreshed.text
        assert refreshed.json()["revision"]["revision"] == 2
        # 等待实际新修订作业结束，不能把仍在运行的任务交给TestClient拆除。
        assert _wait_for_delivery(client, task_id)["delivery"] is not None
        assert runtime.start_calls == 2
        assert len(runtime.requests) == 2
        assert runtime.requests[1].revision == 2
        assert {item.host_path.read_bytes() for item in runtime.requests[1].sources} == {table, *pages.values()}
        assert set(_original_bytes(client, task_id, 2)) == {table, *pages.values()}
        assert set(_original_bytes(client, task_id, 1)) == {table, original_b, pages["c.example.com"]}
        replay = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
            headers={"Idempotency-Key": "refresh-only-b"},
            json={"expected_active_revision": 1, "target_source_snapshot_id": snapshots[0]},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["attempt"]["attempt_id"] == refreshed.json()["attempt"]["attempt_id"]
        assert calls == ["b.example.com", "c.example.com", "b.example.com"]

        removed = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/revisions",
            headers={"Idempotency-Key": "remove-web-groups"},
            json={
                "expected_active_revision": 2,
                "instruction": "移除两组网页，只保留表格现有资料，继续输出JSON并保留来源依据",
                "upload_ids": [upload.upload_id], "source_snapshot_ids": [],
            },
        )
        assert removed.status_code == 202, removed.text
        assert removed.json()["revision"] == 3
        third = _wait_for_delivery(client, task_id)
        assert third["delivery"] is not None
        assert third["source_contract"]["web_sources"] == []
        assert third["source_contract"]["goal_contract"]["quantity_requirement"] == "尽可能多"
        assert runtime.start_calls == 3
        assert runtime.requests[2].revision == 3
        assert [item.upload_id for item in runtime.requests[2].sources] == [upload.upload_id]
        assert _original_bytes(client, task_id, 3, expected_count=1) == [table]
        assert set(_original_bytes(client, task_id, 1)) == {table, original_b, pages["c.example.com"]}
        assert calls == ["b.example.com", "c.example.com", "b.example.com"]
