# -*- coding: utf-8 -*-
"""CapabilityCatalog 公共 Interface。"""

from .catalog import CapabilityCatalog, CapabilityCatalogRepository
from .models import (
    AutomationProcedureRef,
    CapabilityComponent,
    CapabilityPackRef,
    PublicCapabilityDescriptor,
    CapabilitySelection,
    CapabilityValidation,
    CatalogActor,
)
from .repository import InMemoryCapabilityCatalogRepository
from .sqlite_repository import SqliteCapabilityCatalogRepository
from .legacy import LegacyCapabilityManifestAdapter, LegacyDraftImporter
from .oci_store import OciArtifactDescriptor, OrasOciLayoutStore
from .mount_resolver import CapabilityMountResolver
from .runtime_gate import CapabilityMountGateRejected, RuntimeGateContract
from .default_mounts import DefaultCapabilityMounts

__all__ = [
    "CapabilityCatalog",
    "CapabilityCatalogRepository",
    "CatalogActor",
    "CapabilityPackRef",
    "CapabilitySelection",
    "AutomationProcedureRef",
    "CapabilityValidation",
    "CapabilityComponent",
    "PublicCapabilityDescriptor",
    "InMemoryCapabilityCatalogRepository",
    "SqliteCapabilityCatalogRepository",
    "LegacyCapabilityManifestAdapter",
    "LegacyDraftImporter",
    "OciArtifactDescriptor",
    "OrasOciLayoutStore",
    "CapabilityMountResolver",
    "CapabilityMountGateRejected",
    "RuntimeGateContract",
    "DefaultCapabilityMounts",
]
