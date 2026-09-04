# -*- coding: utf-8 -*-
"""Mangrove AgentKernel 公共合同测试。"""
from __future__ import annotations

from pathlib import Path
import asyncio
import hashlib
import re

import pytest

from src.agentic_runtime.kernel import (
    AGENT_KERNEL_EVENT_SCHEMA_VERSION,
    AGENT_KERNEL_PROTOCOL_VERSION,
    AgentKernel,
    AgentKernelCapabilityError,
    AgentKernelCapabilityManifest,
    AgentKernelError,
    AgentKernelResultUnknownError,
    PiAgentKernelAdapter,
)
from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
from src.agentic_runtime.models import (
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeEvent,
    RuntimeStatus,
    RuntimeTaskConfig,
    RuntimeVersion,
)


from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.agentic_runtime.pi_runtime import PiRuntime
from tests.database_migration_helpers import migrated_webui_database
from tests.test_coremind_agent_kernel_adapter import (
    _FakeCoreMindClient,
    _InteractiveCoreMindClient,
    _PassingCandidateService,
)


class _NeverStartedAdapter:
    """能力门失败时，底层 Runtime 绝不能收到启动调用。"""

    def __init__(self, manifest: AgentKernelCapabilityManifest) -> None:
        self.manifest = manifest
        self.start_calls = 0

    def new_external_run_id(self) -> str:
        return "pi_run_0123456789abcdef"

    async def start(self, request, *, binding, on_event):
        del request, binding, on_event
        self.start_calls += 1
        raise AssertionError("能力门失败后不应启动 Adapter")


class _CompletingAdapter(_NeverStartedAdapter):
    """形成已知结果，并在启动瞬间检查绑定是否已经持久化。"""

    def __init__(
        self,
        manifest: AgentKernelCapabilityManifest,
        repository: AgenticRuntimeRepository,
    ) -> None:
        super().__init__(manifest)
        self._repository = repository
        self.binding = None

    async def start(self, request, *, binding, on_event):
        del on_event
        self.start_calls += 1
        self.binding = binding
        frozen = self._repository.list_events(
            request.user_id,
            request.task_id,
            request.revision,
        )
        assert [event["event_type"] for event in frozen] == [
            "kernel.binding.frozen"
        ]
        assert frozen[0]["details"]["binding"]["external_run_id"] == (
            binding.external_run_id
        )
        return PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id=binding.external_run_id,
            workspace_root=Path(request.sources[0].host_path).parent,
        )

    async def resume(self, request, *, binding, checkpoint, on_event):
        del checkpoint
        return await self.start(request, binding=binding, on_event=on_event)

    async def cancel(self, _user_id, _task_id, _revision) -> None:
        return None


class _ContractAdapter:
    """用于验证所有 Adapter 都必须遵守的 Kernel 运行语义。"""

    manifest = AgentKernelCapabilityManifest(
        adapter_id="fake-runtime",
        adapter_version="1.0.0",
        runtime_artifact="python-runtime:tests.fake-runtime@1.0.0",
        protocol_version=AGENT_KERNEL_PROTOCOL_VERSION,
        event_schema_version=AGENT_KERNEL_EVENT_SCHEMA_VERSION,
        required_capabilities=("start", "resume", "cancel"),
        optional_capabilities=("steer",),
        available_capabilities=("start", "resume", "cancel"),
    )

    def __init__(self) -> None:
        self.start_calls = 0
        self.resume_calls = 0
        self.cancel_calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.on_event = None

    def new_external_run_id(self) -> str:
        return "pi_run_abcdef0123456789"

    async def start(self, request, *, binding, on_event):
        del request
        self.start_calls += 1
        self.on_event = on_event
        await on_event(RuntimeEvent(event_type="agent.started", summary="开始执行"))
        await on_event(
            RuntimeEvent(
                event_type="tool.completed",
                summary="完成读取",
                details={"tool": "read"},
            )
        )
        return PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id=binding.external_run_id,
            workspace_root=Path.cwd(),
        )

    async def resume(self, request, *, binding, checkpoint, on_event):
        del request, checkpoint
        self.resume_calls += 1
        return PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id=binding.external_run_id,
            workspace_root=Path.cwd(),
        )

    async def cancel(self, _user_id, _task_id, _revision) -> None:
        self.cancel_calls += 1


class _FakePiRuntimeEngine:
    """Pi Adapter 后方的最小 Runtime 引擎，不启动 Docker。"""

    def __init__(
        self,
        contract: _ContractAdapter | None = None,
        *,
        image: str = "mangrove/pi-coding-agent:test-a",
        image_digest: str = "sha256:" + "a" * 64,
    ) -> None:
        self.contract = contract or _ContractAdapter()
        self.image = image
        self.runtime_artifact_digest = image_digest

    async def start(self, request, *, on_event, run_id=None):
        binding = type("Binding", (), {"external_run_id": run_id})()
        return await self.contract.start(
            request,
            binding=binding,
            on_event=on_event,
        )

    async def resume(self, request, *, checkpoint, on_event):
        binding = type(
            "Binding",
            (),
            {"external_run_id": checkpoint.run_id},
        )()
        return await self.contract.resume(
            request,
            binding=binding,
            checkpoint=checkpoint,
            on_event=on_event,
        )

    async def cancel(self, user_id, task_id, revision) -> None:
        await self.contract.cancel(user_id, task_id, revision)


class _BlockingAdapter(_ContractAdapter):
    async def start(self, request, *, binding, on_event):
        del request
        self.start_calls += 1
        self.on_event = on_event
        await on_event(RuntimeEvent(event_type="agent.started", summary="开始执行"))
        self.started.set()
        await self.release.wait()
        return PiRuntimeResult(
            status=RuntimeStatus.CANCELLED,
            run_id=binding.external_run_id,
            workspace_root=Path.cwd(),
        )


@pytest.mark.asyncio
async def test_pi_binding_preparation_allocates_runtime_accepted_run_id() -> None:
    kernel = AgentKernel(
        adapter=PiAgentKernelAdapter(_FakePiRuntimeEngine()),
        repository=None,
    )

    binding, _manifest = await kernel.prepare_binding(
        model_connection_id=None,
        model_connection_version=None,
        model="local-model",
    )

    assert re.fullmatch(r"pi_run_[0-9a-f]{16}", binding.external_run_id)


class _LateCandidateAdapter(_BlockingAdapter):
    async def start(self, request, *, binding, on_event):
        result = await super().start(
            request,
            binding=binding,
            on_event=on_event,
        )
        return result.model_copy(update={"status": RuntimeStatus.CANDIDATE_READY})


class _CancelFailingAdapter(_BlockingAdapter):
    async def cancel(self, _user_id, _task_id, _revision) -> None:
        self.cancel_calls += 1
        raise RuntimeError("底层 Runtime 取消失败")


class _ReplayEventAdapter(_ContractAdapter):
    async def start(self, request, *, binding, on_event):
        del request
        self.start_calls += 1
        await on_event(
            RuntimeEvent(
                event_type="tool.completed",
                summary="完成一次读取",
                details={"runtime_event_id": "runtime-event-1"},
            )
        )
        return PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id=binding.external_run_id,
            workspace_root=Path.cwd(),
        )

    async def resume(self, request, *, binding, checkpoint, on_event):
        del request, checkpoint
        self.resume_calls += 1
        await on_event(
            RuntimeEvent(
                event_type="tool.completed",
                summary="完成一次读取",
                details={"runtime_event_id": "runtime-event-1"},
            )
        )
        return PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id=binding.external_run_id,
            workspace_root=Path.cwd(),
        )


class _UnknownAdapter(_ContractAdapter):
    async def start(self, request, *, binding, on_event):
        del request, binding, on_event
        self.start_calls += 1
        raise AgentKernelResultUnknownError("模型请求结果不确定")


class WorkerExitedError(RuntimeError):
    pass


class _CoreMindContractClient(_InteractiveCoreMindClient):
    """用 Protocol v2 假客户端驱动真实 CoreMind Adapter 公共合同。"""

    def __init__(self, contract: _ContractAdapter) -> None:
        super().__init__()
        self.contract = contract
        self.cancelled = False

    def run(self, prompt: str, *, run_id: str):
        self.contract.start_calls += 1
        self.contract.started.set()
        if isinstance(self.contract, (_BlockingAdapter, _UnknownAdapter)):
            return _FakeCoreMindClient.run(self, prompt, run_id=run_id)
        return super().run(prompt, run_id=run_id)

    def resume_run(self, run_id: str, *, input: str | None = None):
        self.contract.resume_calls += 1
        result = _FakeCoreMindClient.resume_run(self, run_id, input=input)
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

    def query(self, run_id: str):
        if isinstance(self.contract, _UnknownAdapter):
            raise WorkerExitedError("敏感 stderr 不得进入状态")
        if isinstance(self.contract, _BlockingAdapter):
            return {
                "runId": run_id,
                "projection": {
                    "status": "finished" if self.cancelled else "running",
                    "outcome": {"status": "cancelled"} if self.cancelled else None,
                },
            }
        return super().query(run_id)

    def cancel(self, run_id: str) -> None:
        self.contract.cancel_calls += 1
        self.cancelled = True
        super().cancel(run_id)

    def control(self, command):
        self.contract.steer_calls += 1
        return {
            "schemaVersion": 1,
            "runId": command["runId"],
            "controlId": command["controlId"],
            "status": "applied",
        }

    def events(self, run_id: str, *, after_sequence: int, limit: int = 1000):
        del limit
        if isinstance(self.contract, (_BlockingAdapter, _UnknownAdapter)):
            return {"events": [], "nextCursor": after_sequence}
        base = {
            "protocolVersion": "2.0",
            "eventSchemaVersion": 1,
            "runId": run_id,
            "turnId": "turn-contract",
            "timestamp": "2026-09-04T18:00:00.000Z",
            "ignorable": False,
            "sensitivity": "local",
        }
        if after_sequence == 0:
            effect_events = []
            if any(item["call_id"] == "call-submit" for item in self.tool_results):
                effect_events = [
                    {
                        **base,
                        "sequence": 3,
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
                        "sequence": 4,
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
                ]
            return {
                "events": [
                    {
                        **base,
                        "sequence": 1,
                        "eventId": "event-agent-start",
                        "eventType": "agent_start",
                        "payload": {"type": "agent_start"},
                    },
                    {
                        **base,
                        "sequence": 2,
                        "eventId": "event-tool-result",
                        "callId": "call-read",
                        "eventType": "tool_result",
                        "payload": {
                            "type": "tool_result",
                            "callId": "call-read",
                            "tool": "mangrove_read_source",
                            "isError": False,
                        },
                    },
                    *effect_events,
                ],
                "nextCursor": 4 if effect_events else 2,
            }
        if (
            after_sequence < 4
            and any(item["call_id"] == "call-submit" for item in self.tool_results)
        ):
            return {
                "events": [
                    {
                        **base,
                        "sequence": 3,
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
                        "sequence": 4,
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
                "nextCursor": 4,
            }
        return {"events": [], "nextCursor": after_sequence}


class _ContractCoreMindAdapter(CoreMindAgentKernelAdapter):
    def __init__(self, contract: _ContractAdapter, *, execution_root: Path) -> None:
        self.contract = contract
        super().__init__(
            execution_root=execution_root,
            client_factory=lambda **_values: _CoreMindContractClient(contract),
            candidate_verifier_factory=lambda _request, _run_id: object(),
            poll_interval_seconds=0,
        )

    async def start(self, request, *, binding, on_event):
        self.contract.on_event = on_event
        return await super().start(
            request,
            binding=binding,
            on_event=on_event,
        )


class _FailingAdapter(_ContractAdapter):
    async def start(self, request, *, binding, on_event):
        del request, binding
        self.on_event = on_event
        raise ValueError("Runtime 执行失败")


class _NonTerminalAdapter(_ContractAdapter):
    async def start(self, request, *, binding, on_event):
        del request, on_event
        return PiRuntimeResult(
            status=RuntimeStatus.RUNNING,
            run_id=binding.external_run_id,
            workspace_root=Path.cwd(),
        )


def _request(tmp_path: Path) -> PiRuntimeRequest:
    source = tmp_path / "来源.txt"
    source.write_text("测试来源", encoding="utf-8")
    return PiRuntimeRequest(
        user_id="user-a",
        task_id="task-a",
        revision=1,
        objective_text="读取来源并形成结果",
        requested_output_formats=("txt",),
        sources=(
            {
                "upload_id": "upload-a",
                "original_name": source.name,
                "host_path": source,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "media_type": "text/plain",
            },
        ),
        model="local-model",
        base_url="http://127.0.0.1:11434/v1",
        api_key="local-runtime",
    )


def _contract_adapter(
    adapter_kind: str,
    contract: _ContractAdapter,
    tmp_path: Path | None = None,
):
    if adapter_kind == "fake":
        return contract
    if adapter_kind == "pi":
        return PiAgentKernelAdapter(_FakePiRuntimeEngine(contract))
    if tmp_path is None:
        raise AssertionError("CoreMind 合同测试缺少隔离工作区")
    adapter = _ContractCoreMindAdapter(contract, execution_root=tmp_path / "coremind")
    adapter.bind_candidate_verification(_PassingCandidateService())
    return adapter


@pytest.mark.asyncio
async def test_pi_runtime_resolves_immutable_image_content_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "c" * 64

    class _InspectProcess:
        returncode = 0

        async def communicate(self):
            return f"{digest}\n".encode("utf-8"), b""

    async def fake_create_subprocess_exec(*arguments, **options):
        assert arguments == (
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "mangrove/pi-coding-agent:0.80.10",
        )
        assert options["stdout"] is asyncio.subprocess.PIPE
        return _InspectProcess()

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    runtime = PiRuntime(
        image="mangrove/pi-coding-agent:0.80.10",
        state_store=AgenticRuntimeRepository(
            migrated_webui_database(tmp_path / "webui.db")
        ),
        configure_as_default_document_broker=False,
    )

    artifact = await runtime.resolve_runtime_artifact()

    assert artifact == (
        "oci-image-ref=mangrove/pi-coding-agent:0.80.10;"
        f"content-digest={digest}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol_version", "required_capabilities", "available_capabilities"),
    [
        (
            AGENT_KERNEL_PROTOCOL_VERSION,
            ("start", "resume", "cancel"),
            ("start", "cancel"),
        ),
        (
            "mangrove.agent-kernel.v0",
            ("start", "resume", "cancel"),
            ("start", "resume", "cancel"),
        ),
        (
            AGENT_KERNEL_PROTOCOL_VERSION,
            (),
            ("start", "resume", "cancel"),
        ),
    ],
)
async def test_kernel_fails_closed_before_run_when_contract_is_incompatible(
    tmp_path: Path,
    protocol_version: str,
    required_capabilities: tuple[str, ...],
    available_capabilities: tuple[str, ...],
) -> None:
    adapter = _NeverStartedAdapter(
        AgentKernelCapabilityManifest(
            adapter_id="test-adapter",
            adapter_version="1.0.0",
            runtime_artifact="python-runtime:tests.test-adapter@1.0.0",
            protocol_version=protocol_version,
            event_schema_version=AGENT_KERNEL_EVENT_SCHEMA_VERSION,
            required_capabilities=required_capabilities,
            optional_capabilities=("steer",),
            available_capabilities=available_capabilities,
        )
    )
    kernel = AgentKernel(adapter=adapter, repository=None)

    with pytest.raises(AgentKernelCapabilityError):
        await kernel.start(_request(tmp_path), on_event=lambda _event: None)

    assert adapter.start_calls == 0


@pytest.mark.asyncio
async def test_kernel_freezes_exact_binding_before_adapter_start(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "webui.db")
    repository = AgenticRuntimeRepository(database)
    repository.register(
        RuntimeTaskConfig(
            user_id="user-a",
            task_id="task-a",
            revision=1,
            runtime_version=RuntimeVersion.PI,
        )
    )
    manifest = AgentKernelCapabilityManifest(
        adapter_id="pi-runtime",
        adapter_version="1.0.0",
        runtime_artifact="oci-image:mangrove/pi-coding-agent:test-a",
        protocol_version=AGENT_KERNEL_PROTOCOL_VERSION,
        event_schema_version=AGENT_KERNEL_EVENT_SCHEMA_VERSION,
        required_capabilities=("start", "resume", "cancel"),
        optional_capabilities=("steer",),
        available_capabilities=("start", "resume", "cancel"),
    )
    adapter = _CompletingAdapter(manifest, repository)
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    result = await kernel.start(_request(tmp_path), on_event=sink)

    assert adapter.start_calls == 1
    assert result.run_id == "pi_run_0123456789abcdef"
    assert adapter.binding is not None
    assert adapter.binding.capability_digest == manifest.digest
    saved = repository.get("user-a", "task-a", 1)
    assert saved is not None
    assert saved["run_id"] == result.run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["fake", "pi", "coremind"])
async def test_adapters_share_kernel_event_contract(
    tmp_path: Path,
    adapter_kind: str,
) -> None:
    repository = _registered_repository(tmp_path)
    contract = _ContractAdapter()
    adapter = _contract_adapter(adapter_kind, contract, tmp_path)
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    await kernel.start(_request(tmp_path), on_event=sink)

    events = kernel.events("user-a", "task-a", 1)
    assert [event["event_type"] for event in events] == [
        "kernel.binding.frozen",
        "agent.started",
        "tool.completed",
    ]
    snapshot = kernel.query("user-a", "task-a", 1)
    assert snapshot.status is RuntimeStatus.CANDIDATE_READY
    assert snapshot.result_known is True
    assert snapshot.quiescent is True


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["fake", "pi", "coremind"])
async def test_resume_reuses_binding_without_duplicate_start_input(
    tmp_path: Path,
    adapter_kind: str,
) -> None:
    repository = _registered_repository(tmp_path)
    contract = _ContractAdapter()
    adapter = _contract_adapter(adapter_kind, contract, tmp_path)
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    first = await kernel.start(_request(tmp_path), on_event=sink)
    checkpoint = PiRuntimeCheckpoint(
        run_id=first.run_id,
        workspace_root=first.workspace_root,
    )
    await kernel.resume(
        _request(tmp_path),
        checkpoint=checkpoint,
        on_event=sink,
    )

    assert contract.start_calls == 1
    assert contract.resume_calls == 1
    assert sum(
        event["event_type"] == "kernel.binding.frozen"
        for event in kernel.events("user-a", "task-a", 1)
    ) == 1


@pytest.mark.asyncio
async def test_resume_rejects_model_failover_before_adapter_call(
    tmp_path: Path,
) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _ContractAdapter()
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    first = await kernel.start(_request(tmp_path), on_event=sink)
    changed_model = _request(tmp_path).model_copy(
        update={"model": "another-local-model"}
    )

    with pytest.raises(AgentKernelError, match="模型或连接"):
        await kernel.resume(
            changed_model,
            checkpoint=PiRuntimeCheckpoint(
                run_id=first.run_id,
                workspace_root=tmp_path,
            ),
            on_event=sink,
        )

    assert adapter.resume_calls == 0


@pytest.mark.asyncio
async def test_repository_rejects_run_id_rebinding_after_freeze(
    tmp_path: Path,
) -> None:
    repository = _registered_repository(tmp_path)
    kernel = AgentKernel(adapter=_ContractAdapter(), repository=repository)

    async def sink(_event) -> None:
        return None

    await kernel.start(_request(tmp_path), on_event=sink)

    with pytest.raises(ValueError, match="Run ID 不可修改"):
        repository.update(
            "user-a",
            "task-a",
            1,
            run_id="pi_run_1111111111111111",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["fake", "pi", "coremind"])
async def test_cancel_is_quiescent_and_discards_late_adapter_events(
    tmp_path: Path,
    adapter_kind: str,
) -> None:
    repository = _registered_repository(tmp_path)
    contract = _BlockingAdapter()
    adapter = _contract_adapter(adapter_kind, contract, tmp_path)
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    execution = asyncio.create_task(kernel.start(_request(tmp_path), on_event=sink))
    await asyncio.wait_for(contract.started.wait(), timeout=2)
    await kernel.cancel("user-a", "task-a", 1)
    assert contract.on_event is not None
    await contract.on_event(
        RuntimeEvent(event_type="tool.completed", summary="取消后的迟到结果")
    )
    contract.release.set()
    await execution

    assert contract.cancel_calls == 1
    assert all(
        event["summary"] != "取消后的迟到结果"
        for event in kernel.events("user-a", "task-a", 1)
    )
    assert kernel.query("user-a", "task-a", 1).quiescent is True


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["fake", "pi", "coremind"])
async def test_steer_follows_each_adapter_capability_contract(
    tmp_path: Path,
    adapter_kind: str,
) -> None:
    repository = _registered_repository(tmp_path)
    contract = _BlockingAdapter()
    contract.steer_calls = 0
    adapter = _contract_adapter(adapter_kind, contract, tmp_path)
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    execution = asyncio.create_task(kernel.start(_request(tmp_path), on_event=sink))
    await asyncio.wait_for(contract.started.wait(), timeout=2)
    if adapter_kind == "coremind":
        receipt = await kernel.steer("user-a", "task-a", 1, "继续当前任务")
        assert receipt["status"] == "applied"
        assert contract.steer_calls == 1
    else:
        with pytest.raises(AgentKernelCapabilityError, match="steer"):
            await kernel.steer("user-a", "task-a", 1, "继续当前任务")
        assert contract.steer_calls == 0
    await kernel.cancel("user-a", "task-a", 1)
    contract.release.set()
    await execution


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["fake", "pi", "coremind"])
async def test_query_exposes_unknown_result_without_retrying(
    tmp_path: Path,
    adapter_kind: str,
) -> None:
    repository = _registered_repository(tmp_path)
    contract = _UnknownAdapter()
    adapter = _contract_adapter(adapter_kind, contract, tmp_path)
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    with pytest.raises(AgentKernelResultUnknownError):
        await kernel.start(_request(tmp_path), on_event=sink)

    snapshot = kernel.query("user-a", "task-a", 1)
    assert snapshot.status is RuntimeStatus.NEEDS_INPUT
    assert snapshot.result_known is False
    assert snapshot.quiescent is True
    assert contract.start_calls == 1


@pytest.mark.asyncio
async def test_failed_run_is_quiescent_and_discards_late_events(
    tmp_path: Path,
) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _FailingAdapter()
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    with pytest.raises(ValueError, match="Runtime 执行失败"):
        await kernel.start(_request(tmp_path), on_event=sink)
    assert adapter.on_event is not None
    await adapter.on_event(
        RuntimeEvent(event_type="tool.completed", summary="失败后的迟到结果")
    )

    snapshot = kernel.query("user-a", "task-a", 1)
    assert snapshot.status is RuntimeStatus.FAILED
    assert snapshot.quiescent is True
    saved = repository.get("user-a", "task-a", 1)
    assert saved is not None
    assert saved["failure"]["error_code"] == "ADAPTER_EXECUTION_FAILED"
    assert all(
        event["summary"] != "失败后的迟到结果"
        for event in kernel.events("user-a", "task-a", 1)
    )


@pytest.mark.asyncio
async def test_query_survives_kernel_restart(tmp_path: Path) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _ContractAdapter()

    async def sink(_event) -> None:
        return None

    await AgentKernel(adapter=adapter, repository=repository).start(
        _request(tmp_path),
        on_event=sink,
    )

    restarted = AgentKernel(adapter=adapter, repository=repository)
    snapshot = restarted.query("user-a", "task-a", 1)
    assert snapshot.status is RuntimeStatus.CANDIDATE_READY
    assert snapshot.result_known is True
    assert snapshot.quiescent is True


@pytest.mark.asyncio
async def test_runtime_event_replay_is_idempotent_after_kernel_restart(
    tmp_path: Path,
) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _ReplayEventAdapter()

    async def sink(_event) -> None:
        return None

    first = await AgentKernel(adapter=adapter, repository=repository).start(
        _request(tmp_path),
        on_event=sink,
    )
    repository.update("user-a", "task-a", 1, status=RuntimeStatus.RUNNING)
    await AgentKernel(adapter=adapter, repository=repository).resume(
        _request(tmp_path),
        checkpoint=PiRuntimeCheckpoint(
            run_id=first.run_id,
            workspace_root=tmp_path,
        ),
        on_event=sink,
    )

    events = repository.list_events("user-a", "task-a", 1)
    assert sum(event["event_id"] == "runtime-event-1" for event in events) == 1


@pytest.mark.asyncio
async def test_resume_rejects_changed_pi_runtime_artifact(tmp_path: Path) -> None:
    repository = _registered_repository(tmp_path)
    first_adapter = PiAgentKernelAdapter(
        _FakePiRuntimeEngine(image="mangrove/pi-coding-agent:test-a")
    )

    async def sink(_event) -> None:
        return None

    first = await AgentKernel(
        adapter=first_adapter,
        repository=repository,
    ).start(_request(tmp_path), on_event=sink)
    changed_runtime = AgentKernel(
        adapter=PiAgentKernelAdapter(
            _FakePiRuntimeEngine(image="mangrove/pi-coding-agent:test-b")
        ),
        repository=repository,
    )

    with pytest.raises(AgentKernelCapabilityError, match="RuntimeBinding"):
        await changed_runtime.resume(
            _request(tmp_path),
            checkpoint=PiRuntimeCheckpoint(
                run_id=first.run_id,
                workspace_root=tmp_path,
            ),
            on_event=sink,
        )


@pytest.mark.asyncio
async def test_resume_rejects_same_tag_with_changed_image_digest(
    tmp_path: Path,
) -> None:
    repository = _registered_repository(tmp_path)
    image = "mangrove/pi-coding-agent:0.80.10"
    first_adapter = PiAgentKernelAdapter(
        _FakePiRuntimeEngine(image=image, image_digest="sha256:" + "a" * 64)
    )

    async def sink(_event) -> None:
        return None

    first = await AgentKernel(
        adapter=first_adapter,
        repository=repository,
    ).start(_request(tmp_path), on_event=sink)
    changed_runtime = AgentKernel(
        adapter=PiAgentKernelAdapter(
            _FakePiRuntimeEngine(
                image=image,
                image_digest="sha256:" + "b" * 64,
            )
        ),
        repository=repository,
    )

    with pytest.raises(AgentKernelCapabilityError, match="RuntimeBinding"):
        await changed_runtime.resume(
            _request(tmp_path),
            checkpoint=PiRuntimeCheckpoint(
                run_id=first.run_id,
                workspace_root=tmp_path,
            ),
            on_event=sink,
        )


@pytest.mark.asyncio
async def test_cancel_failure_does_not_claim_quiescence(tmp_path: Path) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _CancelFailingAdapter()
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    execution = asyncio.create_task(kernel.start(_request(tmp_path), on_event=sink))
    await asyncio.wait_for(adapter.started.wait(), timeout=2)
    with pytest.raises(RuntimeError, match="取消失败"):
        await kernel.cancel("user-a", "task-a", 1)

    snapshot = kernel.query("user-a", "task-a", 1)
    assert snapshot.status is not RuntimeStatus.CANCELLED
    assert snapshot.quiescent is False
    adapter.release.set()
    await execution


@pytest.mark.asyncio
async def test_cancelled_run_rejects_late_candidate_result(tmp_path: Path) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _LateCandidateAdapter()
    kernel = AgentKernel(adapter=adapter, repository=repository)

    async def sink(_event) -> None:
        return None

    execution = asyncio.create_task(kernel.start(_request(tmp_path), on_event=sink))
    await asyncio.wait_for(adapter.started.wait(), timeout=2)
    await kernel.cancel("user-a", "task-a", 1)
    adapter.release.set()
    result = await execution

    assert result.status is RuntimeStatus.CANCELLED
    snapshot = kernel.query("user-a", "task-a", 1)
    assert snapshot.status is RuntimeStatus.CANCELLED
    assert snapshot.quiescent is True


@pytest.mark.asyncio
async def test_restarted_kernel_persists_cancel_via_repository_factory(
    tmp_path: Path,
) -> None:
    repository = _registered_repository(tmp_path)
    adapter = _ContractAdapter()

    async def sink(_event) -> None:
        return None

    await AgentKernel(adapter=adapter, repository=repository).start(
        _request(tmp_path),
        on_event=sink,
    )
    repository.update(
        "user-a",
        "task-a",
        1,
        status=RuntimeStatus.RUNNING,
    )
    kernel = AgentKernel(adapter=adapter, repository=lambda: repository)

    await kernel.cancel("user-a", "task-a", 1)

    snapshot = kernel.query("user-a", "task-a", 1)
    assert snapshot.status is RuntimeStatus.CANCELLED
    assert snapshot.quiescent is True


@pytest.mark.asyncio
async def test_adapter_cannot_return_before_run_is_quiescent(
    tmp_path: Path,
) -> None:
    kernel = AgentKernel(
        adapter=_NonTerminalAdapter(),
        repository=_registered_repository(tmp_path),
    )

    async def sink(_event) -> None:
        return None

    with pytest.raises(AgentKernelError, match="非静止状态"):
        await kernel.start(_request(tmp_path), on_event=sink)


def _registered_repository(tmp_path: Path) -> AgenticRuntimeRepository:
    database = migrated_webui_database(tmp_path / "webui.db")
    repository = AgenticRuntimeRepository(database)
    repository.register(
        RuntimeTaskConfig(
            user_id="user-a",
            task_id="task-a",
            revision=1,
            runtime_version=RuntimeVersion.PI,
        )
    )
    return repository
