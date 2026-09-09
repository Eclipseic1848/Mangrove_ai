"""本人历史原件与正式输出复用；生成/语义报告为替身，持久发布与原件实际执行。"""
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


def test_saved_web_and_formal_output_reuse_real_bytes_without_refetch(tmp_path, monkeypatch):
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    client.app.include_router(source_routes.router)
    uploads = UploadStore(root=settings.data_prep_upload_root, max_bytes=1024 * 1024)
    yesterday_bytes = "名称,数量\n青林,2\n".encode("utf-8")
    today_bytes = "名称,本次数量\n青林,3\n".encode("utf-8")
    yesterday = uploads.save_bytes("user-a", "yesterday.csv", yesterday_bytes, media_type="text/csv")
    today = uploads.save_bytes("user-a", "today.csv", today_bytes, media_type="text/csv")
    page_bytes = b"<html><body>Saved source: Qinglin, public quantity 2</body></html>"
    calls = []

    def handler(request):
        calls.append(request.headers["host"])
        return httpx.Response(200, content=page_bytes, headers={"content-type": "text/html"})

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
        acquired = client.post(
            "/api/semantic-workspace/source-acquisitions",
            headers={"Idempotency-Key": "reuse-frozen-web"},
            json={"url": "https://reuse.example.com/data", "purpose": "保存本次获准资料"},
        )
        assert acquired.status_code == 202, acquired.text
        snapshot_id = acquired.json()["snapshot_id"]
        producer = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "reuse-producer"},
            json={"objective_text": "汇总附件和网页，输出JSON并保留依据", "upload_ids": [yesterday.upload_id],
                  "source_snapshot_ids": [snapshot_id], "output_formats": ["json"],
                  "quantity_requirement": "尽可能多", "completeness_requirement": "允许披露缺口后交付",
                  "runtime_version": "pi", "provider": "local"},
        )
        assert producer.status_code == 202, producer.text
        producer_id = producer.json()["task_id"]
        produced = _wait_for_delivery(client, producer_id)
        output = produced["delivery"]["outputs"][0]
        output_id = output["output_id"]
        downloaded = client.get(f"/api/semantic-deliveries/outputs/{output_id}")
        assert downloaded.status_code == 200, downloaded.text
        produced_bytes = downloaded.content
        assert hashlib.sha256(produced_bytes).hexdigest() == output["sha256"]

        # 清理的是临时测试任务记录；正式对象与真实出处仍须独立可读。
        recycled = client.delete(f"/api/semantic-workspace/tasks/{producer_id}")
        assert recycled.status_code == 200, recycled.text
        purged = client.delete(f"/api/semantic-workspace/tasks/{producer_id}/permanent")
        assert purged.status_code == 200, purged.text
        assert client.get(f"/api/semantic-workspace/tasks/{producer_id}").status_code == 404
        preview = client.get(f"/api/semantic-workspace/reusable-sources/outputs/{output_id}/preview")
        assert preview.status_code == 200, preview.text
        assert preview.json()["origin"]["task_id"] == producer_id
        assert preview.json()["representation"]["sha256"] == output["sha256"]

        history = client.get("/api/semantic-workspace/reusable-sources")
        assert history.status_code == 200, history.text
        selected = next(item for item in history.json()["items"] if item.get("output_id") == output_id)
        assert selected["identity"] == "derived"
        assert selected["origin"]["task_id"] == producer_id
        assert selected["origin"]["revision"] == 1
        # 所有采集入口变为硬失败；后续仍必须从已保存对象完成发布与导出。
        def forbid_acquisition():
            raise AssertionError("历史资料复用不得重新采集")
        monkeypatch.setattr(source_routes, "get_source_acquisition_service", forbid_acquisition)
        monkeypatch.setattr(workspace_routes, "_source_acquisition_service", forbid_acquisition)
        reused = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "reuse-consumer"},
            json={"objective_text": "使用历史资料和已有处理结果，对照今天数据，输出JSON", "upload_ids": [today.upload_id],
                  "source_snapshot_ids": [snapshot_id], "delivery_output_ids": [output_id],
                  "quantity_requirement": "尽可能多", "completeness_requirement": "允许披露缺口后交付",
                  "output_formats": ["json"], "runtime_version": "pi", "provider": "local"},
        )
        assert reused.status_code == 202, reused.text
        consumer_id = reused.json()["task_id"]
        consumed = _wait_for_delivery(client, consumer_id)
        assert consumed["delivery"] is not None
        assert consumed["delivery_output_ids"] == [output_id]
        assert len(runtime.requests) == 2
        assert len(runtime.requests[1].sources) == 3
        assert {item.host_path.read_bytes() for item in runtime.requests[1].sources} == {
            today_bytes, page_bytes, produced_bytes,
        }
        bundle = client.get(f"/api/semantic-workspace/tasks/{consumer_id}/source-bundle", params={"revision": 1})
        assert bundle.status_code == 200, bundle.text
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert len(manifest["sources"]) == 3
            contents = []
            for source in manifest["sources"]:
                original = next(item for item in source["files"] if item["role"] == "original")
                content = archive.read(original["path"])
                assert hashlib.sha256(content).hexdigest() == original["sha256"]
                contents.append(content)
            assert set(contents) == {today_bytes, page_bytes, produced_bytes}
            derived = next(item for item in manifest["sources"] if item["artifact_id"] == output_id)
            assert derived["provenance"]["source_kind"] == "delivery_output"
            assert derived["provenance"]["output_id"] == output_id
            assert derived["provenance"]["delivery_id"] == produced["delivery"]["delivery_id"]
        references = client.get("/api/semantic-workspace/source-references", params={"kind": "delivery_output", "id": output_id})
        assert references.status_code == 200, references.text
        assert any(item["task_id"] == consumer_id and item["revision"] == 1 for item in references.json()["items"])
        assert references.json()["unknown_uses"] == 0
        # 任务记录清理后，已发布结果的真实来源关系不能被当成零引用。
        assert client.delete(f"/api/semantic-workspace/tasks/{consumer_id}").status_code == 200
        assert client.delete(f"/api/semantic-workspace/tasks/{consumer_id}/permanent").status_code == 200
        assert client.get(f"/api/semantic-workspace/tasks/{consumer_id}").status_code == 404
        retained = client.get("/api/semantic-workspace/source-references", params={"kind": "delivery_output", "id": output_id})
        assert retained.status_code == 200, retained.text
        published_ref = next(item for item in retained.json()["items"]
                             if item["task_id"] == consumer_id and item["revision"] == 1
                             and item["reference_kind"] == "delivery")
        assert published_ref["task_exists"] is False
        assert published_ref["delivery_id"] == consumed["delivery"]["delivery_id"]
        for kind, identity in (("upload", today.upload_id), ("snapshot", snapshot_id)):
            retained_source = client.get("/api/semantic-workspace/source-references", params={"kind": kind, "id": identity})
            assert retained_source.status_code == 200, retained_source.text
            assert any(item["reference_kind"] == "delivery" and item["task_id"] == consumer_id
                       and item["delivery_id"] == published_ref["delivery_id"]
                       and item["task_exists"] is False for item in retained_source.json()["items"])
        assert calls == ["reuse.example.com"]
