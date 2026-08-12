# -*- coding: utf-8 -*-
"""生产 Pi Runtime 的只读能力挂载装配，不隐式执行数据库迁移。"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .catalog import CapabilityCatalog
from .mount_resolver import CapabilityMountResolver
from .oci_store import OrasOciLayoutStore
from .sqlite_repository import SqliteCapabilityCatalogRepository
from .models import PublicCapabilityDescriptor


class DefaultCapabilityMounts:
    """迁移完成后自动装配；表尚不存在时保持零写入、零挂载。"""

    def __init__(
        self,
        *,
        db_path: str | Path,
        oci_layout_path: str | Path,
        mount_root: str | Path,
    ) -> None:
        self._db_path = Path(db_path)
        self._oci_layout_path = Path(oci_layout_path)
        self._mount_root = Path(mount_root)
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

    def _get_resolver(self) -> CapabilityMountResolver | None:
        if not self._schema_exists():
            return None
        if self._resolver is None:
            repository = SqliteCapabilityCatalogRepository(
                str(self._db_path),
                initialize_schema=False,
            )
            self._resolver = CapabilityMountResolver(
                CapabilityCatalog(repository),
                OrasOciLayoutStore(
                    self._oci_layout_path,
                    layout_id="mangrove-capabilities",
                ),
                self._mount_root,
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
