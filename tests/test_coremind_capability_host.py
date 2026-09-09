# -*- coding: utf-8 -*-
"""CoreMind 冻结能力桥的协议与 Host 边界；不启动 Docker。"""
import json
import asyncio
from types import SimpleNamespace
import pytest

from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter, _tool_definitions
from src.capability_host import CapabilityHost
from src.agentic_runtime.egress_policy import DockerCommandResult
from src.agentic_runtime.kernel import AgentKernelCapabilityError, AgentKernelResultUnknownError
from tests.test_coremind_agent_kernel_adapter import _account_execution, _request, _binding, _InteractiveCoreMindClient
from tests.test_capability_host import _native_pack


@pytest.mark.parametrize("event_type", ["fact.resume", "fact.telemetry_configuration"])
def test_resume_fact_is_validated_without_exposing_internal_recovery_payload(event_type):
    from src.agentic_runtime.coremind_events import project_coremind_event
    event = dict(protocolVersion="2.0", eventSchemaVersion=1, runId="run-a", sequence=1,
        eventId="resume-a", timestamp="2026-09-09T01:00:00.000Z", ignorable=False,
        sensitivity="local", eventType=event_type, payload={"internal_path": "private-path"})
    assert project_coremind_event(event, run_id="run-a", model="chosen") is None
    with pytest.raises(AgentKernelCapabilityError):
        project_coremind_event({**event, "runId": "other-owner"}, run_id="run-a", model="chosen")


def test_capability_profile_freezes_distinct_tool_digest(tmp_path):
    normal = CoreMindAgentKernelAdapter(execution_root=tmp_path)
    enabled = CoreMindAgentKernelAdapter(execution_root=tmp_path, capability_tools_enabled=True)
    assert normal.manifest.digest != enabled.manifest.digest
    assert len(_tool_definitions()) == 2
    assert [item["name"] for item in _tool_definitions(True)][-1] == "mangrove_invoke_capability"


@pytest.mark.asyncio
async def test_host_invoke_uses_owned_container_and_fixed_protocol(tmp_path):
    commands = []
    async def runner(command):
        commands.append(command)
        if "inspect" in command:
            return DockerCommandResult(0, "a" * 64, "")
        return DockerCommandResult(0, json.dumps({"stdout": "ok", "stderr": ""}), "")
    host = CapabilityHost(image="test", execution_root=tmp_path, command_runner=runner)
    lease = host.cleanup_lease("owner", "task", 1, "run").model_copy(update={"capability_names": ("tool",), "capability_kinds": (("tool", "python"),)})
    assert await host.invoke(lease, {"capability": "tool", "arguments": ["--read"]}) == {"stdout": "ok", "stderr": ""}
    assert commands[-1][:4] == ("docker", "exec", "a" * 64, "node")
    with pytest.raises(ValueError):
        await host.invoke(lease, {"capability": "unselected", "arguments": []})


class BridgeHost:
    def __init__(self, tmp_path):
        self.host = CapabilityHost(image="unused", execution_root=tmp_path)
        self.calls = []
        self.starts = []
        self.stops = []
        self.error = False
        self.wait = None

    def cleanup_lease(self, *identity):
        return self.host.cleanup_lease(*identity)

    async def start(self, request):
        self.starts.append(request)
        return self.cleanup_lease(request.user_id, request.task_id, request.revision, request.run_id).model_copy(update={"capability_names": ("tool",), "capability_kinds": (("tool", "node"),)})

    async def stop(self, lease):
        self.stops.append(lease)

    async def invoke(self, lease, args):
        self.calls.append(args)
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise TimeoutError("unknown")
        return {"stdout": "bounded-result", "stderr": ""}


async def _bridge(tmp_path):
    host = BridgeHost(tmp_path / "host")
    pack = _native_pack(tmp_path, "tool")
    resolutions = []
    def resolve(*identity):
        resolutions.append(identity)
        return (pack,)
    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path, capability_tools_enabled=True,
        capability_host=host, capability_mount_resolver=resolve,
        capability_call_validator=lambda *values: None,
        capability_contract_describer=lambda *values: [{"capability": "tool", "parameters_schema": {"type": "array", "maxItems": 0}, "tools": []}])
    request = _request(tmp_path)
    binding = _binding(adapter, "run-a")
    root = tmp_path / "run"
    root.mkdir()
    await adapter._prepare_capability_host(request, binding, root)
    definition = _tool_definitions(True)[-1]
    call = {**{key: definition[key] for key in ("name", "toolId", "registrationId")},
            "runId": "run-a", "callId": "invoke-1", "args": {"capability": "tool", "arguments": []}}
    return adapter, host, request, binding, root, call, resolutions


@pytest.mark.asyncio
async def test_prompt_includes_frozen_invocation_contract(tmp_path):
    adapter, host, request, binding, root, _, _ = await _bridge(tmp_path)
    prompt = adapter._capability_prompt(request)
    assert 'parameters_schema' in prompt and 'maxItems' in prompt
    assert str(root) not in prompt
    adapter._capability_contract_describer = None
    with pytest.raises(AgentKernelCapabilityError):
        await adapter._prepare_capability_host(request, binding, root)


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_run_identity_is_durable_before_host_creation(tmp_path, monkeypatch, resume):
    import hashlib
    from src.agentic_runtime.coremind_runtime import _WORKER_STATE
    from src.agentic_runtime.kernel import AgentKernelError

    host = BridgeHost(tmp_path / 'host')
    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path, capability_host=host)
    request = _request(tmp_path)
    binding = _binding(adapter, 'run-window')
    owner = hashlib.sha256(request.user_id.encode()).hexdigest()[:16]
    root = tmp_path / 'coremind' / owner / request.task_id / 'r1' / binding.external_run_id
    root.mkdir(parents=True)
    monkeypatch.setattr(adapter, '_new_client', lambda *args: (_InteractiveCoreMindClient(), root))
    monkeypatch.setattr(adapter, '_revoke_model_grants', lambda *args, **kwargs: None)
    async def no_close(*args, **kwargs):
        pass
    monkeypatch.setattr(adapter, '_close_client', no_close)
    async def creation_window(*args):
        assert (root / _WORKER_STATE).is_file()
        fresh = CoreMindAgentKernelAdapter(execution_root=tmp_path, capability_host=host)
        with pytest.raises(AgentKernelError):
            await fresh.cancel(request.user_id, request.task_id, 1)
        assert len(host.stops) == 1
        raise asyncio.CancelledError
    monkeypatch.setattr(adapter, '_prepare_capability_host', creation_window)
    with pytest.raises(asyncio.CancelledError):
        if resume:
            await adapter.resume(request, binding=binding, checkpoint=SimpleNamespace(run_id=binding.external_run_id, workspace_root=root), on_event=no_close)
        else:
            await adapter.start(request, binding=binding, on_event=no_close)


@pytest.mark.asyncio
async def test_bridge_invokes_once_and_rechecks_mount_on_resume(tmp_path):
    adapter, host, request, binding, root, call, resolutions = await _bridge(tmp_path)
    client = _InteractiveCoreMindClient()
    await adapter._answer_tool_call(client, request=request, binding=binding, run_root=root, call=call)
    await adapter._prepare_capability_host(request, binding, root)
    await adapter._answer_tool_call(client, request=request, binding=binding, run_root=root, call=call)
    assert len(host.calls) == 1
    assert len(resolutions) == 2
    assert all(item.network_name == "none" for item in host.starts)
    assert client.tool_results[-1]["result"]["stdout"] == "bounded-result"


@pytest.mark.asyncio
async def test_bridge_unknown_result_is_not_replayed(tmp_path):
    adapter, host, request, binding, root, call, _ = await _bridge(tmp_path)
    host.error = True
    client = _InteractiveCoreMindClient()
    for _ in range(2):
        with pytest.raises(AgentKernelResultUnknownError):
            await adapter._answer_tool_call(client, request=request, binding=binding, run_root=root, call=call)
    assert len(host.calls) == 1
    assert client.tool_results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_bridge_requires_business_validator_before_host_call(tmp_path, missing):
    adapter, host, request, binding, root, call, _ = await _bridge(tmp_path)
    seen = []
    def reject(*values):
        seen.append(values)
        raise ValueError("业务合同拒绝")
    adapter._capability_call_validator = None if missing else reject
    with pytest.raises(AgentKernelCapabilityError):
        await adapter._answer_tool_call(_InteractiveCoreMindClient(), request=request, binding=binding, run_root=root, call=call)
    assert host.calls == []
    assert not list(root.glob(".capability-call-*"))
    if not missing:
        assert seen == [("user-a", "task-a", 1, "tool", [], None)]


@pytest.mark.asyncio
async def test_tools_profile_requires_validator_before_starting_host(tmp_path):
    adapter, host, request, binding, root, _, _ = await _bridge(tmp_path)
    adapter._capability_call_validator = None
    starts = len(host.starts)
    with pytest.raises(AgentKernelCapabilityError):
        await adapter._prepare_capability_host(request, binding, root)
    assert len(host.starts) == starts


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"runId": "wrong"}, {"toolId": "wrong"}, {"registrationId": "wrong"}, {"name": "wrong"}, {"args": {"capability": "other", "arguments": []}}, {"args": {"capability": "tool", "arguments": {}, "shell": "bad"}}])
async def test_bridge_rejects_unfrozen_identity_or_schema(tmp_path, change):
    adapter, host, request, binding, root, call, _ = await _bridge(tmp_path)
    with pytest.raises(AgentKernelCapabilityError):
        await adapter._answer_tool_call(_InteractiveCoreMindClient(), request=request, binding=binding, run_root=root, call={**call, **change})
    assert host.calls == []


@pytest.mark.asyncio
async def test_bridge_cancel_discards_late_result(tmp_path):
    adapter, host, request, binding, root, call, _ = await _bridge(tmp_path)
    host.wait = asyncio.Event()
    client = _InteractiveCoreMindClient()
    pending = asyncio.create_task(adapter._answer_tool_call(client, request=request, binding=binding, run_root=root, call=call))
    await asyncio.sleep(0)
    adapter._clients[("user-a", "task-a", 1)] = ("run-a", client, root)
    await adapter.cancel("user-a", "task-a", 1)
    host.wait.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert client.tool_results == []
    assert host.stops


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,has_mount", [(True, False), (False, True)])
async def test_profile_rejects_actual_mount_mismatch(tmp_path, enabled, has_mount):
    pack = _native_pack(tmp_path, "tool")
    host = BridgeHost(tmp_path / "host")
    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path, capability_tools_enabled=enabled,
        capability_host=host, capability_mount_resolver=lambda *identity: (pack,) if has_mount else ())
    with pytest.raises(AgentKernelCapabilityError):
        await adapter._prepare_capability_host(_request(tmp_path), _binding(adapter, "run-a"), tmp_path)
    assert host.starts == []


@pytest.mark.asyncio
async def test_bridge_rejects_revoked_owner_before_invoke(tmp_path):
    from src.api.auth import get_store
    from src.account_execution import ExecutionDenied
    adapter, host, request, binding, root, call, _ = await _bridge(tmp_path)
    with get_store()._conn() as connection:
        connection.execute("UPDATE users SET disabled=1 WHERE user_id='user-a'")
    with pytest.raises(ExecutionDenied):
        await adapter._answer_tool_call(_InteractiveCoreMindClient(), request=request, binding=binding, run_root=root, call=call)
    assert host.calls == []


@pytest.mark.asyncio
async def test_bridge_revocation_during_invoke_does_not_submit_late_result(tmp_path):
    from src.api.auth import get_store
    from src.account_execution import ExecutionDenied
    adapter, host, request, binding, root, call, _ = await _bridge(tmp_path)
    host.wait = asyncio.Event()
    client = _InteractiveCoreMindClient()
    pending = asyncio.create_task(adapter._answer_tool_call(client, request=request, binding=binding, run_root=root, call=call))
    await asyncio.sleep(0)
    with get_store()._conn() as connection:
        connection.execute("UPDATE users SET disabled=1 WHERE user_id='user-a'")
    host.wait.set()
    with pytest.raises(ExecutionDenied):
        await pending
    assert client.tool_results == []
