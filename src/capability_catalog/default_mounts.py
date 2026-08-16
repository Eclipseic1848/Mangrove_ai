# -*- coding: utf-8 -*-
"""生产 Pi Runtime 的只读能力挂载装配，不隐式执行数据库迁移。"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Callable

from .catalog import CapabilityCatalog
from .mount_resolver import CapabilityMountResolver
from .oci_store import OrasOciLayoutStore
from .sqlite_repository import SqliteCapabilityCatalogRepository
from .models import PublicCapabilityDescriptor


class DefaultCapabilityMounts:
    """迁移完成后自动装配；表尚不存在时保持零写入、零挂载。

    治理门依赖 capability_governance（其顶层依赖本包），因此装配在
    _get_resolver 内延迟 import，避免包级循环。
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        oci_layout_path: str | Path,
        mount_root: str | Path,
        platform_oci_layout_path: str | Path | None = None,
        platform_oras_executable_factory: Callable[[], str] | None = None,
        platform_signing_public_key_path: str | Path | None = None,
        signing_runtime_factory: Callable[[], object] | None = None,
        actor_role_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._oci_layout_path = Path(oci_layout_path)
        self._mount_root = Path(mount_root)
        self._platform_oci_layout_path = (
            Path(platform_oci_layout_path)
            if platform_oci_layout_path is not None
            else None
        )
        self._platform_oras_executable_factory = (
            platform_oras_executable_factory
        )
        self._platform_signing_public_key_path = (
            Path(platform_signing_public_key_path)
            if platform_signing_public_key_path is not None
            else None
        )
        self._signing_runtime_factory = signing_runtime_factory
        self._actor_role_resolver = actor_role_resolver
        self._resolver: CapabilityMountResolver | None = None

    def _schema_exists(self) -> bool:
        if not self._db_path.is_file():
            return False
        with sqlite3.connect(str(self._db_path), timeout=30) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_selections'"
            ).fetchone()
        return row is not None

    def _build_runtime_gate(
        self,
        catalog: CapabilityCatalog,
    ):
        """延迟装配治理门；读路径缺表时由投影降级到 legacy_compat（fail-closed）。

        与冻结侧（get_runtime_gate）始终装配保持一致：个人 draft 拒绝、
        平台 legacy 旧路径放行，避免两处语义漂移。
        """

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
            str(self._db_path)
        )
        governance = CapabilityGovernance(
            catalog,
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
        if (
            self._platform_oci_layout_path is not None
            and self._platform_signing_public_key_path is not None
            and self._signing_runtime_factory is not None
        ):
            try:
                verifier = OciPlatformSignatureVerifier(
                    signing_runtime=self._signing_runtime_factory(),
                    platform_layout=self._platform_oci_layout_path,
                    public_key_path=self._platform_signing_public_key_path,
                )
            except Exception:
                # 签名运行时不可用时，有发布事件的平台 Pack 装载被门拒绝
                # （fail-closed）；个人 Pack 三轴门照常生效。
                verifier = None
        return CapabilityGovernanceRuntimeGate(
            projection_for=governance.runtime_projection_for_pack,
            platform_publication_for=platform_publication_for,
            signature_verifier=verifier,
        )

    def _get_resolver(self) -> CapabilityMountResolver | None:
        if not self._schema_exists():
            return None
        if self._resolver is None:
            repository = SqliteCapabilityCatalogRepository(
                str(self._db_path),
                initialize_schema=False,
            )
            catalog = CapabilityCatalog(repository)
            platform_store = None
            if self._platform_oci_layout_path is not None:
                oras_executable = None
                if self._platform_oras_executable_factory is not None:
                    try:
                        oras_executable = (
                            self._platform_oras_executable_factory()
                        )
                    except Exception:
                        # 平台工具链不可用时平台 Pack 物化失败关闭；
                        # 个人 Pack 与无能力任务不受影响。
                        oras_executable = None
                if oras_executable is not None:
                    try:
                        platform_store = OrasOciLayoutStore(
                            self._platform_oci_layout_path,
                            oras_executable=oras_executable,
                            layout_id="mangrove-platform-capabilities",
                        )
                    except Exception:
                        platform_store = None
            self._resolver = CapabilityMountResolver(
                catalog,
                OrasOciLayoutStore(
                    self._oci_layout_path,
                    layout_id="mangrove-capabilities",
                ),
                self._mount_root,
                runtime_gate=self._build_runtime_gate(catalog),
                platform_artifact_store=platform_store,
                actor_role_resolver=self._actor_role_resolver,
            )
        return self._resolver

    def __call__(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> tuple[Path, ...]:
        resolver = self._get_resolver()
        if resolver is None:
            return ()
        return resolver.resolve_for_owner(owner_id, task_id, revision)

    def describe_for_owner(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> tuple[PublicCapabilityDescriptor, ...]:
        resolver = self._get_resolver()
        if resolver is None:
            return ()
        return resolver.describe_for_owner(owner_id, task_id, revision)

    def copy_selection_for_owner(
        self,
        owner_id: str,
        *,
        source_task_id: str,
        source_revision: int,
        target_task_id: str,
        target_revision: int,
    ) -> bool:
        resolver = self._get_resolver()
        if resolver is None:
            return False
        return resolver.copy_selection_for_owner(
            owner_id,
            source_task_id=source_task_id,
            source_revision=source_revision,
            target_task_id=target_task_id,
            target_revision=target_revision,
        )
