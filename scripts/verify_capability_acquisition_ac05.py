# -*- coding: utf-8 -*-
"""AC-05：真实 Smokescreen、BuildKit、ORAS 冷热获取探针。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.agentic_runtime.egress_policy import SmokescreenEgressController
from src.capability_acquisition import (
    AcquisitionCandidate,
    AcquisitionRequest,
    AcquisitionSourceKind,
    CapabilityAcquisition,
    DockerBuildkitAcquisitionEnvironment,
    InMemoryAcquisitionRepository,
    SourcePolicy,
)
from src.capability_catalog import OrasOciLayoutStore
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountResolver,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.conversation_steering import (
    AcquisitionBudget,
    AcquisitionStatus,
    CapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _find_oras() -> str:
    executable = shutil.which("oras")
    if executable:
        return executable
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        matches = list(
            Path(local_app_data).glob(
                "Microsoft/WinGet/Packages/ORASProject.ORAS*/oras.exe"
            )
        )
        if matches:
            return str(matches[0])
    raise RuntimeError("未找到 ORAS 可执行文件")


class FailOnceDockerEnvironment(DockerBuildkitAcquisitionEnvironment):
    """仅用于证明真实 Adapter 的第二次尝试会重建干净上下文。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.attempts = 0

    async def prepare(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts == 1:
            lease = args[0]
            stale = lease.workspace / "build-context"
            stale.mkdir(parents=True, exist_ok=True)
            (stale / "partial").write_text("stale", encoding="utf-8")
            raise RuntimeError("synthetic first-attempt failure")
        return await super().prepare(*args, **kwargs)


class SlowDownloaderEnvironment(DockerBuildkitAcquisitionEnvironment):
    """把真实下载容器短暂阻塞，以验证外部取消会终止运行中容器。"""

    def _container_command(self, lease, *, name, script, args, mount_workspace):
        if "download" in name:
            script = "import time; time.sleep(30)\n" + script
        return super()._container_command(
            lease,
            name=name,
            script=script,
            args=args,
            mount_workspace=mount_workspace,
        )


def _service(
    root: Path,
    oras: str,
    *,
    environment_type=DockerBuildkitAcquisitionEnvironment,
    repository=None,
) -> CapabilityAcquisition:
    environment = environment_type(
        workspace_root=root / "active",
        cache_root=root / "cache",
        model_base_url="http://192.168.1.20:6012/v1",
        downloader_image="python:3.13-slim-bookworm",
        egress_controller=SmokescreenEgressController(
            image="mangrove/smokescreen:da4840c9"
        ),
        artifact_store=OrasOciLayoutStore(
            root / "oci",
            oras_executable=oras,
            layout_id="ac05-probe",
        ),
    )
    return CapabilityAcquisition(
        repository or InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )


def _request(
    acquisition_id: str,
    *,
    expected_sha256: str = (
        "sha256:946d195a0d259cbba61165e88e65941f16e9b36ea6ddb97f00452bae8b1287d3"
    ),
    max_retries_per_source: int = 0,
) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_id=acquisition_id,
        owner_id="ac05-probe-user",
        need_summary="获取固定 idna wheel 作为隔离构建探针",
        candidates=(
            AcquisitionCandidate(
                candidate_id="idna-wheel",
                kind=AcquisitionSourceKind.PYPI,
                source_uri=(
                    "https://files.pythonhosted.org/packages/76/c6/"
                    "c88e154df9c4e1a2a66ccf0005a88dfb2650c1dffb6f5ce603dfbd452ce3/"
                    "idna-3.10-py3-none-any.whl"
                ),
                version="3.10",
                expected_sha256=expected_sha256,
            ),
        ),
        budget=AcquisitionBudget(
            max_duration_seconds=120,
            max_download_bytes=100_000,
            max_unpacked_bytes=100_000,
            max_candidates=1,
            max_retries_per_source=max_retries_per_source,
            max_concurrency=1,
        ),
    )


def _docker_names(command: tuple[str, ...]) -> tuple[str, ...]:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(line for line in completed.stdout.splitlines() if line)


async def _main() -> None:
    oras = _find_oras()
    with tempfile.TemporaryDirectory(prefix="mangrove-ac05-probe-") as directory:
        root = Path(directory)
        first_service = _service(root, oras)
        second_service = _service(root, oras)
        cold_events: list[object] = []
        concurrent_events: list[object] = []
        cold, concurrent = await asyncio.gather(
            first_service.acquire(
                _request("ac05-probe-cold"),
                cold_events.append,
            ),
            second_service.acquire(
                _request("ac05-probe-concurrent"),
                concurrent_events.append,
            ),
        )
        # 重建 Module 和 ORAS Adapter，证明热复用不依赖进程内缓存。
        hot_events = []
        hot = await _service(root, oras).acquire(
            _request("ac05-probe-hot"),
            hot_events.append,
        )
        tampered = await _service(root, oras).acquire(
            _request(
                "ac05-probe-tampered",
                expected_sha256="sha256:" + "0" * 64,
            ),
            lambda _event: None,
        )
        cancel_root = root / "cancel"
        cancel_repository = InMemoryAcquisitionRepository()
        cancel_service = _service(
            cancel_root,
            oras,
            environment_type=SlowDownloaderEnvironment,
            repository=cancel_repository,
        )
        cross_instance_canceller = _service(
            cancel_root,
            oras,
            repository=cancel_repository,
        )
        cancel_task: asyncio.Task[None] | None = None

        async def cancel_running_downloader() -> None:
            for _attempt in range(100):
                names = _docker_names(
                    ("docker", "ps", "--format", "{{.Names}}")
                )
                if any("mangrove-acq-download-ac05-probe-cancel" in name for name in names):
                    await cross_instance_canceller.cancel(
                        "ac05-probe-cancel",
                        "ac05-probe-user",
                    )
                    return
                await asyncio.sleep(0.05)
            raise AssertionError("未观察到运行中的下载容器")

        async def cancel_on_build(event) -> None:
            nonlocal cancel_task
            if event.status is AcquisitionStatus.BUILDING:
                cancel_task = asyncio.create_task(cancel_running_downloader())

        cancelled = await cancel_service.acquire(
            _request("ac05-probe-cancel"),
            cancel_on_build,
        )
        if cancel_task is not None:
            await cancel_task
        retry_root = root / "retry"
        retried = await _service(
            retry_root,
            oras,
            environment_type=FailOnceDockerEnvironment,
        ).acquire(
            _request("ac05-probe-retry", max_retries_per_source=1),
            lambda _event: None,
        )
        recovery_root = root / "recovery"
        recovery_environment = DockerBuildkitAcquisitionEnvironment(
            workspace_root=recovery_root / "active",
            cache_root=recovery_root / "cache",
            model_base_url="http://192.168.1.20:6012/v1",
            downloader_image="python:3.13-slim-bookworm",
            egress_controller=SmokescreenEgressController(
                image="mangrove/smokescreen:da4840c9"
            ),
            artifact_store=OrasOciLayoutStore(
                recovery_root / "oci",
                oras_executable=oras,
                layout_id="ac05-recovery",
            ),
        )
        recovery_request = _request("ac05-probe-recovery")
        recovery_lease = await recovery_environment.start(
            recovery_request,
            ("files.pythonhosted.org",),
        )
        orphan_name = "mangrove-acq-download-ac05-probe-recovery"
        subprocess.run(
            (
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                orphan_name,
                "--network",
                recovery_lease.egress.network_name,
                "python:3.13-slim-bookworm",
                "python",
                "-c",
                "import time; time.sleep(60)",
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        await recovery_environment.recover(recovery_request)
        if cold.status is not AcquisitionStatus.READY:
            raise AssertionError("首个并发获取未形成 READY 制品")
        if hot.status is not AcquisitionStatus.READY or not hot.reused:
            raise AssertionError("重启后的热获取未复用 OCI digest")
        if concurrent.status is not AcquisitionStatus.READY:
            raise AssertionError("并发获取未就绪")
        if sum(result.reused is False for result in (cold, concurrent)) != 1:
            raise AssertionError("并发冷获取重复下载或构建")
        if len({cold.digest, concurrent.digest, hot.digest}) != 1:
            raise AssertionError("并发和冷热获取 digest 不一致")
        fresh = cold if not cold.reused else concurrent
        assert fresh.pack_ref is not None and fresh.digest is not None
        pack_id = fresh.pack_ref.split("@", 1)[0]
        catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
        actor = CatalogActor(owner_id="ac05-probe-user", role="user")
        catalog.register_pack(
            actor,
            CapabilityPack(
                pack_id=pack_id,
                version="3.10",
                digest=fresh.digest,
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                owner_id=actor.owner_id,
            ),
        )
        catalog.freeze_selection(
            actor,
            task_id="ac05-business-mount",
            revision=1,
            pack_refs=(
                CapabilityPackRef(
                    pack_id=pack_id,
                    version="3.10",
                    digest=fresh.digest,
                ),
            ),
        )
        mounts = CapabilityMountResolver(
            catalog,
            OrasOciLayoutStore(
                root / "oci",
                oras_executable=oras,
                layout_id="ac05-probe",
            ),
            root / "mounts",
        ).resolve_for_owner(
            actor.owner_id,
            "ac05-business-mount",
            1,
        )
        if len(mounts) != 1 or not (mounts[0] / "payload").is_file():
            raise AssertionError("冻结 TaskRevision 未物化为能力目录")
        if tampered.status is not AcquisitionStatus.FAILED:
            raise AssertionError("错误 digest 未失败关闭")
        if cancelled.status is not AcquisitionStatus.CANCELLED:
            raise AssertionError("真实获取取消未失败关闭")
        if retried.status is not AcquisitionStatus.READY:
            raise AssertionError("真实 Adapter 重试未恢复")
        if recovery_lease.workspace.exists():
            raise AssertionError("真实崩溃恢复未删除遗留工作目录")
        if orphan_name in _docker_names(
            ("docker", "ps", "-a", "--format", "{{.Names}}")
        ):
            raise AssertionError("真实崩溃恢复未删除遗留下载容器")
        if any((root / "active").glob("*")):
            raise AssertionError("获取工作目录存在残留")
        if any((cancel_root / "active").glob("*")):
            raise AssertionError("取消后的获取工作目录存在残留")
        if any((retry_root / "active").glob("*")):
            raise AssertionError("重试后的获取工作目录存在残留")
        containers = _docker_names(
            ("docker", "ps", "-a", "--format", "{{.Names}}")
        )
        networks = _docker_names(
            ("docker", "network", "ls", "--format", "{{.Name}}")
        )
        if any("ac05-probe" in name for name in (*containers, *networks)):
            raise AssertionError("获取容器或网络存在残留")
        print(
            json.dumps(
                {
                    "cold": cold.model_dump(mode="json"),
                    "hot": hot.model_dump(mode="json"),
                    "concurrent": concurrent.model_dump(mode="json"),
                    "tampered": tampered.model_dump(mode="json"),
                    "cancelled": cancelled.model_dump(mode="json"),
                    "retried": retried.model_dump(mode="json"),
                    "cold_statuses": [event.status.value for event in cold_events],
                    "hot_statuses": [event.status.value for event in hot_events],
                    "buildkit_cache": (
                        root / "cache" / "buildkit" / "index.json"
                    ).is_file(),
                    "cross_instance_singleflight": True,
                    "cancelled_running_downloader": True,
                    "recovered_orphan_container": True,
                    "business_mount_digest": fresh.digest,
                    "residual_resources": 0,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(_main())
