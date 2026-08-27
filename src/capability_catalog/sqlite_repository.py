# -*- coding: utf-8 -*-
"""CapabilityCatalog 的前向 SQLite Adapter。"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import sqlite3

from src.conversation_steering import (
    AutomationProcedure,
    CapabilityPack,
    ProcedureScope,
)

from .models import (
    CapabilityComponent,
    CapabilitySelection,
    CapabilityValidation,
)
from src.database_migrations import DatabaseTarget, inspect_database


def _owner_key(pack: CapabilityPack) -> str:
    return (
        pack.owner_id
        if pack.scope is ProcedureScope.PERSONAL
        else "__platform__"
    )


class SqliteCapabilityCatalogRepository:
    def __init__(self, db_path: str, *, initialize_schema: bool = True) -> None:
        self._db_path = db_path
        inspect_database(
            DatabaseTarget(profile="webui", path=Path(self._db_path))
        ).require_current()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def save_pack(self, pack: CapabilityPack) -> CapabilityPack:
        key = _owner_key(pack)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_pack_versions "
                "(owner_key, scope, pack_id, version, digest, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    pack.scope.value,
                    pack.pack_id,
                    pack.version,
                    pack.digest,
                    pack.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_pack_versions "
                "WHERE owner_key=? AND pack_id=? AND version=?",
                (key, pack.pack_id, pack.version),
            ).fetchone()
            assert row is not None
            saved = CapabilityPack.model_validate_json(row["payload_json"])
            if saved != pack:
                raise ValueError("同一能力包版本不可覆盖")
        return saved

    def list_packs(self) -> tuple[CapabilityPack, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM capability_pack_versions "
                "ORDER BY owner_key, pack_id, version"
            ).fetchall()
        return tuple(
            CapabilityPack.model_validate_json(row["payload_json"])
            for row in rows
        )

    def get_personal_pack(
        self,
        owner_id: str,
        pack_id: str,
        version: str,
    ) -> CapabilityPack | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capability_pack_versions "
                "WHERE owner_key=? AND scope='personal' AND pack_id=? AND version=?",
                (owner_id, pack_id, version),
            ).fetchone()
        return (
            CapabilityPack.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def deprecate_platform_pack(self, pack_id: str, version: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE capability_pack_versions SET deprecated=1 "
                "WHERE owner_key='__platform__' AND pack_id=? AND version=?",
                (pack_id, version),
            )
        if cursor.rowcount != 1:
            raise KeyError("平台能力包不存在")

    def is_platform_pack_deprecated(self, pack_id: str, version: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT deprecated FROM capability_pack_versions "
                "WHERE owner_key='__platform__' AND pack_id=? AND version=?",
                (pack_id, version),
            ).fetchone()
        return bool(row["deprecated"]) if row is not None else False

    def save_selection(
        self,
        selection: CapabilitySelection,
    ) -> CapabilitySelection:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_selections "
                "(owner_id, task_id, revision, selection_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    selection.owner_id,
                    selection.task_id,
                    selection.revision,
                    selection.selection_id,
                    selection.model_dump_json(),
                    selection.created_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_selections "
                "WHERE owner_id=? AND task_id=? AND revision=?",
                (selection.owner_id, selection.task_id, selection.revision),
            ).fetchone()
            assert row is not None
            saved = CapabilitySelection.model_validate_json(row["payload_json"])
            if (
                saved.pack_refs != selection.pack_refs
                or saved.procedure_refs != selection.procedure_refs
            ):
                raise ValueError("同一 TaskRevision 的能力选择不可覆盖")
        return saved

    def get_selection(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> CapabilitySelection | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capability_selections "
                "WHERE owner_id=? AND task_id=? AND revision=?",
                (owner_id, task_id, revision),
            ).fetchone()
        return (
            CapabilitySelection.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def save_procedure(
        self,
        procedure: AutomationProcedure,
    ) -> AutomationProcedure:
        owner_key = (
            procedure.owner_id
            if procedure.scope is ProcedureScope.PERSONAL
            else "__platform__"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO automation_procedure_versions "
                "(owner_key, scope, procedure_id, version, digest, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    owner_key,
                    procedure.scope.value,
                    procedure.procedure_id,
                    procedure.version,
                    procedure.digest,
                    procedure.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM automation_procedure_versions "
                "WHERE owner_key=? AND procedure_id=? AND version=?",
                (owner_key, procedure.procedure_id, procedure.version),
            ).fetchone()
            assert row is not None
            saved = AutomationProcedure.model_validate_json(row["payload_json"])
            if saved != procedure:
                raise ValueError("同一自动化方案版本不可覆盖")
        return saved

    def list_procedures(self) -> tuple[AutomationProcedure, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM automation_procedure_versions "
                "ORDER BY owner_key, procedure_id, version"
            ).fetchall()
        return tuple(
            AutomationProcedure.model_validate_json(row["payload_json"])
            for row in rows
        )

    def save_validation(
        self,
        validation: CapabilityValidation,
    ) -> CapabilityValidation:
        owner_key = validation.owner_id or "__platform__"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_validations "
                "(owner_key, validation_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    owner_key,
                    validation.validation_id,
                    validation.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_validations "
                "WHERE owner_key=? AND validation_id=?",
                (owner_key, validation.validation_id),
            ).fetchone()
            assert row is not None
            saved = CapabilityValidation.model_validate_json(row["payload_json"])
            if saved != validation:
                raise ValueError("验证记录不可覆盖")
        return saved

    def list_validations(self) -> tuple[CapabilityValidation, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM capability_validations "
                "ORDER BY owner_key, validation_id"
            ).fetchall()
        return tuple(
            CapabilityValidation.model_validate_json(row["payload_json"])
            for row in rows
        )

    def save_component(
        self,
        component: CapabilityComponent,
    ) -> CapabilityComponent:
        owner_key = component.owner_id or "__platform__"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_components "
                "(owner_key, scope, component_id, version, digest, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    owner_key,
                    component.scope.value,
                    component.component_id,
                    component.version,
                    component.digest,
                    component.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_components "
                "WHERE owner_key=? AND component_id=? AND version=?",
                (owner_key, component.component_id, component.version),
            ).fetchone()
            assert row is not None
            saved = CapabilityComponent.model_validate_json(row["payload_json"])
            if saved != component:
                raise ValueError("同一能力组件版本不可覆盖")
        return saved

    def list_components(self) -> tuple[CapabilityComponent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM capability_components "
                "ORDER BY owner_key, component_id, version"
            ).fetchall()
        return tuple(
            CapabilityComponent.model_validate_json(row["payload_json"])
            for row in rows
        )
