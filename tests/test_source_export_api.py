"""完整来源下载走真实 HTTP、冻结修订和隔离库，不启动模型或重新抓取。"""
import hashlib
import io
import json
import zipfile
import asyncio
from pathlib import Path

import httpx
import pytest

from src.account_execution import ExecutionAuthorization, execution_context
from src.api.auth import get_store
from src.config.settings import settings
from tests.test_semantic_workspace_api import _client
from tests.test_workspace_canvas import canvas, execution_owner
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionService, SourceAcquisitionRequest


def test_frozen_web_source_exports_full_text_without_delivery_or_network(tmp_path, monkeypatch):
    client, owner = _client(tmp_path, monkeypatch)
    text = "完整中文原文。" * 900 + "末尾不可截断"
    raw = f'<html><body><p>{text}</p><a href="/next">下一页</a></body></html>'.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    calls = []
    def fetch(request):
        calls.append(request.url)
        return httpx.Response(200, content=raw, headers={"content-type": "text/html"})
    repository = SourceAcquisitionRepository(settings.webui_db_path)
    service = SourceAcquisitionService(repository, AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=lambda _: ["93.184.216.34"]),
        transport=httpx.MockTransport(fetch), max_bytes=100_000,
    ))
    with execution_context(ExecutionAuthorization("user-a", 0)):
        acquired = asyncio.run(service.acquire(owner_id="user-a", idempotency_key="export-fixture", request=SourceAcquisitionRequest(url="https://example.com/product", purpose="导出合成资料")))
        snapshot_id = acquired["snapshot_id"]
        artifact_id = repository.get_snapshot("user-a", snapshot_id)["artifacts"][0]["artifact_id"]
        get_store().create_semantic_workspace_task(
            "user-a", task_id="frozen-web-export", title="完整来源", objective_text="读取资料",
            upload_ids=[], output_formats=["json"], provider="local", model="fixture", external_api_confirmed=False,
            source_refs=[{"kind": "web_artifact", "artifact_id": artifact_id, "snapshot_id": snapshot_id, "sha256": digest}],
        )
    url = "/api/semantic-workspace/tasks/frozen-web-export/source-bundle"
    response = client.get(url, params={"revision": 1})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["revision"] == 1
        entry = manifest["sources"][0]
        assert entry["provenance"]["snapshot_id"] == snapshot_id
        for file in entry["files"]:
            content = archive.read(file["path"])
            assert hashlib.sha256(content).hexdigest() == file["sha256"]
            assert len(content) == file["size_bytes"]
        original = next(file for file in entry["files"] if file["role"] == "original")
        assert archive.read(original["path"]) == raw
        markdown = next(file for file in entry["files"] if file["format"] == "markdown")
        assert text in archive.read(markdown["path"]).decode("utf-8")
        assert "https://example.com/next" in archive.read(markdown["path"]).decode("utf-8")
    assert len(calls) == 1
    assert not list((Path(settings.semantic_execution_root) / "_bundles").glob("*.zip"))
    owner["value"] = "user-b"
    assert client.get(url, params={"revision": 1}).status_code == 404
    owner["value"] = "user-a"
    assert client.get(url, params={"revision": 2}).status_code == 404
    assert client.get(url, params={"revision": 1, "artifact_id": "other"}).status_code == 404
    assert client.get(url, params={"revision": 1, "table_format": "exe"}).status_code == 422
    with execution_context(ExecutionAuthorization("user-a", 0)):
        get_store().create_semantic_workspace_revision("user-a", "frozen-web-export", objective_text="新的无来源版本", output_formats=["json"], change_summary="修改来源", source_refs=[])
    assert client.get(url, params={"revision": 2}).status_code == 409
    again = client.get(url, params={"revision": 1})
    assert again.status_code == 200
    with zipfile.ZipFile(io.BytesIO(again.content)) as archive:
        assert json.loads(archive.read("manifest.json")) == manifest
    assert len(calls) == 1
    original_get = SourceAcquisitionRepository.get_artifact
    def corrupted_read(self, *args, **kwargs):
        result = original_get(self, *args, **kwargs)
        if result and kwargs.get("include_content"):
            return {**result, "content_blob": b"changed"}
        return result
    monkeypatch.setattr(SourceAcquisitionRepository, "get_artifact", corrupted_read)
    assert client.get(url, params={"revision": 1}).status_code == 409
    assert not list((Path(settings.semantic_execution_root) / "_bundles").iterdir())
    assert original_get(repository, "user-a", artifact_id, include_content=True)["content_blob"] == raw
    monkeypatch.setattr(SourceAcquisitionRepository, "get_artifact", lambda *args, **kwargs: None)
    assert client.get(url, params={"revision": 1}).status_code == 409
    client.close()


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
def test_bundle_disconnect_removes_only_generated_zip(canvas, failure):
    from src.api.routes.semantic_workspace import download_bundle

    _, _, task_id, _, upload = canvas
    response = download_bundle(task_id, include_sources=False, revision=1, user={"user_id": "user-a"})
    path = Path(response.response.path)
    assert path.exists()
    async def send(message):
        if message["type"] == "http.response.body":
            raise failure()
    async def receive():
        return {"type": "http.disconnect"}
    with pytest.raises(failure):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
    assert not path.exists()
    assert Path(upload.storage_path).exists()


def test_source_bundle_disk_full_preserves_original(canvas, monkeypatch):
    import errno
    import src.source_acquisition.export as exporter

    client, _, task_id, _, upload = canvas
    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic disk full")
    monkeypatch.setattr(exporter, "write_source_entries", disk_full)
    response = client.get(f"/api/semantic-workspace/tasks/{task_id}/source-bundle", params={"revision": 1})
    assert response.status_code == 507
    assert not list((Path(settings.semantic_execution_root) / "_bundles").iterdir())
    assert hashlib.sha256(Path(upload.storage_path).read_bytes()).hexdigest() == upload.sha256


def test_paginated_source_bundle_preserves_available_pages_and_failure_gap(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    bodies = {"/first": b'<html><body>Page one<a href="/second">next</a><a href="/failed">missing</a></body></html>',
              "/second": '<html><body>第二页尾部</body></html>'.encode("utf-8")}
    calls = []
    def fetch(request):
        calls.append(request.url.path)
        return httpx.Response(200 if request.url.path in bodies else 503,
                              content=bodies.get(request.url.path, b"unavailable"), headers={"content-type": "text/html"})
    repository = SourceAcquisitionRepository(settings.webui_db_path)
    service = SourceAcquisitionService(repository, AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=lambda _: ["93.184.216.34"]), transport=httpx.MockTransport(fetch),
    ))
    with execution_context(ExecutionAuthorization("user-a", 0)):
        acquired = asyncio.run(service.acquire(owner_id="user-a", idempotency_key="pages", request=SourceAcquisitionRequest(
            url="https://example.com/first", purpose="多页资料", scope_kind="same_site", page_limit=3,
        )))
        snapshot = repository.get_snapshot("user-a", acquired["snapshot_id"])
        refs = [{"kind": "web_artifact", "artifact_id": a["artifact_id"], "snapshot_id": snapshot["snapshot_id"], "sha256": a["content_sha256"]} for a in snapshot["artifacts"]]
        get_store().create_semantic_workspace_task("user-a", task_id="pages-export", title="分页", objective_text="资料导出", upload_ids=[],
            source_refs=refs, output_formats=["json"], provider="local", model="fixture", external_api_confirmed=False)
    assert len(calls) == 3
    response = client.get("/api/semantic-workspace/tasks/pages-export/source-bundle", params={"revision": 1})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["sources"]) == 2
        assert manifest["snapshots"][0]["valid_page_count"] == 2
        assert manifest["snapshots"][0]["failed_page_count"] == 1
        assert len(manifest["snapshots"][0]["failures"]) == 1
        for source in manifest["sources"]:
            path = source["provenance"]["final_url"].removeprefix("https://example.com")
            assert archive.read(source["files"][0]["path"]) == bodies[path]
    assert len(calls) == 3
    client.close()
