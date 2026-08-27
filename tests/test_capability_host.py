# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from src.agentic_runtime.egress_policy import (
    DockerCommandResult,
    EgressLease,
    EgressPhase,
    SmokescreenEgressController,
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


def test_capability_host_is_disabled_by_default() -> None:
    """原生能力仍是显式灰度项，不能改变既有任务的默认执行路径。"""
    assert Settings.model_fields["pi_capability_host_enabled"].default is False


class RecordingDocker:
    """Docker 是系统外部 seam；测试 Adapter 只返回确定性结果。"""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    async def __call__(self, command: tuple[str, ...]) -> DockerCommandResult:
        self.commands.append(command)
        return DockerCommandResult(returncode=0, stdout="", stderr="")


class FailedRemovalDocker(RecordingDocker):
    async def __call__(self, command: tuple[str, ...]) -> DockerCommandResult:
        self.commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return DockerCommandResult(returncode=1, stdout="", stderr="daemon unavailable")
        return DockerCommandResult(returncode=0, stdout="", stderr="")


class HangingDocker(RecordingDocker):
    async def __call__(self, command: tuple[str, ...]) -> DockerCommandResult:
        self.commands.append(command)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


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

    assert any(item[:4] == ("docker", "rm", "-f", lease.container_name) for item in docker.commands)
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

    host_remove = max(
        index
        for index, item in enumerate(docker.commands)
        if item[:3] == ("docker", "rm", "-f")
        and item[3].startswith("mangrove-cap-host-")
    )
    network_remove = next(
        index
        for index, item in enumerate(docker.commands)
        if item[:3] == ("docker", "network", "rm")
    )
    assert host_remove < network_remove


@pytest.mark.asyncio
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
