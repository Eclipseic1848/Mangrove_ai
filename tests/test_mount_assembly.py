# -*- coding: utf-8 -*-
"""AC-07-08 S4：DefaultCapabilityMounts 生产装配接线（门/双 store/角色）。"""
from __future__ import annotations

import sqlite3

import pytest

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountGateRejected,
    CapabilityPackRef,
    CatalogActor,
    DefaultCapabilityMounts,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityMaturity,
    InMemoryCapabilityGovernanceRepository,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


class _FakeOciStore:
    """替身 OCI store：记录物化，不依赖真实 ORAS。"""

    def __init__(self, layout_path, **kwargs):
        self.layout_path = str(layout_path)
        self.calls: list[str] = []

    def materialize(self, *, artifact_name, version, digest, destination):
        from pathlib import Path

        self.calls.append(artifact_name)
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination) / "payload").write_bytes(b"frozen")
        return Path(destination)


class _FakeSigningRuntime:
    """替身签名运行时：装配时只要求可构造。"""

    def verify_local(self, request):
        raise AssertionError("装配测试不应触发真实验签")


def _schema_db(db_path, *, with_governance: bool) -> str:
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "CREATE TABLE capability_selections "
            "(owner_id TEXT, task_id TEXT, revision INTEGER, payload_json TEXT)"
        )
        connection.execute(
            "CREATE TABLE capability_packs "
            "(owner_id TEXT, pack_id TEXT, version TEXT, digest TEXT, "
            "payload_json TEXT)"
        )
        if with_governance:
            connection.execute(
                "CREATE TABLE capability_governance_events "
                "(owner_key TEXT, pack_id TEXT, version TEXT, digest TEXT, "
                "payload_json TEXT)"
            )
    return str(db_path)


def _patch_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.capability_governance as governance_module
    from src.capability_catalog import default_mounts

    catalog_repository = InMemoryCapabilityCatalogRepository()
    governance_repository = InMemoryCapabilityGovernanceRepository()
    monkeypatch.setattr(
        default_mounts,
        "SqliteCapabilityCatalogRepository",
        lambda db_path, initialize_schema=True: catalog_repository,
    )
    # 门装配的延迟 import 从 capability_governance 包取名字；
    # patch 包级导出而不是 default_mounts 模块属性。
    monkeypatch.setattr(
        governance_module,
        "SqliteCapabilityGovernanceRepository",
        lambda db_path: governance_repository,
    )
    monkeypatch.setattr(
        default_mounts,
        "OrasOciLayoutStore",
        _FakeOciStore,
    )
    monkeypatch.setattr(
        default_mounts,
        "CapabilityMountResolver",
        _spy_resolver(),
    )
    # 供测试后续通过目录注册 pack/事件。
    monkeypatch.setattr(
        default_mounts,
        "_test_catalog_repository",
        catalog_repository,
        raising=False,
    )
    monkeypatch.setattr(
        default_mounts,
        "_test_governance_repository",
        governance_repository,
        raising=False,
    )


def _spy_resolver():
    from src.capability_catalog import mount_resolver as module

    return module.CapabilityMountResolver


def _mounts(
    tmp_path,
    *,
    with_governance: bool = True,
) -> DefaultCapabilityMounts:
    return DefaultCapabilityMounts(
        db_path=_schema_db(
            tmp_path / "webui.db",
            with_governance=with_governance,
        ),
        oci_layout_path=tmp_path / "oci",
        mount_root=tmp_path / "mounts",
        platform_oci_layout_path=tmp_path / "platform-oci",
        platform_oras_executable_factory=lambda: "fake-oras",
        platform_signing_public_key_path=tmp_path / "pub.pem",
        signing_runtime_factory=lambda: _FakeSigningRuntime(),
        actor_role_resolver=lambda owner_id: "admin"
        if owner_id == "admin-a"
        else "user",
    )


def _register_personal(
    mounts: DefaultCapabilityMounts,
    *,
    digest_char: str = "a",
) -> None:
    from src.capability_catalog import default_mounts

    catalog_repository = default_mounts._test_catalog_repository
    catalog = CapabilityCatalog(catalog_repository)
    actor = CatalogActor(owner_id="owner-a", role="user")
    pack = CapabilityPack(
        pack_id="private-a",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=LegacyCapabilityMaturity.DRAFT,
        owner_id="owner-a",
    )
    catalog.register_pack(actor, pack)
    catalog.freeze_selection(
        actor,
        task_id="workspace-s4",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )


def _write_promotion(mounts: DefaultCapabilityMounts) -> None:
    from src.capability_catalog import default_mounts

    default_mounts._test_governance_repository.save_promotion_event(
        CapabilityGovernanceEvent(
            event_type="promoted_to_verified",
            idempotency_key="promotion:run-a",
            target=CapabilityGovernanceTarget(
                owner_id="owner-a",
                scope=ProcedureScope.PERSONAL,
                pack_id="private-a",
                version="1.0.0",
                digest="sha256:" + "a" * 64,
            ),
            maturity=CapabilityMaturity.VERIFIED,
            actor_id="owner-a",
            actor_role="user",
            source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
            source_supply_chain_evidence_id="supply_" + "a" * 20,
        )
    )


class TestS4Assembly:
    def test_assembly_wires_gate_and_platform_store(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_assembly(monkeypatch)
        mounts = _mounts(tmp_path)
        _register_personal(mounts)
        _write_promotion(mounts)
        resolver = mounts._get_resolver()
        assert resolver is not None
        assert resolver._runtime_gate is not None
        assert resolver._platform_artifact_store is not None
        assert resolver._actor_role_resolver is not None
        # verified 个人 pack 过三轴门后正常物化。
        result = mounts("owner-a", "workspace-s4", 1)
        assert len(result) == 1

    def test_assembly_rejects_draft_personal_pack(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_assembly(monkeypatch)
        mounts = _mounts(tmp_path)
        _register_personal(mounts)
        # 无治理事件 → legacy draft 投影 → 门拒绝，零物化。
        with pytest.raises(CapabilityMountGateRejected):
            mounts("owner-a", "workspace-s4", 1)

    def test_assembly_without_governance_schema_fails_closed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_assembly(monkeypatch)
        mounts = _mounts(tmp_path, with_governance=False)
        _register_personal(mounts)
        resolver = mounts._get_resolver()
        assert resolver is not None
        # B3：治理表缺失时门仍装配（读路径降级到 legacy_compat 投影），
        # 个人 draft 拒绝（fail-closed），与冻结侧语义一致。
        assert resolver._runtime_gate is not None
        with pytest.raises(CapabilityMountGateRejected):
            mounts("owner-a", "workspace-s4", 1)

    def test_assembly_without_catalog_schema_returns_empty(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_assembly(monkeypatch)
        mounts = DefaultCapabilityMounts(
            db_path=tmp_path / "absent.db",
            oci_layout_path=tmp_path / "oci",
            mount_root=tmp_path / "mounts",
        )
        # 目录表不存在：零写入、零挂载（无能力任务零回归）。
        assert mounts("user-a", "task-a", 1) == ()
        assert not (tmp_path / "absent.db").exists()
