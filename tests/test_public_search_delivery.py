"""合成公开搜索经真实来源 API 到交付和原文下载，不调用外部模型。"""
import hashlib
import io
import json
import zipfile

import httpx

from src.api.routes import source_acquisition as routes
from src.api.routes import semantic_workspace as workspace_routes
from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionService
from src.source_acquisition.public_search import PublicSearchClient
from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime, _client
from tests.test_pi_runtime_workspace_api import _wait_for_delivery


def test_query_snapshot_reaches_delivery_and_preserves_original_without_refetch(tmp_path, monkeypatch):
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    client.app.include_router(routes.router)
    calls = []
    raw = ("<html><body>公开产品说明。" + "保留原文。" * 1000 + "末尾证据</body></html>").encode("utf-8")

    def handler(request):
        calls.append(request.headers["host"])
        if request.headers["host"] == "html.duckduckgo.com":
            return httpx.Response(200, text='<a class="result__a" href="https://example.com/product">公开手册</a>', headers={"content-type": "text/html"})
        return httpx.Response(200, content=raw, headers={"content-type": "text/html"})

    guard = HttpSecurityGuard(resolver=lambda _: ["93.184.216.34"])
    transport = httpx.MockTransport(handler)
    service = SourceAcquisitionService(
        SourceAcquisitionRepository(settings.webui_db_path),
        AnonymousWebFetcher(security_guard=guard, transport=transport),
        search_client=PublicSearchClient(security_guard=guard, transport=transport),
    )
    monkeypatch.setattr(routes, "get_source_acquisition_service", lambda: service)
    monkeypatch.setattr(workspace_routes, "_source_acquisition_service", lambda: service)
    with client:
        acquired = client.post("/api/semantic-workspace/source-acquisitions", headers={"Idempotency-Key": "query-delivery"}, json={
            "query": "公开产品手册", "purpose": "根据已读取资料生成摘要", "page_limit": 1,
        })
        assert acquired.status_code == 202, acquired.text
        source = acquired.json()
        assert source["search_report"]["read_count"] == 1
        created = client.post("/api/semantic-workspace/tasks", headers={"Idempotency-Key": "query-task"}, json={
            "objective_text": "根据已取得网页资料生成产品摘要", "source_snapshot_id": source["snapshot_id"],
            "quantity_requirement": "尽可能多", "completeness_requirement": "允许披露缺口后交付",
            "output_formats": ["json"], "runtime_version": "pi", "permission_profile": "standard", "provider": "local",
        })
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        task = _wait_for_delivery(client, task_id)
        assert task["delivery"] is not None
        assert runtime.start_calls == 1
        assert runtime.requests[0].sources[0].host_path.read_bytes() == raw
        bundle = client.get(f"/api/semantic-workspace/tasks/{task_id}/source-bundle", params={"revision": 1})
        assert bundle.status_code == 200, bundle.text
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            exported = manifest["sources"][0]
            original = next(item for item in exported["files"] if item["role"] == "original")
            assert archive.read(original["path"]) == raw
            assert original["sha256"] == hashlib.sha256(raw).hexdigest()
        assert calls == ["html.duckduckgo.com", "example.com"]
        refreshed = client.post(f"/api/semantic-workspace/tasks/{task_id}/source-refresh", headers={"Idempotency-Key": "query-refresh"}, json={"expected_active_revision": 1})
        assert refreshed.status_code == 202, refreshed.text
        assert refreshed.json()["revision"]["revision"] == 2
        assert refreshed.json()["attempt"]["allowed_scope"]["query"] == "公开产品手册"
        assert refreshed.json()["attempt"]["snapshot_id"] != source["snapshot_id"]
        _wait_for_delivery(client, task_id)
        replay = client.post(f"/api/semantic-workspace/tasks/{task_id}/source-refresh", headers={"Idempotency-Key": "query-refresh"}, json={"expected_active_revision": 1})
        assert replay.status_code == 202, replay.text
        assert replay.json()["attempt"]["attempt_id"] == refreshed.json()["attempt"]["attempt_id"]
        assert calls == ["html.duckduckgo.com", "example.com"] * 2
