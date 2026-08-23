# -*- coding: utf-8 -*-
"""AC-07-08 S5：新任务选择过滤与冻结拦截（HTTP 层）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager

from src.api import semantic_workspace_runtime as runtime_mod
from src.api.routes import semantic_workspace
from src.api.routes.semantic_workspace import (
    _selectable_for_task,
)
from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)
from src.config.settings import settings


class _FakePiRuntime:
    """替身 Pi Runtime：S5 只测列表与冻结，不进入任务执行。"""

    async def start(self, request):
        raise AssertionError("选择门测试不应启动容器任务")


def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: str = "admin",
) -> TestClient:
    from src.agentic_runtime.pi_runtime import PiRuntime
    import src.api.auth as auth_mod
    from src.api.auth import get_current_user

    monkeypatch.setattr(
        settings, "webui_db_path", str(tmp_path / "workspace.db")
    )
    monkeypatch.setattr(
        settings, "data_prep_upload_root", str(tmp_path / "uploads")
    )
    monkeypatch.setattr(
        settings,
        "semantic_execution_root",
        str(tmp_path / "executions"),
    )
    monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
    monkeypatch.setattr(auth_mod, "_store", None)
    # 每个测试重置门单例，避免沿用前一个测试的 DB 路径装配。
    import src.api.capability_governance_runtime as gate_runtime

    monkeypatch.setattr(gate_runtime, "_runtime_gate", None)
    manager = runtime_mod.SemanticWorkspaceManager(
        pi_runtime=_FakePiRuntime(),
    )
    monkeypatch.setattr(runtime_mod, "_manager", manager)

    @asynccontextmanager
    async def lifespan(_app):
        manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": role,
    }
    return TestClient(app)


def _save_platform_pack(
    db_path: Path,
    *,
    pack_id: str,
    version: str = "1.0.0",
    maturity: LegacyCapabilityMaturity = LegacyCapabilityMaturity.VERIFIED,
    digest_char: str = "a",
) -> str:
    from src.capability_catalog import SqliteCapabilityCatalogRepository

    digest = "sha256:" + digest_char * 64
    SqliteCapabilityCatalogRepository(str(db_path)).save_pack(
        CapabilityPack(
            pack_id=pack_id,
            version=version,
            digest=digest,
            scope=ProcedureScope.PLATFORM,
            maturity=maturity,
            manifest=(
                ("display_name", "平台能力"),
                ("purpose", "平台治理样本"),
                ("kind", "tool"),
            ),
        )
    )
    return digest


def _save_personal_pack(
    db_path: Path,
    *,
    owner_id: str = "user-a",
    digest_char: str = "b",
) -> str:
    from src.capability_catalog import (
        CapabilityCatalog,
        CatalogActor,
        SqliteCapabilityCatalogRepository,
    )

    digest = "sha256:" + digest_char * 64
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(str(db_path))
    )
    catalog.register_pack(
        CatalogActor(owner_id=owner_id, role="user"),
        CapabilityPack(
            pack_id="private-a",
            version="1.0.0",
            digest=digest,
            scope=ProcedureScope.PERSONAL,
            maturity=LegacyCapabilityMaturity.DRAFT,
            owner_id=owner_id,
        ),
    )
    return digest


def _governance_schema(db_path: Path) -> None:
    """建与迁移 0001+0004 一致的治理事件表（含 event_type 列）。"""
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS capability_governance_events ("
            "event_id TEXT NOT NULL PRIMARY KEY,"
            "owner_key TEXT NOT NULL,"
            "scope TEXT NOT NULL,"
            "pack_id TEXT NOT NULL,"
            "version TEXT NOT NULL,"
            "digest TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL,"
            "event_type TEXT NOT NULL,"
            "payload_json TEXT NOT NULL,"
            "occurred_at TEXT NOT NULL,"
            "UNIQUE (owner_key, pack_id, version, digest, idempotency_key)"
            ")"
        )


def _promote_personal(db_path: Path, digest: str) -> None:
    from src.capability_governance import (
        SqliteCapabilityGovernanceRepository,
    )

    _governance_schema(db_path)
    SqliteCapabilityGovernanceRepository(str(db_path)).save_promotion_event(
        CapabilityGovernanceEvent(
            event_type="promoted_to_verified",
            idempotency_key="promotion:run-a",
            target=CapabilityGovernanceTarget(
                owner_id="user-a",
                scope=ProcedureScope.PERSONAL,
                pack_id="private-a",
                version="1.0.0",
                digest=digest,
            ),
            maturity=CapabilityMaturity.VERIFIED,
            actor_id="user-a",
            actor_role="user",
            source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
            source_supply_chain_evidence_id="supply_" + "a" * 20,
        )
    )


def _create_payload(refs: list[dict], upload_ids: list[str]) -> dict:
    return {
        "objective_text": "汇总并输出 JSON",
        "upload_ids": upload_ids,
        "output_formats": ["json"],
        "runtime_version": "pi",
        "permission_profile": "standard",
        "provider": "local",
        "capability_pack_refs": refs,
    }


def _upload(tmp_path: Path) -> str:
    from src.api.semantic_workspace_runtime import _upload_store

    upload = _upload_store().save_bytes(
        "user-a",
        "补充信息.csv",
        b"name,value\nx,1\n",
        media_type="text/csv",
    )
    return upload.upload_id


class TestS5SelectionPredicate:
    def test_deprecated_revoked_quarantined_not_selectable(self) -> None:
        target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id="p",
            version="1.0.0",
            digest="sha256:" + "a" * 64,
        )
        base = dict(
            target=target,
            maturity=CapabilityMaturity.VERIFIED,
            lifecycle=CapabilityLifecycle.ACTIVE,
            eligibility=CapabilityEligibility.ELIGIBLE,
            source="governance_event",
            audience="admin_gray",
        )
        assert _selectable_for_task(CapabilityGovernanceProjection(**base))
        assert not _selectable_for_task(
            CapabilityGovernanceProjection(
                **{**base, "lifecycle": CapabilityLifecycle.DEPRECATED}
            )
        )
        assert not _selectable_for_task(
            CapabilityGovernanceProjection(
                **{**base, "lifecycle": CapabilityLifecycle.REVOKED}
            )
        )
        assert not _selectable_for_task(
            CapabilityGovernanceProjection(
                **{
                    **base,
                    "eligibility": CapabilityEligibility.QUARANTINED,
                }
            )
        )
        assert not _selectable_for_task(
            CapabilityGovernanceProjection(
                **{**base, "maturity": CapabilityMaturity.DRAFT}
            )
        )


class TestS5CapabilitiesList:
    def test_deprecated_platform_pack_hidden_from_selection(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path, monkeypatch)
        _save_platform_pack(
            tmp_path / "workspace.db",
            pack_id="gray-python-table",
        )
        _save_platform_pack(
            tmp_path / "workspace.db",
            pack_id="gray-legacy-deprecated",
            maturity=LegacyCapabilityMaturity.DEPRECATED,
            digest_char="d",
        )
        with client:
            listed = client.get("/api/semantic-workspace/capabilities")
        assert listed.status_code == 200, listed.text
        pack_ids = [item["pack_id"] for item in listed.json()["items"]]
        assert "gray-python-table" in pack_ids
        # deprecated 不进入新任务选择列表（AC3）。
        assert "gray-legacy-deprecated" not in pack_ids

    def test_draft_personal_pack_hidden_from_selection(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path, monkeypatch)
        _save_personal_pack(tmp_path / "workspace.db")
        with client:
            listed = client.get("/api/semantic-workspace/capabilities")
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == []


class TestS5FreezeGate:
    def test_freeze_draft_personal_pack_rejected(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path, monkeypatch)
        digest = _save_personal_pack(tmp_path / "workspace.db")
        with client:
            created = client.post(
                "/api/semantic-workspace/tasks",
                json=_create_payload(
                    [
                        {
                            "pack_id": "private-a",
                            "version": "1.0.0",
                            "digest": digest,
                        }
                    ],
                    [_upload(tmp_path)],
                ),
            )
        # draft 个人 Pack 冻结被门拒绝（fail-closed）。
        assert created.status_code == 409, created.text

    def test_freeze_verified_personal_pack_allowed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path, monkeypatch)
        digest = _save_personal_pack(tmp_path / "workspace.db")
        _promote_personal(tmp_path / "workspace.db", digest)
        with client:
            created = client.post(
                "/api/semantic-workspace/tasks",
                json=_create_payload(
                    [
                        {
                            "pack_id": "private-a",
                            "version": "1.0.0",
                            "digest": digest,
                        }
                    ],
                    [_upload(tmp_path)],
                ),
            )
        assert created.status_code == 202, created.text

    def test_freeze_digest_mismatch_is_422(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A3：引用 digest 与目录 Pack 不一致是调用方输入错误，保持 422。"""
        client = _client(tmp_path, monkeypatch)
        digest = _save_personal_pack(tmp_path / "workspace.db")
        _promote_personal(tmp_path / "workspace.db", digest)
        wrong_digest = "sha256:" + "f" * 64
        with client:
            created = client.post(
                "/api/semantic-workspace/tasks",
                json=_create_payload(
                    [
                        {
                            "pack_id": "private-a",
                            "version": "1.0.0",
                            "digest": wrong_digest,
                        }
                    ],
                    [_upload(tmp_path)],
                ),
            )
        assert created.status_code == 422, created.text
        assert "digest" in created.json()["detail"]

    def test_freeze_deprecated_pack_rejected(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A5：装载门放行 DEPRECATED 是为历史恢复；冻结是新任务入口，
        必须被可选谓词拦截（AC3：deprecated 不进入新任务选择）。"""
        client = _client(tmp_path, monkeypatch)
        digest = _save_platform_pack(
            tmp_path / "workspace.db",
            pack_id="gray-legacy-deprecated",
            maturity=LegacyCapabilityMaturity.DEPRECATED,
            digest_char="d",
        )
        with client:
            created = client.post(
                "/api/semantic-workspace/tasks",
                json=_create_payload(
                    [
                        {
                            "pack_id": "gray-legacy-deprecated",
                            "version": "1.0.0",
                            "digest": digest,
                        }
                    ],
                    [_upload(tmp_path)],
                ),
            )
        # legacy 装载路径放行（历史恢复），但冻结被可选谓词拒绝。
        assert created.status_code == 409, created.text
        assert "新任务" in created.json()["detail"]


class TestS6RecommendedPointer:
    def test_recommended_version_marks_and_sorts(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """回滚指针：recommendation_changed 折叠出的推荐版本标记并置顶。"""
        client = _client(tmp_path, monkeypatch)
        db_path = tmp_path / "workspace.db"
        _save_platform_pack(
            db_path,
            pack_id="gray-python-table",
            version="1.0.0",
            digest_char="a",
        )
        _save_platform_pack(
            db_path,
            pack_id="gray-python-table",
            version="2.0.0",
            digest_char="c",
        )
        _governance_schema(db_path)
        from src.capability_governance import (
            SqliteCapabilityGovernanceRepository,
        )

        SqliteCapabilityGovernanceRepository(str(db_path)).save_governance_event(
            CapabilityGovernanceEvent(
                event_type="recommendation_changed",
                idempotency_key="recommend:rollback",
                # 指针挂在被推荐的目标版本上（与 rollback 命令语义一致）。
                target=CapabilityGovernanceTarget(
                    owner_id=None,
                    scope=ProcedureScope.PLATFORM,
                    pack_id="gray-python-table",
                    version="1.0.0",
                    digest="sha256:" + "a" * 64,
                ),
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id="admin-a",
                actor_role="admin",
                reason="回滚：推荐切回 1.0.0",
                recommended_version="1.0.0",
            )
        )
        with client:
            listed = client.get("/api/semantic-workspace/capabilities")
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        versions = [item["version"] for item in items]
        assert "1.0.0" in versions and "2.0.0" in versions
        # 推荐版本标记并置顶。
        assert items[0]["version"] == "1.0.0"
        assert items[0]["recommended"] is True
        assert items[1]["recommended"] is False

    def test_no_pointer_marks_nothing(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(tmp_path, monkeypatch)
        _save_platform_pack(
            tmp_path / "workspace.db",
            pack_id="gray-python-table",
        )
        with client:
            listed = client.get("/api/semantic-workspace/capabilities")
        items = listed.json()["items"]
        assert all(item["recommended"] is False for item in items)

    def test_freeze_ac06_legacy_platform_pack_still_allowed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-06 历史灰度包（无发布事件）冻结保持旧路径，直至 #17 切换。"""
        client = _client(tmp_path, monkeypatch)
        digest = _save_platform_pack(
            tmp_path / "workspace.db",
            pack_id="gray-python-table",
        )
        with client:
            created = client.post(
                "/api/semantic-workspace/tasks",
                json=_create_payload(
                    [
                        {
                            "pack_id": "gray-python-table",
                            "version": "1.0.0",
                            "digest": digest,
                        }
                    ],
                    [_upload(tmp_path)],
                ),
            )
        assert created.status_code == 202, created.text

    def test_freeze_platform_pack_with_publication_requires_signing(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有发布事件的平台 Pack 冻结必须通过签名门；验证器缺失时失败关闭。"""
        from src.capability_governance import (
            SqliteCapabilityGovernanceRepository,
        )

        client = _client(tmp_path, monkeypatch)
        digest = _save_platform_pack(
            tmp_path / "workspace.db",
            pack_id="gray-python-table",
        )
        _governance_schema(tmp_path / "workspace.db")
        SqliteCapabilityGovernanceRepository(
            str(tmp_path / "workspace.db")
        ).save_platform_event(
            CapabilityGovernanceEvent(
                event_type="platform_published",
                idempotency_key="publish:sha256-a",
                target=CapabilityGovernanceTarget(
                    owner_id=None,
                    scope=ProcedureScope.PLATFORM,
                    pack_id="gray-python-table",
                    version="1.0.0",
                    digest=digest,
                ),
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="admin-a",
                actor_role="admin",
                reason="发布：六步验证与签名全部通过",
                source_digest="sha256:" + "b" * 64,
                platform_digest=digest,
                audience="admin_gray",
                platform_validation_run_id="pfval_" + "a" * 20,
                signing_signature_digest="sha256:" + "c" * 64,
                signing_public_key_sha256="d" * 64,
            )
        )
        with client:
            created = client.post(
                "/api/semantic-workspace/tasks",
                json=_create_payload(
                    [
                        {
                            "pack_id": "gray-python-table",
                            "version": "1.0.0",
                            "digest": digest,
                        }
                    ],
                    [_upload(tmp_path)],
                ),
            )
        # 签名验证器缺失/失败 → 拒绝冻结（fail-closed，不降级）。
        assert created.status_code == 409, created.text
        assert "签名" in created.json()["detail"]
