"""显式上下文保留用户选择，自动教训不跨目标继承。"""
import sqlite3
from pathlib import Path

import pytest

from src.memory import lessons
from src.task_context import TaskContextSelection, MemorySelection
from tests.test_task_context import _service


def test_selected_memory_kept_but_lessons_do_not_follow_changed_goal(tmp_path):
    service, repository, store = _service(tmp_path)
    lessons.LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    path = lessons.LESSONS_DIR / "expense.md"
    path.write_text("---\nowner_id: user-a\nscope: owner\nstatus: active\ntitle: 费用提醒\n"
        "data_type: workspace_document\nkeywords: [费用]\n---\n缺失费用不得补猜", encoding="utf-8")
    memory = store.memory_add("user-a", "金额保留两位小数", purpose="general")
    selected = service.preview(owner_id="user-a", purpose="general", objective_text="汇总费用",
        output_formats=("json",), selection=TaskContextSelection(memories=(MemorySelection(memory_id=memory["id"]),)))
    args = dict(owner_id="user-a", purpose="general", objective_text="汇总费用",
        output_formats=("json",), data_type="workspace_document", confirmed_preview=selected)
    with pytest.raises(ValueError, match="不一致"):
        service.automatic_preview(**{**args, "owner_id": "user-b"})
    combined = service.automatic_preview(**args)
    assert combined.memories == selected.memories and combined.proposed_changes == selected.proposed_changes
    assert combined.automatically_selected is False
    assert "金额保留两位小数" in combined.compiled_context.content
    assert "缺失费用不得补猜" in combined.compiled_context.content
    with sqlite3.connect(repository.database) as connection:
        service.freeze(connection, owner_id="user-a", task_id="explicit-task", revision=1,
            preview=combined, expected_preview_sha256=combined.preview_sha256, require_current=True)
    path.unlink()
    for goal, formats, keep in [("汇总费用", ("json",), True), ("比较合同", ("json",), False),
                                ("汇总费用", ("csv",), False)]:
        carried = service.carry_forward(owner_id="user-a", source_task_id="explicit-task", source_revision=1,
            target_task_id="explicit-task", target_revision=2, objective_text=goal, output_formats=formats)
        assert carried.memories == selected.memories
        assert bool(carried.lessons) is keep
        assert ("缺失费用不得补猜" in carried.compiled_context.content) is keep
    assert repository.get_frozen("user-a", "explicit-task", 1).lessons == combined.lessons
    assert service.automatic_preview(**args) == selected


def test_explicit_web_context_keeps_lessons_on_source_refresh(tmp_path, monkeypatch):
    from src.config.settings import settings
    from tests.test_web_source_delivery_api import _client, _seed_snapshot
    from tests.test_pi_runtime_workspace_api import _wait_for_delivery

    client = _client(tmp_path, monkeypatch, role="admin")
    root = tmp_path / "lessons"
    root.mkdir()
    monkeypatch.setattr(lessons, "LESSONS_DIR", root)
    path = root / "expense.md"
    path.write_text("---\nowner_id: user-a\nscope: owner\nstatus: active\ntitle: 费用提醒\n"
        "data_type: workspace_web\nkeywords: [费用]\n---\n缺失费用不得补猜", encoding="utf-8")
    snapshot, _ = _seed_snapshot(Path(settings.webui_db_path))
    base = "/api/semantic-workspace"
    with client:
        preview = client.post(base + "/context-preview", json={"objective_text": "汇总费用",
            "output_formats": ["json"], "selection": {}})
        assert preview.status_code == 200, preview.text
        response = client.post(base + "/tasks", json={"objective_text": "汇总费用",
            "output_formats": ["json"], "runtime_version": "pi", "provider": "local",
            "source_snapshot_ids": [snapshot], "quantity_requirement": "当前证据",
            "completeness_requirement": "披露缺口", "context_selection": {},
            "context_preview_sha256": preview.json()["preview_sha256"]})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        detail = _wait_for_delivery(client, task_id)
        frozen = detail["task_context"]
        assert len(frozen["lessons"]) == 1
        path.unlink()
        revised = client.post(base + f"/tasks/{task_id}/revisions", json={
            "instruction": "刷新来源", "expected_active_revision": 1, "source_snapshot_id": snapshot})
        assert revised.status_code == 202, revised.text
        reopened = _wait_for_delivery(client, task_id)
        assert reopened["active_revision"] == 2
        assert reopened["task_context"]["lessons"] == frozen["lessons"]
