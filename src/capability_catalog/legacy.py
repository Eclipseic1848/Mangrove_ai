# -*- coding: utf-8 -*-
"""把 Legacy CapabilityManifest 映射为只读平台能力版本。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from src.conversation_steering import (
    CapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)
from src.semantic_harness.models import CapabilityManifest

from .catalog import CapabilityCatalog
from .models import CatalogActor, CapabilityComponent
from .oci_store import OciArtifactDescriptor


class LegacyArtifactStore(Protocol):
    def push_file(
        self,
        source_path: str | Path,
        *,
        artifact_name: str,
        version: str,
        artifact_type: str,
        layer_media_type: str = "application/octet-stream",
    ) -> OciArtifactDescriptor: ...


class LegacyCapabilityManifestAdapter:
    def __init__(self, manifests: tuple[CapabilityManifest, ...]) -> None:
        self._manifests = manifests

    def list_packs(self) -> tuple[CapabilityPack, ...]:
        packs = []
        for manifest in self._manifests:
            canonical = json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
            packs.append(
                CapabilityPack(
                    pack_id=manifest.capability_id,
                    version=manifest.version,
                    digest=digest,
                    scope=ProcedureScope.PLATFORM,
                    maturity=CapabilityMaturity.VERIFIED,
                    component_refs=(
                        f"builtin:{manifest.capability_id}@{manifest.version}",
                    ),
                )
            )
        return tuple(packs)


class LegacyDraftImporter:
    """只有显式调用才把旧 Skill/模板复制为个人草稿。"""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        artifact_store: LegacyArtifactStore,
    ) -> None:
        self._catalog = catalog
        self._artifact_store = artifact_store

    def import_skill(
        self,
        actor: CatalogActor,
        source_path: str | Path,
        *,
        pack_id: str,
        version: str,
    ) -> CapabilityPack:
        source = Path(source_path)
        artifact = self._artifact_store.push_file(
            source,
            artifact_name=pack_id,
            version=version,
            artifact_type="application/vnd.mangrove.capability.v1",
            layer_media_type="text/markdown",
        )
        component_id = f"{pack_id}-skill"
        self._catalog.register_component(
            actor,
            CapabilityComponent(
                component_id=component_id,
                version=version,
                digest=artifact.digest,
                scope=ProcedureScope.PERSONAL,
                owner_id=actor.owner_id,
                kind="skill",
                oci_reference=artifact.reference,
                source_provenance=(f"legacy-skill:{source.name}",),
            ),
        )
        return self._catalog.register_pack(
            actor,
            CapabilityPack(
                pack_id=pack_id,
                version=version,
                digest=artifact.digest,
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                owner_id=actor.owner_id,
                component_refs=(
                    f"{component_id}@{version}@{artifact.digest}",
                ),
                source_provenance=(f"legacy-skill:{source.name}",),
                created_by=actor.owner_id,
            ),
        )

    def import_template(
        self,
        actor: CatalogActor,
        source_path: str | Path,
        *,
        procedure_id: str,
        version: str,
        capability_refs: tuple[str, ...] = (),
    ) -> "AutomationProcedure":
        from src.conversation_steering import AutomationProcedure

        source = Path(source_path)
        artifact = self._artifact_store.push_file(
            source,
            artifact_name=procedure_id,
            version=version,
            artifact_type="application/vnd.mangrove.procedure.v1",
            layer_media_type="text/markdown",
        )
        return self._catalog.register_procedure(
            actor,
            AutomationProcedure(
                procedure_id=procedure_id,
                version=version,
                digest=artifact.digest,
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                owner_id=actor.owner_id,
                capability_refs=capability_refs,
                artifact_reference=artifact.reference,
            ),
        )
