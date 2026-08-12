# -*- coding: utf-8 -*-
"""CapabilityCatalog 的内存 Adapter。"""
from __future__ import annotations

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


class InMemoryCapabilityCatalogRepository:
    def __init__(self) -> None:
        self._packs: dict[
            tuple[str | None, str, str],
            CapabilityPack,
        ] = {}
        self._selections: dict[
            tuple[str, str, int],
            CapabilitySelection,
        ] = {}
        self._procedures: dict[
            tuple[str | None, str, str],
            AutomationProcedure,
        ] = {}
        self._validations: dict[
            tuple[str | None, str],
            CapabilityValidation,
        ] = {}
        self._components: dict[
            tuple[str | None, str, str],
            CapabilityComponent,
        ] = {}

    def save_pack(self, pack: CapabilityPack) -> CapabilityPack:
        owner_key = (
            pack.owner_id if pack.scope is ProcedureScope.PERSONAL else None
        )
        key = (owner_key, pack.pack_id, pack.version)
        existing = self._packs.get(key)
        if existing is not None:
            if existing == pack:
                return existing
            raise ValueError("同一能力包版本不可覆盖")
        self._packs[key] = pack
        return pack

    def list_packs(self) -> tuple[CapabilityPack, ...]:
        return tuple(self._packs.values())

    def save_selection(
        self,
        selection: CapabilitySelection,
    ) -> CapabilitySelection:
        key = (selection.owner_id, selection.task_id, selection.revision)
        existing = self._selections.get(key)
        if existing is not None:
            if (
                existing.pack_refs == selection.pack_refs
                and existing.procedure_refs == selection.procedure_refs
            ):
                return existing
            raise ValueError("同一 TaskRevision 的能力选择不可覆盖")
        self._selections[key] = selection
        return selection

    def get_selection(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> CapabilitySelection | None:
        return self._selections.get((owner_id, task_id, revision))

    def save_procedure(
        self,
        procedure: AutomationProcedure,
    ) -> AutomationProcedure:
        owner_key = (
            procedure.owner_id
            if procedure.scope is ProcedureScope.PERSONAL
            else None
        )
        key = (owner_key, procedure.procedure_id, procedure.version)
        existing = self._procedures.get(key)
        if existing is not None:
            if existing == procedure:
                return existing
            raise ValueError("同一自动化方案版本不可覆盖")
        self._procedures[key] = procedure
        return procedure

    def list_procedures(self) -> tuple[AutomationProcedure, ...]:
        return tuple(self._procedures.values())

    def save_validation(
        self,
        validation: CapabilityValidation,
    ) -> CapabilityValidation:
        key = (validation.owner_id, validation.validation_id)
        existing = self._validations.get(key)
        if existing is not None:
            if existing == validation:
                return existing
            raise ValueError("验证记录不可覆盖")
        self._validations[key] = validation
        return validation

    def list_validations(self) -> tuple[CapabilityValidation, ...]:
        return tuple(self._validations.values())

    def save_component(
        self,
        component: CapabilityComponent,
    ) -> CapabilityComponent:
        owner_key = (
            component.owner_id
            if component.scope is ProcedureScope.PERSONAL
            else None
        )
        key = (owner_key, component.component_id, component.version)
        existing = self._components.get(key)
        if existing is not None:
            if existing == component:
                return existing
            raise ValueError("同一能力组件版本不可覆盖")
        self._components[key] = component
        return component

    def list_components(self) -> tuple[CapabilityComponent, ...]:
        return tuple(self._components.values())
