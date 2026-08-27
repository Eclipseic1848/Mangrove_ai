# -*- coding: utf-8 -*-
"""匿名网页快照进入统一任务、验证和正式 Delivery 的纵切面。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_store
from src.api import semantic_workspace_runtime as runtime_mod
from src.config.settings import settings
from src.delivery_publishing.service import DeliveryPublisher
from tests.test_pi_runtime_workspace_api import (
    FakePiRuntime,
    _client,
    _wait_for_delivery,
    _wait_for_status,
)


def _seed_snapshot(database: Path, *, owner_id: str = "user-a") -> tuple[str, bytes]:
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
            "valid_page_count, failed_page_count, created_at) "
            "VALUES (?, ?, ?, ?, 1, 0, ?)",
            (
                snapshot_id,
                owner_id,
                attempt_id,
                json.dumps({"kind": "current_page"}),
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
