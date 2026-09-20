"""记忆写入到工作台执行请求及历史读取，模型仅使用隔离替身。"""
from pathlib import Path

from src.api.routes import memory_routes
from src.config.settings import settings
from src.memory import loader
from tests.test_web_source_delivery_api import _client, _seed_snapshot, CoverageAwareWebPiRuntime
from tests.test_pi_runtime_workspace_api import _uploads, _wait_for_delivery


def test_explicit_preview_includes_global_memory_and_failed_read_can_retry(tmp_path, monkeypatch):
    pref_dir = tmp_path / "preferences"
    monkeypatch.setattr(loader, "MEMORY_DIR", pref_dir)
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    client.app.include_router(memory_routes.router)
    document, _ = _uploads(tmp_path)
    base = "/api/semantic-workspace"
    with client:
        assert client.post("/api/memory", json={"text": "报告保留来源"}).status_code == 200
        preview = client.post(base + "/context-preview", json={"objective_text": "汇总费用", "output_formats": ["json"], "selection": {}})
        assert preview.status_code == 200, preview.text
        assert "报告保留来源" in preview.json()["compiled_context"]["content"]
        # 真实文件系统故障，不替换产品内部方法。
        pref_file = pref_dir / "user-preferences.md"
        pref_file.unlink()
        pref_file.mkdir()
        loader._preferences_cache.invalidate()
        payload = {"objective_text": "汇总费用", "upload_ids": [document], "output_formats": ["json"], "runtime_version": "pi", "provider": "local"}
        response = client.post(base + "/tasks", json=payload, headers={"Idempotency-Key": "memory-unavailable"})
        assert response.status_code == 503, response.text
        pref_file.rmdir()
        retry = client.post(base + "/tasks", json=payload, headers={"Idempotency-Key": "memory-unavailable"})
        assert retry.status_code == 202, retry.text
        _wait_for_delivery(client, retry.json()["task_id"])


def test_memory_reaches_execution_and_history_keeps_frozen_version(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "preferences")
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    client.app.include_router(memory_routes.router)
    document, _ = _uploads(tmp_path)
    base = "/api/semantic-workspace"
    with client:
        response = client.post("/api/memory/self", json={"text": "费用按部门分组，缺失金额不得补猜"})
        assert response.status_code == 200, response.text
        memory_id = response.json()["item"]["id"]
        assert client.post("/api/memory", json={"text": "报告必须列出来源"}).status_code == 200
        created = client.post(base + "/tasks", json={"objective_text": "汇总费用", "upload_ids": [document], "output_formats": ["json"], "runtime_version": "pi", "provider": "local"})
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        detail = _wait_for_delivery(client, task_id)
        assert "缺失金额不得补猜" in runtime.requests[0].compiled_context.content
        assert "报告必须列出来源" in runtime.requests[0].compiled_context.content
        assert detail["task_context"]["memories"][0]["memory_id"] == memory_id
        frozen = detail["task_context"]
        assert client.delete(f"/api/memory/self/{memory_id}").status_code == 200
        pref = client.get("/api/memory").json()
        assert client.patch("/api/memory", json={"text": "新规范", "expected_digest": pref["preferences_digest"]}).status_code == 200
        assert client.get(base + f"/tasks/{task_id}").json()["task_context"] == frozen
        snapshot, _ = _seed_snapshot(Path(settings.webui_db_path))
        next_task = client.post(base + "/tasks", json={"objective_text": "汇总费用", "source_snapshot_ids": [snapshot], "output_formats": ["json"], "runtime_version": "pi", "provider": "local", "quantity_requirement": "当前证据", "completeness_requirement": "披露缺口"})
        assert next_task.status_code == 202, next_task.text
        new_detail = _wait_for_delivery(client, next_task.json()["task_id"])
        assert new_detail["task_context"]["memories"] == []
        assert "缺失金额不得补猜" not in runtime.requests[-1].compiled_context.content
        assert "新规范" in runtime.requests[-1].compiled_context.content
