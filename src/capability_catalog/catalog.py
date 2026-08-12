# -*- coding: utf-8 -*-
"""隐藏能力版本、作用域和权限判断的深 Module。"""
from __future__ import annotations

from typing import Protocol

from src.conversation_steering import (
    CapabilityMaturity,
    AutomationProcedure,
    CapabilityPack,
    ProcedureScope,
)

from .models import CatalogActor
from .models import (
    AutomationProcedureRef,
    CapabilityPackRef,
    CapabilityComponent,
    CapabilitySelection,
    CapabilityValidation,
)


class CapabilityCatalogRepository(Protocol):
    def save_pack(self, pack: CapabilityPack) -> CapabilityPack: ...

    def list_packs(self) -> tuple[CapabilityPack, ...]: ...

    def save_selection(
        self,
        selection: CapabilitySelection,
    ) -> CapabilitySelection: ...

    def get_selection(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> CapabilitySelection | None: ...

    def save_procedure(
        self,
        procedure: AutomationProcedure,
    ) -> AutomationProcedure: ...

    def list_procedures(self) -> tuple[AutomationProcedure, ...]: ...

    def save_validation(
        self,
        validation: CapabilityValidation,
    ) -> CapabilityValidation: ...

    def list_validations(self) -> tuple[CapabilityValidation, ...]: ...

    def save_component(
        self,
        component: CapabilityComponent,
    ) -> CapabilityComponent: ...

    def list_components(self) -> tuple[CapabilityComponent, ...]: ...


class BuiltinCapabilityAdapter(Protocol):
    def list_packs(self) -> tuple[CapabilityPack, ...]: ...


class CapabilityCatalog:
    """调用者只需表达 Actor 与能力身份，Owner 过滤留在 Module 内部。"""

    def __init__(
        self,
        repository: CapabilityCatalogRepository,
        *,
        builtin_adapter: BuiltinCapabilityAdapter | None = None,
    ) -> None:
        self._repository = repository
        self._builtin_adapter = builtin_adapter

    def _all_packs(self) -> tuple[CapabilityPack, ...]:
        merged: dict[tuple[str | None, str, str], CapabilityPack] = {}
        sources = []
        if self._builtin_adapter is not None:
            sources.extend(self._builtin_adapter.list_packs())
        sources.extend(self._repository.list_packs())
        for pack in sources:
            key = (pack.owner_id, pack.pack_id, pack.version)
            existing = merged.get(key)
            if existing is not None and existing.digest != pack.digest:
                raise ValueError("内置与动态能力版本身份冲突")
            merged[key] = pack
        return tuple(merged.values())

    def register_pack(
        self,
        actor: CatalogActor,
        pack: CapabilityPack,
    ) -> CapabilityPack:
        if pack.scope is ProcedureScope.PLATFORM:
            # AC-04 只提供目录登记；平台快照必须由后续审核发布流程写入。
            raise ValueError("平台能力包只能由发布流程写入目录")
        if pack.owner_id != actor.owner_id:
            raise PermissionError("不能为其他用户登记个人能力包")
        if pack.maturity is not CapabilityMaturity.DRAFT:
            raise ValueError("新登记的个人能力包必须保持草稿状态")
        return self._repository.save_pack(pack)

    def list_visible_packs(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityPack, ...]:
        visible = [
            pack
            for pack in self._all_packs()
            if pack.scope is ProcedureScope.PLATFORM
            or pack.owner_id == actor.owner_id
        ]
        return tuple(
            sorted(
                visible,
                key=lambda item: (
                    0 if item.scope is ProcedureScope.PLATFORM else 1,
                    item.pack_id,
                    item.version,
                ),
            )
        )

    def list_governable_packs(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityPack, ...]:
        """管理员治理投影可跨 Owner；普通目录读取仍保持 Owner 隔离。"""

        if not actor.is_admin:
            raise PermissionError("只有管理员或超级管理员可以跨 Owner 治理能力包")
        return tuple(
            sorted(
                self._all_packs(),
                key=lambda item: (
                    0 if item.scope is ProcedureScope.PLATFORM else 1,
                    item.pack_id,
                    item.version,
                    item.owner_id or "",
                ),
            )
        )

    def resolve_pack(
        self,
        actor: CatalogActor,
        pack_id: str,
        version: str,
        digest: str | None = None,
    ) -> CapabilityPack | None:
        return next(
            (
                pack
                for pack in self.list_visible_packs(actor)
                if pack.pack_id == pack_id
                and pack.version == version
                and (digest is None or pack.digest == digest)
            ),
            None,
        )

    def freeze_selection(
        self,
        actor: CatalogActor,
        *,
        task_id: str,
        revision: int,
        pack_refs: tuple[CapabilityPackRef, ...],
        procedure_refs: tuple[AutomationProcedureRef, ...] = (),
    ) -> CapabilitySelection:
        for ref in pack_refs:
            pack = self.resolve_pack(actor, ref.pack_id, ref.version)
            if pack is None:
                raise ValueError("能力包不存在或当前用户不可见")
            if (
                pack.scope is ProcedureScope.PLATFORM
                and pack.maturity is not CapabilityMaturity.VERIFIED
            ):
                raise ValueError("平台能力包未处于已发布状态")
            if pack.digest != ref.digest:
                # TaskRevision 只接受内容寻址身份；名称相同不能替代 digest。
                raise ValueError("能力包 digest 与目录冻结版本不一致")
        for ref in procedure_refs:
            procedure = self.resolve_procedure(
                actor,
                ref.procedure_id,
                ref.version,
            )
            if procedure is None:
                raise ValueError("自动化方案不存在或当前用户不可见")
            if (
                procedure.scope is ProcedureScope.PLATFORM
                and procedure.maturity is not CapabilityMaturity.VERIFIED
            ):
                raise ValueError("平台自动化方案未处于已发布状态")
            if procedure.digest != ref.digest:
                raise ValueError("自动化方案 digest 与目录冻结版本不一致")
        return self._repository.save_selection(
            CapabilitySelection(
                owner_id=actor.owner_id,
                task_id=task_id,
                revision=revision,
                pack_refs=pack_refs,
                procedure_refs=procedure_refs,
            )
        )

    def resolve_selection(
        self,
        actor: CatalogActor,
        *,
        task_id: str,
        revision: int,
    ) -> CapabilitySelection | None:
        """TaskRevision 的能力选择只允许原 Owner 解析。"""

        return self._repository.get_selection(actor.owner_id, task_id, revision)

    def register_procedure(
        self,
        actor: CatalogActor,
        procedure: AutomationProcedure,
    ) -> AutomationProcedure:
        if procedure.scope is ProcedureScope.PLATFORM:
            raise ValueError("平台自动化方案只能由发布流程写入目录")
        if procedure.owner_id != actor.owner_id:
            raise PermissionError("不能为其他用户登记个人自动化方案")
        if procedure.maturity is not CapabilityMaturity.DRAFT:
            raise ValueError("新登记的个人自动化方案必须保持草稿状态")
        return self._repository.save_procedure(procedure)

    def list_visible_procedures(
        self,
        actor: CatalogActor,
    ) -> tuple[AutomationProcedure, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._repository.list_procedures()
                    if (
                        item.scope is ProcedureScope.PLATFORM
                        and item.maturity is CapabilityMaturity.VERIFIED
                    )
                    or (
                        item.scope is ProcedureScope.PERSONAL
                        and item.owner_id == actor.owner_id
                    )
                ),
                key=lambda item: (item.procedure_id, item.version),
            )
        )

    def resolve_procedure(
        self,
        actor: CatalogActor,
        procedure_id: str,
        version: str,
    ) -> AutomationProcedure | None:
        return next(
            (
                item
                for item in self.list_visible_procedures(actor)
                if item.procedure_id == procedure_id and item.version == version
            ),
            None,
        )

    def register_validation(
        self,
        actor: CatalogActor,
        validation: CapabilityValidation,
    ) -> CapabilityValidation:
        if validation.owner_id is None:
            if not actor.is_admin:
                raise PermissionError("只有管理员或超级管理员可以登记平台验证")
        elif validation.owner_id != actor.owner_id:
            raise PermissionError("不能为其他用户登记验证记录")
        return self._repository.save_validation(validation)

    def list_visible_validations(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityValidation, ...]:
        return tuple(
            item
            for item in self._repository.list_validations()
            if item.owner_id is None or item.owner_id == actor.owner_id
        )

    def register_component(
        self,
        actor: CatalogActor,
        component: CapabilityComponent,
    ) -> CapabilityComponent:
        if component.scope is ProcedureScope.PLATFORM:
            raise ValueError("平台能力组件只能由发布流程写入目录")
        if component.owner_id != actor.owner_id:
            raise PermissionError("不能为其他用户登记个人能力组件")
        return self._repository.save_component(component)

    def list_visible_components(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityComponent, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._repository.list_components()
                    if (
                        item.scope is ProcedureScope.PLATFORM
                        and item.published
                    )
                    or (
                        item.scope is ProcedureScope.PERSONAL
                        and item.owner_id == actor.owner_id
                    )
                ),
                key=lambda item: (item.component_id, item.version),
            )
        )
