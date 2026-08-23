# -*- coding: utf-8 -*-
"""CapabilityAcquisition 公共 Interface。"""

from .models import (
    AcquisitionCandidate,
    AcquisitionEvent,
    AcquisitionRecord,
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionSourceKind,
    PreparedCapability,
    ResolvedCandidate,
)
from .repository import AcquisitionRepository, InMemoryAcquisitionRepository
from .service import CapabilityAcquisition, SourcePolicy
from .sqlite_repository import (
    SqliteAcquisitionRepository,
    migrate_capability_acquisition,
)
from .docker_environment import DockerBuildkitAcquisitionEnvironment

__all__ = [
    "AcquisitionCandidate",
    "AcquisitionEvent",
    "AcquisitionRecord",
    "AcquisitionRepository",
    "AcquisitionRequest",
    "AcquisitionResult",
    "AcquisitionSourceKind",
    "CapabilityAcquisition",
    "InMemoryAcquisitionRepository",
    "PreparedCapability",
    "ResolvedCandidate",
    "SourcePolicy",
    "SqliteAcquisitionRepository",
    "migrate_capability_acquisition",
    "DockerBuildkitAcquisitionEnvironment",
]
