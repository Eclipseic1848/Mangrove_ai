# -*- coding: utf-8 -*-
"""CV-06 Provider 重验的外发确认、Attempt 与未知结果安全接缝。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest
from src.agentic_runtime.candidate_verifier import BrokerSemanticJudge, CandidateVerifier
from src.candidate_verification import ReverificationContractError
from src.model_connections import (
    AccessGrant,
    ConnectionBroker,
    ProviderOutcomeUnknownError,
)
from src.model_connections.storage import ModelConnectionRepository
from tests.test_candidate_reverification_offer import (
    _PassingVerifier,
    _prepare_candidate,
    _service,
)


class _OutcomeUnknownVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, **_kwargs):
        self.calls += 1
        raise ProviderOutcomeUnknownError("Provider 响应状态无法确认")


class _UnknownBroker:
    def __init__(self) -> None:
        self.issued: list[dict[str, object]] = []
        self.relay_calls = 0
        self.revoked: list[tuple[str, str]] = []

    def issue_grant(self, **values) -> AccessGrant:
        self.issued.append(values)
        return AccessGrant(
            grant_id=str(values["grant_id"]),
            token="t" * 32,
            connection_id=str(values["connection_id"]),
            connection_version=str(values["connection_version"]),
            owner_user_id=str(values["owner_user_id"]),
            task_id=str(values["task_id"]),
            revision=int(values["revision"]),
            run_id=str(values["run_id"]),
            purpose=str(values["purpose"]),
            api_format="openai_chat_completions",
            model=str(values["model_id"]),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    async def relay(self, **_values):
        self.relay_calls += 1
        raise ProviderOutcomeUnknownError("Provider 响应状态无法确认")

    def revoke_grant(self, grant_id: str, reason: str) -> bool:
        self.revoked.append((grant_id, reason))
        return True


def _enable_execution_claim(database: Path) -> None:
    """补齐产品原子认领依赖的最小现有表，不绕过认领 Interface。"""

    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE runtime_rollout_state (
                state_id INTEGER PRIMARY KEY,
                p0_blocked INTEGER NOT NULL
            );
            INSERT INTO runtime_rollout_state VALUES (1, 0);
            CREATE TABLE semantic_workspace_tasks (
                task_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                active_revision INTEGER NOT NULL
            );
            INSERT INTO semantic_workspace_tasks VALUES ('task-a', 'owner-a', 1);
            CREATE TABLE formal_delivery_runs (
                owner_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE semantic_delivery_runs (
                user_id TEXT NOT NULL,
                run_id TEXT NOT NULL
            );
            """
        )


def test_provider_reverification_persists_frozen_attempt_before_execution(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")

    attempt = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=previous.attempt_id,
            external_api_confirmed=True,
            idempotency_key="provider-reverify-1",
            verifier_factory=lambda _request, _run_id: _PassingVerifier(),
        )
    )

    assert attempt.status.value == "requested"
    assert attempt.connection_id == "connection-a"
    assert attempt.connection_version == "version-a"
    assert attempt.model_id == "provider-model-a"
    assert attempt.egress_confirmed_at is not None
    assert attempt.provider_attempt_id is not None
    assert repository.get("owner-a", attempt.attempt_id) == attempt


def test_provider_reverification_requires_fresh_owner_egress_confirmation(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")

    with pytest.raises(ReverificationContractError, match="重新确认"):
        asyncio.run(
            service.request_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                expected_previous_attempt_id=previous.attempt_id,
                external_api_confirmed=False,
                idempotency_key="provider-missing-confirmation-1",
                verifier_factory=lambda _request, _run_id: _PassingVerifier(),
            )
        )

    assert repository.get_by_idempotency(
        "owner-a", "provider-missing-confirmation-1"
    ) is None


def test_provider_attempt_persistence_failure_issues_no_grant_and_sends_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")
    broker = _UnknownBroker()

    def verifier_factory(request, run_id, provider_attempt_id=None):
        return CandidateVerifier(
            semantic_judge=BrokerSemanticJudge(
                broker=broker,
                owner_user_id=request.user_id,
                connection_id=request.model_connection_id,
                connection_version=request.model_connection_version,
                model_id=request.model_connection_model,
                task_id=request.task_id,
                revision=request.revision,
                run_id=run_id,
                provider_attempt_id=provider_attempt_id,
                allow_response_retry=False,
            )
        )

    def fail_persistence(_attempt):
        raise sqlite3.OperationalError("synthetic persistence failure")

    monkeypatch.setattr(repository, "create_with_result", fail_persistence)
    with pytest.raises(sqlite3.OperationalError, match="persistence"):
        asyncio.run(
            service.request_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                expected_previous_attempt_id=previous.attempt_id,
                external_api_confirmed=True,
                idempotency_key="provider-persistence-failure-1",
                verifier_factory=verifier_factory,
            )
        )

    assert broker.issued == []
    assert broker.relay_calls == 0


def test_provider_connection_for_other_owner_is_permission_denied(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)

    class _ForbiddenAuthority:
        def blockers(self, _request, _run_id):
            from src.candidate_verification import ReverificationBlocker

            return (ReverificationBlocker.PROVIDER_BINDING_FORBIDDEN,)

    service = _service(repository, "9", authority=_ForbiddenAuthority())

    with pytest.raises(PermissionError, match="Owner"):
        asyncio.run(
            service.request_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                expected_previous_attempt_id=previous.attempt_id,
                external_api_confirmed=True,
                idempotency_key="provider-other-owner-1",
                verifier_factory=lambda _request, _run_id: _PassingVerifier(),
            )
        )


def test_provider_timeout_finishes_outcome_unknown_and_idempotency_never_resends(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")
    verifier = _OutcomeUnknownVerifier()
    _enable_execution_claim(tmp_path / "workspace.db")
    arguments = {
        "owner_id": "owner-a",
        "task_id": "task-a",
        "revision": 1,
        "expected_previous_attempt_id": previous.attempt_id,
        "external_api_confirmed": True,
        "idempotency_key": "provider-timeout-1",
        "verifier_factory": (
            lambda _request, _run_id, _provider_attempt_id=None: verifier
        ),
    }
    requested = asyncio.run(service.request_reverification(**arguments))

    finished = asyncio.run(
        service.execute_requested_reverification(
            owner_id="owner-a",
            attempt_id=requested.attempt_id,
            verifier_factory=arguments["verifier_factory"],
        )
    )
    replayed = asyncio.run(service.request_reverification(**arguments))

    assert finished.status.value == "outcome_unknown"
    assert finished.report_json is None
    assert replayed == finished
    assert verifier.calls == 1


def test_concurrent_provider_workers_claim_once_and_send_once(tmp_path: Path) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")
    _enable_execution_claim(tmp_path / "workspace.db")
    started = asyncio.Event()
    release = asyncio.Event()

    class _SlowPassingVerifier(_PassingVerifier):
        def __init__(self) -> None:
            self.calls = 0

        async def verify(self, **kwargs):
            self.calls += 1
            started.set()
            await release.wait()
            return await super().verify(**kwargs)

    verifier = _SlowPassingVerifier()
    factory = lambda _request, _run_id, _provider_id=None: verifier
    requested = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=previous.attempt_id,
            external_api_confirmed=True,
            idempotency_key="provider-concurrent-workers-1",
            verifier_factory=factory,
        )
    )

    async def scenario():
        first = asyncio.create_task(
            service.execute_requested_reverification(
                owner_id="owner-a",
                attempt_id=requested.attempt_id,
                verifier_factory=factory,
            )
        )
        await started.wait()
        second = asyncio.create_task(
            service.execute_requested_reverification(
                owner_id="owner-a",
                attempt_id=requested.attempt_id,
                verifier_factory=factory,
            )
        )
        second_result = await second
        release.set()
        first_result = await first
        return first_result, second_result

    first_result, second_result = asyncio.run(scenario())

    assert verifier.calls == 1
    assert first_result.status.value == "passed"
    assert second_result.status.value == "running"


def test_provider_attempt_is_grant_identity_and_unknown_path_revokes_without_retry(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")
    _enable_execution_claim(tmp_path / "workspace.db")
    broker = _UnknownBroker()

    def verifier_factory(request, run_id, provider_attempt_id=None):
        return CandidateVerifier(
            semantic_judge=BrokerSemanticJudge(
                broker=broker,
                owner_user_id=request.user_id,
                connection_id=request.model_connection_id,
                connection_version=request.model_connection_version,
                model_id=request.model_connection_model,
                task_id=request.task_id,
                revision=request.revision,
                run_id=run_id,
                provider_attempt_id=provider_attempt_id,
                allow_response_retry=False,
            )
        )

    requested = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=previous.attempt_id,
            external_api_confirmed=True,
            idempotency_key="provider-grant-identity-1",
            verifier_factory=verifier_factory,
        )
    )
    finished = asyncio.run(
        service.execute_requested_reverification(
            owner_id="owner-a",
            attempt_id=requested.attempt_id,
            verifier_factory=verifier_factory,
        )
    )

    assert finished.status.value == "outcome_unknown"
    assert broker.relay_calls == 1
    assert len(broker.issued) == 1
    assert broker.issued[0]["grant_id"] == requested.provider_attempt_id
    assert broker.revoked == [
        (requested.provider_attempt_id, "candidate_verify_closed")
    ]


def test_outcome_unknown_recovery_requires_new_cost_confirmation_and_attempt(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    service = _service(repository, "9")
    _enable_execution_claim(tmp_path / "workspace.db")
    verifier = _OutcomeUnknownVerifier()
    factory = lambda _request, _run_id, _provider_attempt_id=None: verifier
    first = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=previous.attempt_id,
            external_api_confirmed=True,
            idempotency_key="provider-unknown-original",
            verifier_factory=factory,
        )
    )
    unknown = asyncio.run(
        service.execute_requested_reverification(
            owner_id="owner-a",
            attempt_id=first.attempt_id,
            verifier_factory=factory,
        )
    )

    with pytest.raises(ReverificationContractError, match="重复费用"):
        asyncio.run(
            service.request_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                expected_previous_attempt_id=unknown.attempt_id,
                external_api_confirmed=True,
                accept_duplicate_provider_cost=False,
                idempotency_key="provider-unknown-recovery-rejected",
                verifier_factory=factory,
            )
        )

    recovered = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=unknown.attempt_id,
            external_api_confirmed=True,
            accept_duplicate_provider_cost=True,
            idempotency_key="provider-unknown-recovery-confirmed",
            verifier_factory=factory,
        )
    )

    assert recovered.status.value == "requested"
    assert recovered.attempt_id != unknown.attempt_id
    assert recovered.previous_attempt_id == unknown.attempt_id
    assert recovered.provider_attempt_id != unknown.provider_attempt_id
    assert repository.get("owner-a", unknown.attempt_id) == unknown

    with pytest.raises(ValueError, match="幂等键"):
        asyncio.run(
            service.request_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                expected_previous_attempt_id=unknown.attempt_id,
                external_api_confirmed=True,
                accept_duplicate_provider_cost=False,
                idempotency_key="provider-unknown-recovery-confirmed",
                verifier_factory=factory,
            )
        )


def test_worker_recovery_revokes_provider_grant_and_marks_outcome_unknown(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    revoked: list[tuple[str, str]] = []
    service = _service(
        repository,
        "9",
        provider_grant_revoker=lambda grant_id, reason: revoked.append(
            (grant_id, reason)
        ),
    )
    requested = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=previous.attempt_id,
            external_api_confirmed=True,
            idempotency_key="provider-worker-recovery-1",
            verifier_factory=lambda _request, _run_id: _PassingVerifier(),
        )
    )
    with sqlite3.connect(tmp_path / "workspace.db") as connection:
        connection.execute(
            "UPDATE candidate_verification_attempts "
            "SET status='running', started_at=? WHERE attempt_id=?",
            (datetime.now(timezone.utc).isoformat(), requested.attempt_id),
        )

    running = repository.get("owner-a", requested.attempt_id)
    assert running is not None
    assert service.list_running_reverifications() == (running,)
    recovered = service.recover_interrupted_reverification(running)

    assert revoked == [
        (requested.provider_attempt_id, "candidate_verify_outcome_unknown")
    ]
    assert recovered.status.value == "outcome_unknown"
    assert service.list_running_reverifications() == ()

    with pytest.raises(ReverificationContractError, match="重复费用"):
        asyncio.run(
            service.request_reverification(
                owner_id="owner-a",
                task_id="task-a",
                revision=1,
                expected_previous_attempt_id=recovered.attempt_id,
                external_api_confirmed=True,
                accept_duplicate_provider_cost=False,
                idempotency_key="provider-worker-recovery-rejected",
                verifier_factory=lambda _request, _run_id: _PassingVerifier(),
            )
        )


def test_running_provider_cancellation_is_outcome_unknown_and_revokes(
    tmp_path: Path,
) -> None:
    repository, previous = _prepare_candidate(tmp_path, provider=True)
    revoked: list[tuple[str, str]] = []
    service = _service(
        repository,
        "9",
        provider_grant_revoker=lambda grant_id, reason: revoked.append(
            (grant_id, reason)
        ),
    )
    _enable_execution_claim(tmp_path / "workspace.db")

    class _CancelledVerifier:
        async def verify(self, **_kwargs):
            raise asyncio.CancelledError

    requested = asyncio.run(
        service.request_reverification(
            owner_id="owner-a",
            task_id="task-a",
            revision=1,
            expected_previous_attempt_id=previous.attempt_id,
            external_api_confirmed=True,
            idempotency_key="provider-cancelled-running-1",
            verifier_factory=lambda _request, _run_id, _provider_id=None: (
                _CancelledVerifier()
            ),
        )
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.execute_requested_reverification(
                owner_id="owner-a",
                attempt_id=requested.attempt_id,
                verifier_factory=lambda _request, _run_id, _provider_id=None: (
                    _CancelledVerifier()
                ),
            )
        )

    finished = repository.get("owner-a", requested.attempt_id)
    assert finished is not None
    assert finished.status.value == "outcome_unknown"
    assert revoked == [
        (requested.provider_attempt_id, "candidate_verify_outcome_unknown")
    ]


def test_provider_usage_is_bound_to_attempt_and_unknown_cost_is_not_zero(
    tmp_path: Path,
) -> None:
    repository = ModelConnectionRepository(str(tmp_path / "connections.db"))
    provider_attempt_id = "grant_cv_1234567890abcdef"
    repository.record_usage(
        grant={
            "grant_id": provider_attempt_id,
            "owner_user_id": "owner-a",
            "task_id": "task-a",
            "revision": 1,
            "run_id": "run-a",
            "connection_id": "connection-a",
            "purpose": "candidate_verify",
        },
        status="unknown",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        native_json="{}",
    )
    broker = ConnectionBroker(repository=repository, vault=object())

    usage = broker.get_usage_for_grant(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        grant_id=provider_attempt_id,
    )

    assert usage == {
        "provider_attempt_id": provider_attempt_id,
        "run_id": "run-a",
        "status": "unknown",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "request_count": 1,
        "cost_status": "unknown",
        "cost": None,
    }
    assert (
        broker.get_usage_for_grant(
            "other-owner",
            task_id="task-a",
            revision=1,
            run_id="run-a",
            grant_id=provider_attempt_id,
        )
        is None
    )

    recorded_attempt_id = "grant_cv_abcdef1234567890"
    repository.record_usage(
        grant={
            "grant_id": recorded_attempt_id,
            "owner_user_id": "owner-a",
            "task_id": "task-a",
            "revision": 1,
            "run_id": "run-a",
            "connection_id": "connection-a",
            "purpose": "candidate_verify",
        },
        status="recorded",
        input_tokens=12,
        output_tokens=7,
        total_tokens=19,
        native_json="{}",
    )
    recorded = broker.get_usage_for_grant(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        grant_id=recorded_attempt_id,
    )
    assert recorded is not None
    assert recorded["status"] == "recorded"
    assert recorded["total_tokens"] == 19
    assert recorded["cost_status"] == "unknown"
    assert recorded["cost"] is None
