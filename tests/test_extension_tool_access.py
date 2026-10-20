"""扩展工具的产品边界：拒绝普通用户及管理员，保留超级管理员入口。"""
import re
from types import SimpleNamespace

import pytest

from src.api.routes import capability_governance, models
from src.config.settings import settings
from tests.test_capability_reuse import need
from tests.test_pi_runtime_workspace_api import _client, _uploads, FakePiRuntime
from tests.test_workspace_revision_safety import proposed


def extension_client(tmp_path, monkeypatch, role):
    client = _client(tmp_path, monkeypatch, role=role, pi_runtime=FakePiRuntime())
    client.app.include_router(capability_governance.router)
    client.app.include_router(capability_governance.admin_router)
    client.app.include_router(models.router)
    return client


@pytest.mark.parametrize("role", ["user", "admin"])
def test_non_super_roles_cannot_access_extension_surfaces(tmp_path, monkeypatch, role):
    client = extension_client(tmp_path, monkeypatch, role)
    monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
    monkeypatch.setattr(capability_governance, "_governance", lambda: pytest.fail("无权限请求不能读取工具治理数据"))
    # 覆盖整组路由，防止后续新增治理操作漏掉权限依赖。
    checked = 0
    for route in [*capability_governance.router.routes, *capability_governance.admin_router.routes]:
        if not getattr(route, "path", "").startswith("/api/capability-governance"):
            continue
        path = re.sub(r"\{[^}]+\}", "not-accessible", route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code == 403, (method, path, response.text)
            checked += 1
    assert checked > 10
    assert client.get("/api/semantic-workspace/capabilities").status_code == 403
    assert client.get("/api/semantic-workspace/tasks/not-accessible/capabilities").status_code == 403
    response = client.post("/api/semantic-workspace/capabilities/resolve", json={"need": need().model_dump(mode="json")})
    assert response.status_code == 403
    _, table = _uploads(tmp_path)
    response = client.post("/api/semantic-workspace/tasks", json={
        "objective_text": "汇总记录", "upload_ids": [table], "output_formats": ["json"],
        "runtime_version": "pi", "provider": "local",
        "capability_pack_refs": [{"pack_id": "table-tool", "version": "1.0.0", "digest": "sha256:" + "a" * 64}],
    })
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["user", "admin", "super_admin"])
def test_extension_availability_matches_role(tmp_path, monkeypatch, role):
    client = extension_client(tmp_path, monkeypatch, role)
    monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
    monkeypatch.setattr(models, "list_models", lambda: {})
    monkeypatch.setattr(models, "available_providers", lambda: [])
    assert client.get("/api/models").json()["pi_capability_host_enabled"] is (role == "super_admin")
    monkeypatch.setattr(capability_governance, "_governance", lambda: SimpleNamespace(list_visible_projections=lambda actor: []))
    response = client.get("/api/capability-governance/packs")
    assert response.status_code == (200 if role == "super_admin" else 403)
    if role == "super_admin":
        assert client.get("/api/semantic-workspace/capabilities").status_code == 200


@pytest.mark.parametrize("role", ["user", "admin"])
@pytest.mark.parametrize("mode", ["cancel_now", "new_task", "after_safe_point", "revision"])
def test_old_tool_selection_cannot_be_inherited_after_demotion(proposed, role, mode):
    from src.api.auth import get_current_user
    from src.capability_catalog import SqliteCapabilityCatalogRepository
    from src.capability_catalog.models import CapabilitySelection, CapabilityPackRef

    client, store, manager, path = proposed
    # 模拟已经冻结过扩展工具的历史版本；不需物化制品或调用模型。
    SqliteCapabilityCatalogRepository(store.db_path).save_selection(CapabilitySelection(
        owner_id="user-a", task_id="safe-revision", revision=1,
        pack_refs=(CapabilityPackRef(pack_id="historical-tool", version="1.0.0", digest="sha256:" + "a" * 64),),
    ))
    client.app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a", "role": role, "execution_generation": 0,
    }
    if mode == "revision":
        response = client.post("/api/semantic-workspace/tasks/safe-revision/revisions",
            json={"instruction": "重新执行", "expected_active_revision": 1},
            headers={"Idempotency-Key": "demoted-revision"})
    else:
        response = client.post(path, json={"mode": mode})
    assert response.status_code == 403, response.text
    assert manager.cancelled == [] and manager.enqueued == []
    assert store.get_semantic_workspace_task("user-a", "safe-revision")["status"] == "running"
    with store._conn() as connection:
        assert connection.execute("SELECT count(*) FROM semantic_workspace_tasks").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM semantic_workspace_revisions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM conversation_revision_decisions").fetchone()[0] == 0
