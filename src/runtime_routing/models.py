# -*- coding: utf-8 -*-
"""生产门快照与 Rollout 的不可变领域契约。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.agentic_runtime import RuntimeVersion


_HASH_PATTERN = r"^[0-9a-f]{64}$"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RolloutMode(StrEnum):
    LEGACY_DEFAULT = "legacy_default"
    ADMIN_GRAY = "admin_gray"
    # 仅兼容旧数据库；当前状态机不允许进入该模式。
    EXPLICIT_OPT_IN = "explicit_opt_in"
    VNEXT_DEFAULT = "vnext_default"
    LEGACY_ROLLBACK = "legacy_rollback"


class RolloutActor(FrozenModel):
    actor_id: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "super_admin", "user"]

    @field_validator("actor_id")
    @classmethod
    def reject_blank_actor_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("actor_id 不能为空白")
        return value


class GateCheck(FrozenModel):
    gate_id: str = Field(min_length=1, max_length=120)
    passed: bool
    evidence_hash: str = Field(pattern=_HASH_PATTERN)

    @field_validator("gate_id")
    @classmethod
    def reject_blank_gate_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate_id 不能为空白")
        return value


class GateSnapshot(FrozenModel):
    snapshot_id: str = Field(pattern=_HASH_PATTERN)
    gate_version: str = Field(min_length=1, max_length=120)
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    environment_digest: str = Field(pattern=_HASH_PATTERN)
    checks: tuple[GateCheck, ...] = Field(min_length=1)

    @field_validator("gate_version")
    @classmethod
    def reject_blank_gate_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate_version 不能为空白")
        return value

    @model_validator(mode="after")
    def reject_duplicate_gate_ids(self) -> "GateSnapshot":
        gate_ids = [item.gate_id for item in self.checks]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("GateSnapshot 不允许重复 gate_id")
        return self

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> "GateSnapshot":
        expected = self._digest(
            gate_version=self.gate_version,
            code_commit=self.code_commit,
            environment_digest=self.environment_digest,
            checks=self.checks,
        )
        if self.snapshot_id != expected:
            raise ValueError("snapshot_id 与 GateSnapshot 内容不一致")
        return self

    @staticmethod
    def _digest(
        *,
        gate_version: str,
        code_commit: str,
        environment_digest: str,
        checks: tuple[GateCheck, ...],
    ) -> str:
        payload = {
            "gate_version": gate_version,
            "code_commit": code_commit,
            "environment_digest": environment_digest,
            "checks": [item.model_dump(mode="json") for item in checks],
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        gate_version: str,
        code_commit: str,
        environment_digest: str,
        checks: tuple[GateCheck, ...],
    ) -> "GateSnapshot":
        payload = {
            "gate_version": gate_version,
            "code_commit": code_commit,
            "environment_digest": environment_digest,
            "checks": [item.model_dump(mode="json") for item in checks],
        }
        snapshot_id = cls._digest(
            gate_version=gate_version,
            code_commit=code_commit,
            environment_digest=environment_digest,
            checks=checks,
        )
        return cls(snapshot_id=snapshot_id, **payload)

    @property
    def qualified(self) -> bool:
        return all(item.passed for item in self.checks)


class RolloutSnapshot(FrozenModel):
    mode: RolloutMode
    p0_blocked: bool
    active_gate_snapshot_id: str = Field(pattern=_HASH_PATTERN)


class RolloutApproval(FrozenModel):
    approval_id: str = Field(min_length=1, max_length=120)
    target_mode: RolloutMode
    gate_snapshot_id: str = Field(pattern=_HASH_PATTERN)
    approved_by: str = Field(min_length=1, max_length=120)

    @field_validator("approval_id", "approved_by")
    @classmethod
    def reject_blank_approval_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Rollout 授权身份不能为空白")
        return value


class RuntimeTaskRevisionRef(FrozenModel):
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    requested_runtime: RuntimeVersion | None = None

    @field_validator("owner_id", "task_id")
    @classmethod
    def reject_blank_task_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("任务身份不能为空白")
        return value


class RuntimeAssignment(FrozenModel):
    task_revision: RuntimeTaskRevisionRef
    runtime_version: RuntimeVersion
    rollout_mode: RolloutMode
    gate_snapshot_id: str = Field(pattern=_HASH_PATTERN)
    assigned_by: str = Field(min_length=1, max_length=120)
    assigned_at: datetime


class GateRecord(FrozenModel):
    snapshot: GateSnapshot
    recorded_by: str = Field(min_length=1, max_length=120)
    recorded_at: datetime


class GateComparison(FrozenModel):
    comparison_id: str = Field(min_length=1, max_length=120)
    baseline_snapshot_id: str = Field(pattern=_HASH_PATTERN)
    candidate_snapshot_id: str = Field(pattern=_HASH_PATTERN)
    baseline_recorded_by: str = Field(min_length=1, max_length=120)
    candidate_recorded_by: str = Field(min_length=1, max_length=120)
    regressed_gate_ids: tuple[str, ...]
    recovered_gate_ids: tuple[str, ...]
    added_gate_ids: tuple[str, ...]
    removed_gate_ids: tuple[str, ...]
    evidence_changed_gate_ids: tuple[str, ...]
    compared_by: str = Field(min_length=1, max_length=120)
    compared_at: datetime
