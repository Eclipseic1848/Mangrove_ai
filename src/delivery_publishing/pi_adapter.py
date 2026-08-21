# -*- coding: utf-8 -*-
"""Pi CandidateAdapter：从服务端冻结记录构造发布命令并解析候选。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from src.agentic_runtime.repository import AgenticRuntimeRepository

from .models import (
    CandidateRef,
    DeliverySpec,
    PublishCommand,
    TableOutputContract,
    canonical_hash,
    with_table_output_contracts,
)


class PiCandidateAdapter:
    """只信任 Owner 隔离的 Repository、TaskRevision 和 UploadStore。"""

    def __init__(
        self,
        *,
        runtime_repository: AgenticRuntimeRepository,
        workspace_store: Any,
        upload_store: Any,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._workspace_store = workspace_store
        self._upload_store = upload_store

    def build_command(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> PublishCommand:
        runtime = self._runtime_repository.get(owner_id, task_id, revision)
        task = self._workspace_store.get_semantic_workspace_task(
            owner_id, task_id
        )
        task_revision = self._workspace_store.get_semantic_workspace_revision(
            owner_id, task_id, revision
        )
        if runtime is None or task is None or task_revision is None:
            raise PermissionError("任务、Runtime 或 revision 不存在或无权访问")
        if not runtime["run_id"]:
            raise ValueError("Pi Run 尚未冻结")
        verification = runtime["verification"]
        if verification is None:
            raise ValueError("候选缺少独立 VerificationReport")
        request = runtime["request"] or {}
        if request.get("objective_text") != task_revision["objective_text"]:
            raise ValueError("Runtime 目标与冻结 TaskRevision 不一致")
        requested_formats = tuple(task_revision["output_formats"])
        if (
            tuple(request.get("requested_output_formats") or ())
            != requested_formats
        ):
            raise ValueError("Runtime 输出格式与冻结 TaskRevision 不一致")
        try:
            runtime_table_output_contracts = tuple(
                TableOutputContract.model_validate(item)
                for item in request.get("table_output_contracts") or ()
            )
            table_output_contracts = tuple(
                TableOutputContract.model_validate(item)
                for item in task_revision.get("table_output_contracts") or ()
            )
        except Exception as exc:
            # Publisher 只能继承 Runtime 已冻结的结构契约，不能在发布时猜测或修补。
            raise ValueError("Runtime 表格输出契约无效") from exc
        if runtime_table_output_contracts != table_output_contracts:
            raise ValueError("Runtime 表格输出契约与冻结 TaskRevision 不一致")

        source_refs: list[str] = []
        source_hashes: dict[str, str] = {}
        for upload_id in task["upload_ids"]:
            upload = self._upload_store.resolve(owner_id, upload_id)
            source_hashes[upload_id] = upload.sha256
            source_refs.append(f"{upload_id}:{upload.sha256}")
        frozen_request_sources = {
            str(item.get("upload_id")): str(item.get("sha256"))
            for item in request.get("sources") or []
            if isinstance(item, dict)
        }
        if frozen_request_sources != source_hashes:
            raise ValueError("Runtime 来源快照与当前 TaskRevision 不一致")

        candidates = tuple(
            CandidateRef(
                artifact_id=item.artifact_id,
                filename=item.filename,
                format=item.format,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
            )
            for item in runtime["candidates"]
        )
        candidate_set_hash = canonical_hash(
            [
                item.model_dump(mode="json")
                for item in sorted(
                    candidates,
                    key=lambda value: value.artifact_id,
                )
            ]
        )
        if runtime["verified_candidate_set_hash"] != candidate_set_hash:
            raise ValueError("VerificationReport 未绑定当前 CandidateSet 哈希")
        revision_payload = with_table_output_contracts({
            "owner_id": owner_id,
            "task_id": task_id,
            "revision": revision,
            "objective_text": task_revision["objective_text"],
            "output_formats": requested_formats,
            "source_snapshot_refs": sorted(source_refs),
        }, table_output_contracts)
        goal_contract_payload = with_table_output_contracts({
            "objective_text": task_revision["objective_text"],
            "requested_output_formats": requested_formats,
            "source_snapshot_refs": sorted(source_refs),
        }, table_output_contracts)
        verification_payload = verification.model_dump(mode="json")
        verification_hash = canonical_hash(verification_payload)
        return PublishCommand.build(
            owner_id=owner_id,
            task_id=task_id,
            task_revision=revision,
            task_revision_hash=canonical_hash(revision_payload),
            goal_contract_hash=canonical_hash(goal_contract_payload),
            run_id=runtime["run_id"],
            candidates=candidates,
            verification_report_id=f"verification_{verification_hash[:16]}",
            verification_report_hash=verification_hash,
            verification_status=verification.status.value,
            delivery_spec=DeliverySpec(
                requested_formats=requested_formats,
                output_name=task["title"] or "交付结果",
                requested_file_count=len(requested_formats),
                table_output_contracts=table_output_contracts,
            ),
            source_snapshot_refs=tuple(sorted(source_refs)),
        )

    def resolve_candidates(
        self,
        command: PublishCommand,
    ) -> Mapping[str, Path]:
        runtime = self._runtime_repository.get(
            command.owner_id,
            command.task_id,
            command.task_revision,
        )
        if runtime is None or runtime["run_id"] != command.run_id:
            raise PermissionError("Pi Run 不存在、Owner 不匹配或已切换")
        workspace_root = Path(runtime["workspace_root"] or "").resolve()
        resolved: dict[str, Path] = {}
        for item in runtime["candidates"]:
            path = item.host_path.resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or workspace_root not in path.parents
            ):
                raise ValueError("候选路径不属于当前 Owner/Run 工作区")
            resolved[item.artifact_id] = path
        return resolved
