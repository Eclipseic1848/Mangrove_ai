# -*- coding: utf-8 -*-
"""AC-07-08 S3：MountResolver 注入运行门与按 scope 路由物化。"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountGateRejected,
    CapabilityMountResolver,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.conversation_steering import (
    CapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _pack(
    pack_id: str,
    *,
    owner_id: str | None,
    scope: ProcedureScope,
    digest_char: str,
) -> CapabilityPack:
    return CapabilityPack(
        pack_id=pack_id,
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
        scope=scope,
        maturity=(
            CapabilityMaturity.DRAFT
            if scope is ProcedureScope.PERSONAL
            else CapabilityMaturity.VERIFIED
        ),
        owner_id=owner_id,
    )


class _RecordingStore:
    """记录物化调用；可配置是否成功。"""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[str, str, str]] = []

    def materialize(self, *, artifact_name, version, digest, destination):
        self.calls.append((artifact_name, version, digest))
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination) / "payload").write_bytes(b"frozen")
        return Path(destination)


class _RecordingGate:
    """记录门调用；可配置拒绝。"""

    def __init__(self):
        self.calls: list[tuple[CatalogActor, CapabilityPack]] = []
        self.reject: CapabilityMountGateRejected | None = None

    def check_mount(
        self, actor, pack, *, validation_exempt: bool = False
    ) -> None:
        # #15 D9 协议新增豁免参数；录制 mock 保持既有行为。
        self.calls.append((actor, pack))
        if self.reject is not None:
            raise self.reject


def _resolver_with_selection(
    mount_root: Path,
    *,
    scope: ProcedureScope,
    gate: _RecordingGate,
    role_resolver=None,
) -> tuple[
    CapabilityMountResolver,
    CapabilityCatalog,
    CapabilityPack,
    _RecordingStore,
    _RecordingStore,
]:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    actor = CatalogActor(owner_id="user-a", role="user")
    if scope is ProcedureScope.PERSONAL:
        pack = _pack(
            "private-a",
            owner_id="user-a",
            scope=scope,
            digest_char="a",
        )
        catalog.register_pack(actor, pack)
    else:
        pack = _pack(
            "platform-a",
            owner_id=None,
            scope=scope,
            digest_char="a",
        )
        repository.save_pack(pack)
    catalog.freeze_selection(
        actor,
        task_id="workspace-gate",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )
    personal_store = _RecordingStore("personal")
    platform_store = _RecordingStore("platform")
    resolver = CapabilityMountResolver(
        catalog,
        personal_store,
        mount_root,
        runtime_gate=gate,
        platform_artifact_store=platform_store,
        actor_role_resolver=role_resolver,
    )
    return resolver, catalog, pack, personal_store, platform_store


class TestS3MountResolverGate:
    def test_gate_rejection_blocks_materialization(self, tmp_path) -> None:
        gate = _RecordingGate()
        gate.reject = CapabilityMountGateRejected(
            pack_id="private-a",
            version="1.0.0",
            digest="sha256:" + "a" * 64,
            reason="成熟度未达到 verified",
        )
        resolver, _, _, personal_store, platform_store = (
            _resolver_with_selection(
                tmp_path / "mounts",
                scope=ProcedureScope.PERSONAL,
                gate=gate,
            )
        )
        with pytest.raises(CapabilityMountGateRejected):
            resolver.resolve_for_owner("user-a", "workspace-gate", 1)
        assert personal_store.calls == []
        assert platform_store.calls == []
        # 拒绝后挂载缓存零残留。
        assert list((tmp_path / "mounts").iterdir()) == []

    def test_gate_checked_before_materialization(self, tmp_path) -> None:
        gate = _RecordingGate()
        resolver, _, pack, personal_store, _ = _resolver_with_selection(
            tmp_path / "mounts",
            scope=ProcedureScope.PERSONAL,
            gate=gate,
        )
        resolver.resolve_for_owner("user-a", "workspace-gate", 1)
        assert len(gate.calls) == 1
        actor, checked = gate.calls[0]
        assert actor.owner_id == "user-a"
        assert checked.digest == pack.digest
        assert len(personal_store.calls) == 1

    def test_platform_pack_materializes_from_platform_store(
        self, tmp_path
    ) -> None:
        gate = _RecordingGate()
        resolver, _, _, personal_store, platform_store = (
            _resolver_with_selection(
                tmp_path / "mounts",
                scope=ProcedureScope.PLATFORM,
                gate=gate,
            )
        )
        mounts = resolver.resolve_for_owner("user-a", "workspace-gate", 1)
        assert len(mounts) == 1
        assert personal_store.calls == []
        assert len(platform_store.calls) == 1
        assert platform_store.calls[0][0] == "platform-a"

    def test_role_resolver_feeds_gate_actor(self, tmp_path) -> None:
        gate = _RecordingGate()
        resolver, _, _, _, _ = _resolver_with_selection(
            tmp_path / "mounts",
            scope=ProcedureScope.PERSONAL,
            gate=gate,
            role_resolver=lambda owner_id: "admin",
        )
        resolver.resolve_for_owner("user-a", "workspace-gate", 1)
        actor, _ = gate.calls[0]
        assert actor.owner_id == "user-a"
        assert actor.role == "admin"

    def test_missing_selection_short_circuits_without_gate(self, tmp_path) -> None:
        repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(repository)
        gate = _RecordingGate()
        resolver = CapabilityMountResolver(
            catalog,
            _RecordingStore("personal"),
            tmp_path / "mounts",
            runtime_gate=gate,
        )
        # 无能力任务：不触碰门、不物化、不创建治理负担。
        assert resolver.resolve_for_owner("user-a", "no-selection", 1) == ()
        assert gate.calls == []

    def test_digest_mismatch_still_rejected_before_gate(self, tmp_path) -> None:
        """损坏的冻结选择（digest 与目录不符）在装载时失败关闭，不越过门。"""
        from src.capability_catalog import CapabilitySelection

        repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(repository)
        actor = CatalogActor(owner_id="user-a", role="user")
        pack = _pack(
            "private-a",
            owner_id="user-a",
            scope=ProcedureScope.PERSONAL,
            digest_char="a",
        )
        catalog.register_pack(actor, pack)
        # 直接写入损坏选择，绕过 freeze_selection 的冻结层拦截（模拟历史数据）。
        repository.save_selection(
            CapabilitySelection(
                owner_id="user-a",
                task_id="workspace-gate",
                revision=1,
                pack_refs=(
                    CapabilityPackRef(
                        pack_id=pack.pack_id,
                        version=pack.version,
                        digest="sha256:" + "9" * 64,
                    ),
                ),
                procedure_refs=(),
            )
        )
        gate = _RecordingGate()
        resolver = CapabilityMountResolver(
            catalog,
            _RecordingStore("personal"),
            tmp_path / "mounts",
            runtime_gate=gate,
        )
        with pytest.raises(PermissionError):
            resolver.resolve_for_owner("user-a", "workspace-gate", 1)
        assert gate.calls == []
