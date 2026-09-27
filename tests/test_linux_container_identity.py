"""服务账号与容器一致，避免 Linux 受限文件不可读或产物归 root。"""
from types import SimpleNamespace

import pytest

from src.agentic_runtime import egress_policy, pi_runtime
from src.services import office_preview
from src.utils import docker_user


@pytest.mark.parametrize("platform", ["posix", "nt", "root"])
@pytest.mark.asyncio
async def test_capability_host_reads_service_owned_config(tmp_path, monkeypatch, platform):
    from src.capability_host import CapabilityHost, CapabilityHostRequest
    from tests.test_capability_host import RecordingDocker, _native_pack

    monkeypatch.setattr(docker_user, "os", _identity(platform))
    docker = RecordingDocker()
    host = CapabilityHost(image="synthetic", execution_root=tmp_path / "host", command_runner=docker)
    lease = await host.start(CapabilityHostRequest(
        user_id="owner", task_id="task", revision=1, run_id="run", network_name="network",
        capability_dirs=(_native_pack(tmp_path / "pack", "synthetic"),),
    ))
    command = next(item for item in docker.commands if item[:3] == ("docker", "run", "-d"))
    _assert_identity(command, platform)
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in command and lease.relay_token not in command
    await host.stop(lease)


def _identity(platform):
    return SimpleNamespace(
        name="posix" if platform == "root" else platform,
        geteuid=lambda: 0 if platform == "root" else 1001, getegid=lambda: 1002,
    )


def _assert_identity(command, platform):
    if platform == "posix":
        assert command[command.index("--user") + 1] == "1001:1002"
    else:
        assert "--user" not in command


@pytest.mark.parametrize("platform", ["posix", "nt", "root"])
def test_preview_can_read_service_owned_input(tmp_path, monkeypatch, platform):
    source = tmp_path / "input.docx"
    source.write_bytes(b"synthetic")
    calls = []
    monkeypatch.setattr(docker_user, "os", _identity(platform))
    monkeypatch.setattr(office_preview.shutil, "which", lambda _: "docker")

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"%PDF-synthetic")

    monkeypatch.setattr(office_preview.subprocess, "run", run)
    assert office_preview.office_preview(source, ".docx") == b"%PDF-synthetic"
    _assert_identity(calls[0], platform)
    assert "--network=none" in calls[0] and "--read-only" in calls[0]
    assert "--cap-drop=ALL" in calls[0]


@pytest.mark.parametrize("platform", ["posix", "nt", "root"])
@pytest.mark.asyncio
async def test_egress_can_read_service_owned_policy(tmp_path, monkeypatch, platform):
    calls = []
    monkeypatch.setattr(docker_user, "os", _identity(platform))

    async def run(command):
        calls.append(command)
        return egress_policy.DockerCommandResult(0, "", "")

    controller = egress_policy.SmokescreenEgressController(image="synthetic", command_runner=run)
    await controller.start(
        policy=egress_policy.EgressPolicy.for_business_execution(model_base_url="http://192.168.1.20:6012/v1"),
        user_id="owner", task_id="task", revision=1, run_id="run",
        policy_dir=tmp_path / "policy",
    )
    command = next(item for item in calls if item[:3] == ("docker", "run", "-d"))
    _assert_identity(command, platform)
    assert command[command.index("--mount") + 1].endswith(",readonly")


@pytest.mark.parametrize("platform", ["posix", "nt", "root"])
def test_pi_writes_as_service_and_can_reach_config(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(docker_user, "os", _identity(platform))
    command = pi_runtime.build_docker_command(
        image="synthetic", container_name="synthetic", input_dir=tmp_path / "input",
        work_dir=tmp_path / "work", output_dir=tmp_path / "output",
        session_dir=tmp_path / "session", config_dir=tmp_path / "config",
        model="synthetic", memory="512m", cpus=1,
    )
    _assert_identity(command, platform)
    if platform == "posix":
        assert "HOME=/workspace/work" in command
        assert "PI_CODING_AGENT_DIR=/workspace/config" in command
        assert command[command.index("--append-system-prompt") + 1] == "/workspace/config/mangrove-system.md"
        assert all("/root/" not in arg for arg in command)
    else:
        assert command[command.index("--append-system-prompt") + 1] == "/root/.pi/agent/mangrove-system.md"
