# -*- coding: utf-8 -*-
"""把 TaskRevision 冻结选择解析为只读业务挂载目录。"""
from __future__ import annotations

from pathlib import Path
import shutil
import uuid
from typing import Callable, Literal

from filelock import FileLock

from .catalog import CapabilityCatalog
from .integrity import (
    capability_integrity_record_exists,
    verify_capability_integrity,
    write_capability_integrity,
)
from .models import CatalogActor
from .models import PublicCapabilityDescriptor
from .oci_store import OrasOciLayoutStore
from .runtime_gate import RuntimeGateContract


class CapabilityMountResolver:
    """Owner 校验、治理门、digest 校验和 OCI 物化收敛在同一深 Module。"""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        artifact_store: OrasOciLayoutStore,
        mount_root: str | Path,
        *,
        runtime_gate: RuntimeGateContract | None = None,
        platform_artifact_store: OrasOciLayoutStore | None = None,
        actor_role_resolver: Callable[
            [str], Literal["user", "admin", "superadmin"]
        ]
        | None = None,
    ) -> None:
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._platform_artifact_store = platform_artifact_store
        self._runtime_gate = runtime_gate
        self._actor_role_resolver = actor_role_resolver
        self._mount_root = Path(mount_root).resolve()
        self._mount_root.mkdir(parents=True, exist_ok=True)

    def _actor(self, owner_id: str) -> CatalogActor:
        role: Literal["user", "admin", "superadmin"] = "user"
        if self._actor_role_resolver is not None:
            resolved = self._actor_role_resolver(owner_id)
            role = (
                resolved
                if resolved in {"user", "admin", "superadmin"}
                else "user"
            )
        return CatalogActor(owner_id=owner_id, role=role)

    def _store_for(self, pack) -> OrasOciLayoutStore:
        """平台 Pack 从平台 Layout 物化（#12 发布写入处）；个人 Pack 从个人 Layout。"""
        from src.conversation_steering import ProcedureScope

        if (
            pack.scope is ProcedureScope.PLATFORM
            and self._platform_artifact_store is not None
        ):
            return self._platform_artifact_store
        return self._artifact_store

    def resolve_for_owner(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> tuple[Path, ...]:
        actor = self._actor(owner_id)
        selection = self._catalog.resolve_selection(
            actor,
            task_id=task_id,
            revision=revision,
        )
        if selection is None:
            return ()
        mounts: list[Path] = []
        for ref in selection.pack_refs:
            pack = self._catalog.resolve_pack(actor, ref.pack_id, ref.version)
            if pack is None or pack.digest != ref.digest:
                raise PermissionError("冻结能力包不存在、不可见或 digest 已失配")
            # 三轴/受众/签名门在物化前失败关闭；拒绝不降级、不换版本。
            if self._runtime_gate is not None:
                self._runtime_gate.check_mount(
                    actor,
                    pack,
                    # #15 D9：验证任务标记匹配的 ref 放行 draft（门内其余条件仍强制）。
                    validation_exempt=(
                        selection.validation_target is not None
                        and selection.validation_target == ref
                    ),
                )
            store = self._store_for(pack)
            digest_key = ref.digest.removeprefix("sha256:")
            destination = (self._mount_root / digest_key).resolve()
            if self._mount_root not in destination.parents:
                raise RuntimeError("能力挂载缓存路径越界")
            lock = FileLock(f"{destination}.lock", timeout=30)
            with lock:
                marker = destination / ".mangrove-capability-digest"
                if marker.is_file():
                    if marker.read_text(encoding="utf-8") != ref.digest:
                        raise RuntimeError("能力挂载缓存 digest 标记失配")
                    if capability_integrity_record_exists(destination):
                        verify_capability_integrity(destination, ref.digest)
                    else:
                        # 旧缓存不能就地补签；必须从冻结 OCI 重建，避免把已变内容登记为可信。
                        temporary = destination.with_name(
                            f"{destination.name}.tmp-{uuid.uuid4().hex[:12]}"
                        )
                        previous = destination.with_name(
                            f"{destination.name}.previous-{uuid.uuid4().hex[:12]}"
                        )
                        try:
                            store.materialize(
                                artifact_name=ref.pack_id,
                                version=ref.version,
                                digest=ref.digest,
                                destination=temporary,
                            )
                            (temporary / ".mangrove-capability-digest").write_text(
                                ref.digest,
                                encoding="utf-8",
                            )
                            destination.replace(previous)
                            try:
                                temporary.replace(destination)
                                write_capability_integrity(destination, ref.digest)
                            except Exception:
                                shutil.rmtree(destination, ignore_errors=True)
                                previous.replace(destination)
                                raise
                            shutil.rmtree(previous)
                        except Exception:
                            shutil.rmtree(temporary, ignore_errors=True)
                            raise
                else:
                    temporary = destination.with_name(
                        f"{destination.name}.tmp-{uuid.uuid4().hex[:12]}"
                    )
                    try:
                        store.materialize(
                            artifact_name=ref.pack_id,
                            version=ref.version,
                            digest=ref.digest,
                            destination=temporary,
                        )
                        (temporary / ".mangrove-capability-digest").write_text(
                            ref.digest,
                            encoding="utf-8",
                        )
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary.replace(destination)
                        try:
                            write_capability_integrity(destination, ref.digest)
                        except Exception:
                            shutil.rmtree(destination, ignore_errors=True)
                            raise
                    except Exception:
                        shutil.rmtree(temporary, ignore_errors=True)
                        raise
            mounts.append(destination)
        return tuple(mounts)

    def describe_for_owner(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> tuple[PublicCapabilityDescriptor, ...]:
        """返回用户可见身份；执行入口、来源、digest 和宿主路径留在目录内部。"""

        actor = CatalogActor(owner_id=owner_id, role="user")
        selection = self._catalog.resolve_selection(
            actor,
            task_id=task_id,
            revision=revision,
        )
        if selection is None:
            return ()
        components = self._catalog.list_visible_components(actor)
        descriptions: list[PublicCapabilityDescriptor] = []
        seen: set[tuple[str, str, str]] = set()
        for ref in selection.pack_refs:
            pack = self._catalog.resolve_pack(actor, ref.pack_id, ref.version)
            if pack is None or pack.digest != ref.digest:
                raise PermissionError("冻结能力包不存在、不可见或 digest 已失配")
            manifest = dict(pack.manifest)
            purpose = (
                manifest.get("purpose")
                or manifest.get("description")
                or "提供当前任务所需的专业处理能力"
            )
            component_ids = {
                value.removeprefix("builtin:").split("@", 1)[0]
                for value in pack.component_refs
            }
            matched = [
                component
                for component in components
                if component.component_id in component_ids
            ]
            if not matched:
                public_kind = manifest.get("kind", "capability_pack")
                if public_kind not in {
                    "tool",
                    "mcp_local",
                    "mcp_remote",
                    "skill",
                    "dependency_bundle",
                    "capability_pack",
                }:
                    public_kind = "capability_pack"
                matched_descriptions = (
                    PublicCapabilityDescriptor(
                        name=manifest.get("display_name") or pack.pack_id,
                        kind=public_kind,
                        version=pack.version,
                        purpose=purpose,
                    ),
                )
            else:
                matched_descriptions = tuple(
                    PublicCapabilityDescriptor(
                        name=component.component_id,
                        kind=component.kind,
                        version=component.version,
                        purpose=purpose,
                    )
                    for component in matched
                )
            for description in matched_descriptions:
                key = (description.name, description.kind, description.version)
                if key not in seen:
                    seen.add(key)
                    descriptions.append(description)
        return tuple(descriptions)

    def copy_selection_for_owner(
        self,
        owner_id: str,
        *,
        source_task_id: str,
        source_revision: int,
        target_task_id: str,
        target_revision: int,
    ) -> bool:
        """新 TaskRevision 继承确切身份，不把可移动版本重新解析为最新版。"""

        actor = CatalogActor(owner_id=owner_id, role="user")
        source = self._catalog.resolve_selection(
            actor,
            task_id=source_task_id,
            revision=source_revision,
        )
        if source is None:
            return False
        self._catalog.freeze_selection(
            actor,
            task_id=target_task_id,
            revision=target_revision,
            pack_refs=source.pack_refs,
            procedure_refs=source.procedure_refs,
        )
        return True
