# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.delivery_publishing.models import (
    CandidateRef,
    DeliverySpec,
    PublicationGate,
    PublishCommand,
)
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher
from src.evaluation.formal_delivery import (
    publish_and_qualify_formal_delivery,
    publish_runtime_result_as_formal_delivery,
    qualify_formal_delivery,
)
from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeStatus,
    SourceInput,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)


def _formal_publish_context(
    root: Path,
) -> tuple[DeliveryPublishingRepository, DeliveryPublisher, PublishCommand]:
    candidate = root / "workspace" / "result.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text('{"answer": 42}', encoding="utf-8")
    candidate_ref = CandidateRef(
        artifact_id="candidate-json",
        filename=candidate.name,
        format="json",
        sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        size_bytes=candidate.stat().st_size,
    )
    command = PublishCommand.build(
        owner_id="g1-eval",
        task_id="g1-safety",
        task_revision=1,
        task_revision_hash="1" * 64,
        goal_contract_hash="2" * 64,
        run_id="run-g1-safety",
        candidates=(candidate_ref,),
        verification_report_id="verification-safety",
        verification_report_hash="3" * 64,
        verification_status="passed",
        delivery_spec=DeliverySpec(
            requested_formats=("json",),
            output_name="G1 安全结果",
            requested_file_count=1,
        ),
        source_snapshot_refs=("upload-json:" + "4" * 64,),
    )
    repository = DeliveryPublishingRepository(root / "evaluation.db")
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=root / "deliveries",
        candidate_resolver=lambda _command: {"candidate-json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )
    return repository, publisher, command


def test_candidate_without_formal_delivery_cannot_pass_g1(tmp_path: Path) -> None:
    repository = DeliveryPublishingRepository(tmp_path / "evaluation.db")

    result = qualify_formal_delivery(
        repository=repository,
        owner_id="g1-eval",
        run_id="run-candidate-only",
        expected_formats=("json",),
        output_root=tmp_path / "deliveries",
    )

    assert result.model_dump(mode="json") == {
        "passed": False,
        "reason_code": "formal_delivery_missing",
        "delivery_id": None,
        "output_ids": [],
        "qa_passed": False,
        "details": ["没有找到已持久化的正式 Delivery"],
    }


def test_persisted_delivery_with_independent_qa_can_pass_g1(tmp_path: Path) -> None:
    candidate = tmp_path / "workspace" / "result.json"
    candidate.parent.mkdir()
    candidate.write_text(
        json.dumps({"items": [{"name": "张三", "amount": 10}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    candidate_ref = CandidateRef(
        artifact_id="candidate-json",
        filename=candidate.name,
        format="json",
        sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        size_bytes=candidate.stat().st_size,
    )
    command = PublishCommand.build(
        owner_id="g1-eval",
        task_id="g1-json-1",
        task_revision=1,
        task_revision_hash="1" * 64,
        goal_contract_hash="2" * 64,
        run_id="run-formal-delivery",
        candidates=(candidate_ref,),
        verification_report_id="verification-json-1",
        verification_report_hash="3" * 64,
        verification_status="passed",
        delivery_spec=DeliverySpec(
            requested_formats=("json",),
            output_name="G1 JSON 结果",
            requested_file_count=1,
        ),
        source_snapshot_refs=("upload-json:" + "4" * 64,),
    )
    repository = DeliveryPublishingRepository(tmp_path / "evaluation.db")
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate-json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )
    result = publish_and_qualify_formal_delivery(
        publisher=publisher,
        repository=repository,
        command=command,
        actor_id="g1-eval",
        output_root=tmp_path / "deliveries",
    )

    assert (
        result.passed,
        result.delivery_id is not None,
        len(result.output_ids),
        result.output_ids[0].startswith("output_") if result.output_ids else False,
        result.reason_code,
        result.qa_passed,
    ) == (True, True, 1, True, "formal_delivery_valid", True)


def test_runtime_result_is_published_before_g1_scoring(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"source": true}', encoding="utf-8")
    candidate = tmp_path / "workspace" / "result.json"
    candidate.parent.mkdir()
    candidate.write_text('{"answer": 42}', encoding="utf-8")
    request = PiRuntimeRequest(
        user_id="g1-eval",
        task_id="g1-json-runtime",
        revision=1,
        objective_text="提取答案",
        requested_output_formats=("json",),
        sources=(SourceInput(
            upload_id="upload-json",
            original_name=source.name,
            host_path=source,
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            media_type="application/json",
        ),),
        permission_profile=PermissionProfile.STANDARD,
        model="test-model",
        base_url="http://127.0.0.1:1/v1",
        api_key="test-only",
    )
    result = PiRuntimeResult(
        status=RuntimeStatus.CANDIDATE_READY,
        run_id="pi-run-json",
        workspace_root=candidate.parent,
        candidates=(CandidateArtifact(
            artifact_id="candidate-json",
            filename=candidate.name,
            format="json",
            host_path=candidate,
            sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            size_bytes=candidate.stat().st_size,
            openable=True,
            qa_checks=("reopened",),
        ),),
        verification=VerificationReport(
            status=VerificationStatus.PASSED,
            summary="候选与目标一致",
            checks=(VerificationCheck(
                code="content",
                passed=True,
                summary="内容正确",
            ),),
            evidence_count=1,
            formal_delivery_eligible=True,
        ),
    )
    repository = DeliveryPublishingRepository(tmp_path / "evaluation.db")

    qualification = publish_runtime_result_as_formal_delivery(
        repository=repository,
        output_root=tmp_path / "deliveries",
        request=request,
        result=result,
        output_name="G1 JSON 结果",
    )

    assert (
        qualification.passed,
        qualification.reason_code,
        qualification.qa_passed,
        len(qualification.output_ids),
        repository.latest_delivery(request.user_id, result.run_id) is not None,
    ) == (True, "formal_delivery_valid", True, 1, True)


def test_cross_owner_cannot_reuse_formal_delivery_score(tmp_path: Path) -> None:
    repository, publisher, command = _formal_publish_context(tmp_path)
    qualification = publish_and_qualify_formal_delivery(
        publisher=publisher,
        repository=repository,
        command=command,
        actor_id=command.owner_id,
        output_root=tmp_path / "deliveries",
    )

    cross_owner = qualify_formal_delivery(
        repository=repository,
        owner_id="another-owner",
        run_id=command.run_id,
        expected_formats=("json",),
        output_root=tmp_path / "deliveries",
    )

    assert qualification.passed is True
    assert cross_owner.reason_code == "formal_delivery_missing"
    assert cross_owner.passed is False


def test_tampered_formal_output_fails_independent_scoring(tmp_path: Path) -> None:
    repository, publisher, command = _formal_publish_context(tmp_path)
    qualification = publish_and_qualify_formal_delivery(
        publisher=publisher,
        repository=repository,
        command=command,
        actor_id=command.owner_id,
        output_root=tmp_path / "deliveries",
    )
    persisted = repository.get_output(command.owner_id, qualification.output_ids[0])
    assert persisted is not None
    Path(str(persisted["file_path"])).write_text('{"tampered": true}', encoding="utf-8")

    rescored = qualify_formal_delivery(
        repository=repository,
        owner_id=command.owner_id,
        run_id=command.run_id,
        expected_formats=("json",),
        output_root=tmp_path / "deliveries",
    )

    assert rescored.reason_code == "formal_delivery_invalid"
    assert rescored.passed is False


def test_symlink_output_root_fails_independent_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, publisher, command = _formal_publish_context(tmp_path)
    qualification = publish_and_qualify_formal_delivery(
        publisher=publisher,
        repository=repository,
        command=command,
        actor_id=command.owner_id,
        output_root=tmp_path / "deliveries",
    )
    output_root = tmp_path / "deliveries"
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path.absolute() == output_root.absolute(),
    )

    rescored = qualify_formal_delivery(
        repository=repository,
        owner_id=command.owner_id,
        run_id=command.run_id,
        expected_formats=("json",),
        output_root=output_root,
    )

    assert qualification.passed is True
    assert rescored.reason_code == "formal_delivery_invalid"
    assert rescored.details == ("正式交付根目录不得是符号链接",)


def test_symlink_stored_output_fails_independent_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, publisher, command = _formal_publish_context(tmp_path)
    qualification = publish_and_qualify_formal_delivery(
        publisher=publisher,
        repository=repository,
        command=command,
        actor_id=command.owner_id,
        output_root=tmp_path / "deliveries",
    )
    persisted = repository.get_output(command.owner_id, qualification.output_ids[0])
    assert persisted is not None
    stored_path = Path(str(persisted["file_path"]))
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path.absolute() == stored_path.absolute(),
    )

    rescored = qualify_formal_delivery(
        repository=repository,
        owner_id=command.owner_id,
        run_id=command.run_id,
        expected_formats=("json",),
        output_root=tmp_path / "deliveries",
    )

    assert rescored.reason_code == "formal_delivery_invalid"
    assert rescored.details == ("正式 output 路径包含符号链接",)


def test_non_owner_actor_cannot_publish_for_g1(tmp_path: Path) -> None:
    repository, publisher, command = _formal_publish_context(tmp_path)

    with pytest.raises(PermissionError, match="不是任务所有者"):
        publish_and_qualify_formal_delivery(
            publisher=publisher,
            repository=repository,
            command=command,
            actor_id="another-owner",
            output_root=tmp_path / "deliveries",
        )

    assert repository.latest_delivery(command.owner_id, command.run_id) is None
