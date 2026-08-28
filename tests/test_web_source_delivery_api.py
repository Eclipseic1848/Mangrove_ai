# -*- coding: utf-8 -*-
"""匿名网页快照进入统一任务、验证和正式 Delivery 的纵切面。"""
from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import httpx
import pytest

from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_store
from src.api import semantic_workspace_runtime as runtime_mod
from src.api.routes import semantic_workspace as semantic_routes
from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.delivery_publishing.service import DeliveryPublisher
from src.source_acquisition import (
    AnonymousWebFetcher,
    SourceAcquisitionRepository,
    SourceAcquisitionService,
)
from tests.test_pi_runtime_workspace_api import (
    FakePiRuntime,
    _client,
    _wait_for_delivery,
    _wait_for_status,
)


def _seed_snapshot(
    database: Path,
    *,
    owner_id: str = "user-a",
    coverage: dict | None = None,
) -> tuple[str, bytes]:
    content = (
        b"<html><head><title>Mangrove</title></head>"
        b"<body>Public product facts</body></html>"
    )
    digest = hashlib.sha256(content).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    attempt_id = "source_attempt_web_delivery"
    snapshot_id = "source_snapshot_web_delivery"
    artifact_id = "source_artifact_web_delivery"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO source_acquisition_attempts "
            "(attempt_id, owner_id, idempotency_key, request_hash, request_url, "
            "normalized_url, allowed_scope_json, purpose, status, started_at, "
            "finished_at, snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
            "'succeeded', ?, ?, ?)",
            (
                attempt_id,
                owner_id,
                "web-delivery-source",
                "a" * 64,
                "https://example.com/product",
                "https://example.com/product",
                json.dumps({"kind": "current_page"}),
                "生成公开产品摘要",
                now,
                now,
                snapshot_id,
            ),
        )
        connection.execute(
            "INSERT INTO source_snapshots "
            "(snapshot_id, owner_id, attempt_id, allowed_scope_json, "
            "valid_page_count, failed_page_count, coverage_json, created_at) "
            "VALUES (?, ?, ?, ?, 1, 0, ?, ?)",
            (
                snapshot_id,
                owner_id,
                attempt_id,
                json.dumps({"kind": "current_page"}),
                json.dumps(coverage) if coverage is not None else None,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO source_artifacts "
            "(artifact_id, owner_id, snapshot_id, request_url, final_url, "
            "read_at, content_sha256, media_type, size_bytes, title, "
            "text_preview, content_blob) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?)",
            (
                artifact_id,
                owner_id,
                snapshot_id,
                "https://example.com/product",
                "https://example.com/product",
                now,
                digest,
                "text/html",
                len(content),
                "Mangrove",
                "Mangrove Public product facts",
                content,
            ),
        )
    return snapshot_id, content


def test_exact_web_snapshot_reaches_formal_delivery_without_refetch(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    snapshot_id, source_content = _seed_snapshot(Path(settings.webui_db_path))
    payload = {
        "objective_text": "根据公开网页生成一份产品摘要",
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

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "web-delivery-task"},
            json=payload,
        )
        assert created.status_code == 202, created.text
        replay = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "web-delivery-task"},
            json=payload,
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["task_id"] == created.json()["task_id"]

        task_id = created.json()["task_id"]
        completed = _wait_for_delivery(client, task_id)
        assert completed["delivery"] is not None
        assert runtime.start_calls == 1
        request = runtime.requests[0]
        assert "必须包含：产品名称" in request.objective_text
        assert "明确不要：不得推测未公开价格" in request.objective_text
        assert "数量要求：当前页面中有证据的全部内容" in request.objective_text
        assert "完整性边界：仅对当前精确页面负责" in request.objective_text
        assert request.sources[0].host_path.read_bytes() == source_content
        assert request.sources[0].sha256 == hashlib.sha256(source_content).hexdigest()

        frozen = AgenticRuntimeRepository(settings.webui_db_path).get(
            "user-a", task_id, 1
        )
        assert frozen is not None
        assert frozen["run_id"]
        with sqlite3.connect(settings.webui_db_path) as connection:
            connection.row_factory = sqlite3.Row
            contract = connection.execute(
                "SELECT * FROM web_task_contracts WHERE owner_id=? AND task_id=? "
                "AND revision=1",
                ("user-a", task_id),
            ).fetchone()
        assert contract is not None
        assert contract["source_snapshot_id"] == snapshot_id
        assert json.loads(contract["goal_contract_json"])["must_include"] == [
            "产品名称",
            "公开说明",
        ]
        assert json.loads(contract["delivery_spec_json"])["formats"] == ["json"]
        runtime_binding = json.loads(contract["runtime_binding_json"])
        assert runtime_binding["external_run_id"] == frozen["run_id"]
        assert runtime_binding["adapter_id"]
        assert runtime_binding["adapter_version"]
        assert runtime_binding["runtime_artifact"]
        assert runtime_binding["protocol_version"]
        assert runtime_binding["event_schema_version"]
        assert runtime_binding["capability_digest"]
        binding_events = AgenticRuntimeRepository(
            settings.webui_db_path
        ).list_events("user-a", task_id, 1)
        assert binding_events[0]["event_type"] == "kernel.binding.frozen"
        assert binding_events[0]["details"]["preallocated_run"] is True


def test_source_refresh_creates_new_snapshot_and_revision_idempotently(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    old_snapshot_id, old_content = _seed_snapshot(Path(settings.webui_db_path))

    def refreshed_page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>Refreshed public facts</body></html>",
            request=request,
        )

    refresh_service = SourceAcquisitionService(
        SourceAcquisitionRepository(settings.webui_db_path),
        AnonymousWebFetcher(
            security_guard=HttpSecurityGuard(
                resolver=lambda _host: ["93.184.216.34"]
            ),
            transport=httpx.MockTransport(refreshed_page),
        ),
    )
    monkeypatch.setattr(
        semantic_routes,
        "_source_acquisition_service",
        lambda: refresh_service,
    )
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": old_snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        _wait_for_delivery(client, task_id)

        refreshed = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
            headers={"Idempotency-Key": "refresh-once"},
            json={"expected_active_revision": 1},
        )
        assert refreshed.status_code == 202, refreshed.text
        body = refreshed.json()
        assert body["status"] == "revision_created"
        assert body["revision"]["revision"] == 2
        new_snapshot_id = body["attempt"]["snapshot_id"]
        assert new_snapshot_id != old_snapshot_id
        _wait_for_delivery(client, task_id)

        replay = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
            headers={"Idempotency-Key": "refresh-once"},
            json={"expected_active_revision": 1},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["revision"]["revision"] == 2
        assert replay.json()["attempt"]["snapshot_id"] == new_snapshot_id

        conflicting_reuse = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
            headers={"Idempotency-Key": "refresh-once"},
            json={"expected_active_revision": 2},
        )
        assert conflicting_reuse.status_code == 409, conflicting_reuse.text
        assert "另一份来源请求" in conflicting_reuse.json()["detail"]

    store = get_store()
    first_revision = store.get_semantic_workspace_revision("user-a", task_id, 1)
    second_revision = store.get_semantic_workspace_revision("user-a", task_id, 2)
    assert first_revision is not None
    assert second_revision is not None
    assert first_revision["source_refs"][0]["snapshot_id"] == old_snapshot_id
    assert second_revision["source_refs"][0]["snapshot_id"] == new_snapshot_id
    assert runtime.requests[0].sources[0].host_path.read_bytes() == old_content
    assert runtime.requests[1].sources[0].host_path.read_bytes() == (
        b"<html><body>Refreshed public facts</body></html>"
    )
    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE owner_id='user-a'"
        ).fetchone()[0] == 2
        contracts = connection.execute(
            "SELECT revision, source_snapshot_id FROM web_task_contracts "
            "WHERE owner_id='user-a' AND task_id=? ORDER BY revision",
            (task_id,),
        ).fetchall()
    assert contracts == [(1, old_snapshot_id), (2, new_snapshot_id)]


def test_failed_source_refresh_keeps_old_revision_and_run(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    monkeypatch.setattr(
        semantic_routes,
        "_source_acquisition_service",
        lambda: SourceAcquisitionService(
            SourceAcquisitionRepository(settings.webui_db_path),
            AnonymousWebFetcher(
                security_guard=HttpSecurityGuard(
                    resolver=lambda _host: ["93.184.216.34"]
                ),
                transport=httpx.MockTransport(refused),
            ),
        ),
    )
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
        task_id = created.json()["task_id"]
        completed = _wait_for_delivery(client, task_id)
        old_run_id = completed["agentic_runtime"]["run_id"]
        rejected = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
            headers={"Idempotency-Key": "refresh-refused"},
            json={"expected_active_revision": 1},
        )
        assert rejected.status_code == 409, rejected.text
        unchanged = client.get(f"/api/semantic-workspace/tasks/{task_id}").json()

    assert unchanged["active_revision"] == 1
    assert unchanged["agentic_runtime"]["run_id"] == old_run_id
    assert runtime.start_calls == 1


def test_source_refresh_intent_serializes_concurrent_and_explicit_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        _wait_for_delivery(client, created.json()["task_id"])
    task_id = created.json()["task_id"]
    store = get_store()
    barrier = threading.Barrier(2)

    def claim() -> bool:
        barrier.wait(timeout=5)
        _, claimed = store.claim_source_refresh_intent(
            "user-a",
            task_id,
            "concurrent-refresh",
            request_hash="b" * 64,
            expected_revision=1,
            attempt_id="source_attempt_web_delivery",
            snapshot_id=snapshot_id,
        )
        return claimed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: claim(), range(2)))
    assert sorted(results) == [False, True]

    # 正常并发不能接管正在绑定的请求；只有用户稍后显式恢复结果未知请求才可接管。
    _, immediate = store.claim_source_refresh_intent(
        "user-a",
        task_id,
        "concurrent-refresh",
        request_hash="b" * 64,
        expected_revision=1,
        attempt_id="source_attempt_web_delivery",
        snapshot_id=snapshot_id,
        resume_unknown=True,
    )
    assert immediate is False
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute(
            "UPDATE source_refresh_intents SET updated_at=? WHERE owner_id=? "
            "AND task_id=? AND idempotency_key=?",
            ("2026-01-01T00:00:00", "user-a", task_id, "concurrent-refresh"),
        )
    _, recovered = store.claim_source_refresh_intent(
        "user-a",
        task_id,
        "concurrent-refresh",
        request_hash="b" * 64,
        expected_revision=1,
        attempt_id="source_attempt_web_delivery",
        snapshot_id=snapshot_id,
        resume_unknown=True,
    )
    assert recovered is True
    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM source_refresh_intents WHERE owner_id=? "
            "AND task_id=? AND idempotency_key=?",
            ("user-a", task_id, "concurrent-refresh"),
        ).fetchone()[0] == 1


def test_web_task_rejects_cross_owner_or_incomplete_source(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    other_snapshot, _ = _seed_snapshot(
        Path(settings.webui_db_path), owner_id="other-owner"
    )
    request = {
        "objective_text": "读取网页",
        "upload_ids": [],
        "source_snapshot_id": other_snapshot,
        "must_include": [],
        "explicit_exclusions": [],
        "quantity_requirement": "当前页面中有证据的内容",
        "completeness_requirement": "仅对当前精确页面负责",
        "output_formats": ["json"],
        "runtime_version": "pi",
        "provider": "local",
    }

    with client:
        rejected = client.post("/api/semantic-workspace/tasks", json=request)
        assert rejected.status_code == 404
        with sqlite3.connect(settings.webui_db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM semantic_workspace_tasks"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM agentic_runtime_runs"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM web_task_contracts"
            ).fetchone()[0] == 0


def test_hard_all_requirement_rejects_unknown_source_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(
        Path(settings.webui_db_path),
        coverage={"limit_reached": True, "attempted_page_count": 1},
    )
    with client:
        rejected = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "返回所有有证据的内容",
                "completeness_requirement": "不得遗漏授权范围内的页面",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
    assert rejected.status_code == 409, rejected.text
    assert "不能承诺" in rejected.json()["detail"]
    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_workspace_revisions"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("objective_text", "must_include"),
    [
        ("汇总全部页面", []),
        ("生成网页摘要", ["所有页面都必须包含"]),
    ],
)
def test_hard_requirement_in_objective_or_must_include_rejects_unknown_coverage(
    tmp_path,
    monkeypatch,
    objective_text: str,
    must_include: list[str],
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(
        Path(settings.webui_db_path),
        coverage={"limit_reached": True, "attempted_page_count": 1},
    )
    with client:
        rejected = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": objective_text,
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": must_include,
                "explicit_exclusions": [],
                "quantity_requirement": "返回有证据的内容",
                "completeness_requirement": "披露未覆盖范围",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )

    assert rejected.status_code == 409, rejected.text
    assert "不能承诺" in rejected.json()["detail"]
    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_workspace_revisions"
        ).fetchone()[0] == 0


def test_web_task_binding_failure_rolls_back_whole_aggregate(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    original = AgenticRuntimeRepository.freeze_runtime_binding

    def fail_after_binding(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("模拟 RuntimeBinding 后续事务失败")

    monkeypatch.setattr(
        AgenticRuntimeRepository,
        "freeze_runtime_binding",
        fail_after_binding,
    )
    with client:
        rejected = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
                "model": "local-model",
            },
        )
    assert rejected.status_code == 409, rejected.text
    with sqlite3.connect(settings.webui_db_path) as connection:
        for table in (
            "semantic_workspace_tasks",
            "semantic_workspace_revisions",
            "agentic_runtime_runs",
            "agentic_runtime_events",
            "web_task_contracts",
        ):
            assert connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0] == 0


def test_binding_prepare_failure_releases_task_idempotency_claim(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    manager = runtime_mod.get_semantic_workspace_manager()
    original = manager.prepare_runtime_binding

    async def fail_prepare(**_kwargs):
        raise RuntimeError("模拟绑定预检失败")

    monkeypatch.setattr(manager, "prepare_runtime_binding", fail_prepare)
    payload = {
        "objective_text": "生成网页摘要",
        "upload_ids": [],
        "source_snapshot_id": snapshot_id,
        "must_include": [],
        "explicit_exclusions": [],
        "quantity_requirement": "当前页面中有证据的内容",
        "completeness_requirement": "仅对当前精确页面负责",
        "output_formats": ["json"],
        "runtime_version": "pi",
        "provider": "local",
    }
    with client:
        with pytest.raises(RuntimeError, match="模拟绑定预检失败"):
            client.post(
                "/api/semantic-workspace/tasks",
                headers={"Idempotency-Key": "prepare-retry"},
                json=payload,
            )
        monkeypatch.setattr(manager, "prepare_runtime_binding", original)
        retried = client.post(
            "/api/semantic-workspace/tasks",
            headers={"Idempotency-Key": "prepare-retry"},
            json=payload,
        )
        assert retried.status_code == 202, retried.text
        _wait_for_delivery(client, retried.json()["task_id"])


def test_web_task_permanent_delete_retains_immutable_contract(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        _wait_for_delivery(client, task_id)
        get_store().soft_delete_semantic_workspace_task("user-a", task_id)
        assert get_store().purge_semantic_workspace_task("user-a", task_id)

    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_workspace_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM web_task_contracts WHERE task_id=?",
            (task_id,),
        ).fetchone()[0] == 1


def test_publisher_failure_recovers_same_candidate_without_rerunning_agent(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakePiRuntime()
    original = DeliveryPublisher.publish
    publish_calls = 0

    def fail_once(self, command, *, actor_id):
        nonlocal publish_calls
        publish_calls += 1
        if publish_calls == 1:
            raise OSError("模拟发布服务瞬时失败")
        return original(self, command, actor_id=actor_id)

    monkeypatch.setattr(DeliveryPublisher, "publish", fail_once)
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        completed = _wait_for_delivery(client, created.json()["task_id"])
        assert completed["delivery"] is not None

    assert publish_calls == 2
    assert runtime.start_calls == 1


def test_permanent_publisher_rejection_fails_closed_without_hot_retry(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakePiRuntime()
    publish_calls = 0

    def reject_contract(_self, _command, *, actor_id):
        nonlocal publish_calls
        assert actor_id == "user-a"
        publish_calls += 1
        raise ValueError("候选哈希变化")

    monkeypatch.setattr(DeliveryPublisher, "publish", reject_contract)
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "生成网页摘要",
                "upload_ids": [],
                "source_snapshot_id": snapshot_id,
                "must_include": [],
                "explicit_exclusions": [],
                "quantity_requirement": "当前页面中有证据的内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        _wait_for_status(client, created.json()["task_id"], "failed")

    assert publish_calls == 1
    assert runtime.start_calls == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"must_include": [f"约束-{index}" for index in range(51)]},
        {
            "must_include": [
                f"{index:02d}" + "x" * 498 for index in range(40)
            ]
        },
    ],
)
def test_web_task_rejects_unbounded_goal_contract(
    tmp_path,
    monkeypatch,
    overrides,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    payload = {
        "objective_text": "生成网页摘要",
        "upload_ids": [],
        "source_snapshot_id": snapshot_id,
        "must_include": [],
        "explicit_exclusions": [],
        "quantity_requirement": "当前页面中有证据的内容",
        "completeness_requirement": "仅对当前精确页面负责",
        "output_formats": ["json"],
        "runtime_version": "pi",
        "provider": "local",
        **overrides,
    }
    with client:
        rejected = client.post("/api/semantic-workspace/tasks", json=payload)
    assert rejected.status_code == 422, rejected.text
