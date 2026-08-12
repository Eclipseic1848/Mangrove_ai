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
    CapabilityValidationManager,
    PiTaskReplayRunner,
    SqliteCapabilityGovernanceRepository,
    SqliteValidationTaskResolver,
    TaskEvidenceValidationExecutor,
)
from src.capability_host import CapabilityHost
from src.config.settings import settings


_manager: CapabilityValidationManager | None = None


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
        governance = CapabilityGovernance(
            CapabilityCatalog(
                SqliteCapabilityCatalogRepository(settings.webui_db_path)
            ),
            SqliteCapabilityGovernanceRepository(settings.webui_db_path),
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
        )
    return _manager
