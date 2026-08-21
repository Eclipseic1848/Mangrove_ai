# -*- coding: utf-8 -*-
"""G1 正式 Delivery 的失败关闭计分接缝。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.agentic_runtime.models import (
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeStatus,
    VerificationStatus,
)
from src.delivery_publishing.models import (
    CandidateRef,
    DeliverySpec,
    PublicationGate,
    PublishCommand,
    canonical_hash,
)
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher
from src.semantic_harness.delivery.models import DeliveryManifest, DeliveryStatus


class FormalDeliveryQualification(BaseModel):
    """评测器从正式发布仓库读取的最小资格结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    reason_code: Literal[
        "formal_delivery_missing",
        "formal_delivery_invalid",
        "formal_delivery_valid",
    ]
    delivery_id: str | None
    output_ids: tuple[str, ...]
    qa_passed: bool
    details: tuple[str, ...]


def publish_runtime_result_as_formal_delivery(
    *,
    repository: DeliveryPublishingRepository,
    output_root: Path,
    request: PiRuntimeRequest,
    result: PiRuntimeResult,
    output_name: str,
    actor_id: str | None = None,
    qualification_owner_id: str | None = None,
) -> FormalDeliveryQualification:
    """把冻结 Runtime 结果送入真实 Publisher，再按持久化结果计分。"""

    verification = result.verification
    if request.model_connection_id is not None and not request.external_api_confirmed:
        raise ValueError("外部模型未冻结外发确认，禁止正式发布")
    if result.status is not RuntimeStatus.CANDIDATE_READY:
        raise ValueError("Pi Runtime 尚未形成候选终态，禁止正式发布")
    if (
        verification is None
        or verification.status is not VerificationStatus.PASSED
    ):
        raise ValueError("候选没有取得正式交付资格")
    source_refs = tuple(
        sorted(f"{source.upload_id}:{source.sha256}" for source in request.sources)
    )
    candidate_refs = tuple(
        CandidateRef(
            artifact_id=candidate.artifact_id,
            filename=candidate.filename,
            format=candidate.format,
            sha256=candidate.sha256,
            size_bytes=candidate.size_bytes,
        )
        for candidate in result.candidates
    )
    revision_payload = {
        "owner_id": request.user_id,
        "task_id": request.task_id,
        "revision": request.revision,
        "objective_text": request.objective_text,
        "output_formats": request.requested_output_formats,
        "source_snapshot_refs": source_refs,
        "permission_profile": request.permission_profile.value,
        "external_api_confirmed": request.external_api_confirmed,
        "model_route": {
            "connection_id": request.model_connection_id,
            "connection_version": request.model_connection_version,
            "connection_model": request.model_connection_model,
            "local_model": request.model,
            "local_base_url": request.base_url,
        },
    }
    command = PublishCommand.build(
        owner_id=request.user_id,
        task_id=request.task_id,
        task_revision=request.revision,
        task_revision_hash=canonical_hash(revision_payload),
        goal_contract_hash=canonical_hash({
            "objective_text": request.objective_text,
            "requested_output_formats": request.requested_output_formats,
            "source_snapshot_refs": source_refs,
            "permission_profile": request.permission_profile.value,
            "external_api_confirmed": request.external_api_confirmed,
            "model_route": revision_payload["model_route"],
        }),
        run_id=result.run_id,
        candidates=candidate_refs,
        verification_report_id=(
            "verification_"
            + canonical_hash(verification.model_dump(mode="json"))[:16]
        ),
        verification_report_hash=canonical_hash(
            verification.model_dump(mode="json")
        ),
        verification_status=verification.status.value,
        delivery_spec=DeliverySpec(
            requested_formats=request.requested_output_formats,
            output_name=output_name,
            requested_file_count=len(request.requested_output_formats),
        ),
        source_snapshot_refs=source_refs,
    )
    candidate_paths = {
        candidate.artifact_id: candidate.host_path
        for candidate in result.candidates
    }
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=output_root,
        candidate_resolver=lambda _command: candidate_paths,
        gate_reader=lambda _command: PublicationGate(),
    )
    return publish_and_qualify_formal_delivery(
        publisher=publisher,
        repository=repository,
        command=command,
        actor_id=actor_id or request.user_id,
        qualification_owner_id=qualification_owner_id or request.user_id,
        output_root=output_root,
    )


def publish_and_qualify_formal_delivery(
    *,
    publisher: DeliveryPublisher,
    repository: DeliveryPublishingRepository,
    command: PublishCommand,
    actor_id: str,
    output_root: Path,
    qualification_owner_id: str | None = None,
) -> FormalDeliveryQualification:
    """经唯一 Publisher 发布后，从持久化记录独立读取资格。"""

    publisher.publish(command, actor_id=actor_id)
    return qualify_formal_delivery(
        repository=repository,
        owner_id=qualification_owner_id or command.owner_id,
        run_id=command.run_id,
        expected_formats=command.delivery_spec.requested_formats,
        output_root=output_root,
    )


def qualify_formal_delivery(
    *,
    repository: DeliveryPublishingRepository,
    owner_id: str,
    run_id: str,
    expected_formats: tuple[str, ...],
    output_root: Path,
) -> FormalDeliveryQualification:
    """只有仓库中的正式 Delivery 才可能取得 G1 通过资格。"""

    delivery = repository.latest_delivery(owner_id, run_id)
    if delivery is None:
        return FormalDeliveryQualification(
            passed=False,
            reason_code="formal_delivery_missing",
            delivery_id=None,
            output_ids=(),
            qa_passed=False,
            details=("没有找到已持久化的正式 Delivery",),
        )
    try:
        manifest = DeliveryManifest.model_validate(
            {**delivery, "user_id": owner_id}
        )
    except ValueError as exc:
        return _invalid_delivery(None, f"正式 Delivery Manifest 无效：{exc}")
    if manifest.status is not DeliveryStatus.SUCCEEDED:
        return _invalid_delivery(manifest.delivery_id, "正式 Delivery 状态不是 succeeded")
    actual_formats = tuple(item.value for item in manifest.requested_formats)
    if actual_formats != expected_formats:
        return _invalid_delivery(manifest.delivery_id, "正式 Delivery 输出格式与冻结目标不一致")
    if not manifest.outputs:
        return _invalid_delivery(manifest.delivery_id, "正式 Delivery 没有 output_id")
    output_formats = tuple(output.format.value for output in manifest.outputs)
    if output_formats != expected_formats:
        return _invalid_delivery(manifest.delivery_id, "正式 output 数量或格式与冻结目标不一致")

    output_ids: list[str] = []
    if output_root.is_symlink():
        return _invalid_delivery(manifest.delivery_id, "正式交付根目录不得是符号链接")
    resolved_output_root = output_root.resolve()
    for output in manifest.outputs:
        persisted = repository.get_output(owner_id, output.output_id)
        if persisted is None or persisted["delivery_id"] != manifest.delivery_id:
            return _invalid_delivery(manifest.delivery_id, "正式 output_id 未持久化或绑定错误")
        persisted_identity = (
            persisted.get("run_id"),
            persisted.get("format"),
            persisted.get("filename"),
            persisted.get("media_type"),
            persisted.get("sha256"),
            persisted.get("size_bytes"),
        )
        manifest_identity = (
            manifest.run_id,
            output.format.value,
            output.filename,
            output.media_type,
            output.sha256,
            output.size_bytes,
        )
        if persisted_identity != manifest_identity:
            return _invalid_delivery(manifest.delivery_id, "正式 output 持久化身份与 Manifest 不一致")
        qa = persisted.get("qa") or {}
        required_checks = {"non_empty", "sha256", "reopened"}
        if not qa.get("openable") or not required_checks.issubset(qa.get("checks") or ()):
            return _invalid_delivery(manifest.delivery_id, "正式 output 独立 QA 未通过")
        if qa != output.qa.model_dump(mode="json"):
            return _invalid_delivery(manifest.delivery_id, "正式 output QA 与 Manifest 不一致")
        stored_path = Path(str(persisted["file_path"]))
        if _contains_symlink(stored_path, output_root):
            return _invalid_delivery(manifest.delivery_id, "正式 output 路径包含符号链接")
        path = stored_path.resolve()
        if not path.is_relative_to(resolved_output_root):
            return _invalid_delivery(manifest.delivery_id, "正式 output 路径越出交付根目录")
        provenance = manifest.provenance
        expected_dir = (
            resolved_output_root
            / _safe_identity(owner_id)
            / _safe_identity(str(provenance.get("task_id") or ""))
            / f"revision-{provenance.get('task_revision')}"
            / _safe_identity(manifest.run_id)
            / "publications"
            / str(provenance.get("publication_key") or "")
            / "final"
        ).resolve()
        if path != (expected_dir / output.filename).resolve():
            return _invalid_delivery(manifest.delivery_id, "正式 output 路径未绑定该 Delivery 与文件名")
        if not path.is_file() or path.stat().st_size != persisted["size_bytes"]:
            return _invalid_delivery(manifest.delivery_id, "正式 output 文件缺失或大小不一致")
        if _sha256(path) != persisted["sha256"]:
            return _invalid_delivery(manifest.delivery_id, "正式 output 文件哈希不一致")
        output_ids.append(output.output_id)

    return FormalDeliveryQualification(
        passed=True,
        reason_code="formal_delivery_valid",
        delivery_id=manifest.delivery_id,
        output_ids=tuple(output_ids),
        qa_passed=True,
        details=("正式 Delivery、output_id 与独立 QA 均已持久化",),
    )


def _invalid_delivery(
    delivery_id: str | None,
    detail: str,
) -> FormalDeliveryQualification:
    return FormalDeliveryQualification(
        passed=False,
        reason_code="formal_delivery_invalid",
        delivery_id=delivery_id,
        output_ids=(),
        qa_passed=False,
        details=(detail,),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _contains_symlink(path: Path, root: Path) -> bool:
    absolute_path = path.absolute()
    absolute_root = root.absolute()
    if not absolute_path.is_relative_to(absolute_root):
        return False
    current = absolute_path
    while True:
        if current.is_symlink():
            return True
        if current == absolute_root:
            return False
        current = current.parent
