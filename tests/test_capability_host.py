# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from types import SimpleNamespace

import pytest

from src.agentic_runtime.egress_policy import (
    DockerCommandResult,
    EgressLease,
    EgressPhase,
    SmokescreenEgressController,
    EgressPolicy,
)
from src.agentic_runtime.models import PiRuntimeCheckpoint, PiRuntimeRequest, SourceInput
from src.agentic_runtime.pi_runtime import PiRuntime, build_docker_command
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.capability_host import (
    CapabilityHost,
    CapabilityHostLease,
    CapabilityHostRequest,
)
from src.config.settings import Settings
from tests.database_migration_helpers import migrated_webui_database


def _state_store(tmp_path: Path) -> AgenticRuntimeRepository:
    return AgenticRuntimeRepository(
        migrated_webui_database(tmp_path / "runtime-state.db")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_creation", [False, True])
async def test_pi_restart_preserves_cleanup_identity_and_owner(tmp_path, unknown_creation):
    commands = []
    failed = True

    async def runner(command):
        commands.append(command)
        if command[:3] == ("docker", "network", "rm") and failed:
            return DockerCommandResult(1, "", "synthetic daemon failure")
        return DockerCommandResult(0, "f" * 64 if "inspect" in command else "", "")

    state = _state_store(tmp_path)

    def runtime():
        return PiRuntime(
            state_store=state, execution_root=tmp_path / "runtime", connection_broker=RecordingBroker(),
            egress_controller=SmokescreenEgressController(image="test", command_runner=runner),
        )

    first = runtime()
    key = ("owner", "task", 1)
    run_id = "pi_run_1234567890abcdef"
    root = first.execution_root / "agentic-vnext" / hashlib.sha256(b"owner").hexdigest()[:16] / "task" / "r1" / run_id
    policy_dir = root / "trace" / "egress-business"
    policy_dir.mkdir(parents=True)
    first._plan_resources(SimpleNamespace(user_id="owner", task_id="task", revision=1), run_id, root, policy_dir, False)
    journal = first._lifecycle_dir(key) / "resources.json"
    if unknown_creation:
        (policy_dir / ".creating").write_text("creation_pending", encoding="utf-8")
    with pytest.raises(RuntimeError):
        await first.cancel(*key)
    assert journal.exists()
    second = runtime()
    count = len(commands)
    await second.cancel("another-owner", "task", 1)
    assert len(commands) == count
    if unknown_creation:
        with pytest.raises(RuntimeError, match="创建结果"):
            await second.cancel(*key)
        assert journal.exists()
        # 故障替身现在给出创建已结算的证据，模拟原执行者解除未知结果。
        (policy_dir / ".creating").unlink()
    failed = False
    await second.cancel(*key)
    assert not journal.exists()
    assert not second._egress_leases


@pytest.mark.asyncio
async def test_pi_restart_rejects_foreign_journal_before_docker(tmp_path):
    runtime = PiRuntime(state_store=_state_store(tmp_path), execution_root=tmp_path / "runtime", connection_broker=RecordingBroker())
    key = ("owner", "task", 1)
    journal = runtime._lifecycle_dir(key) / "resources.json"
    journal.write_text(json.dumps({"owner": "other", "task": "task", "revision": 1}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Owner"):
        await runtime.cancel(*key)
    assert journal.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_worker", [False, True])
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("barrier", ["network", "proxy", "host", "ready"])
async def test_pi_cancel_during_resource_creation(tmp_path, monkeypatch, barrier, resume, other_worker):
    entered, release = asyncio.Event(), asyncio.Event()
    commands = []
    enabled = not resume

    async def runner(command):
        commands.append(command)
        kind = (
            "network" if command[:3] == ("docker", "network", "create") else
            "host" if command[:3] == ("docker", "run", "-d") and "mangrove.capability-host=true" in command else
            "proxy" if command[:3] == ("docker", "run", "-d") else
            "ready" if command[:2] == ("docker", "exec") else "other"
        )
        if enabled and kind == barrier:
            entered.set()
            await release.wait()
        return DockerCommandResult(0, "f" * 64 if "inspect" in command else "", "")

    source = tmp_path / "source.txt"
    source.write_text("synthetic", encoding="utf-8")
    request = PiRuntimeRequest(
        user_id="owner", task_id="task", revision=1, objective_text="read", requested_output_formats=("txt",),
        sources=(SourceInput(upload_id="upload", original_name="source.txt", host_path=source, sha256=hashlib.sha256(source.read_bytes()).hexdigest()),),
        model="test", base_url="http://192.168.1.20:6012/v1", api_key="synthetic-not-in-journal",
    )
    host = CapabilityHost(image="test", execution_root=tmp_path / "hosts", command_runner=runner)
    runtime = PiRuntime(
        execution_root=tmp_path / "runtime", state_store=_state_store(tmp_path), connection_broker=RecordingBroker(),
        egress_controller=SmokescreenEgressController(image="test", command_runner=runner), capability_host=host,
        capability_mount_resolver=lambda *_: (_native_pack(tmp_path / "pack", "prettier"),),
    )
    pack = _native_pack(tmp_path / "pack", "prettier")
    runtime._capability_mount_resolver = lambda *_: (pack,)

    async def no_image():
        pass

    async def remove(name, _owner_identity):
        commands.append(("docker", "rm", "-f", name))

    monkeypatch.setattr(runtime, "_assert_image", no_image)
    monkeypatch.setattr(runtime, "_remove_owned_container", remove)
    checkpoint = {}

    class Prepared(Exception):
        pass

    async def event(event):
        if event.event_type == "runtime.preparing":
            checkpoint.update(event.details["_checkpoint"])
            if not enabled:
                raise Prepared

    run_id = "pi_run_1234567890abcdef"
    if resume:
        with pytest.raises(Prepared):
            await runtime.start(request, run_id=run_id, on_event=event)
        root = Path(checkpoint["workspace_root"])
        (root / "session" / "persisted.jsonl").write_text("{}\n", encoding="utf-8")
        enabled = True
        operation = runtime.resume(request, checkpoint=PiRuntimeCheckpoint(run_id=run_id, workspace_root=root, container_name=None, session_file="session/persisted.jsonl"), on_event=event)
    else:
        operation = runtime.start(request, run_id=run_id, on_event=event)
    task = asyncio.create_task(operation)
    await asyncio.wait_for(entered.wait(), 3)
    journal = runtime._lifecycle_dir(("owner", "task", 1)) / "resources.json"
    assert "synthetic-not-in-journal" not in journal.read_text(encoding="utf-8")
    canceller = runtime
    if other_worker:
        # 独立实例无执行Task/Lease，只能依赖持久标记和操作系统文件锁。
        canceller = PiRuntime(
            execution_root=runtime.execution_root, state_store=runtime._state_store,
            connection_broker=RecordingBroker(), egress_controller=runtime.egress_controller, capability_host=host,
        )
        monkeypatch.setattr(canceller, "_remove_owned_container", remove)
    cancel = asyncio.create_task(canceller.cancel("owner", "task", 1))
    await asyncio.sleep(0.02)
    if barrier != "ready":
        assert not cancel.done()
    release.set()
    await asyncio.wait_for(cancel, 3)
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(commands)
    await runtime.cancel("owner", "task", 1)
    assert len(commands) == count
    assert not journal.exists()
    assert not runtime._containers and not runtime._egress_leases and not runtime._capability_host_leases


def test_capability_host_is_disabled_by_default() -> None:
    """原生能力仍是显式灰度项，不能改变既有任务的默认执行路径。"""
    assert Settings.model_fields["pi_capability_host_enabled"].default is False


class RecordingDocker:
    """Docker 是系统外部 seam；测试 Adapter 只返回确定性结果。"""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    async def __call__(self, command: tuple[str, ...]) -> DockerCommandResult:
        self.commands.append(command)
        return DockerCommandResult(returncode=0, stdout="f" * 64 if "inspect" in command else "", stderr="")


class FailedRemovalDocker(RecordingDocker):
    async def __call__(self, command: tuple[str, ...]) -> DockerCommandResult:
        self.commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return DockerCommandResult(returncode=1, stdout="", stderr="daemon unavailable")
        return DockerCommandResult(returncode=0, stdout="f" * 64 if "inspect" in command else "", stderr="")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["host", "egress"])
async def test_predicted_resource_collision_never_removes_foreign_owner(tmp_path, kind):
    commands = []

    async def runner(command):
        commands.append(command)
        if "inspect" in command:
            return DockerCommandResult(0, "", "")
        return DockerCommandResult(1, "", "name already in use")

    if kind == "host":
        host = CapabilityHost(image="test", execution_root=tmp_path / "hosts", command_runner=runner)
        operation = host.start(CapabilityHostRequest(user_id="owner", task_id="task", revision=1, run_id="run", network_name="network", capability_dirs=(_native_pack(tmp_path / "pack", "prettier"),)))
    else:
        controller = SmokescreenEgressController(image="test", command_runner=runner)
        operation = controller.start(policy=EgressPolicy.for_business_execution(model_base_url="http://192.168.1.20:6012/v1"), user_id="owner", task_id="task", revision=1, run_id="run", policy_dir=tmp_path / "policy")
    with pytest.raises(RuntimeError, match="身份不匹配"):
        await operation
    assert not any("rm" in command or "stop" in command or "logs" in command for command in commands)


@pytest.mark.asyncio
async def test_host_cleanup_uses_verified_id_instead_of_predicted_name(tmp_path):
    commands = []

    async def runner(command):
        commands.append(command)
        return DockerCommandResult(0, "a" * 64 if "inspect" in command else "", "")

    host = CapabilityHost(image="test", execution_root=tmp_path / "hosts", command_runner=runner)
    lease = host.cleanup_lease("owner", "task", 1, "run")
    await host.stop(lease)
    assert commands[-1] == ("docker", "rm", "-f", "a" * 64)
    assert lease.container_name not in commands[-1]


class HangingDocker(RecordingDocker):
    async def __call__(self, command: tuple[str, ...]) -> DockerCommandResult:
        self.commands.append(command)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["run", "exec"])
async def test_host_creation_cancel_waits_and_cleans(tmp_path, barrier):
    entered, release = asyncio.Event(), asyncio.Event()
    commands = []

    async def runner(command):
        commands.append(command)
        if command[1] == barrier:
            entered.set()
            await release.wait()
        return DockerCommandResult(0, "f" * 64 if "inspect" in command else "", "")

    host = CapabilityHost(image="test", execution_root=tmp_path / "hosts", command_runner=runner)
    task = asyncio.create_task(host.start(CapabilityHostRequest(
        user_id="owner", task_id="task", revision=1, run_id="run", network_name="network",
        capability_dirs=(_native_pack(tmp_path / "pack", "prettier"),),
    )))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    if barrier == "run":
        assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert commands[-1][:3] == ("docker", "rm", "-f")
    assert not any((tmp_path / "hosts").iterdir())


class FailingHostCleanup:
    def __init__(self) -> None:
        self.cancel_calls = 0

    async def cancel(self, _lease: CapabilityHostLease) -> None:
        self.cancel_calls += 1
        raise RuntimeError("synthetic host cleanup failure")


class RecordingBroker:
    def __init__(self) -> None:
        self.revisions: list[tuple[str, str, int, str]] = []

    def revoke_revision_grants(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        *,
        reason: str,
    ) -> int:
        self.revisions.append((user_id, task_id, revision, reason))
        return 0


def _native_pack(root: Path, name: str) -> Path:
    pack = root / "prettier-pack"
    pack.mkdir(parents=True)
    (pack / "mangrove-capability.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": name,
                "version": "3.9.6",
                "kind": "node",
                "purpose": "格式化 JSON",
                "entrypoint": {
                    "program": "node",
                    "arguments": ["tool.mjs"],
                },
                "permissions": ["process:child", "network:none"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pack / "tool.mjs").write_text(
        "process.stdout.write(JSON.stringify(process.argv.slice(2)))\n",
        encoding="utf-8",
    )
    return pack


@pytest.mark.asyncio
async def test_capability_host_starts_one_isolated_sidecar_for_multiple_packs(
    tmp_path: Path,
) -> None:
    docker = RecordingDocker()
    first = _native_pack(tmp_path / "first", "prettier")
    second = _native_pack(tmp_path / "second", "prettier-2")
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=docker,
    )

    lease = await host.start(
        CapabilityHostRequest(
            user_id="user-a",
            task_id="task-a",
            revision=1,
            run_id="run-a",
            network_name="mangrove-pi-net-a",
            capability_dirs=(first, second),
        )
    )

    assert lease.capability_names == ("prettier", "prettier-2")
    assert lease.relay_url.endswith(":8765")
    run_commands = [item for item in docker.commands if item[:3] == ("docker", "run", "-d")]
    assert len(run_commands) == 1
    joined = "\n".join(run_commands[0])
    assert "/workspace/input" not in joined
    assert "/root/.pi/agent" not in joined
    assert "/var/run/docker.sock" not in joined
    assert "no-new-privileges" in joined
    assert lease.relay_token not in joined
    assert "--env-file" in run_commands[0]

    await host.stop(lease)

    assert any(item[:4] == ("docker", "rm", "-f", "f" * 64) for item in docker.commands)
    assert not lease.runtime_dir.exists()


@pytest.mark.asyncio
async def test_capability_host_preserves_manifest_index_after_legacy_directory(
    tmp_path: Path,
) -> None:
    docker = RecordingDocker()
    legacy = tmp_path / "legacy-pack"
    legacy.mkdir()
    native = _native_pack(tmp_path / "native", "prettier")
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=docker,
    )

    lease = await host.start(
        CapabilityHostRequest(
            user_id="user-a",
            task_id="task-legacy-index",
            revision=1,
            run_id="run-legacy-index",
            network_name="mangrove-pi-net-legacy-index",
            capability_dirs=(legacy, native),
        )
    )
    run_command = next(
        command
        for command in docker.commands
        if command[:3] == ("docker", "run", "-d")
    )

    assert any("target=/capabilities/2" in argument for argument in run_command)

    await host.stop(lease)


@pytest.mark.asyncio
async def test_capability_host_preserves_runtime_evidence_when_forced_remove_fails(
    tmp_path: Path,
) -> None:
    docker = FailedRemovalDocker()
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=docker,
    )
    runtime_dir = tmp_path / "hosts" / "lease"
    runtime_dir.mkdir(parents=True)
    lease = CapabilityHostLease(
        container_name="mangrove-cap-host-failed-remove",
        relay_url="http://mangrove-cap-host-failed-remove:8765",
        relay_token="secret",
        capability_names=("prettier",),
        capability_kinds=(("prettier", "node"),),
        runtime_dir=runtime_dir,
        owner_identity="a" * 64,
    )

    with pytest.raises(RuntimeError, match="无法清理 Capability Host"):
        await host.stop(lease)

    assert runtime_dir.exists()


@pytest.mark.asyncio
async def test_capability_host_bounds_every_docker_operation(tmp_path: Path) -> None:
    pack = _native_pack(tmp_path / "pack", "prettier")
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=HangingDocker(),
        docker_timeout_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="Docker 操作超时"):
        await host.start(
            CapabilityHostRequest(
                user_id="user-a",
                task_id="task-timeout",
                revision=1,
                run_id="run-timeout",
                network_name="network-timeout",
                capability_dirs=(pack,),
            )
        )


@pytest.mark.asyncio
async def test_pi_cancel_revokes_network_and_revision_when_host_cleanup_fails(
    tmp_path: Path,
) -> None:
    docker = RecordingDocker()
    host = FailingHostCleanup()
    broker = RecordingBroker()
    runtime = PiRuntime(
        execution_root=tmp_path / "runtime",
        egress_controller=SmokescreenEgressController(
            image="mangrove/smokescreen:da4840c9",
            command_runner=docker,
        ),
        connection_broker=broker,  # type: ignore[arg-type]
        capability_host=host,  # type: ignore[arg-type]
        state_store=_state_store(tmp_path),
    )
    run_key = ("user-a", "task-cleanup", 1)
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    runtime._capability_host_leases[run_key] = CapabilityHostLease(
        container_name="mangrove-cap-host-cleanup",
        relay_url="http://mangrove-cap-host-cleanup:8765",
        relay_token="secret",
        capability_names=("prettier",),
        capability_kinds=(("prettier", "node"),),
        runtime_dir=host_dir,
    )
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    runtime._egress_leases[run_key] = EgressLease(
        phase=EgressPhase.BUSINESS_EXECUTION,
        network_name="mangrove-pi-net-cleanup",
        proxy_container_name="mangrove-pi-proxy-cleanup",
        proxy_url="http://mangrove-pi-proxy-cleanup:4750",
        policy_dir=policy_dir,
        owner_identity="a" * 64,
    )

    with pytest.raises(Exception, match="任务授权清理未完全成功"):
        await runtime.cancel(*run_key)

    assert any(item[:3] == ("docker", "network", "rm") for item in docker.commands)
    assert broker.revisions == [("user-a", "task-cleanup", 1, "run_cancelled")]
    assert host.cancel_calls == 1

    with pytest.raises(Exception, match="任务授权清理未完全成功"):
        await runtime.cancel(*run_key)
    assert host.cancel_calls == 2


@pytest.mark.asyncio
async def test_capability_host_rejects_empty_or_remote_only_selection(
    tmp_path: Path,
) -> None:
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=RecordingDocker(),
    )

    with pytest.raises(ValueError, match="本地原生能力"):
        await host.start(
            CapabilityHostRequest(
                user_id="user-a",
                task_id="task-a",
                revision=1,
                run_id="run-a",
                network_name="mangrove-pi-net-a",
                capability_dirs=(),
            )
        )


@pytest.mark.asyncio
async def test_pi_runtime_uses_sidecar_only_for_native_capability(
    tmp_path: Path,
) -> None:
    docker = RecordingDocker()
    pack = _native_pack(tmp_path / "pack", "prettier")
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=docker,
    )
    runtime = PiRuntime(
        execution_root=tmp_path / "runtime",
        egress_controller=SmokescreenEgressController(
            image="mangrove/smokescreen:da4840c9",
            command_runner=docker,
        ),
        capability_mount_resolver=lambda *_args: (pack,),
        capability_host=host,
        state_store=_state_store(tmp_path),
    )
    request = PiRuntimeRequest(
        user_id="user-a",
        task_id="task-a",
        revision=1,
        objective_text="格式化来源",
        requested_output_formats=("json",),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.txt",
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
        model="local-model",
        base_url="http://192.168.1.20:6012/v1",
        api_key="local-runtime",
    )

    class Prepared(RuntimeError):
        pass

    captured: dict[str, object] = {}

    async def stop_after_preparing(event: object) -> None:
        if getattr(event, "event_type", "") == "runtime.preparing":
            captured.update(getattr(event, "details")["_checkpoint"])
            raise Prepared

    with pytest.raises(Prepared):
        await runtime.start(request, on_event=stop_after_preparing)

    trace_path = next((tmp_path / "runtime").rglob("docker-command.json"))
    pi_command = json.loads(trace_path.read_text(encoding="utf-8"))["argv"]
    joined = "\n".join(pi_command)
    assert "/capabilities/" not in joined
    config_dir = trace_path.parents[1] / "config"
    bridge = json.loads(
        (config_dir / "capability-host.json").read_text(encoding="utf-8")
    )
    assert bridge["capabilities"] == [{"name": "prettier", "kind": "node"}]
    assert bridge["relayUrl"].startswith("http://mangrove-cap-host-")
    host_runs = [
        item
        for item in docker.commands
        if item[:3] == ("docker", "run", "-d")
        and "mangrove.capability-host=true" in item
    ]
    assert len(host_runs) == 1

    root = Path(str(captured["workspace_root"]))
    session = root / "session" / "persisted.jsonl"
    session.write_text("{}\n", encoding="utf-8")
    checkpoint = PiRuntimeCheckpoint(
        run_id=str(captured["run_id"]),
        workspace_root=root,
        container_name=None,
        session_file="session/persisted.jsonl",
    )

    class Resumed(RuntimeError):
        pass

    async def stop_after_resuming(event: object) -> None:
        if getattr(event, "event_type", "") == "runtime.resuming":
            raise Resumed

    with pytest.raises(Resumed):
        await runtime.resume(
            request,
            checkpoint=checkpoint,
            on_event=stop_after_resuming,
        )
    resume_trace = next(root.joinpath("trace").glob("docker-command-resume-*.json"))
    resumed_command = json.loads(resume_trace.read_text(encoding="utf-8"))["argv"]
    assert "/capabilities/" not in "\n".join(resumed_command)
    assert sum(
        1
        for item in docker.commands
        if item[:3] == ("docker", "run", "-d")
        and "mangrove.capability-host=true" in item
    ) == 2


@pytest.mark.asyncio
async def test_pi_runtime_cancel_revokes_host_before_return(tmp_path: Path) -> None:
    docker = RecordingDocker()
    broker = RecordingBroker()
    pack = _native_pack(tmp_path / "pack", "prettier")
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=tmp_path / "hosts",
        command_runner=docker,
    )
    runtime = PiRuntime(
        execution_root=tmp_path / "runtime",
        egress_controller=SmokescreenEgressController(
            image="mangrove/smokescreen:da4840c9",
            command_runner=docker,
        ),
        capability_mount_resolver=lambda *_args: (pack,),
        capability_host=host,
        connection_broker=broker,  # type: ignore[arg-type]
        state_store=_state_store(tmp_path),
    )
    request = PiRuntimeRequest(
        user_id="user-a",
        task_id="task-cancel",
        revision=1,
        objective_text="格式化来源",
        requested_output_formats=("json",),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.txt",
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
        model="local-model",
        base_url="http://192.168.1.20:6012/v1",
        api_key="local-runtime",
    )
    preparing = asyncio.Event()
    release = asyncio.Event()

    async def block_preparing(event: object) -> None:
        if getattr(event, "event_type", "") == "runtime.preparing":
            preparing.set()
            await release.wait()

    task = asyncio.create_task(runtime.start(request, on_event=block_preparing))
    await asyncio.wait_for(preparing.wait(), timeout=10)
    await runtime.cancel("user-a", "task-cancel", 1)
    task.cancel()
    release.set()
    with suppress(asyncio.CancelledError):
        await task

    host_stop = max(
        index
        for index, item in enumerate(docker.commands)
        if item[:3] == ("docker", "stop", "--time")
    )
    host_remove = next(
        index
        for index, item in enumerate(docker.commands)
        if index > host_stop and item[:4] == ("docker", "rm", "-f", "f" * 64)
    )
    network_remove = next(
        index
        for index, item in enumerate(docker.commands)
        if item[:3] == ("docker", "network", "rm")
    )
    assert host_remove < network_remove


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("MANGROVE_RUN_DOCKER_TESTS") != "1", reason="真实 Docker 验证需显式启用")
async def test_real_capability_host_invokes_command_without_business_mounts(
    tmp_path: Path,
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker 不可用")
    image = "mangrove/pi-coding-agent:0.80.10"
    if subprocess.run(
        ("docker", "image", "inspect", image),
        check=False,
        capture_output=True,
    ).returncode != 0:
        pytest.skip("固定 Pi 镜像不存在")
    network = f"mangrove-cap-host-test-{uuid.uuid4().hex[:10]}"
    subprocess.run(("docker", "network", "create", "--internal", network), check=True)
    pack = _native_pack(tmp_path / "real-pack", "echo-argv")
    host = CapabilityHost(
        image=image,
        execution_root=tmp_path / "hosts",
    )
    lease = None
    try:
        lease = await host.start(
            CapabilityHostRequest(
                user_id="user-real",
                task_id="task-real",
                revision=1,
                run_id="run-real",
                network_name=network,
                capability_dirs=(pack,),
            )
        )
        probe = subprocess.run(
            (
                "docker", "run", "--rm", "--network", network,
                "--env", f"RELAY={lease.relay_url}",
                "--env", f"TOKEN={lease.relay_token}",
                image, "node", "-e",
                "fetch(process.env.RELAY+'/invoke',{method:'POST',headers:{authorization:'Bearer '+process.env.TOKEN,'content-type':'application/json'},body:JSON.stringify({capability:'echo-argv',arguments:['hello']})}).then(async r=>{console.log(await r.text());if(!r.ok)process.exit(1)})",
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert probe.returncode == 0, probe.stderr
        assert '\\"hello\\"' in probe.stdout

        runtime_root = tmp_path / "pi-runtime"
        paths = {
            name: runtime_root / name
            for name in ("input", "work", "output", "session", "config")
        }
        for path in paths.values():
            path.mkdir(parents=True)
        synthetic_source = paths["input"] / "source.txt"
        synthetic_source.write_text("synthetic", encoding="utf-8")
        request = PiRuntimeRequest(
            user_id="user-real",
            task_id="task-real",
            revision=1,
            objective_text="验证 Bridge",
            requested_output_formats=("json",),
            sources=(
                SourceInput(
                    upload_id="upload-real",
                    original_name="source.txt",
                    host_path=synthetic_source,
                    sha256=hashlib.sha256(synthetic_source.read_bytes()).hexdigest(),
                ),
            ),
            model="synthetic-model",
            base_url="http://127.0.0.1:9/v1",
            api_key="synthetic-no-request",
        )
        PiRuntime._write_runtime_files(
            request,
            source_names=("source.txt",),
            config_dir=paths["config"],
            work_dir=paths["work"],
            capability_dirs=(pack,),
            capability_host_lease=lease,
        )
        pi_command = build_docker_command(
            image=image,
            container_name=f"mangrove-cap-pi-test-{uuid.uuid4().hex[:8]}",
            input_dir=paths["input"],
            work_dir=paths["work"],
            output_dir=paths["output"],
            session_dir=paths["session"],
            config_dir=paths["config"],
            model="synthetic-model",
            memory="1g",
            cpus=1,
            network_name=network,
            capability_dirs=(pack,),
            capability_host_lease=lease,
        )
        pi_probe = subprocess.run(
            pi_command,
            input='{"type":"get_state"}\n',
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert pi_probe.returncode == 0, pi_probe.stderr
        assert '"command":"get_state"' in pi_probe.stdout.replace(" ", "")
        assert "extension_error" not in pi_probe.stdout
        assert "mangrove-capability-host" not in pi_probe.stderr.casefold()
    finally:
        if lease is not None:
            await host.stop(lease)
        subprocess.run(("docker", "network", "rm", network), check=False, capture_output=True)
    remaining = subprocess.check_output(
        ("docker", "ps", "-a", "--filter", "label=mangrove.capability-host=true", "--format", "{{.Names}}"),
        text=True,
        encoding="utf-8",
    )
    assert lease is None or lease.container_name not in remaining.splitlines()
