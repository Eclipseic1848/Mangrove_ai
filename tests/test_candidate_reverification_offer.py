# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
    RuntimeStatus,
    RuntimeTaskConfig,
    RuntimeVersion,
    SourceInput,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.candidate_verification import (
    AttemptReason,
    AttemptStatus,
    CandidateVerificationService,
    ReverificationBlocker,
    SqliteCandidateVerificationRepository,
    VerifierRulesetBinding,
    migrate_candidate_verification,
)


_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class _FailingVerifier:
    async def verify(self, *, request, candidates, manifest_path):
        del request, candidates, manifest_path
        return VerificationReport(
            status=VerificationStatus.FAILED,
            summary="候选未通过独立验证",
            checks=(
                VerificationCheck(
                    code="artifact_count",
                    passed=False,
                    summary="候选数量不符合交付规格",
                ),
            ),
            evidence_count=0,
            formal_delivery_eligible=False,
        )


class _InconclusiveVerifier:
    async def verify(self, *, request, candidates, manifest_path):
        del request, candidates, manifest_path
        return VerificationReport(
            status=VerificationStatus.INCONCLUSIVE,
            summary="独立语义验证暂时没有可靠结论",
            checks=(
                VerificationCheck(
                    code="semantic_goal",
                    passed=False,
                    summary="语义验证服务暂时不可用",
                ),
            ),
            evidence_count=1,
            formal_delivery_eligible=False,
        )


class _PassingVerifier:
    async def verify(self, *, request, candidates, manifest_path):
        del request, candidates, manifest_path
        return VerificationReport(
            status=VerificationStatus.PASSED,
            summary="候选已通过独立验证",
            checks=(
                VerificationCheck(
                    code="artifact_set",
                    passed=True,
                    summary="候选集合与清单一致",
                ),
            ),
            evidence_count=1,
            formal_delivery_eligible=False,
        )


class _FixedRulesetResolver:
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def _binding(self) -> VerifierRulesetBinding:
        ruleset_hash = self._marker * 64
        return VerifierRulesetBinding(
            verifier_ruleset_hash=ruleset_hash,
            verifier_code_commit=self._marker * 40,
            verifier_source_hash=self._marker * 64,
            verifier_execution_identity_hash=self._marker * 64,
            verifier_ruleset_manifest_json=json.dumps(
                {
                    "schema_version": 1,
                    "verifier_ruleset_hash": ruleset_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def resolve(self, _verifier) -> VerifierRulesetBinding:
        return self._binding()

    def resolve_target(self) -> VerifierRulesetBinding:
        return self._binding()


class _UnavailableRulesetResolver(_FixedRulesetResolver):
    def resolve_target(self) -> VerifierRulesetBinding:
        raise RuntimeError("VerifierRuleset 相关源码存在未提交语义变化")


class _AllowVerifierAdapter:
    def assert_verifier_binding(self, _request, _run_id, _verifier) -> None:
        return None


class _AllowReverificationAuthority:
    def blockers(self, _request, _run_id):
        return ()


class _BlockingReverificationAuthority:
    def blockers(self, _request, _run_id):
        return (ReverificationBlocker.PROVIDER_BINDING_UNAVAILABLE,)


def _manifest_json(candidate: CandidateArtifact) -> str:
    return json.dumps(
        {
            "version": 1,
            "artifacts": [
                {
                    "filename": candidate.filename,
                    "format": candidate.format,
                    "description": "测试候选",
                    "evidence": [
                        {
                            "source": "upload-a",
                            "locator": "line:1",
                            "quote": "已确认来源",
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )


def _service(
    repository: SqliteCandidateVerificationRepository,
    marker: str,
    *,
    p0_blocked: bool = False,
    p0_reader=None,
    authority=None,
    provider_grant_revoker=None,
) -> CandidateVerificationService:
    return CandidateVerificationService(
        repository=repository,
        ruleset_resolver=_FixedRulesetResolver(marker),
        p0_reader=p0_reader or (lambda _request: p0_blocked),
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=lambda _event_type, _attempt: None,
        reverification_authority=authority or _AllowReverificationAuthority(),
        provider_grant_revoker=provider_grant_revoker,
        clock=lambda: _NOW,
    )


def _prepare_candidate(
    tmp_path: Path,
    verifier=None,
    *,
    provider: bool = False,
    valid_manifest: bool = True,
):
    database = tmp_path / "workspace.db"
    runtime_repository = AgenticRuntimeRepository(database)
    runtime_repository.register(
        RuntimeTaskConfig(
            user_id="owner-a",
            task_id="task-a",
            revision=1,
            runtime_version=RuntimeVersion.PI,
            permission_profile=PermissionProfile.STANDARD,
            model_connection_id=("connection-a" if provider else None),
            model_connection_version=("version-a" if provider else None),
            model_connection_model=("provider-model-a" if provider else None),
            external_api_confirmed=provider,
        )
    )
    source = tmp_path / "source.txt"
    source.write_text("已确认来源", encoding="utf-8")
    request_values = dict(
        user_id="owner-a",
        task_id="task-a",
        revision=1,
        objective_text="读取来源并输出 JSON",
        requested_output_formats=("json",),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name=source.name,
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
    )
    if provider:
        request_values.update(
            {
                "model_connection_id": "connection-a",
                "model_connection_version": "version-a",
                "model_connection_model": "provider-model-a",
                "external_api_confirmed": True,
            }
        )
    else:
        request_values.update(
            {
                "model": "local-model",
                "base_url": "http://127.0.0.1:18080/v1",
                "api_key": "test-only",
            }
        )
    request = PiRuntimeRequest(**request_values)
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        request=request.model_dump(mode="json", exclude={"api_key"}),
    )
    migrate_candidate_verification(database, tmp_path / "before-cv.db")
    repository = SqliteCandidateVerificationRepository(database)
    workspace = tmp_path / "runtime-workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    candidate_path = output / "result.json"
    candidate_path.write_text('{"ok":true}', encoding="utf-8")
    candidate_digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    candidate = CandidateArtifact(
        artifact_id=f"candidate_{candidate_digest[:16]}",
        filename=candidate_path.name,
        format="json",
        host_path=candidate_path,
        sha256=candidate_digest,
        size_bytes=candidate_path.stat().st_size,
        openable=True,
        qa_checks=("non_empty", "reopened"),
    )
    manifest = output / "candidate-manifest.json"
    manifest.write_text(
        _manifest_json(candidate) if valid_manifest else '{"artifacts":[]}',
        encoding="utf-8",
    )
    attempt = asyncio.run(
        _service(repository, "5").verify_initial_current(
            request=request,
            run_id="pi_run_0123456789abcdef",
            candidates=(candidate,),
            manifest_path=manifest,
            verifier=verifier or _FailingVerifier(),
            actor_id="owner-a",
        )
    )
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        status=RuntimeStatus.CANDIDATE_READY,
        workspace_root=workspace,
    )
    return repository, attempt


def test_prepare_publication_returns_only_the_exact_current_passed_attempt(
    tmp_path: Path,
) -> None:
    repository, attempt = _prepare_candidate(
        tmp_path,
        verifier=_PassingVerifier(),
    )
    service = _service(repository, "5")

    prepared = service.prepare_publication(
        owner_id="owner-a",
        task_id="task-a",
        revision=1,
        attempt_id=attempt.attempt_id,
    )

    assert prepared == attempt
    assert prepared.status is AttemptStatus.PASSED
    try:
        service.prepare_publication(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            attempt_id="verification-missing",
        )
    except PermissionError as exc:
        assert "不存在或 Owner 不匹配" in str(exc)
    else:
        raise AssertionError("不存在的 Attempt 必须失败关闭")


def test_prepare_publication_rechecks_p0_drift_and_current_ruleset(
    tmp_path: Path,
) -> None:
    repository, attempt = _prepare_candidate(
        tmp_path,
        verifier=_PassingVerifier(),
    )

    for service, expected in (
        (_service(repository, "5", p0_blocked=True), "显式发布资格"),
        (_service(repository, "6"), "规则身份已变化"),
    ):
        try:
            service.prepare_publication(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                attempt_id=attempt.attempt_id,
            )
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("P0 或规则身份变化时必须失败关闭")

    candidate = tmp_path / "runtime-workspace" / "output" / "result.json"
    candidate.write_text('{"ok":false}', encoding="utf-8")
    try:
        _service(repository, "5").prepare_publication(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            attempt_id=attempt.attempt_id,
        )
    except ValueError as exc:
        assert "显式发布资格" in str(exc)
    else:
        raise AssertionError("候选字节漂移时必须失败关闭")


def test_prepare_publication_does_not_replace_requested_attempt_with_latest(
    tmp_path: Path,
) -> None:
    repository, attempt = _prepare_candidate(
        tmp_path,
        verifier=_PassingVerifier(),
    )
    requested = attempt.model_copy(
        update={
            "attempt_id": "verification-newer-passed",
            "previous_attempt_id": attempt.attempt_id,
            "idempotency_key": "newer-passed",
            "request_hash": "9" * 64,
            "status": AttemptStatus.REQUESTED,
            "report_json": None,
            "report_hash": None,
            "created_at": attempt.created_at + timedelta(seconds=1),
            "started_at": None,
            "finished_at": None,
        }
    )
    repository.create_and_start_if_p0_allowed(
        requested,
        started_at=attempt.created_at + timedelta(seconds=2),
    )
    newer = repository.finish(
        "owner-a",
        requested.attempt_id,
        status=AttemptStatus.PASSED,
        report_json=attempt.report_json,
        report_hash=attempt.report_hash,
        finished_at=attempt.created_at + timedelta(seconds=3),
    )
    service = _service(repository, "5")

    try:
        service.prepare_publication(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            attempt_id=attempt.attempt_id,
        )
    except ValueError as exc:
        assert "不是当前精确结果" in str(exc)
    else:
        raise AssertionError("latest 指针变化不得替换请求中的精确 Attempt")
    assert service.prepare_publication(
        owner_id="owner-a",
        task_id="task-a",
        revision=1,
        attempt_id=newer.attempt_id,
    ) == newer


def _prepare_legacy_candidate(tmp_path: Path):
    database = tmp_path / "workspace.db"
    runtime_repository = AgenticRuntimeRepository(database)
    runtime_repository.register(
        RuntimeTaskConfig(
            user_id="owner-a",
            task_id="task-a",
            revision=1,
            runtime_version=RuntimeVersion.PI,
            permission_profile=PermissionProfile.STANDARD,
        )
    )
    source = tmp_path / "source.txt"
    source.write_text("已确认来源", encoding="utf-8")
    request = PiRuntimeRequest(
        user_id="owner-a",
        task_id="task-a",
        revision=1,
        objective_text="读取来源并输出 JSON",
        requested_output_formats=("json",),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name=source.name,
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model="local-model",
        base_url="http://127.0.0.1:18080/v1",
        api_key="test-only",
    )
    workspace = tmp_path / "runtime-workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    candidate_path = output / "result.json"
    candidate_path.write_text('{"ok":true}', encoding="utf-8")
    candidate_digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    candidate = CandidateArtifact(
        artifact_id=f"candidate_{candidate_digest[:16]}",
        filename=candidate_path.name,
        format="json",
        host_path=candidate_path,
        sha256=candidate_digest,
        size_bytes=candidate_path.stat().st_size,
        openable=True,
        qa_checks=("non_empty", "reopened"),
    )
    (output / "candidate-manifest.json").write_text(
        _manifest_json(candidate), encoding="utf-8"
    )
    report = asyncio.run(
        _FailingVerifier().verify(
            request=request,
            candidates=(candidate,),
            manifest_path=output / "candidate-manifest.json",
        )
    )
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        status=RuntimeStatus.CANDIDATE_READY,
        run_id="pi_run_0123456789abcdef",
        workspace_root=workspace,
        request=request.model_dump(mode="json", exclude={"api_key"}),
        candidates=(candidate,),
        verification=report,
    )
    migrate_candidate_verification(database, tmp_path / "before-cv.db")
    return SqliteCandidateVerificationRepository(database)


def test_failed_candidate_is_eligible_when_ruleset_changed(tmp_path: Path) -> None:
    repository, previous = _prepare_candidate(tmp_path)

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is True
    assert offer.reason is AttemptReason.RULESET_CHANGED
    assert offer.previous_attempt_id == previous.attempt_id
    assert offer.ruleset_changed is True
    assert offer.candidate_count == 1
    assert offer.candidate_formats == ("json",)
    assert offer.requires_provider is False
    assert offer.egress_categories == ()
    assert offer.egress_summary == "本次不外发"


def test_failed_candidate_is_not_eligible_under_same_ruleset(tmp_path: Path) -> None:
    repository, previous = _prepare_candidate(tmp_path)

    offer = asyncio.run(
        _service(repository, "5").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.previous_attempt_id == previous.attempt_id
    assert offer.ruleset_changed is False
    assert offer.blockers == ("ruleset_unchanged",)


def test_inconclusive_candidate_maps_to_semantic_retry(tmp_path: Path) -> None:
    repository, previous = _prepare_candidate(tmp_path, _InconclusiveVerifier())

    offer = asyncio.run(
        _service(repository, "5").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is True
    assert offer.reason is AttemptReason.SEMANTIC_INCONCLUSIVE
    assert offer.previous_attempt_id == previous.attempt_id
    assert offer.ruleset_changed is False


def test_candidate_drift_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    (tmp_path / "runtime-workspace" / "output" / "result.json").write_text(
        '{"tampered":true}',
        encoding="utf-8",
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.reason is None
    assert offer.blockers == ("candidate_drift",)


def test_candidate_projection_drift_returns_blocked_offer(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    changed_path = tmp_path / "runtime-workspace" / "output" / "changed.json"
    changed_path.write_text('{"changed":true}', encoding="utf-8")
    changed_digest = hashlib.sha256(changed_path.read_bytes()).hexdigest()
    changed = CandidateArtifact(
        artifact_id=f"candidate_{changed_digest[:16]}",
        filename=changed_path.name,
        format="json",
        host_path=changed_path,
        sha256=changed_digest,
        size_bytes=changed_path.stat().st_size,
        openable=True,
        qa_checks=("non_empty", "reopened"),
    )
    AgenticRuntimeRepository(tmp_path / "workspace.db").update(
        "owner-a",
        "task-a",
        1,
        candidates=(changed,),
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("candidate_drift", "manifest_drift")


def test_manifest_drift_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    (tmp_path / "runtime-workspace" / "output" / "candidate-manifest.json").write_text(
        '{"artifacts":[{"tampered":true}]}',
        encoding="utf-8",
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("manifest_drift",)


def test_invalid_frozen_manifest_identity_blocks_reverification(
    tmp_path: Path,
) -> None:
    repository, _previous = _prepare_candidate(
        tmp_path,
        valid_manifest=False,
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("manifest_drift",)


def test_source_drift_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    (tmp_path / "source.txt").write_text("来源已被修改", encoding="utf-8")

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("source_drift",)


def test_goal_contract_drift_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    runtime_repository = AgenticRuntimeRepository(tmp_path / "workspace.db")
    row = runtime_repository.get("owner-a", "task-a", 1)
    assert row is not None and row["request"] is not None
    request_values = dict(row["request"])
    request_values["api_key"] = "local-runtime"
    changed = PiRuntimeRequest.model_validate(request_values).model_copy(
        update={"objective_text": "已改变的任务目标"}
    )
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        request=changed.model_dump(mode="json", exclude={"api_key"}),
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("goal_contract_drift",)


def test_delivery_spec_drift_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    runtime_repository = AgenticRuntimeRepository(tmp_path / "workspace.db")
    row = runtime_repository.get("owner-a", "task-a", 1)
    assert row is not None and row["request"] is not None
    request_values = dict(row["request"])
    request_values["api_key"] = "local-runtime"
    changed = PiRuntimeRequest.model_validate(request_values).model_copy(
        update={"requested_output_formats": ("txt",)}
    )
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        request=changed.model_dump(mode="json", exclude={"api_key"}),
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("delivery_spec_drift",)


def test_active_attempt_blocks_reverification(tmp_path: Path) -> None:
    repository, previous = _prepare_candidate(tmp_path)
    repository.create(
        previous.model_copy(
            update={
                "attempt_id": "verification_active",
                "previous_attempt_id": previous.attempt_id,
                "reason_code": AttemptReason.RULESET_CHANGED,
                "idempotency_key": "active-attempt",
                "request_hash": "a" * 64,
                "status": AttemptStatus.REQUESTED,
                "report_json": None,
                "report_hash": None,
                "created_at": _NOW + timedelta(seconds=1),
                "started_at": None,
                "finished_at": None,
            }
        )
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("active_attempt",)


def test_outcome_unknown_attempt_blocks_reverification(tmp_path: Path) -> None:
    repository, previous = _prepare_candidate(tmp_path)
    repository.create(
        previous.model_copy(
            update={
                "attempt_id": "verification_unknown",
                "previous_attempt_id": previous.attempt_id,
                "reason_code": AttemptReason.RULESET_CHANGED,
                "idempotency_key": "unknown-attempt",
                "request_hash": "b" * 64,
                "status": AttemptStatus.REQUESTED,
                "report_json": None,
                "report_hash": None,
                "created_at": _NOW + timedelta(seconds=1),
                "started_at": None,
                "finished_at": None,
            }
        )
    )
    repository.start(
        "owner-a",
        "verification_unknown",
        started_at=_NOW + timedelta(seconds=2),
    )
    repository.finish(
        "owner-a",
        "verification_unknown",
        status=AttemptStatus.OUTCOME_UNKNOWN,
        report_json=None,
        report_hash=None,
        finished_at=_NOW + timedelta(seconds=3),
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("outcome_unknown",)


def test_p0_blocked_rejects_reverification_offer(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)

    offer = asyncio.run(
        _service(repository, "9", p0_blocked=True).inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("p0_blocked",)


def test_existing_delivery_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    with sqlite3.connect(tmp_path / "workspace.db") as connection:
        connection.execute(
            "CREATE TABLE formal_delivery_runs ("
            "owner_id TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO formal_delivery_runs VALUES (?, ?, 'succeeded')",
            ("owner-a", "pi_run_0123456789abcdef"),
        )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("delivery_exists",)


def test_legacy_delivery_also_blocks_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    with sqlite3.connect(tmp_path / "workspace.db") as connection:
        connection.execute(
            "CREATE TABLE semantic_delivery_runs ("
            "user_id TEXT NOT NULL, run_id TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO semantic_delivery_runs VALUES (?, ?)",
            ("owner-a", "pi_run_0123456789abcdef"),
        )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("delivery_exists",)


def test_legacy_unversioned_attempt_is_not_automatically_eligible(
    tmp_path: Path,
) -> None:
    repository = _prepare_legacy_candidate(tmp_path)

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.ruleset_changed is None
    assert offer.ruleset_change_summary == "当前验证规则身份暂时无法证明"
    assert offer.blockers == ("legacy_unversioned",)


def test_offer_queries_leave_database_logically_unchanged(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path)

    def logical_fingerprint() -> str:
        with sqlite3.connect(tmp_path / "workspace.db") as connection:
            dump = "\n".join(connection.iterdump())
        return hashlib.sha256(dump.encode("utf-8")).hexdigest()

    before = logical_fingerprint()
    for _ in range(2):
        asyncio.run(
            _service(repository, "9").inspect_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
            )
        )

    assert logical_fingerprint() == before


def test_provider_offer_exposes_only_product_egress_summary(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path, provider=True)

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.requires_provider is True
    assert offer.connection_id == "connection-a"
    assert offer.model_id == "provider-model-a"
    assert offer.egress_categories == (
        "task_goal",
        "candidate_previews",
        "source_evidence",
    )
    assert offer.egress_summary == "将外发任务目标、候选预览和来源证据"
    serialized = offer.model_dump_json().lower()
    for forbidden in (
        "base_url",
        "api_key",
        "prompt",
        "host_path",
        "candidate_verifier.py",
    ):
        assert forbidden not in serialized


def test_authority_blocker_prevents_provider_reverification(tmp_path: Path) -> None:
    repository, _previous = _prepare_candidate(tmp_path, provider=True)

    offer = asyncio.run(
        _service(
            repository,
            "9",
            authority=_BlockingReverificationAuthority(),
        ).inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("provider_binding_unavailable",)


def test_passed_candidate_without_delivery_is_waiting_for_publication(
    tmp_path: Path,
) -> None:
    repository, _previous = _prepare_candidate(tmp_path, _PassingVerifier())

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.blockers == ("already_passed",)
    assert offer.awaiting_publication is True


def test_passed_candidate_with_drift_is_not_waiting_for_publication(
    tmp_path: Path,
) -> None:
    repository, _previous = _prepare_candidate(tmp_path, _PassingVerifier())
    (tmp_path / "runtime-workspace" / "output" / "result.json").write_text(
        '{"tampered":true}',
        encoding="utf-8",
    )

    offer = asyncio.run(
        _service(repository, "9").inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.awaiting_publication is False
    assert "candidate_drift" in offer.blockers


def test_unprovable_current_ruleset_fails_closed_without_internal_details(
    tmp_path: Path,
) -> None:
    repository, _previous = _prepare_candidate(tmp_path)
    service = CandidateVerificationService(
        repository=repository,
        ruleset_resolver=_UnavailableRulesetResolver("9"),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=lambda _event_type, _attempt: None,
        reverification_authority=_AllowReverificationAuthority(),
        clock=lambda: _NOW,
    )

    offer = asyncio.run(
        service.inspect_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
        )
    )

    assert offer.eligible is False
    assert offer.ruleset_changed is None
    assert offer.blockers == ("ruleset_unavailable",)
    assert "源码" not in offer.model_dump_json()
