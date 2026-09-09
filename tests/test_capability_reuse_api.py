"""能力复用灰度 HTTP 接缝；隔离库与本地替身，不发发现请求。"""
import pytest
from fastapi import HTTPException

from src.config.settings import settings
from src.api.routes import semantic_workspace as routes
from tests.test_pi_runtime_workspace_api import _client, _uploads, FakePiRuntime
from tests.test_capability_reuse import need


def setup(tmp_path, monkeypatch, role="admin"):
    from src.capability_catalog import SqliteCapabilityCatalogRepository
    from src.conversation_steering import CapabilityPack, ProcedureScope, CapabilityMaturity
    from src.semantic_harness.capabilities import TABLE_DUCKDB_MANIFEST
    client = _client(tmp_path, monkeypatch, role=role, pi_runtime=FakePiRuntime())
    monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
    ref = dict(pack_id="table.duckdb", version="1.0.0", digest="sha256:" + "a" * 64)
    SqliteCapabilityCatalogRepository(settings.webui_db_path).save_pack(CapabilityPack(
        **ref, scope=ProcedureScope.PERSONAL, owner_id="user-a", maturity=CapabilityMaturity.VERIFIED,
        source_provenance=("https://github.com/duckdb/duckdb",),
        manifest=(("reuse_contract", TABLE_DUCKDB_MANIFEST.model_dump_json()), ("license", "MIT")),
    ))
    return client, ref


def test_resolve_is_admin_only(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch, "user")
    response = client.post("/api/semantic-workspace/capabilities/resolve", json={"need": need().model_dump(mode="json")})
    assert response.status_code == 403


def test_second_resolve_uses_governed_match_without_discovery(tmp_path, monkeypatch):
    client, ref = setup(tmp_path, monkeypatch)
    checked = []
    monkeypatch.setattr(routes, "_check_freeze_gate", lambda actor, refs, catalog, **kwargs: checked.extend(refs))
    async def no_discovery(*args, **kwargs):
        pytest.fail("命中不得外部发现")
    monkeypatch.setattr(routes, "discover_open_source_candidates", no_discovery, raising=False)
    for _ in range(2):
        response = client.post("/api/semantic-workspace/capabilities/resolve", json={"need": need().model_dump(mode="json"), "allow_discovery": True})
        assert response.status_code == 200, response.text
        assert response.json()["matches"][0]["ref"] == ref
        assert response.json()["matches"][0]["health"] == "not_checked"
    assert len(checked) == 2

def test_rejected_governance_never_returns_reusable_match(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch)
    def denied(*args, **kwargs):
        raise HTTPException(409, "已撤销")
    monkeypatch.setattr(routes, "_check_freeze_gate", denied)
    response = client.post("/api/semantic-workspace/capabilities/resolve", json={"need": need().model_dump(mode="json")})
    assert response.status_code == 200
    assert response.json()["matches"] == []
    assert response.json()["gaps"][0]["code"] == "governance_rejected"


def test_create_ref_must_match_need_before_task_execution(tmp_path, monkeypatch):
    client, ref = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "_check_freeze_gate", lambda *args, **kwargs: None)
    _, table = _uploads(tmp_path)
    response = client.post("/api/semantic-workspace/tasks", json={
        "objective_text": "筛选资料", "upload_ids": [table], "output_formats": ["json"],
        "runtime_version": "pi", "provider": "local", "capability_pack_refs": [ref],
        "capability_need": {**need().model_dump(mode="json"), "operations": ["translate"]},
    })
    assert response.status_code == 409, response.text

def test_create_auto_selects_and_records_identity_without_purpose(tmp_path, monkeypatch):
    from src.api.auth import get_store
    from src.capability_catalog import CapabilityCatalog, SqliteCapabilityCatalogRepository
    from src.capability_catalog.models import CatalogActor
    client, ref = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "_check_freeze_gate", lambda *args, **kwargs: None)
    _, table = _uploads(tmp_path)
    purpose = "本地敏感用途不进入事件"
    for _ in range(2):
        response = client.post("/api/semantic-workspace/tasks", json={
            "objective_text": "筛选资料", "upload_ids": [table], "output_formats": ["parquet"],
            "runtime_version": "pi", "provider": "local", "capability_need": {**need().model_dump(mode="json"), "purpose": purpose},
        })
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        selected = CapabilityCatalog(SqliteCapabilityCatalogRepository(settings.webui_db_path)).resolve_selection(CatalogActor(owner_id="user-a", role="admin"), task_id=task_id, revision=1)
        assert selected.pack_refs[0].model_dump() == ref
        events = get_store().list_semantic_workspace_events("user-a", task_id)
        event = next(item for item in events if item["event_type"] == "capability_reuse_planned")
        assert event["details"]["matches"][0]["ref"] == ref
        assert purpose not in str(event)


@pytest.mark.parametrize("stale", ["input", "output"])
def test_create_rejects_need_from_previous_files_or_outputs(tmp_path, monkeypatch, stale):
    client, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "_check_freeze_gate", lambda *args, **kwargs: None)
    document, table = _uploads(tmp_path)
    response = client.post("/api/semantic-workspace/tasks", json={
        "objective_text": "筛选资料", "upload_ids": [document if stale == "input" else table],
        "output_formats": ["json" if stale == "output" else "parquet"],
        "runtime_version": "pi", "provider": "local", "capability_need": need().model_dump(mode="json"),
    })
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_discovery_requires_explicit_permission_after_governance_rejection(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch)
    def denied(*args, **kwargs):
        raise HTTPException(409, "已撤销")
    monkeypatch.setattr(routes, "_check_freeze_gate", denied)
    calls = []
    async def discovery(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "needs_confirmation", "candidates": [], "gaps": []}
    monkeypatch.setattr(routes, "discover_open_source_candidates", discovery)
    result = await routes.resolve_gray_capabilities(routes.CapabilityResolveRequest(need=need()), user={"user_id": "user-a", "role": "admin"})
    assert not result["matches"] and calls == []
    await routes.resolve_gray_capabilities(routes.CapabilityResolveRequest(need=need(), allow_discovery=True), user={"user_id": "user-a", "role": "admin"})
    assert calls == [{"allow_discovery": True}]

def test_resolve_runs_full_mount_gate_not_only_catalog_visibility(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.capability_catalog.runtime_gate import CapabilityMountGateRejected
    client, ref = setup(tmp_path, monkeypatch)
    calls = []
    def check(actor, pack, **kwargs):
        calls.append(pack.pack_id)
        raise CapabilityMountGateRejected(**ref, reason="revoked")
    monkeypatch.setattr(routes, "_runtime_gate", lambda: SimpleNamespace(check_mount=check))
    monkeypatch.setattr(routes, "_runtime_gate_projection_governance", lambda: SimpleNamespace())
    response = client.post("/api/semantic-workspace/capabilities/resolve", json={"need": need().model_dump(mode="json")})
    assert response.status_code == 200 and response.json()["matches"] == []
    assert calls == ["table.duckdb"]
