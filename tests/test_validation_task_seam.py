# -*- coding: utf-8 -*-
"""AC07-10 D9：验证任务 Seam（validation_target 豁免贯通冻结/装载/继承）。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.main import app
from src.api.routes import semantic_workspace as runtime_mod
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountResolver,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_catalog.models import CapabilitySelection
from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
)
from src.capability_governance.runtime_gate import CapabilityGovernanceRuntimeGate
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)
from src.config.settings import settings


def _personal_pack(owner: str = "owner-a") -> CapabilityPack:
    return CapabilityPack(
        pack_id="gray-python-table",
        version="2.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=LegacyCapabilityMaturity.DRAFT,
        owner_id=owner,
    )


def _target(owner: str = "owner-a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=owner,
        scope=ProcedureScope.PERSONAL,
        pack_id="gray-python-table",
        version="2.0.0",
        digest="sha256:" + "a" * 64,
    )


def _projection(
    *,
    maturity=CapabilityMaturity.DRAFT,
    lifecycle=CapabilityLifecycle.ACTIVE,
    eligibility=CapabilityEligibility.ELIGIBLE,
) -> CapabilityGovernanceProjection:
    return CapabilityGovernanceProjection(
        target=_target(),
        maturity=maturity,
        lifecycle=lifecycle,
        eligibility=eligibility,
        source="legacy_compat",  # type: ignore[arg-type]
        audience=None,
    )


def _actor() -> CatalogActor:
    return CatalogActor(owner_id="owner-a", role="user")


class TestD9GateExemption:
    def test_exempt_allows_own_draft_personal_pack(self) -> None:
        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(),
            platform_publication_for=lambda pack: None,
        )
        gate.check_mount(_actor(), _personal_pack(), validation_exempt=True)

    def test_draft_personal_pack_rejected_without_exempt(self) -> None:
        """现状回归：无豁免时 draft 个人包仍被拒绝（#13 语义不变）。"""
        import pytest

        from src.capability_catalog import CapabilityMountGateRejected

        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(),
            platform_publication_for=lambda pack: None,
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_actor(), _personal_pack())

    def test_exempt_still_rejects_owner_mismatch(self) -> None:
        import pytest

        from src.capability_catalog import CapabilityMountGateRejected

        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(),
            platform_publication_for=lambda pack: None,
        )
        with pytest.raises(CapabilityMountGateRejected, match="不属于当前用户"):
            gate.check_mount(
                CatalogActor(owner_id="owner-b", role="user"),
                _personal_pack(),
                validation_exempt=True,
            )

    def test_exempt_still_rejects_quarantined(self) -> None:
        import pytest

        from src.capability_catalog import CapabilityMountGateRejected

        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(
                eligibility=CapabilityEligibility.QUARANTINED
            ),
            platform_publication_for=lambda pack: None,
        )
        with pytest.raises(CapabilityMountGateRejected, match="运行资格"):
            gate.check_mount(_actor(), _personal_pack(), validation_exempt=True)

    def test_exempt_still_rejects_revoked(self) -> None:
        import pytest

        from src.capability_catalog import CapabilityMountGateRejected

        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(
                lifecycle=CapabilityLifecycle.REVOKED
            ),
            platform_publication_for=lambda pack: None,
        )
        with pytest.raises(CapabilityMountGateRejected, match="生命周期"):
            gate.check_mount(_actor(), _personal_pack(), validation_exempt=True)


class _RecordingGate:
    """记录门调用与豁免参数；不实际拒绝。"""

    def __init__(self):
        self.calls: list[bool] = []

    def check_mount(self, actor, pack, *, validation_exempt: bool = False):
        self.calls.append(validation_exempt)


class _RecordingStore:
    def materialize(self, *, artifact_name, version, digest, destination):
        Path(destination).mkdir(parents=True, exist_ok=True)
        return Path(destination)


class TestD9ResolverExemption:
    def _resolver_with_selection(
        self, tmp_path: Path, *, marker: bool
    ) -> tuple[CapabilityMountResolver, _RecordingGate]:
        repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(repository)
        actor = _actor()
        pack = _personal_pack()
        catalog.register_pack(actor, pack)
        ref = CapabilityPackRef(
            pack_id=pack.pack_id, version=pack.version, digest=pack.digest
        )
        catalog.freeze_selection(
            actor,
            task_id="task-v",
            revision=1,
            pack_refs=(ref,),
            validation_target=ref if marker else None,
        )
        gate = _RecordingGate()
        resolver = CapabilityMountResolver(
            catalog,
            _RecordingStore(),
            tmp_path / "mounts",
            runtime_gate=gate,
        )
        return resolver, gate

    def test_marker_selection_mounts_with_exempt(self, tmp_path) -> None:
        resolver, gate = self._resolver_with_selection(tmp_path, marker=True)
        mounts = resolver.resolve_for_owner("owner-a", "task-v", 1)
        assert len(mounts) == 1
        assert gate.calls == [True]

    def test_plain_selection_mounts_without_exempt(self, tmp_path) -> None:
        resolver, gate = self._resolver_with_selection(tmp_path, marker=False)
        resolver.resolve_for_owner("owner-a", "task-v", 1)
        assert gate.calls == [False]


class TestD9SelectionMarker:
    def test_selection_roundtrip_carries_marker(self) -> None:
        ref = CapabilityPackRef(
            pack_id="gray-python-table",
            version="2.0.0",
            digest="sha256:" + "a" * 64,
        )
        selection = CapabilitySelection(
            owner_id="owner-a",
            task_id="task-v",
            revision=1,
            pack_refs=(ref,),
            validation_target=ref,
        )
        assert selection.validation_target == ref
        # 默认无标记（既有 payload 零破坏）。
        plain = CapabilitySelection(
            owner_id="owner-a",
            task_id="task-v",
            revision=1,
            pack_refs=(ref,),
        )
        assert plain.validation_target is None

    def test_freeze_selection_persists_marker(self) -> None:
        repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(repository)
        actor = _actor()
        pack = _personal_pack()
        catalog.register_pack(actor, pack)
        ref = CapabilityPackRef(
            pack_id=pack.pack_id, version=pack.version, digest=pack.digest
        )
        catalog.freeze_selection(
            actor,
            task_id="task-v",
            revision=1,
            pack_refs=(ref,),
            validation_target=ref,
        )
        saved = catalog.resolve_selection(actor, task_id="task-v", revision=1)
        assert saved is not None and saved.validation_target == ref

    def test_copy_selection_drops_marker(self, tmp_path) -> None:
        repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(repository)
        actor = _actor()
        pack = _personal_pack()
        catalog.register_pack(actor, pack)
        ref = CapabilityPackRef(
            pack_id=pack.pack_id, version=pack.version, digest=pack.digest
        )
        catalog.freeze_selection(
            actor,
            task_id="task-v",
            revision=1,
            pack_refs=(ref,),
            validation_target=ref,
        )
        resolver = CapabilityMountResolver(
            catalog,
            _RecordingStore(),
            tmp_path / "mounts",
        )
        assert resolver.copy_selection_for_owner(
            "owner-a",
            source_task_id="task-v",
            source_revision=1,
            target_task_id="task-v2",
            target_revision=1,
        )
        inherited = catalog.resolve_selection(
            actor, task_id="task-v2", revision=1
        )
        assert inherited is not None
        # 验证豁免不继承：新 revision 的标记被丢弃。
        assert inherited.validation_target is None


class TestD9CreateTaskApi:
    def _prepare(self, tmp_path, monkeypatch, *, owner_id: str = "user-a"):
        """装配真实 sqlite 目录 + 个人 draft 包，返回 (client, pack_ref)。"""
        from src.capability_catalog import SqliteCapabilityCatalogRepository

        monkeypatch.setattr(
            settings, "webui_db_path", str(tmp_path / "workspace.db")
        )
        monkeypatch.setattr(
            settings, "data_prep_upload_root", str(tmp_path / "uploads")
        )
        monkeypatch.setattr(
            settings, "semantic_execution_root", str(tmp_path / "executions")
        )
        monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
        repository = SqliteCapabilityCatalogRepository(settings.webui_db_path)
        actor = CatalogActor(owner_id=owner_id, role="admin")
        pack = CapabilityPack(
            pack_id="gray-python-table",
            version="2.0.0",
            digest="sha256:" + "a" * 64,
            scope=ProcedureScope.PERSONAL,
            maturity=LegacyCapabilityMaturity.DRAFT,
            owner_id=owner_id,
        )
        repository.save_pack(pack)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": owner_id,
            "role": "admin",
        }
        client = TestClient(app)
        ref = {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "digest": pack.digest,
        }
        return client, ref

    def _uploads(self, tmp_path) -> str:
        from src.services.upload_store import UploadStore

        store = UploadStore(
            root=str(tmp_path / "uploads"), max_bytes=10 * 1024 * 1024
        )
        upload = store.save_bytes(
            "user-a",
            "补充信息.csv",
            b"name,value\nx,1\n",
            media_type="text/csv",
        )
        return upload.upload_id

    def test_create_task_with_validation_target_accepted(
        self, tmp_path, monkeypatch
    ) -> None:
        client, ref = self._prepare(tmp_path, monkeypatch)
        upload = self._uploads(tmp_path)
        response = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "按部门汇总金额并输出 JSON",
                "upload_ids": [upload],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
                "capability_pack_refs": [ref],
                "validation_target": ref,
            },
        )
        assert response.status_code == 202, response.text
        # selection 落库且携带标记。
        from src.capability_catalog import (
            CapabilityCatalog,
            SqliteCapabilityCatalogRepository,
        )

        catalog = CapabilityCatalog(
            SqliteCapabilityCatalogRepository(settings.webui_db_path)
        )
        selection = catalog.resolve_selection(
            CatalogActor(owner_id="user-a", role="admin"),
            task_id=response.json()["task_id"],
            revision=1,
        )
        assert selection is not None
        assert selection.validation_target is not None
        assert selection.validation_target.pack_id == "gray-python-table"

    def test_create_task_draft_rejected_without_target(
        self, tmp_path, monkeypatch
    ) -> None:
        """现状回归：无验证标记时 draft 个人包冻结仍 409。"""
        client, ref = self._prepare(tmp_path, monkeypatch)
        upload = self._uploads(tmp_path)
        response = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "按部门汇总金额并输出 JSON",
                "upload_ids": [upload],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
                "capability_pack_refs": [ref],
            },
        )
        assert response.status_code == 409, response.text

    def test_target_not_in_pack_refs_rejected(
        self, tmp_path, monkeypatch
    ) -> None:
        client, ref = self._prepare(tmp_path, monkeypatch)
        upload = self._uploads(tmp_path)
        response = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "按部门汇总金额并输出 JSON",
                "upload_ids": [upload],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
                "capability_pack_refs": [],
                "validation_target": ref,
            },
        )
        assert response.status_code in (404, 409, 422), response.text

    def test_platform_target_rejected(self, tmp_path, monkeypatch) -> None:
        """平台包不能作为验证目标（豁免仅限个人包）。"""
        from src.capability_catalog import SqliteCapabilityCatalogRepository

        client, ref = self._prepare(tmp_path, monkeypatch)
        platform_ref = {
            "pack_id": "gray-python-table",
            "version": "1.0.0",
            "digest": "sha256:" + "b" * 64,
        }
        repository = SqliteCapabilityCatalogRepository(settings.webui_db_path)
        repository.save_pack(
            CapabilityPack(
                pack_id="gray-python-table",
                version="1.0.0",
                digest="sha256:" + "b" * 64,
                scope=ProcedureScope.PLATFORM,
                maturity=LegacyCapabilityMaturity.VERIFIED,
            )
        )
        upload = self._uploads(tmp_path)
        response = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "按部门汇总金额并输出 JSON",
                "upload_ids": [upload],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
                "capability_pack_refs": [platform_ref],
                "validation_target": platform_ref,
            },
        )
        assert response.status_code in (403, 409), response.text
        assert "个人" in response.json()["detail"]
