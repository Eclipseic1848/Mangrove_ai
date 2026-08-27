# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
    RuntimeTaskConfig,
    RuntimeStatus,
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
    SqliteCandidateVerificationRepository,
    VerifierRulesetBinding,
)
from tests.database_migration_helpers import migrated_webui_database


_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


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


class _CountingPassingVerifier(_PassingVerifier):
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, *, request, candidates, manifest_path):
        self.calls += 1
        return await super().verify(
            request=request,
            candidates=candidates,
            manifest_path=manifest_path,
        )


class _ExplodingVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("synthetic verifier failure")


class _CancellingVerifier:
    async def verify(self, **_kwargs):
        raise asyncio.CancelledError


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


class _PassingSemanticRetryVerifier:
    async def retry_semantic_verification(
        self,
        *,
        request,
        candidates,
        manifest_path,
        previous_report,
    ):
        del request, candidates, manifest_path
        assert previous_report.status is VerificationStatus.INCONCLUSIVE
        return await _PassingVerifier().verify(
            request=None,
            candidates=(),
            manifest_path=None,
        )


class _ExplodingSemanticRetryVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def retry_semantic_verification(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("synthetic semantic retry failure")


class _FixedRulesetResolver:
    def resolve(self, _verifier) -> VerifierRulesetBinding:
        ruleset_hash = "5" * 64
        return VerifierRulesetBinding(
            verifier_ruleset_hash=ruleset_hash,
            verifier_code_commit="6" * 40,
            verifier_source_hash="7" * 64,
            verifier_execution_identity_hash="8" * 64,
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


class _P0FlippingRulesetResolver(_FixedRulesetResolver):
    def __init__(self, database: Path) -> None:
        self._database = database

    def resolve(self, verifier) -> VerifierRulesetBinding:
        with sqlite3.connect(self._database) as connection:
            connection.execute(
                "UPDATE runtime_rollout_state SET p0_blocked=1 WHERE state_id=1"
            )
        return super().resolve(verifier)


class _AllowVerifierAdapter:
    def assert_verifier_binding(self, _request, _run_id, _verifier) -> None:
        return None


def _ignore_event(_event_type, _attempt) -> None:
    return None


def _candidate(tmp_path: Path) -> CandidateArtifact:
    output = tmp_path / "result.json"
    output.write_text('{"ok":true}', encoding="utf-8")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return CandidateArtifact(
        artifact_id=f"candidate_{digest[:16]}",
        filename=output.name,
        format="json",
        host_path=output,
        sha256=digest,
        size_bytes=output.stat().st_size,
        openable=True,
        qa_checks=("non_empty", "reopened"),
    )


def _request(tmp_path: Path) -> PiRuntimeRequest:
    source = tmp_path / "source.txt"
    source.write_text("已确认来源", encoding="utf-8")
    return PiRuntimeRequest(
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


def _prepared_service(tmp_path: Path, *, p0_blocked: bool = False):
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    request = _request(tmp_path)
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        request=request.model_dump(mode="json", exclude={"api_key"}),
    )
    repository = SqliteCandidateVerificationRepository(database)
    events = []
    service = CandidateVerificationService(
        repository=repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: p0_blocked,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=lambda event_type, attempt: events.append(
            (event_type, attempt.status)
        ),
        clock=lambda: _NOW,
    )
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")
    return service, repository, request, _candidate(tmp_path), manifest, events


def test_p0_blocked_rejects_before_attempt_and_verifier_execution(
    tmp_path: Path,
) -> None:
    service, repository, request, candidate, manifest, events = _prepared_service(
        tmp_path,
        p0_blocked=True,
    )
    verifier = _CountingPassingVerifier()

    with pytest.raises(PermissionError, match="P0/Gate"):
        asyncio.run(
            service.verify_initial_current(
                request=request,
                run_id="pi_run_0123456789abcdef",
                candidates=(candidate,),
                manifest_path=manifest,
                verifier=verifier,
                actor_id="owner-a",
            )
        )

    assert verifier.calls == 0
    assert repository.list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="pi_run_0123456789abcdef",
        candidate_set_hash="0" * 64,
    ) == ()
    assert events == []


def test_verifier_exception_closes_attempt_as_inconclusive(
    tmp_path: Path,
) -> None:
    service, repository, request, candidate, manifest, events = _prepared_service(
        tmp_path
    )
    verifier = _ExplodingVerifier()
    arguments = {
        "request": request,
        "run_id": "pi_run_0123456789abcdef",
        "candidates": (candidate,),
        "manifest_path": manifest,
        "verifier": verifier,
        "actor_id": "owner-a",
    }

    with pytest.raises(RuntimeError, match="synthetic verifier failure"):
        asyncio.run(service.verify_initial_current(**arguments))
    replayed = asyncio.run(service.verify_initial_current(**arguments))

    assert replayed.status is AttemptStatus.INCONCLUSIVE
    assert verifier.calls == 1
    assert [status for _, status in events] == [
        AttemptStatus.RUNNING,
        AttemptStatus.INCONCLUSIVE,
    ]
    assert repository.get("owner-a", replayed.attempt_id) == replayed


def test_cancellation_closes_attempt_without_fabricating_report(
    tmp_path: Path,
) -> None:
    service, _repository, request, candidate, manifest, _events = _prepared_service(
        tmp_path
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.verify_initial_current(
                request=request,
                run_id="pi_run_0123456789abcdef",
                candidates=(candidate,),
                manifest_path=manifest,
                verifier=_CancellingVerifier(),
                actor_id="owner-a",
            )
        )

    with sqlite3.connect(tmp_path / "workspace.db") as connection:
        row = connection.execute(
            "SELECT status, report_json FROM candidate_verification_attempts"
        ).fetchone()
    assert row == (AttemptStatus.CANCELLED.value, None)


def test_p0_flip_during_ruleset_resolution_blocks_atomic_attempt_start(
    tmp_path: Path,
) -> None:
    service, repository, request, candidate, manifest, _events = _prepared_service(
        tmp_path
    )
    service._ruleset_resolver = _P0FlippingRulesetResolver(
        tmp_path / "workspace.db"
    )

    with pytest.raises(PermissionError, match="P0/Gate"):
        asyncio.run(
            service.verify_initial_current(
                request=request,
                run_id="pi_run_0123456789abcdef",
                candidates=(candidate,),
                manifest_path=manifest,
                verifier=_PassingVerifier(),
                actor_id="owner-a",
            )
        )

    with sqlite3.connect(tmp_path / "workspace.db") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM candidate_verification_attempts"
        ).fetchone()[0]
    assert count == 0


def test_semantic_retry_exception_projects_new_basis_for_followup_attempt(
    tmp_path: Path,
) -> None:
    service, repository, request, candidate, manifest, _events = _prepared_service(
        tmp_path
    )
    initial = asyncio.run(
        service.verify_initial_current(
            request=request,
            run_id="pi_run_0123456789abcdef",
            candidates=(candidate,),
            manifest_path=manifest,
            verifier=_InconclusiveVerifier(),
            actor_id="owner-a",
        )
    )
    workspace = tmp_path / "runtime-workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    (output / "candidate-manifest.json").write_text(
        manifest.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    AgenticRuntimeRepository(tmp_path / "workspace.db").update(
        "owner-a",
        "task-a",
        1,
        status=RuntimeStatus.CANDIDATE_READY,
        workspace_root=workspace,
    )
    exploding = _ExplodingSemanticRetryVerifier()

    with pytest.raises(RuntimeError, match="synthetic semantic retry failure"):
        asyncio.run(
            service.retry_current_semantic(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                verifier_factory=lambda _request, _run_id: exploding,
            )
        )
    recovered = asyncio.run(
        service.retry_current_semantic(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            verifier_factory=lambda _request, _run_id: (
                _PassingSemanticRetryVerifier()
            ),
        )
    )

    assert initial.status is AttemptStatus.INCONCLUSIVE
    assert exploding.calls == 1
    assert recovered.status is AttemptStatus.PASSED
    history = repository.list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="pi_run_0123456789abcdef",
        candidate_set_hash=initial.candidate_set_hash,
    )
    assert [item.status for item in history] == [
        AttemptStatus.INCONCLUSIVE,
        AttemptStatus.INCONCLUSIVE,
        AttemptStatus.PASSED,
    ]


def test_initial_verification_persists_attempt_and_runtime_projection(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
        request=_request(tmp_path).model_dump(mode="json", exclude={"api_key"}),
    )
    attempt_repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=attempt_repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    candidate = _candidate(tmp_path)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")

    attempt = asyncio.run(
        service.verify_initial(
            request=_request(tmp_path),
            run_id="pi_run_0123456789abcdef",
            candidates=(candidate,),
            manifest_path=manifest,
            verifier=_PassingVerifier(),
            actor_id="owner-a",
            idempotency_key="initial-task-a-r1",
            goal_contract_hash="2" * 64,
            delivery_spec_hash="3" * 64,
        )
    )

    assert attempt.reason_code is AttemptReason.INITIAL
    assert attempt.status is AttemptStatus.PASSED
    assert attempt_repository.get("owner-a", attempt.attempt_id) == attempt
    projected = runtime_repository.get("owner-a", "task-a", 1)
    assert projected is not None
    assert projected["verification"].status is VerificationStatus.PASSED
    assert projected["verified_candidate_set_hash"] == attempt.candidate_set_hash


def test_runtime_projection_failure_rolls_back_attempt_terminal_state(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
        request=_request(tmp_path).model_dump(mode="json", exclude={"api_key"}),
    )
    attempt_repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=attempt_repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    candidate = _candidate(tmp_path)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_runtime_projection "
            "BEFORE UPDATE OF verification_json ON agentic_runtime_runs "
            "BEGIN SELECT RAISE(ABORT, 'projection rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="projection rejected"):
        asyncio.run(
            service.verify_initial(
                request=_request(tmp_path),
                run_id="pi_run_0123456789abcdef",
                candidates=(candidate,),
                manifest_path=manifest,
                verifier=_PassingVerifier(),
                actor_id="owner-a",
                idempotency_key="initial-task-a-r1",
                goal_contract_hash="2" * 64,
                delivery_spec_hash="3" * 64,
            )
        )

    attempt_id = "verification_" + hashlib.sha256(
        b"owner-a\x1finitial-task-a-r1"
    ).hexdigest()[:32]
    attempt = attempt_repository.get("owner-a", attempt_id)
    assert attempt is not None
    assert attempt.status is AttemptStatus.INCONCLUSIVE
    projected = runtime_repository.get("owner-a", "task-a", 1)
    assert projected is not None
    assert projected["verification"] is None
    assert projected["verified_candidate_set_hash"] is None


def test_semantic_retry_appends_attempt_and_preserves_inconclusive_history(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    request = _request(tmp_path)
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
        request=request.model_dump(mode="json", exclude={"api_key"}),
    )
    attempt_repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=attempt_repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    candidate = _candidate(tmp_path)
    candidates = (candidate,)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")
    initial = asyncio.run(
        service.verify_initial(
            request=request,
            run_id="pi_run_0123456789abcdef",
            candidates=candidates,
            manifest_path=manifest,
            verifier=_InconclusiveVerifier(),
            actor_id="owner-a",
            idempotency_key="initial-task-a-r1",
            goal_contract_hash="2" * 64,
            delivery_spec_hash="3" * 64,
        )
    )

    retried = asyncio.run(
        service.retry_semantic(
            request=request,
            run_id="pi_run_0123456789abcdef",
            candidates=candidates,
            manifest_path=manifest,
            verifier=_PassingSemanticRetryVerifier(),
            previous_report=VerificationReport.model_validate_json(
                initial.report_json
            ),
            previous_attempt_id=initial.attempt_id,
            actor_id="owner-a",
            idempotency_key="semantic-retry-task-a-r1",
            goal_contract_hash="2" * 64,
            delivery_spec_hash="3" * 64,
        )
    )

    assert initial.status is AttemptStatus.INCONCLUSIVE
    assert retried.status is AttemptStatus.PASSED
    assert retried.reason_code is AttemptReason.SEMANTIC_INCONCLUSIVE
    assert retried.previous_attempt_id == initial.attempt_id
    history = attempt_repository.list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="pi_run_0123456789abcdef",
        candidate_set_hash=initial.candidate_set_hash,
    )
    assert [item.status for item in history] == [
        AttemptStatus.INCONCLUSIVE,
        AttemptStatus.PASSED,
    ]


def test_cross_wired_runtime_is_rejected_before_verifier_execution(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
    )
    attempt_repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=attempt_repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    verifier = _CountingPassingVerifier()
    candidate = _candidate(tmp_path)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")

    with pytest.raises(PermissionError, match="Runtime"):
        asyncio.run(
            service.verify_initial(
                request=_request(tmp_path).model_copy(
                    update={"task_id": "task-b"}
                ),
                run_id="pi_run_0123456789abcdef",
                candidates=(candidate,),
                manifest_path=manifest,
                verifier=verifier,
                actor_id="owner-a",
                idempotency_key="cross-wired",
                goal_contract_hash="2" * 64,
                delivery_spec_hash="3" * 64,
            )
        )

    assert verifier.calls == 0
    attempt_id = "verification_" + hashlib.sha256(
        b"owner-a\x1fcross-wired"
    ).hexdigest()[:32]
    assert attempt_repository.get("owner-a", attempt_id) is None


def test_failed_initial_verification_is_an_immutable_attempt(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    request = _request(tmp_path)
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
        request=request.model_dump(mode="json", exclude={"api_key"}),
    )
    repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    candidate = _candidate(tmp_path)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")

    attempt = asyncio.run(
        service.verify_initial(
            request=request,
            run_id="pi_run_0123456789abcdef",
            candidates=(candidate,),
            manifest_path=manifest,
            verifier=_FailingVerifier(),
            actor_id="owner-a",
            idempotency_key="failed-initial",
            goal_contract_hash="2" * 64,
            delivery_spec_hash="3" * 64,
        )
    )

    assert attempt.status is AttemptStatus.FAILED
    assert attempt.report_json is not None
    assert json.loads(attempt.report_json)["status"] == "failed"


def test_same_idempotency_request_reuses_attempt_and_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    request = _request(tmp_path)
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
        request=request.model_dump(mode="json", exclude={"api_key"}),
    )
    repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    verifier = _CountingPassingVerifier()
    candidate = _candidate(tmp_path)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")
    arguments = {
        "request": request,
        "run_id": "pi_run_0123456789abcdef",
        "candidates": (candidate,),
        "manifest_path": manifest,
        "verifier": verifier,
        "actor_id": "owner-a",
        "idempotency_key": "same-action",
        "goal_contract_hash": "2" * 64,
        "delivery_spec_hash": "3" * 64,
    }

    first = asyncio.run(service.verify_initial(**arguments))
    replayed = asyncio.run(service.verify_initial(**arguments))

    assert replayed == first
    assert verifier.calls == 1
    with pytest.raises(ValueError, match="幂等键已绑定"):
        asyncio.run(
            service.verify_initial(
                **{**arguments, "delivery_spec_hash": "4" * 64}
            )
        )


def test_attempt_terminal_failure_rolls_back_runtime_projection(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
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
    request = _request(tmp_path)
    runtime_repository.update(
        "owner-a",
        "task-a",
        1,
        run_id="pi_run_0123456789abcdef",
        request=request.model_dump(mode="json", exclude={"api_key"}),
    )
    repository = SqliteCandidateVerificationRepository(database)
    service = CandidateVerificationService(
        repository=repository,
        ruleset_resolver=_FixedRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowVerifierAdapter(),
        event_writer=_ignore_event,
        clock=lambda: _NOW,
    )
    candidate = _candidate(tmp_path)
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_attempt_terminal "
            "BEFORE UPDATE OF status ON candidate_verification_attempts "
            "WHEN NEW.status='passed' "
            "BEGIN SELECT RAISE(ABORT, 'attempt rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="attempt rejected"):
        asyncio.run(
            service.verify_initial(
                request=request,
                run_id="pi_run_0123456789abcdef",
                candidates=(candidate,),
                manifest_path=manifest,
                verifier=_PassingVerifier(),
                actor_id="owner-a",
                idempotency_key="attempt-write-fails",
                goal_contract_hash="2" * 64,
                delivery_spec_hash="3" * 64,
            )
        )

    projected = runtime_repository.get("owner-a", "task-a", 1)
    assert projected is not None
    assert projected["verification"] is None
    assert projected["verified_candidate_set_hash"] is None
