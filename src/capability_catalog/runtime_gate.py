# -*- coding: utf-8 -*-
"""运行时装载治理门的最小契约；实现位于 capability_governance.runtime_gate。"""
from __future__ import annotations

from typing import Protocol

from src.conversation_steering import CapabilityPack

from .models import CatalogActor


class CapabilityMountGateRejected(RuntimeError):
    """装载门拒绝：携带 pack 身份与原因，供调用方失败关闭展示。"""

    def __init__(
        self,
        *,
        pack_id: str,
        version: str,
        digest: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"能力装载被治理门拒绝：{pack_id}@{version}（{digest}）：{reason}"
        )
        self.pack_id = pack_id
        self.version = version
        self.digest = digest
        self.reason = reason


class RuntimeGateContract(Protocol):
    """装载前检查三轴、受众与签名的门；不满足时抛 CapabilityMountGateRejected。"""

    def check_mount(
        self,
        actor: CatalogActor,
        pack: CapabilityPack,
    ) -> None: ...
