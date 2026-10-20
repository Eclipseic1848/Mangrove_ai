"""迁移旧 Run 只在当前服务器资源已明确不存在时完成停止。"""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from filelock import FileLock, Timeout as FileLockTimeout
import pytest

from src.agentic_runtime import pi_runtime as runtime_module
from src.agentic_runtime.egress_policy import DockerCommandResult
from src.agentic_runtime.models import RuntimeStatus, RuntimeTaskConfig, RuntimeVersion
from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.capability_host import CapabilityHost
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def migrated_run(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "runtime.db")
    seed_execution_owner(database, "owner-a")
    repository = AgenticRuntimeRepository(database)
    key = ("owner-a", "workspace-legacy", 1)
    run_id = "pi_run_1234567890abcdef"
    base = tmp_path / "data" / "semantic-executions"
    relative = Path("agentic-vnext") / sha256(b"owner-a").hexdigest()[:16] / key[1] / "r1" / run_id
    root = base / relative
    (root / "trace" / "egress-business").mkdir(parents=True)
    (root / "trace" / "egress-business-resume-0123abcd").mkdir()
    (root / "output").mkdir()
    result_file = root / "output" / "result.json"
    result_file.write_text('{"original":true}', encoding="utf-8")
    repository.register(RuntimeTaskConfig(user_id=key[0], task_id=key[1], revision=key[2],
                                          runtime_version=RuntimeVersion.PI, run_id=run_id))
    name = PiRuntime._container_name(key[1], key[2], run_id)
    legacy_root = "C:/retired-host/data/semantic-executions/" + relative.as_posix()
    repository.update(*key, status=RuntimeStatus.FAILED, workspace_root=legacy_root, container_name=name)
    revoked = []

    def revoke(*args, **kwargs):
        revoked.append((args, kwargs))

    host = CapabilityHost(image="synthetic-host", execution_root=base / "capability-hosts")
    runtime = PiRuntime(state_store=repository, execution_root=base, capability_host=host,
                        connection_broker=SimpleNamespace(revoke_revision_grants=revoke),
                        document_tool_broker=SimpleNamespace(revoke_revision_grants=revoke),
                        configure_as_default_document_broker=False)
    commands = []

    async def docker(command):
        commands.append(command)
        # Docker 探针期间必须仍持有执行锁，不允许另一个执行者开始创建资源。
        with pytest.raises(FileLockTimeout):
            FileLock(str(runtime._lifecycle_dir(key) / "execution.lock")).acquire(timeout=0)
        if command[2] == "inspect":
            return DockerCommandResult(1, "", f"No such {command[1]}")
        assert command[2] == "ls"
        return DockerCommandResult(0, "", "")

    monkeypatch.setattr(runtime_module, "_run_docker", docker)
    return SimpleNamespace(runtime=runtime, repository=repository, key=key, run_id=run_id,
                           root=root, relative=relative, result_file=result_file, name=name,
                           commands=commands, revoked=revoked, docker=docker)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_host", [False, True])
async def test_migrated_terminal_run_is_confirmed_without_deleting_resources(migrated_run, configured_host):
    case = migrated_run
    if not configured_host:
        case.runtime._capability_host = None
    await case.runtime.cancel(*case.key)
    assert (case.runtime._lifecycle_dir(case.key) / "cleaned").exists()
    assert case.result_file.read_text(encoding="utf-8") == '{"original":true}'
    assert case.repository.get(*case.key)["status"] is RuntimeStatus.FAILED
    assert len(case.revoked) == 2
    assert {command[1] for command in case.commands} == {"container", "network"}
    inspected = {command[-1] for command in case.commands if command[2] == "inspect"}
    assert case.name in inspected
    assert sum(name.startswith("mangrove-pi-proxy-") for name in inspected) == 2
    assert sum(name.startswith("mangrove-pi-net-") for name in inspected) == 2
    assert sum(name.startswith("mangrove-cap-host-") for name in inspected) == 1
    assert any(command[:3] == ("docker", "container", "ls") and "--all" in command for command in case.commands)
    assert any(command[:3] == ("docker", "network", "ls") for command in case.commands)
    before = list(case.commands)
    await case.runtime.cancel(*case.key)
    assert case.commands == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RuntimeStatus.PREPARING, RuntimeStatus.RUNNING, RuntimeStatus.NEEDS_INPUT])
async def test_nonterminal_run_stays_unconfirmed(migrated_run, status):
    case = migrated_run
    case.repository.update(*case.key, status=status)
    with pytest.raises(PiRuntimeError, match="缺少可验证资源账本"):
        await case.runtime.cancel(*case.key)
    assert case.commands == []
    assert not (case.runtime._lifecycle_dir(case.key) / "cleaned").exists()
    assert len(case.revoked) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["native", "relative", "wrong_owner", "wrong_task", "wrong_revision", "wrong_run", "wrong_container", "traversal"])
async def test_unsafe_or_unmigrated_identity_stays_unconfirmed(migrated_run, failure):
    case = migrated_run
    raw = case.repository.get(*case.key)["workspace_root"]
    if failure == "native":
        raw = str(case.root)
    elif failure == "relative":
        raw = case.relative.as_posix()
    elif failure == "wrong_container":
        case.repository.update(*case.key, container_name="unrelated-container")
    elif failure == "traversal":
        raw = raw.replace("/agentic-vnext/", "/../agentic-vnext/")
    else:
        original, replacement = {
            "wrong_owner": (sha256(b"owner-a").hexdigest()[:16], sha256(b"owner-b").hexdigest()[:16]),
            "wrong_task": (case.key[1], "other-task"),
            "wrong_revision": ("/r1/", "/r2/"),
            "wrong_run": (case.run_id, "pi_run_ffffffffffffffff"),
        }[failure]
        raw = raw.replace(original, replacement)
    case.repository.update(*case.key, workspace_root=raw)
    with pytest.raises(PiRuntimeError, match="缺少可验证资源账本"):
        await case.runtime.cancel(*case.key)
    assert case.commands == []
    assert not (case.runtime._lifecycle_dir(case.key) / "cleaned").exists()
    assert case.result_file.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["main", "business", "resume", "host", "journal"])
async def test_pending_creation_blocks_legacy_confirmation(migrated_run, marker):
    case = migrated_run
    host_directory = case.runtime._capability_host.cleanup_lease(*case.key, case.run_id).runtime_dir
    markers = {
        "main": case.root / "trace" / ".creating-main",
        "business": case.root / "trace" / "egress-business" / ".creating",
        "resume": case.root / "trace" / "egress-business-resume-0123abcd" / ".creating",
        "host": host_directory / ".creating",
        "journal": case.runtime._lifecycle_dir(case.key) / "resources.tmp",
    }
    marker_path = markers[marker]
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("creation_pending", encoding="utf-8")
    with pytest.raises(PiRuntimeError, match="缺少可验证资源账本"):
        await case.runtime.cancel(*case.key)
    assert case.commands == []
    assert marker_path.exists()
    assert not (case.runtime._lifecycle_dir(case.key) / "cleaned").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["daemon", "container", "network", "host", "collision", "renamed_container", "renamed_network"])
async def test_resource_presence_or_unknown_docker_blocks_confirmation(migrated_run, monkeypatch, failure):
    case = migrated_run

    async def docker(command):
        case.commands.append(command)
        if failure == "daemon":
            return DockerCommandResult(1, "", "synthetic daemon unavailable")
        if command[2] == "inspect":
            name = command[-1]
            found = (failure == "container" and name == case.name
                     or failure == "network" and command[1] == "network"
                     or failure == "host" and name.startswith("mangrove-cap-host-"))
            if found or failure == "collision" and name == case.name:
                return DockerCommandResult(0, "" if failure == "collision" else "a" * 64, "")
            return DockerCommandResult(1, "", f"No such {command[1]}")
        assert command[2] == "ls"
        found = failure == "renamed_container" and command[1] == "container" or failure == "renamed_network" and command[1] == "network"
        return DockerCommandResult(0, "unexpected-owned-resource\n" if found else "", "")

    monkeypatch.setattr(runtime_module, "_run_docker", docker)
    with pytest.raises(PiRuntimeError, match="缺少可验证资源账本"):
        await case.runtime.cancel(*case.key)
    assert not (case.runtime._lifecycle_dir(case.key) / "cleaned").exists()
    assert case.result_file.exists()
    assert len(case.revoked) == 2
    assert all(command[2] in {"inspect", "ls"} for command in case.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["trace", "ancestor", "host"])
async def test_redirected_resource_paths_stay_unconfirmed(migrated_run, monkeypatch, location):
    case = migrated_run
    if location == "host":
        path = case.runtime._capability_host.cleanup_lease(*case.key, case.run_id).runtime_dir
    elif location == "ancestor":
        path = case.root.parent
    else:
        path = case.root / "trace" / "redirected"
    if path.exists():
        moved = path.with_name(path.name + "-original")
        path.rename(moved)
    else:
        moved = path.with_name(path.name + "-original")
        moved.mkdir(parents=True)
    try:
        path.symlink_to(moved, target_is_directory=True)
    except OSError:
        # Windows 未授予建链接权限时模拟路径重定向标志，仍验证拒绝门不会漏掉该路径。
        moved.rename(path)
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda item: item == path or original_is_symlink(item))
    with pytest.raises(PiRuntimeError, match="缺少可验证资源账本"):
        await case.runtime.cancel(*case.key)
    assert case.commands == []
    assert not (case.runtime._lifecycle_dir(case.key) / "cleaned").exists()
