# -*- coding: utf-8 -*-
"""把 TaskRevision 冻结选择解析为只读业务挂载目录。"""
from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from filelock import FileLock

from .catalog import CapabilityCatalog
from .models import CatalogActor
from .models import PublicCapabilityDescriptor
from .oci_store import OrasOciLayoutStore


class CapabilityMountResolver:
    """Owner 校验、digest 校验和 OCI 物化收敛在同一深 Module。"""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        artifact_store: OrasOciLayoutStore,
        mount_root: str | Path,
    ) -> None:
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._mount_root = Path(mount_root).resolve()
        self._mount_root.mkdir(parents=True, exist_ok=True)

    def resolve_for_owner(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> tuple[Path, ...]:
        actor = CatalogActor(owner_id=owner_id, role="user")
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
                else:
                    temporary = destination.with_name(
                        f"{destination.name}.tmp-{uuid.uuid4().hex[:12]}"
                    )
                    try:
                        self._artifact_store.materialize(
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
