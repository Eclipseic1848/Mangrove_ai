# -*- coding: utf-8 -*-
"""Web 进程内能力验证 worker 的统一装配。"""
from __future__ import annotations

from pathlib import Path

from src.capability_catalog import (
    CapabilityCatalog,
    CatalogActor,
    DefaultCapabilityMounts,
    SqliteCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernance,
    CapabilitySupplyChainEvidenceService,
    CapabilityValidationManager,
    LockedCliSupplyChainTools,
    PiTaskReplayRunner,
    SqliteCapabilityGovernanceRepository,
    SqliteValidationTaskResolver,
    TaskEvidenceValidationExecutor,
)
from src.capability_host import CapabilityHost
from src.config.settings import settings


_manager: CapabilityValidationManager | None = None
_platform_manager: object | None = None
_platform_dependencies: tuple[object, object] | None = None
_signing_toolchain: object | None = None
_signing_runtime: object | None = None
_runtime_gate: object | None = None


def get_locked_signing_toolchain():
    """#12 发布与 #13 装载验签共享的工具链单例。"""
    global _signing_toolchain
    if _signing_toolchain is None:
        from src.capability_governance.oci_signing import (
            LockedOciSigningToolchain,
        )

        _signing_toolchain = LockedOciSigningToolchain.load(
            tool_root=settings.capability_supply_chain_tool_root,
            lock_path=settings.capability_supply_chain_lock_path,
        )
    return _signing_toolchain


def get_platform_signing_runtime():
    """#12 签名事务与 #13 装载验签共享的签名运行时单例。"""
    global _signing_runtime
    if _signing_runtime is None:
        from src.capability_governance.oci_signing import (
            LockedCliOciSigningRuntime,
        )

        project_root = (
            Path(settings.webui_db_path).resolve().parent.parent
        )
        toolchain = get_locked_signing_toolchain()
        _signing_runtime = LockedCliOciSigningRuntime(
            toolchain=toolchain,
            work_root=(
                project_root / "data/capability-governance/signing-runtime"
            ),
            project_root=project_root,
            protected_key_roots=(project_root / "data",),
        )
    return _signing_runtime


def get_runtime_gate():
    """#13 冻结拦截与选择过滤共用的运行时门装配（同一 check_mount 实现）。"""
    global _runtime_gate
    if _runtime_gate is None:
        from src.capability_catalog import SqliteCapabilityCatalogRepository
        from src.capability_governance import (
            CapabilityGovernance,
            CapabilityGovernanceTarget,
            SqliteCapabilityGovernanceRepository,
        )
        from src.capability_governance.runtime_gate import (
            CapabilityGovernanceRuntimeGate,
            OciPlatformSignatureVerifier,
        )
        from src.conversation_steering import ProcedureScope

        governance_repository = SqliteCapabilityGovernanceRepository(
            settings.webui_db_path
        )
        governance = CapabilityGovernance(
            CapabilityCatalog(
                SqliteCapabilityCatalogRepository(settings.webui_db_path)
            ),
            governance_repository,
        )

        def platform_publication_for(pack):
            return governance_repository.get_latest_platform_event(
                CapabilityGovernanceTarget(
                    owner_id=None,
                    scope=ProcedureScope.PLATFORM,
                    pack_id=pack.pack_id,
                    version=pack.version,
                    digest=pack.digest,
                ),
                "platform_published",
            )

        verifier = None
        public_key = settings.capability_platform_signing_public_key
        if public_key:
            try:
                verifier = OciPlatformSignatureVerifier(
                    signing_runtime=get_platform_signing_runtime(),
                    platform_layout=Path(
                        settings.capability_platform_oci_layout_path
                    ),
                    public_key_path=Path(public_key),
                )
            except Exception:
                # 签名运行时不可用时，有发布事件的平台 Pack 冻结被门拒绝
                # （fail-closed）；个人 Pack 与 legacy 平台 Pack 不受影响。
                verifier = None
        _runtime_gate = CapabilityGovernanceRuntimeGate(
            projection_for=governance.runtime_projection_for_pack,
            platform_publication_for=platform_publication_for,
            signature_verifier=verifier,
        )
    return _runtime_gate


def _replay_guard(catalog, governance):
    """重放前投影检查：被隔离/撤销的目标拒绝重放（draft 验证目标允许）。"""

    def guard(run) -> None:
        from src.capability_governance import (
            CapabilityEligibility,
            CapabilityLifecycle,
        )

        actor = CatalogActor(
            owner_id=run.owner_id, role=run.actor_role
        )
        pack = catalog.resolve_pack(
            actor, run.target.pack_id, run.target.version
        )
        if pack is None:
            raise RuntimeError("验证目标能力不存在或不可见")
        projection = governance.runtime_projection_for_pack(pack)
        if projection.lifecycle is CapabilityLifecycle.REVOKED:
            raise RuntimeError("验证目标能力已被撤销")
        if projection.eligibility is CapabilityEligibility.QUARANTINED:
            raise RuntimeError("验证目标能力已被隔离")

    return guard


def get_capability_validation_manager() -> CapabilityValidationManager:
    global _manager
    if _manager is None:
        mounts = DefaultCapabilityMounts(
            db_path=settings.webui_db_path,
            oci_layout_path=settings.capability_oci_layout_path,
            mount_root=settings.capability_mount_cache_path,
        )
        task_resolver = SqliteValidationTaskResolver(
            settings.webui_db_path,
            capability_mounts=mounts,
        )
        repository = SqliteCapabilityGovernanceRepository(
            settings.webui_db_path
        )
        supply_chain_evidence = CapabilitySupplyChainEvidenceService(
            repository,
            LockedCliSupplyChainTools(
                tool_root=settings.capability_supply_chain_tool_root,
                evidence_root=settings.capability_supply_chain_evidence_root,
                cache_root=settings.capability_supply_chain_cache_root,
                lock_path=settings.capability_supply_chain_lock_path,
            ),
        )
        governance = CapabilityGovernance(
            CapabilityCatalog(
                SqliteCapabilityCatalogRepository(settings.webui_db_path)
            ),
            repository,
            task_resolver=task_resolver,
        )
        _manager = CapabilityValidationManager(
            governance,
            lambda run: TaskEvidenceValidationExecutor(
                task_resolver=task_resolver,
                capability_mounts=mounts,
                capability_host=CapabilityHost(
                    image=settings.pi_capability_host_image,
                    execution_root=(
                        Path(settings.semantic_execution_root)
                        / "capability-validations"
                    ),
                ),
                execution_root=(
                    Path(settings.semantic_execution_root)
                    / "capability-validations"
                ),
                task_replay=PiTaskReplayRunner(
                    task_resolver=task_resolver,
                    capability_mounts=mounts,
                    execution_root=(
                        Path(settings.semantic_execution_root)
                        / "capability-validations"
                    ),
                    cancel_requested=lambda: governance.get_validation(
                        CatalogActor(
                            owner_id=run.owner_id,
                            role=run.actor_role,
                        ),
                        run.run_id,
                    ).cancel_requested,
                    replay_guard=_replay_guard(
                        catalog,
                        governance,
                    ),
                ),
            ),
            supply_chain_evidence=supply_chain_evidence,
            capability_mounts=mounts,
        )
    return _manager


def get_platform_publication_dependencies() -> tuple[object, object]:
    """平台发布的快照生成器与目录写入 Adapter；routes 与 worker 共享装配。"""
    global _platform_dependencies
    if _platform_dependencies is None:
        from src.capability_catalog import (
            OrasOciLayoutStore,
            SqliteCapabilityCatalogRepository,
        )
        from src.capability_governance.platform_snapshot import (
            PlatformSnapshotGenerator,
        )

        toolchain = get_locked_signing_toolchain()
        generator = PlatformSnapshotGenerator(
            OrasOciLayoutStore(
                settings.capability_oci_layout_path,
                oras_executable=str(toolchain.oras_executable),
            ),
            OrasOciLayoutStore(
                settings.capability_platform_oci_layout_path,
                oras_executable=str(toolchain.oras_executable),
            ),
        )
        publisher = SqliteCapabilityCatalogRepository(
            settings.webui_db_path
        ).save_pack
        _platform_dependencies = (generator, publisher)
    return _platform_dependencies


def get_platform_validation_manager() -> object:
    """平台验证与签名 worker 的统一装配；六步执行器用目录级真实实现。"""
    global _platform_manager
    if _platform_manager is None:
        from src.capability_governance import (
            CapabilitySupplyChainEvidenceService,
            LockedCliSupplyChainTools,
            PlatformValidationManager,
            SqliteCapabilityGovernanceRepository,
        )
        from src.capability_governance.oci_signing import (
            OciSigningTransaction,
        )
        from src.capability_governance.platform_executors import (
            FailClosedDirectoryRunner,
            IndependentVerifierDirectoryRunner,
            MountProbeDirectoryRunner,
            SyntheticSmokeDirectoryRunner,
        )
        from src.capability_governance.platform_validation import (
            LockedPlatformValidationExecutor,
        )

        toolchain = get_locked_signing_toolchain()
        signing_runtime = get_platform_signing_runtime()
        repository = SqliteCapabilityGovernanceRepository(
            settings.webui_db_path
        )
        supply_chain = CapabilitySupplyChainEvidenceService(
            repository,
            LockedCliSupplyChainTools(
                tool_root=settings.capability_supply_chain_tool_root,
                evidence_root=settings.capability_supply_chain_evidence_root,
                cache_root=settings.capability_supply_chain_cache_root,
                lock_path=settings.capability_supply_chain_lock_path,
            ),
        )
        from src.capability_catalog import OrasOciLayoutStore

        platform_store = OrasOciLayoutStore(
            settings.capability_platform_oci_layout_path,
            oras_executable=str(toolchain.oras_executable),
        )

        def materialize_platform(target) -> Path:
            # 按平台目标物化快照目录（缓存幂等）；写入 digest 标记供供应链
            # 扫描复核主体身份（materialize 内部已按冻结 digest 校验内容）。
            output = (
                Path(settings.capability_mount_cache_path)
                / "platform-probes"
                / f"{target.pack_id}-{target.version}-"
                + target.digest.replace(":", "-")
            )
            if output.is_dir():
                return output
            materialized = platform_store.materialize(
                artifact_name=target.pack_id,
                version=target.version,
                digest=target.digest,
                destination=output,
            )
            # 供应链扫描的身份复核是两段式：digest 标记 + 外置完整性记录，
            # 与 mount_resolver 的既有物化模式完全一致。
            from src.capability_catalog.integrity import (
                write_capability_integrity,
            )

            (materialized / ".mangrove-capability-digest").write_text(
                target.digest,
                encoding="utf-8",
            )
            write_capability_integrity(materialized, target.digest)
            return materialized

        executor = LockedPlatformValidationExecutor(
            materialize=materialize_platform,
            smoke=SyntheticSmokeDirectoryRunner(),
            fail_closed=FailClosedDirectoryRunner(),
            supply_chain=supply_chain,
            mount_probe=MountProbeDirectoryRunner(),
            verifier=IndependentVerifierDirectoryRunner(),
        )
        _platform_manager = PlatformValidationManager(
            repository,
            executor=executor,
            signing=OciSigningTransaction(signing_runtime),
            layout_path=settings.capability_platform_oci_layout_path,
            private_key_path=settings.capability_platform_signing_private_key,
            public_key_path=settings.capability_platform_signing_public_key,
        )
    return _platform_manager
