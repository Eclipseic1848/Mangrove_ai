# -*- coding: utf-8 -*-
"""CoreMind AgentKernel Adapter 的锁定身份与生命周期合同。"""
from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from src.agentic_runtime.coremind_runtime import (
    COREMIND_MERGE_COMMIT,
    COREMIND_PROVENANCE_SHA256,
    COREMIND_PROTOCOL_FINGERPRINT,
    COREMIND_REVIEWED_COMMIT,
    COREMIND_SDK_TREE_SHA256,
    COREMIND_SOURCE_COMMIT,
    COREMIND_WHEEL_SHA256,
    COREMIND_WORKER_MANIFEST_SHA256,
    COREMIND_WORKER_SHA256,
    CoreMindAgentKernelAdapter,
)
from src.agentic_runtime.coremind_worker_launcher import sanitized_worker_environment
from src.agentic_runtime.kernel import (
    AgentKernelCapabilityError,
    AgentKernelError,
    AgentKernelResultUnknownError,
    RuntimeBinding,
)
from src.agentic_runtime.models import (
    PermissionProfile,
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    RuntimeStatus,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from src.conversation_steering import CompiledContext
from src.model_connections import ProviderOutcomeUnknownError
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager


def _sse_response(*chunks: dict) -> bytes:
    return (
        "".join(
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            for chunk in chunks
        )
        + "data: [DONE]\n\n"
    ).encode("utf-8")


class _FakeCoreMindClient:
    def __init__(self, *, terminal: bool = True) -> None:
        self.terminal = terminal
        self.run_calls: list[tuple[str, str]] = []
        self.resume_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.event_cursors: list[int] = []
        self.closed = False

    def run(self, prompt: str, *, run_id: str):
        self.run_calls.append((prompt, run_id))
        return {"runId": run_id, "availableControls": ["cancel"]}

    def resume_run(self, run_id: str, *, input: str | None = None):
        del input
        self.resume_calls.append(run_id)
        return {"runId": run_id, "availableControls": ["cancel"]}

    def events(self, run_id: str, *, after_sequence: int, limit: int = 1000):
        del limit
        self.event_cursors.append(after_sequence)
        if after_sequence:
            return {"events": [], "nextCursor": after_sequence}
        return {
            "events": [{
                "protocolVersion": "2.0",
                "eventSchemaVersion": 1,
                "runId": run_id,
                "sequence": 1,
                "eventId": "event-1",
                "turnId": "turn-1",
                "timestamp": "2026-09-04T18:00:00.000Z",
                "ignorable": False,
                "sensitivity": "local",
                "eventType": "turn_end",
                "payload": {"type": "turn_end", "tokens": 15},
            }],
            "nextCursor": 1,
        }

    def query(self, run_id: str):
        status = "finished" if self.terminal else "running"
        return {
            "runId": run_id,
            "projection": {
                "status": status,
                "outcome": {"status": "succeeded"} if self.terminal else None,
            },
        }

    def cancel(self, run_id: str) -> None:
        self.cancel_calls.append(run_id)
        self.terminal = True

    def close(self) -> None:
        self.closed = True


class _CloseFailingClient(_FakeCoreMindClient):
    def close(self) -> None:
        raise RuntimeError("close failed")


class WorkerExitedError(RuntimeError):
    pass


class _WorkerCrashClient(_FakeCoreMindClient):
    def query(self, _run_id: str):
        raise WorkerExitedError("stderr contains host-secret")


def _request(tmp_path: Path) -> PiRuntimeRequest:
    source = tmp_path / "来源.txt"
    source.write_text("测试来源", encoding="utf-8")
    return PiRuntimeRequest(
        user_id="user-a",
        task_id="task-a",
        revision=1,
        objective_text="读取来源并形成结果",
        requested_output_formats=("txt",),
        sources=({
            "upload_id": "upload-a",
            "original_name": source.name,
            "host_path": source,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },),
        model="chosen-model",
        base_url="http://127.0.0.1:11434/v1",
        api_key="local-runtime",
    )


class _InteractiveCoreMindClient(_FakeCoreMindClient):
    def __init__(self) -> None:
        super().__init__(terminal=False)
        self.registered: list[dict] = []
        self.received_tool_calls: list[dict] = []
        self.received_tool_cancellations: list[dict] = []
        self.received_verification_requests: list[dict] = []
        self.tool_results: list[dict] = []
        self.verification_results: list[dict] = []

    def register_tool_definition(self, definition):
        self.registered.append(dict(definition))
        return {
            "schemaVersion": 1,
            "registrationId": definition["registrationId"],
            "toolId": definition["toolId"],
            "definitionFingerprint": "sha256:" + "a" * 64,
            "status": "registered",
        }

    def run(self, prompt: str, *, run_id: str):
        result = super().run(prompt, run_id=run_id)
        read = self.registered[0]
        self.received_tool_calls.append({
            "schemaVersion": 1,
            "runId": run_id,
            "callId": "call-read",
            "registrationId": read["registrationId"],
            "toolId": read["toolId"],
            "name": read["name"],
            "argumentsFingerprint": "sha256:" + "b" * 64,
            "args": {"source_id": "upload-a"},
        })
        return result

    def submit_tool_result(
        self,
        run_id,
        call_id,
        registration_id,
        *,
        result=None,
        error=None,
        result_id=None,
    ):
        self.tool_results.append({
            "run_id": run_id,
            "call_id": call_id,
            "registration_id": registration_id,
            "result": result,
            "error": error,
            "result_id": result_id,
        })
        if call_id == "call-read":
            assert result["content"] == "测试来源"
            submit = self.registered[1]
            self.received_tool_calls.append({
                "schemaVersion": 1,
                "runId": run_id,
                "callId": "call-submit",
                "registrationId": submit["registrationId"],
                "toolId": submit["toolId"],
                "name": submit["name"],
                "argumentsFingerprint": "sha256:" + "c" * 64,
                "args": {
                    "filename": "result.txt",
                    "format": "txt",
                    "content": "测试来源",
                    "description": "来源内容",
                    "evidence": [{
                        "source": "upload-a",
                        "locator": "全文",
                        "quote": "测试来源",
                    }],
                    "result_items": [{
                        "result_id": "result-1",
                        "label": "测试来源",
                        "source": "upload-a",
                        "locator": "全文",
                        "quote": "测试来源",
                    }],
                    "result_search_complete": True,
                },
            })
        else:
            self.received_verification_requests.append({
                "schemaVersion": 1,
                "runId": run_id,
                "requestId": "verify-1",
                "candidate": "候选已写入 Mangrove 输出目录",
                "candidateSha256": hashlib.sha256(
                    "候选已写入 Mangrove 输出目录".encode("utf-8")
                ).hexdigest(),
                "iteration": 1,
            })
        return {
            "schemaVersion": 1,
            "resultId": result_id,
            "runId": run_id,
            "callId": call_id,
            "registrationId": registration_id,
            "status": "accepted",
        }

    def submit_verification(self, run_id, request_id, candidate_sha256, **values):
        self.verification_results.append({
            "run_id": run_id,
            "request_id": request_id,
            "candidate_sha256": candidate_sha256,
            **values,
        })
        self.terminal = values["decision"] == "accept"
        return {
            "schemaVersion": 1,
            "runId": run_id,
            "controlId": values["control_id"],
            "status": "applied",
        }

    def events(self, run_id: str, *, after_sequence: int, limit: int = 1000):
        if (
            any(item["call_id"] == "call-submit" for item in self.tool_results)
            and after_sequence < 3
        ):
            base = {
                "protocolVersion": "2.0",
                "eventSchemaVersion": 1,
                "runId": run_id,
                "turnId": "turn-1",
                "timestamp": "2026-09-04T18:00:00.000Z",
                "ignorable": False,
                "sensitivity": "local",
            }
            return {
                "events": [
                    {
                        **base,
                        "sequence": 2,
                        "eventId": "event-checkpoint",
                        "eventType": "checkpoint_created",
                        "payload": {
                            "type": "checkpoint_created",
                            "checkpointId": "checkpoint-submit",
                            "tool": "mangrove_submit_candidate",
                            "callId": "call-submit",
                            "reversible": True,
                        },
                    },
                    {
                        **base,
                        "sequence": 3,
                        "eventId": "event-effect",
                        "eventType": "effect_receipt",
                        "payload": {
                            "type": "effect_receipt",
                            "idempotencyKey": "effect-submit",
                            "tool": "mangrove_submit_candidate",
                            "status": "committed",
                            "callId": "call-submit",
                        },
                    },
                ],
                "nextCursor": 3,
            }
        return super().events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )


class _MissingEffectReceiptClient(_InteractiveCoreMindClient):
    def events(self, run_id: str, *, after_sequence: int, limit: int = 1000):
        page = super().events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return {
            **page,
            "events": [
                event
                for event in page["events"]
                if event.get("eventType") != "effect_receipt"
            ],
        }


class _PassingCandidateService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def verify_initial_current(self, **values):
        self.calls.append(values)
        report = VerificationReport(
            status=VerificationStatus.PASSED,
            summary="候选通过独立验证",
            checks=(
                VerificationCheck(
                    code="fixture",
                    passed=True,
                    summary="来源和候选一致",
                ),
            ),
            evidence_count=1,
            formal_delivery_eligible=True,
        )
        return SimpleNamespace(
            status=SimpleNamespace(value="passed"),
            report_json=report.model_dump_json(),
        )


class _UnknownCandidateService:
    async def verify_initial_current(self, **_values):
        return SimpleNamespace(
            status=SimpleNamespace(value="outcome_unknown"),
            report_json=None,
        )


class _FakeConnectionBroker:
    def __init__(self) -> None:
        self.issued: list[dict] = []
        self.revoked: list[dict] = []

    def issue_grant(self, **values):
        self.issued.append(values)
        return SimpleNamespace(
            grant_id="grant-coremind",
            token="g" * 40,
            model="chosen-external-model",
            api_format="openai_chat_completions",
        )

    def revoke_run_grants(self, owner_user_id, task_id, revision, run_id, *, reason):
        self.revoked.append({
            "user_id": owner_user_id,
            "task_id": task_id,
            "revision": revision,
            "run_id": run_id,
            "reason": reason,
        })
        return 1


class _RoutingKernel:
    def __init__(self, adapter_id: str, frozen_adapter_id: str | None = None) -> None:
        self.adapter_id = adapter_id
        self.frozen_adapter_id = frozen_adapter_id
        self.prepared = 0

    async def prepare_binding(self, **_values):
        self.prepared += 1
        return self.adapter_id, self.adapter_id

    def frozen_binding(self, _user_id, _task_id, _revision):
        if self.frozen_adapter_id is None:
            return None
        return SimpleNamespace(adapter_id=self.frozen_adapter_id)

    def bind_candidate_verification(self, _service) -> None:
        return None


def test_worker_environment_exposes_only_the_current_run_grant(tmp_path: Path) -> None:
    environment = sanitized_worker_environment(
        {
            "SYSTEMROOT": "C:\\Windows",
            "HOST_SECRET": "must-not-leak",
            "HTTPS_PROXY": "http://proxy.invalid",
            "RUN_GRANT": "run-scoped-token",
        },
        grant_env_name="RUN_GRANT",
        runtime_root=tmp_path,
        node_path=tmp_path / "node.exe",
    )

    assert environment["MANGROVE_COREMIND_MODEL_GRANT"] == "run-scoped-token"
    assert environment["HOME"] == str(tmp_path.resolve())
    assert "HOST_SECRET" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "RUN_GRANT" not in environment


@pytest.mark.asyncio
async def test_manager_uses_primary_for_new_runs_and_frozen_adapter_for_resume() -> None:
    pi = _RoutingKernel("pi-runtime")
    coremind = _RoutingKernel("coremind-runtime", frozen_adapter_id="pi-runtime")
    manager = SemanticWorkspaceManager(
        agent_kernels={"pi-runtime": pi, "coremind-runtime": coremind},
        primary_adapter_id="coremind-runtime",
    )

    prepared = await manager.prepare_runtime_binding(
        model_connection_id=None,
        model_connection_version=None,
        model="chosen-model",
    )

    assert prepared == ("coremind-runtime", "coremind-runtime")
    assert coremind.prepared == 1
    assert pi.prepared == 0
    assert manager._kernel_for_run("user-a", "task-a", 1) is pi


def _binding(adapter: CoreMindAgentKernelAdapter, run_id: str) -> RuntimeBinding:
    manifest = adapter.manifest
    return RuntimeBinding(
        adapter_id=manifest.adapter_id,
        adapter_version=manifest.adapter_version,
        runtime_artifact=manifest.runtime_artifact,
        protocol_version=manifest.protocol_version,
        event_schema_version=manifest.event_schema_version,
        capability_digest=manifest.digest,
        external_run_id=run_id,
        model="chosen-model",
    )


def _workspace(execution_root: Path, run_id: str) -> Path:
    owner = hashlib.sha256("user-a".encode("utf-8")).hexdigest()[:16]
    return execution_root / "coremind" / owner / "task-a" / "r1" / run_id


def _external_request(tmp_path: Path) -> PiRuntimeRequest:
    local = _request(tmp_path)
    return PiRuntimeRequest(
        **local.model_dump(
            exclude={
                "model",
                "base_url",
                "api_key",
                "external_api_confirmed",
                "model_connection_id",
                "model_connection_version",
                "model_connection_model",
            },
        ),
        external_api_confirmed=True,
        model_connection_id="connection-a",
        model_connection_version="version-a",
        model_connection_model="chosen-external-model",
    )


def _external_binding(
    adapter: CoreMindAgentKernelAdapter,
    run_id: str,
) -> RuntimeBinding:
    return _binding(adapter, run_id).model_copy(
        update={
            "model_connection_id": "connection-a",
            "model_connection_version": "version-a",
            "model": "chosen-external-model",
        }
    )


def test_manifest_freezes_the_approved_coremind_artifact_without_importing_sdk(
    tmp_path: Path,
) -> None:
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: _FakeCoreMindClient(),
    )

    assert COREMIND_WHEEL_SHA256 == "3fa5301c444da2e3bdaca51bd4800b1bdbcb6dc68e3abef4b39197bde3625e74"
    assert COREMIND_WORKER_SHA256 == "ba4590a68841e520dcd3a91e206ca9e346d10fd9a23b3ed4c560f59707cfa71e"
    assert COREMIND_PROTOCOL_FINGERPRINT == "sha256:94c8e093979be73a13ecc1090167454567d0602a70b065ceffeed4cb1eca4ce3"
    assert COREMIND_WHEEL_SHA256 in adapter.manifest.runtime_artifact
    assert COREMIND_WORKER_SHA256 in adapter.manifest.runtime_artifact
    assert adapter.manifest.runtime_version == f"0.7.1+{COREMIND_SOURCE_COMMIT}"
    assert COREMIND_REVIEWED_COMMIT in adapter.manifest.runtime_artifact
    assert COREMIND_MERGE_COMMIT in adapter.manifest.runtime_artifact
    assert COREMIND_WORKER_MANIFEST_SHA256 in adapter.manifest.runtime_artifact
    assert COREMIND_PROVENANCE_SHA256 in adapter.manifest.runtime_artifact
    assert "execution-contract=sha256:" in adapter.manifest.runtime_artifact
    assert COREMIND_SDK_TREE_SHA256 == (
        "812258edd429587ba01a31101c64fc74ed110b5d91d1d0330044eae9039a2488"
    )
    assert adapter.manifest.runtime_protocol_version == "2.0"
    assert adapter.manifest.runtime_event_schema_version == COREMIND_PROTOCOL_FINGERPRINT


def test_sdk_tree_digest_changes_when_imported_client_changes(tmp_path: Path) -> None:
    package = tmp_path / "coremind"
    package.mkdir()
    client = package / "client.py"
    client.write_text("VALUE = 1\n", encoding="utf-8")
    before = CoreMindAgentKernelAdapter._sdk_tree_sha256(package)

    client.write_text("VALUE = 2\n", encoding="utf-8")

    assert CoreMindAgentKernelAdapter._sdk_tree_sha256(package) != before


def test_approval_only_allows_frozen_tool_semantics_for_current_run() -> None:
    definition = {
        "type": "approval_required",
        "runId": "cm_run_a",
        "tool": "mangrove_read_source",
        "args": {"source_id": "upload-a"},
        "effect": {"operations": ["read"], "reversible": True},
        "capability": {
            "effect": "none",
            "replay": "safe",
            "concurrency": "parallel",
            "checkpoint": "none",
            "durability": "ordinary",
        },
    }

    assert CoreMindAgentKernelAdapter._approval_decision(definition, "cm_run_a") == "allow"
    assert CoreMindAgentKernelAdapter._approval_decision(
        {**definition, "runId": "cm_run_b"},
        "cm_run_a",
    ) == "deny"
    assert CoreMindAgentKernelAdapter._approval_decision(
        {**definition, "tool": "bash"},
        "cm_run_a",
    ) == "deny"


@pytest.mark.asyncio
async def test_tool_failure_does_not_expose_host_path_to_worker(
    tmp_path: Path,
) -> None:
    secret_path = tmp_path / "不得外发的本机目录"
    secret_path.mkdir()
    request = _request(tmp_path)
    request = request.model_copy(
        update={
            "sources": (
                request.sources[0].model_copy(
                    update={"host_path": secret_path, "sha256": "0" * 64}
                ),
            )
        }
    )
    client = _InteractiveCoreMindClient()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: client,
        candidate_verifier_factory=lambda _request, _run_id: object(),
    )
    adapter.bind_candidate_verification(_PassingCandidateService())
    adapter._register_tools(client)
    definition = client.registered[0]
    run_root = tmp_path / "run"
    (run_root / "output").mkdir(parents=True)

    await adapter._answer_tool_call(
        client,
        request=request,
        binding=_binding(adapter, "cm_run_safe_error"),
        run_root=run_root,
        call={
            "runId": "cm_run_safe_error",
            "callId": "call-secret",
            "registrationId": definition["registrationId"],
            "toolId": definition["toolId"],
            "name": definition["name"],
            "args": {"source_id": "upload-a"},
        },
    )

    assert client.tool_results[0]["error"] == "Mangrove 隔离来源或候选参数无效"
    assert str(secret_path) not in json.dumps(
        client.tool_results[0],
        ensure_ascii=False,
    )


def test_execution_contract_changes_the_frozen_binding_identity(tmp_path: Path) -> None:
    first = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: _FakeCoreMindClient(),
        timeout_seconds=10,
    )
    changed = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: _FakeCoreMindClient(),
        timeout_seconds=11,
    )

    assert first.manifest.digest != changed.manifest.digest
    with pytest.raises(AgentKernelCapabilityError, match="RuntimeBinding"):
        changed._assert_binding(_binding(first, "cm_run_policy_drift"))


@pytest.mark.asyncio
async def test_nonstandard_permission_is_rejected_before_worker_creation(
    tmp_path: Path,
) -> None:
    created = []
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **values: created.append(values),
    )

    with pytest.raises(AgentKernelCapabilityError, match="权限档位"):
        await adapter.start(
            _request(tmp_path).model_copy(
                update={"permission_profile": PermissionProfile.EXTENDED}
            ),
            binding=_binding(adapter, "cm_run_permission"),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert created == []


@pytest.mark.asyncio
async def test_setup_failure_closes_worker_and_revokes_grant(tmp_path: Path) -> None:
    broker = _FakeConnectionBroker()
    client = _FakeCoreMindClient()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: client,
        connection_broker=broker,
        relay_base_url="http://127.0.0.1:8088/internal/model-relay",
    )
    request = _external_request(tmp_path)
    request.sources[0].host_path.write_text("已被修改", encoding="utf-8")

    with pytest.raises(AgentKernelCapabilityError, match="来源内容哈希"):
        await adapter.start(
            request,
            binding=_external_binding(adapter, "cm_run_setup_failure"),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert client.closed is True
    assert broker.revoked[0]["reason"] == "run_closed"


@pytest.mark.asyncio
async def test_close_failure_still_revokes_grant_and_fails_closed(
    tmp_path: Path,
) -> None:
    broker = _FakeConnectionBroker()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: _CloseFailingClient(),
        connection_broker=broker,
        relay_base_url="http://127.0.0.1:8088/internal/model-relay",
        poll_interval_seconds=0,
    )

    with pytest.raises(AgentKernelResultUnknownError, match="清理"):
        await adapter.start(
            _external_request(tmp_path),
            binding=_external_binding(adapter, "cm_run_close_failure"),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert broker.revoked[0]["reason"] == "run_closed"


@pytest.mark.asyncio
async def test_worker_crash_is_safe_result_unknown(tmp_path: Path) -> None:
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: _WorkerCrashClient(),
        poll_interval_seconds=0,
    )

    with pytest.raises(AgentKernelResultUnknownError) as caught:
        await adapter.start(
            _request(tmp_path),
            binding=_binding(adapter, "cm_run_crash"),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert "host-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_new_adapter_accepts_cancel_only_after_closed_worker_proof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    first = CoreMindAgentKernelAdapter(
        execution_root=root,
        client_factory=lambda **_kwargs: _FakeCoreMindClient(),
        poll_interval_seconds=0,
    )
    await first.start(
        _request(tmp_path),
        binding=_binding(first, "cm_run_closed"),
        on_event=lambda _event: asyncio.sleep(0),
    )
    restarted = CoreMindAgentKernelAdapter(
        execution_root=root,
        client_factory=lambda **_kwargs: _FakeCoreMindClient(),
    )

    await restarted.cancel("user-a", "task-a", 1)
    with pytest.raises(AgentKernelError, match="静止状态无法证明"):
        await restarted.cancel("user-a", "missing-task", 1)


def test_candidate_verification_binding_fails_closed_until_real_candidate_mapping(
    tmp_path: Path,
) -> None:
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: _FakeCoreMindClient(),
    )

    with pytest.raises(AgentKernelCapabilityError, match="CandidateSet"):
        adapter.bind_candidate_verification(object())


@pytest.mark.asyncio
async def test_finished_run_projects_events_but_fails_closed_before_candidate_mapping(
    tmp_path: Path,
) -> None:
    client = _FakeCoreMindClient()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: client,
        poll_interval_seconds=0,
    )
    events = []

    async def sink(event) -> None:
        events.append(event)

    request = _request(tmp_path)
    result = await adapter.start(
        request,
        binding=_binding(adapter, "cm_run_a"),
        on_event=sink,
    )

    assert len(client.run_calls) == 1
    assert "读取来源并形成结果" in client.run_calls[0][0]
    assert str(request.sources[0].host_path) not in client.run_calls[0][0]
    assert client.run_calls[0][1] == "cm_run_a"
    assert client.event_cursors == [0]
    assert [event.event_type for event in events] == ["provider.usage"]
    assert result.status is RuntimeStatus.FAILED
    assert result.failure["error_code"] == "COREMIND_CANDIDATE_NOT_MAPPED"
    assert client.closed is True


@pytest.mark.asyncio
async def test_resume_uses_the_frozen_run_identity_and_sdk_resume_name(
    tmp_path: Path,
) -> None:
    client = _FakeCoreMindClient()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: client,
        poll_interval_seconds=0,
    )
    binding = _binding(adapter, "cm_run_resume")

    result = await adapter.resume(
        _request(tmp_path),
        binding=binding,
        checkpoint=PiRuntimeCheckpoint(
            run_id="cm_run_resume",
            workspace_root=_workspace(tmp_path, "cm_run_resume"),
        ),
        on_event=lambda _event: asyncio.sleep(0),
    )

    assert client.resume_calls == ["cm_run_resume"]
    assert result.run_id == "cm_run_resume"
    assert client.closed is True


@pytest.mark.asyncio
async def test_cancel_hard_stops_the_worker_before_returning(tmp_path: Path) -> None:
    client = _FakeCoreMindClient(terminal=False)
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: client,
        poll_interval_seconds=0,
    )
    request = _request(tmp_path)
    binding = _binding(adapter, "cm_run_cancel")
    execution = asyncio.create_task(
        adapter.start(
            request,
            binding=binding,
            on_event=lambda _event: asyncio.sleep(0),
        )
    )
    while not client.run_calls:
        await asyncio.sleep(0)

    await adapter.cancel(request.user_id, request.task_id, request.revision)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert client.cancel_calls == ["cm_run_cancel"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_resume_timeout_is_result_unknown_and_never_restarts_the_run(
    tmp_path: Path,
) -> None:
    client = _FakeCoreMindClient(terminal=False)
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path,
        client_factory=lambda **_kwargs: client,
        poll_interval_seconds=0,
        timeout_seconds=0.01,
    )
    binding = _binding(adapter, "cm_run_unknown")

    with pytest.raises(Exception, match="结果不确定") as caught:
        await adapter.resume(
            _request(tmp_path),
            binding=binding,
            checkpoint=PiRuntimeCheckpoint(
                run_id="cm_run_unknown",
                workspace_root=_workspace(tmp_path, "cm_run_unknown"),
            ),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert type(caught.value).__name__ == "AgentKernelResultUnknownError"
    assert client.resume_calls == ["cm_run_unknown"]
    assert client.run_calls == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_coremind_tools_form_a_verified_candidate_without_taking_delivery_authority(
    tmp_path: Path,
) -> None:
    client = _InteractiveCoreMindClient()
    service = _PassingCandidateService()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: client,
        candidate_verifier_factory=lambda _request, _run_id: object(),
        poll_interval_seconds=0,
    )
    adapter.bind_candidate_verification(service)
    request = _request(tmp_path).model_copy(update={
        "goal_contract": {"must_include": ["测试来源"]},
        "compiled_context": CompiledContext(
            context_id="context-a",
            owner_id="user-a",
            task_id="task-a",
            revision=1,
            content="已确认记忆摘要：保持原文。",
            composition=(),
            char_count=13,
            estimated_tokens=6,
            summary_sha256="sha256:" + "d" * 64,
        ),
    })

    result = await adapter.start(
        request,
        binding=_binding(adapter, "cm_run_verified"),
        on_event=lambda _event: asyncio.sleep(0),
    )

    assert [item["name"] for item in client.registered] == [
        "mangrove_read_source",
        "mangrove_submit_candidate",
    ]
    prompt = client.run_calls[0][0]
    assert "读取来源并形成结果" in prompt
    assert "已确认记忆摘要：保持原文。" in prompt
    assert "upload-a" in prompt
    assert str(request.sources[0].host_path) not in prompt
    assert result.status is RuntimeStatus.CANDIDATE_READY
    assert result.verification is not None
    assert result.verification.status is VerificationStatus.PASSED
    assert [candidate.filename for candidate in result.candidates] == ["result.txt"]
    assert len(service.calls) == 1
    assert client.verification_results[0]["decision"] == "accept"
    assert client.closed is True


@pytest.mark.asyncio
async def test_resume_reverifies_persisted_candidate_without_runtime_request(
    tmp_path: Path,
) -> None:
    client = _InteractiveCoreMindClient()
    service = _PassingCandidateService()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: client,
        candidate_verifier_factory=lambda _request, _run_id: object(),
        poll_interval_seconds=0,
    )
    adapter.bind_candidate_verification(service)
    request = _request(tmp_path)
    binding = _binding(adapter, "cm_run_reverify")
    first = await adapter.start(
        request,
        binding=binding,
        on_event=lambda _event: asyncio.sleep(0),
    )
    client.received_tool_calls.clear()
    client.received_verification_requests.clear()

    resumed = await adapter.resume(
        request,
        binding=binding,
        checkpoint=PiRuntimeCheckpoint(
            run_id=first.run_id,
            workspace_root=first.workspace_root,
        ),
        on_event=lambda _event: asyncio.sleep(0),
    )

    assert resumed.status is RuntimeStatus.CANDIDATE_READY
    assert len(service.calls) == 2
    assert client.resume_calls == ["cm_run_reverify"]


@pytest.mark.asyncio
async def test_candidate_side_effect_requires_checkpoint_and_effect_receipt(
    tmp_path: Path,
) -> None:
    client = _MissingEffectReceiptClient()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: client,
        candidate_verifier_factory=lambda _request, _run_id: object(),
        poll_interval_seconds=0,
    )
    adapter.bind_candidate_verification(_PassingCandidateService())

    with pytest.raises(AgentKernelResultUnknownError, match="EffectReceipt"):
        await adapter.start(
            _request(tmp_path),
            binding=_binding(adapter, "cm_run_missing_effect"),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert len(client.run_calls) == 1
    assert client.closed is True


@pytest.mark.asyncio
async def test_unknown_candidate_verification_pauses_without_model_retry(
    tmp_path: Path,
) -> None:
    client = _InteractiveCoreMindClient()
    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=lambda **_kwargs: client,
        candidate_verifier_factory=lambda _request, _run_id: object(),
        poll_interval_seconds=0,
    )
    adapter.bind_candidate_verification(_UnknownCandidateService())

    with pytest.raises(Exception, match="结果不确定") as caught:
        await adapter.start(
            _request(tmp_path),
            binding=_binding(adapter, "cm_run_verification_unknown"),
            on_event=lambda _event: asyncio.sleep(0),
        )

    assert type(caught.value).__name__ == "AgentKernelResultUnknownError"
    assert len(client.run_calls) == 1
    assert client.verification_results == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_external_model_uses_run_scoped_broker_grant_and_revokes_it(
    tmp_path: Path,
) -> None:
    broker = _FakeConnectionBroker()
    client = _FakeCoreMindClient()
    captured = {}

    def factory(**values):
        captured.update(values)
        return client

    adapter = CoreMindAgentKernelAdapter(
        execution_root=tmp_path / "runs",
        client_factory=factory,
        connection_broker=broker,
        relay_base_url="http://127.0.0.1:8088/internal/model-relay",
        poll_interval_seconds=0,
    )
    local = _request(tmp_path)
    request = PiRuntimeRequest(
        **local.model_dump(
            exclude={
                "model",
                "base_url",
                "api_key",
                "external_api_confirmed",
                "model_connection_id",
                "model_connection_version",
                "model_connection_model",
            },
        ),
        external_api_confirmed=True,
        model_connection_id="connection-a",
        model_connection_version="version-a",
        model_connection_model="chosen-external-model",
    )
    binding = _binding(adapter, "cm_run_grant").model_copy(
        update={
            "model_connection_id": "connection-a",
            "model_connection_version": "version-a",
            "model": "chosen-external-model",
        }
    )

    result = await adapter.start(
        request,
        binding=binding,
        on_event=lambda _event: asyncio.sleep(0),
    )

    assert result.failure["error_code"] == "COREMIND_CANDIDATE_NOT_MAPPED"
    assert captured["model_route"] == {
        "model": "chosen-external-model",
        "base_url": "http://127.0.0.1:8088/internal/model-relay",
        "api_key": "g" * 40,
    }
    assert broker.issued[0]["purpose"] == "agent_inference"
    assert broker.issued[0]["run_id"] == "cm_run_grant"
    assert broker.revoked == [{
        "user_id": "user-a",
        "task_id": "task-a",
        "revision": 1,
        "run_id": "cm_run_grant",
        "reason": "run_closed",
    }]


@pytest.mark.skipif(
    os.environ.get("MANGROVE_COREMIND_ADAPTER_TEST") != "1",
    reason="需显式启用锁定 CoreMind Adapter 纵切面",
)
@pytest.mark.asyncio
async def test_locked_worker_executes_real_tools_and_host_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import coremind

    clients = []
    original_client = coremind.CoreMindClient

    class RecordingClient(original_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.event_pages = []

        def start(self):
            result = super().start()
            if self not in clients:
                clients.append(self)
            return result

        def events(self, *args, **kwargs):
            page = super().events(*args, **kwargs)
            self.event_pages.append(page)
            return page

    monkeypatch.setattr(coremind, "CoreMindClient", RecordingClient)
    requests: list[dict] = []
    candidate_args = {
        "filename": "result.txt",
        "format": "txt",
        "content": "测试来源",
        "description": "来源内容",
        "evidence": [{
            "source": "upload-a",
            "locator": "全文",
            "quote": "测试来源",
        }],
        "result_items": [{
            "result_id": "result-1",
            "label": "测试来源",
            "source": "upload-a",
            "locator": "全文",
            "quote": "测试来源",
        }],
        "result_search_complete": True,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return None

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            index = len(requests)
            if index == 1:
                delta = {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-read",
                        "type": "function",
                        "function": {
                            "name": "mangrove_read_source",
                            "arguments": json.dumps(
                                {"source_id": "upload-a"},
                                separators=(",", ":"),
                            ),
                        },
                    }],
                }
                finish_reason = "tool_calls"
            elif index == 2:
                delta = {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-submit",
                        "type": "function",
                        "function": {
                            "name": "mangrove_submit_candidate",
                            "arguments": json.dumps(
                                candidate_args,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }],
                }
                finish_reason = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "候选已提交。"}
                finish_reason = "stop"
            body = _sse_response(
                {
                    "id": f"fixture-{index}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "chosen-model",
                    "choices": [{
                        "index": 0,
                        "delta": delta,
                        "finish_reason": None,
                    }],
                },
                {
                    "id": f"fixture-{index}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "chosen-model",
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": finish_reason,
                    }],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                },
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = _PassingCandidateService()
        adapter = CoreMindAgentKernelAdapter(
            execution_root=tmp_path / "runs",
            candidate_verifier_factory=lambda _request, _run_id: object(),
            poll_interval_seconds=0.01,
            timeout_seconds=10,
        )
        adapter.bind_candidate_verification(service)
        request = _request(tmp_path).model_copy(update={
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        })
        events = []

        async def on_event(event):
            events.append(event)

        await adapter.prepare_manifest()
        binding = _binding(adapter, "cm_run_real_adapter")
        result = await adapter.start(request, binding=binding, on_event=on_event)

        assert result.status is RuntimeStatus.CANDIDATE_READY, (
            result.model_dump(mode="json"),
            requests,
            [event.model_dump(mode="json") for event in events],
        )
        assert requests[0].get("tools")
        assert len(requests) == 3
        assert [item.filename for item in result.candidates] == ["result.txt"]
        assert all(item["model"] == "chosen-model" for item in requests)
        assert len(clients) == 1
        assert all(client._process.poll() is not None for client in clients)
        runtime_payloads = [
            event.get("payload") or {}
            for client in clients
            for page in client.event_pages
            for event in page.get("events", [])
        ]
        observed_types = [payload.get("type") for payload in runtime_payloads]
        assert any(
            payload.get("type") == "checkpoint_created"
            and payload.get("callId") == "call-submit"
            for payload in runtime_payloads
        ), observed_types
        assert any(
            payload.get("type") == "effect_receipt"
            and payload.get("callId") == "call-submit"
            and payload.get("status") == "committed"
            for payload in runtime_payloads
        ), observed_types
        assert not any(
            key.startswith("MANGROVE_COREMIND_RUN_GRANT_")
            for key in os.environ
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
