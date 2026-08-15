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
        from src.capability_governance.oci_signing import (
            LockedOciSigningToolchain,
        )
        from src.capability_governance.platform_snapshot import (
            PlatformSnapshotGenerator,
        )

        toolchain = LockedOciSigningToolchain.load(
            tool_root=settings.capability_supply_chain_tool_root,
            lock_path=settings.capability_supply_chain_lock_path,
        )
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
            LockedCliOciSigningRuntime,
            LockedOciSigningToolchain,
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

        project_root = (
            Path(settings.webui_db_path).resolve().parent.parent
        )
        toolchain = LockedOciSigningToolchain.load(
            tool_root=settings.capability_supply_chain_tool_root,
            lock_path=settings.capability_supply_chain_lock_path,
        )
        signing_runtime = LockedCliOciSigningRuntime(
            toolchain=toolchain,
            work_root=(
                project_root / "data/capability-governance/signing-runtime"
            ),
            project_root=project_root,
            protected_key_roots=(project_root / "data",),
        )
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
