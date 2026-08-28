# -*- coding: utf-8 -*-
"""P1-01：上下文草案通过网页工作台 HTTP 接缝冻结并执行。"""
from __future__ import annotations

from pathlib import Path

from src.api.auth import get_store
from src.config.settings import settings
from src.task_context import TaskContextRepository, TaskTemplateDraft
from tests.test_web_source_delivery_api import (
    CoverageAwareWebPiRuntime,
    _client,
    _seed_snapshot,
)
from tests.test_pi_runtime_workspace_api import _wait_for_delivery


def _seed_context_options() -> tuple[int, int]:
    repository = TaskContextRepository(settings.webui_db_path)
    repository.save_template(
        "user-a",
        TaskTemplateDraft(
            template_id="public-company-summary",
            version=1,
            title="公开公司摘要",
            source="owner_created",
            purpose="web_research",
            goal_contract_draft="按公司提取名称、主营业务和逐项来源证据",
            delivery_spec_draft={"formats": ["json"]},
            method_draft="逐页读取、按公司去重，再核对引用",
        ),
    )
    current = get_store().memory_add(
        "user-a",
        "公司名使用官网全称",
        purpose="web_research",
    )
    other = get_store().memory_add(
        "user-b",
        "不应被 user-a 看到",
        purpose="web_research",
    )
    return int(current["id"]), int(other["id"])


def test_context_preview_lists_only_current_owner_and_does_not_create_task(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    memory_id, other_memory_id = _seed_context_options()

    with client:
        options = client.get(
            "/api/semantic-workspace/context-options?purpose=web_research"
        )
        assert options.status_code == 200, options.text
        assert [item["template_id"] for item in options.json()["templates"]] == [
            "public-company-summary"
        ]
        assert [item["memory_id"] for item in options.json()["memories"]] == [
            memory_id
        ]
        preview = client.post(
            "/api/semantic-workspace/context-preview",
            json={
                "purpose": "web_research",
                "objective_text": "总结当前公开网页",
                "output_formats": ["json"],
                "selection": {
                    "template": {
                        "template_id": "public-company-summary",
                        "version": 1,
                    },
                    "memories": [{"memory_id": memory_id}],
                },
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["proposed_changes"]["goal_contract"].startswith(
            "按公司提取"
        )
        assert get_store().list_semantic_workspace_tasks("user-a") == []
        forbidden = client.post(
            "/api/semantic-workspace/context-preview",
            json={
                "objective_text": "总结当前公开网页",
                "output_formats": ["json"],
                "selection": {"memories": [{"memory_id": other_memory_id}]},
            },
        )
        assert forbidden.status_code == 404


def test_confirmed_context_is_frozen_with_web_revision_and_reaches_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    memory_id, _other_memory_id = _seed_context_options()
    snapshot_id, _content = _seed_snapshot(Path(settings.webui_db_path))
    selection = {
        "template": {"template_id": "public-company-summary", "version": 1},
        "memories": [{"memory_id": memory_id}],
    }

    with client:
        preview = client.post(
            "/api/semantic-workspace/context-preview",
            json={
                "objective_text": "总结当前公开网页",
                "output_formats": ["json"],
                "selection": selection,
            },
        )
        assert preview.status_code == 200, preview.text
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "总结当前公开网页",
                "source_snapshot_id": snapshot_id,
                "quantity_requirement": "当前页面中有证据的全部内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
                "context_selection": selection,
                "context_preview_sha256": preview.json()["preview_sha256"],
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        detail = _wait_for_delivery(client, task_id)

        assert detail["task_context"]["template"]["version"] == 1
        assert detail["task_context"]["memories"][0]["memory_id"] == memory_id
        assert runtime.requests[0].compiled_context is not None
        assert runtime.requests[0].compiled_context.task_id == task_id
        assert runtime.requests[0].compiled_context.revision == 1
        assert "公司名使用官网全称" in runtime.requests[0].compiled_context.content
        assert "逐页读取、按公司去重" in runtime.requests[0].compiled_context.content


def test_web_task_rejects_context_hash_not_shown_to_user(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    memory_id, _other_memory_id = _seed_context_options()
    snapshot_id, _content = _seed_snapshot(Path(settings.webui_db_path))

    with client:
        response = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "总结当前公开网页",
                "source_snapshot_id": snapshot_id,
                "quantity_requirement": "当前页面中有证据的全部内容",
                "completeness_requirement": "仅对当前精确页面负责",
                "output_formats": ["json"],
                "runtime_version": "pi",
                "provider": "local",
                "context_selection": {"memories": [{"memory_id": memory_id}]},
                "context_preview_sha256": "sha256:" + "0" * 64,
            },
        )
        assert response.status_code == 409
        assert "重新检查" in response.json()["detail"]
