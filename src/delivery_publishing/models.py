# -*- coding: utf-8 -*-
"""正式发布命令与门禁的冻结契约。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_HASH_PATTERN = r"^[0-9a-f]{64}$"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateRef(FrozenModel):
    artifact_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    format: str = Field(min_length=1)
    sha256: str = Field(pattern=_HASH_PATTERN)
    size_bytes: int = Field(ge=1)


class DeliverySpec(FrozenModel):
    requested_formats: tuple[str, ...]
    output_name: str = Field(min_length=1, max_length=200)
    requested_file_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_formats(self) -> "DeliverySpec":
        if not self.requested_formats:
            raise ValueError("正式交付至少需要一种输出格式")
        if len(set(self.requested_formats)) != len(self.requested_formats):
            raise ValueError("正式交付格式不能重复")
        if (
            self.requested_file_count is not None
            and self.requested_file_count != len(self.requested_formats)
        ):
            raise ValueError("请求文件数必须与正式输出格式数一致")
        return self


class PublicationGate(FrozenModel):
    cancel_requested: bool = False
    p0_blocked: bool = False


class PublishCommand(FrozenModel):
    owner_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_revision: int = Field(ge=1)
    task_revision_hash: str = Field(pattern=_HASH_PATTERN)
    goal_contract_hash: str = Field(pattern=_HASH_PATTERN)
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    candidates: tuple[CandidateRef, ...]
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    verification_report_id: str = Field(min_length=1)
    verification_report_hash: str = Field(pattern=_HASH_PATTERN)
    verification_status: Literal["passed", "failed", "inconclusive"]
    delivery_spec: DeliverySpec
    delivery_spec_hash: str = Field(pattern=_HASH_PATTERN)
    source_snapshot_refs: tuple[str, ...]
    publication_key: str = Field(pattern=_HASH_PATTERN)

    @classmethod
    def build(
        cls,
        *,
        owner_id: str,
        task_id: str,
        task_revision: int,
        task_revision_hash: str,
        goal_contract_hash: str,
        run_id: str,
        candidates: tuple[CandidateRef, ...],
        verification_report_id: str,
        verification_report_hash: str,
        verification_status: Literal["passed", "failed", "inconclusive"],
        delivery_spec: DeliverySpec,
        source_snapshot_refs: tuple[str, ...],
    ) -> "PublishCommand":
        if not candidates:
            raise ValueError("发布命令缺少候选文件")
        candidate_payload = [
            item.model_dump(mode="json")
            for item in sorted(candidates, key=lambda value: value.artifact_id)
        ]
        candidate_set_hash = canonical_hash(candidate_payload)
        delivery_spec_hash = canonical_hash(
            delivery_spec.model_dump(mode="json")
        )
        publication_key = canonical_hash(
            {
                "owner_id": owner_id,
                "task_revision_hash": task_revision_hash,
                "candidate_set_hash": candidate_set_hash,
                "verification_report_hash": verification_report_hash,
                "delivery_spec_hash": delivery_spec_hash,
            }
        )
        return cls(
            owner_id=owner_id,
            task_id=task_id,
            task_revision=task_revision,
            task_revision_hash=task_revision_hash,
            goal_contract_hash=goal_contract_hash,
            run_id=run_id,
            candidate_id=candidates[0].artifact_id,
            candidates=candidates,
            candidate_set_hash=candidate_set_hash,
            verification_report_id=verification_report_id,
            verification_report_hash=verification_report_hash,
            verification_status=verification_status,
            delivery_spec=delivery_spec,
            delivery_spec_hash=delivery_spec_hash,
            source_snapshot_refs=source_snapshot_refs,
            publication_key=publication_key,
        )

    def frozen_hash(self) -> str:
        return canonical_hash(
            self.model_dump(mode="json", exclude={"publication_key"})
        )
