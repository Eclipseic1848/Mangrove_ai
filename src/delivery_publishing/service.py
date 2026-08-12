# -*- coding: utf-8 -*-
"""Candidate -> staging -> QA -> committing -> Delivery 的可恢复发布。"""
from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Callable, Mapping
import uuid

from src.semantic_harness.delivery.models import (
    DeliveryManifest,
    DeliveryOutput,
    DeliveryStatus,
)
from src.semantic_harness.delivery.service import qa_delivery_artifact
from src.semantic_harness.models import DeliveryFormat

from .models import PublicationGate, PublishCommand
from .repository import DeliveryPublishingRepository


_MEDIA_TYPES = {
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_EXTENSIONS = {"markdown": "md"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    return cleaned[:80] or "交付结果"


class DeliveryPublisher:
    """正式发布唯一入口；Agent 和候选目录都不能直接登记 Delivery。"""

    def __init__(
        self,
        *,
        repository: DeliveryPublishingRepository,
        output_root: Path,
        candidate_resolver: Callable[[PublishCommand], Mapping[str, Path]],
        gate_reader: Callable[[PublishCommand], PublicationGate],
    ) -> None:
        self._repository = repository
        self._output_root = Path(output_root)
        self._candidate_resolver = candidate_resolver
        self._gate_reader = gate_reader

    def publish(
        self,
        command: PublishCommand,
        *,
        actor_id: str,
    ) -> DeliveryManifest:
        if actor_id != command.owner_id:
            raise PermissionError("发布 actor 不是任务所有者")
        if command.verification_status != "passed":
            raise ValueError("候选独立验证未通过，禁止正式发布")
        candidate_formats = tuple(item.format for item in command.candidates)
        if sorted(candidate_formats) != sorted(
            command.delivery_spec.requested_formats
        ):
            raise ValueError("候选格式集合与冻结 DeliverySpec 不一致")
        if len(command.candidates) != len(
            command.delivery_spec.requested_formats
        ):
            raise ValueError("候选文件数量与冻结 DeliverySpec 不一致")

        def safe_identity(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

        base = (
            self._output_root
            / safe_identity(command.owner_id)
            / safe_identity(command.task_id)
            / f"revision-{command.task_revision}"
            / safe_identity(command.run_id)
            / "publications"
            / command.publication_key
        )
        staging = base / ".staging"
        final_dir = base / "final"
        intent = self._repository.claim_intent(
            command,
            staging_dir=staging,
            final_dir=final_dir,
        )
        if intent["status"] == "published":
            delivery = self._repository.get_delivery(
                command.owner_id,
                str(intent["delivery_id"] or ""),
            )
            if delivery is None:
                raise RuntimeError("发布记录与正式 Delivery 不一致")
            manifest = DeliveryManifest.model_validate(
                {**delivery, "user_id": command.owner_id}
            )
            self._verify_published(manifest, final_dir)
            return manifest
        if intent["status"] == "committing" and final_dir.is_dir():
            return self._recover_commit(command, final_dir)

        gate = self._gate_reader(command)
        if gate.cancel_requested or gate.p0_blocked:
            reason = "任务已取消" if gate.cancel_requested else "P0 发布门已阻断"
            self._repository.set_intent_status(
                command.publication_key,
                "aborted",
                error={"reason": reason},
            )
            raise ValueError(reason)

        paths = self._candidate_resolver(command)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=False)
        outputs: list[DeliveryOutput] = []
        try:
            for candidate in command.candidates:
                source = Path(paths.get(candidate.artifact_id, "")).resolve()
                if not source.is_file() or source.is_symlink():
                    raise ValueError(f"候选文件不存在：{candidate.artifact_id}")
                if _sha256(source) != candidate.sha256:
                    raise ValueError("候选文件哈希已变化")
                if source.stat().st_size != candidate.size_bytes:
                    raise ValueError("候选文件大小已变化")
                fmt = DeliveryFormat(candidate.format)
                extension = _EXTENSIONS.get(candidate.format, candidate.format)
                filename = f"{_safe_name(command.delivery_spec.output_name)}.{extension}"
                target = staging / filename
                shutil.copyfile(source, target)
                report = qa_delivery_artifact(target, fmt)
                output_id = f"output_{uuid.uuid4().hex[:16]}"
                outputs.append(
                    DeliveryOutput(
                        output_id=output_id,
                        format=fmt,
                        filename=filename,
                        media_type=_MEDIA_TYPES.get(
                            candidate.format,
                            mimetypes.guess_type(filename)[0]
                            or "application/octet-stream",
                        ),
                        sha256=report.sha256,
                        size_bytes=report.size_bytes,
                        qa=report,
                        download_url=(
                            "/api/semantic-deliveries/outputs/" + output_id
                        ),
                    )
                )
            delivery_id = f"delivery_{uuid.uuid4().hex[:16]}"
            manifest = DeliveryManifest(
                schema_version="2",
                delivery_id=delivery_id,
                run_id=command.run_id,
                plan_id=f"vnext:{command.task_id}:{command.task_revision}",
                user_id=command.owner_id,
                status=DeliveryStatus.SUCCEEDED,
                source_artifact_hashes={
                    ref.split(":", 1)[0]: ref.split(":", 1)[1]
                    for ref in command.source_snapshot_refs
                    if ":" in ref
                },
                requested_formats=tuple(
                    DeliveryFormat(value)
                    for value in command.delivery_spec.requested_formats
                ),
                outputs=tuple(outputs),
                renderer_versions={"publisher": "candidate-copy+independent-qa"},
                provenance={
                    "runtime": "pi",
                    "task_id": command.task_id,
                    "task_revision": command.task_revision,
                    "task_revision_hash": command.task_revision_hash,
                    "goal_contract_hash": command.goal_contract_hash,
                    "candidate_id": command.candidate_id,
                    "candidate_set_hash": command.candidate_set_hash,
                    "verification_report_id": command.verification_report_id,
                    "verification_report_hash": command.verification_report_hash,
                    "delivery_spec_hash": command.delivery_spec_hash,
                    "publication_key": command.publication_key,
                },
            )
            (staging / "manifest.json").write_text(
                manifest.model_dump_json(indent=2, exclude={"user_id"}),
                encoding="utf-8",
            )
            # committing 是取消线性化点；进入前最后一次读取业务门禁。
            gate = self._gate_reader(command)
            if gate.cancel_requested or gate.p0_blocked:
                reason = "任务已取消" if gate.cancel_requested else "P0 发布门已阻断"
                shutil.rmtree(staging)
                self._repository.set_intent_status(
                    command.publication_key,
                    "aborted",
                    error={"reason": reason},
                )
                raise ValueError(reason)
            commit_token = uuid.uuid4().hex
            self._repository.set_intent_status(
                command.publication_key,
                "committing",
                commit_token=commit_token,
                manifest=manifest,
            )
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_dir.exists():
                # final 目录只能由 committing 恢复路径接管，普通发布不得覆盖未知内容。
                raise RuntimeError("正式发布 final 目录已存在，拒绝覆盖")
            staging.replace(final_dir)
            return self._repository.commit_delivery(command, manifest, final_dir)
        except Exception as exc:
            current = self._repository.get_intent(command.publication_key)
            if current and current["status"] not in {
                "committing",
                "published",
                "aborted",
            }:
                self._repository.set_intent_status(
                    command.publication_key,
                    "failed",
                    error={"reason": str(exc)[:1000]},
                )
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _recover_commit(
        self,
        command: PublishCommand,
        final_dir: Path,
    ) -> DeliveryManifest:
        manifest_path = final_dir / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("提交恢复缺少冻结 Manifest")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = DeliveryManifest.model_validate(
            {**payload, "user_id": command.owner_id}
        )
        self._verify_published(manifest, final_dir)
        return self._repository.commit_delivery(command, manifest, final_dir)

    @staticmethod
    def _verify_published(manifest: DeliveryManifest, final_dir: Path) -> None:
        for output in manifest.outputs:
            path = (final_dir / output.filename).resolve()
            if not path.is_file() or path.stat().st_size != output.size_bytes:
                raise RuntimeError("正式交付文件缺失或大小不一致")
            if _sha256(path) != output.sha256:
                raise RuntimeError("正式交付文件哈希不一致")
