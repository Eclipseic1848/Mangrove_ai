# -*- coding: utf-8 -*-
"""把一个任务的多个原生能力收口到单一无来源 Sidecar。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
from typing import Awaitable, Callable

from src.agentic_runtime.egress_policy import DockerCommandResult
from src.capability_adapters import load_runtime_manifests

from .models import CapabilityHostLease, CapabilityHostRequest


DockerCommandRunner = Callable[[tuple[str, ...]], Awaitable[DockerCommandResult]]
_NATIVE_KINDS = {"python", "node", "cli", "mcp_local"}


async def _run_docker(command: tuple[str, ...]) -> DockerCommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    return DockerCommandResult(
        returncode=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


class CapabilityHost:
    """调用者只管理 Lease；Docker、Token、清理和健康门留在实现内部。"""

    def __init__(
        self,
        *,
        image: str,
        execution_root: str | Path,
        command_runner: DockerCommandRunner | None = None,
        docker_timeout_seconds: float = 30.0,
    ) -> None:
        self.image = image
        self.execution_root = Path(execution_root).resolve()
        self.execution_root.mkdir(parents=True, exist_ok=True)
        self._run = command_runner or _run_docker
        self._docker_timeout_seconds = docker_timeout_seconds

    async def _docker(self, command: tuple[str, ...]) -> DockerCommandResult:
        try:
            return await asyncio.wait_for(
                self._run(command),
                timeout=self._docker_timeout_seconds,
            )
        except TimeoutError as error:
            raise RuntimeError(
                f"Docker 操作超时：{' '.join(command[:3])}"
            ) from error

    async def start(self, request: CapabilityHostRequest) -> CapabilityHostLease:
        mounted = tuple(
            item
            for item in load_runtime_manifests(request.capability_dirs)
            if item.manifest.kind in _NATIVE_KINDS
        )
        if not mounted:
            raise ValueError("任务没有可由 Sidecar 执行的本地原生能力")
        identity = hashlib.sha256(
            f"{request.user_id}:{request.task_id}:{request.revision}:{request.run_id}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        safe_task = re.sub(r"[^a-z0-9-]", "-", request.task_id.casefold())[-16:]
        container_name = f"mangrove-cap-host-{safe_task}-{identity}"[:63]
        runtime_dir = (self.execution_root / identity).resolve()
        if self.execution_root not in runtime_dir.parents:
            raise RuntimeError("Capability Host 运行目录越界")
        runtime_dir.mkdir(parents=True, exist_ok=False)
        token = secrets.token_urlsafe(32)
        config = {
            "capabilities": [
                {
                    "root": f"/capabilities/{item.mount_index}",
                    "manifest": item.manifest.model_dump(mode="json"),
                }
                for item in mounted
            ]
        }
        (runtime_dir / "capability-host.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        token_file = runtime_dir / "capability-host.env"
        token_file.write_text(
            f"MANGROVE_CAPABILITY_TOKEN={token}\n",
            encoding="utf-8",
        )
        shutil.copyfile(
            Path(__file__).with_name("capability_host_server.mjs"),
            runtime_dir / "capability-host-server.mjs",
        )
        command: list[str] = [
            "docker", "run", "-d", "--name", container_name,
            "--network", request.network_name,
            "--label", "mangrove.agentic-runtime=true",
            "--label", "mangrove.capability-host=true",
            "--init", "--stop-timeout", "3",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "2g", "--cpus", "2",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--env-file", str(token_file),
            "--mount",
            f"type=bind,source={runtime_dir},target=/opt/mangrove-host,readonly",
        ]
        for item in mounted:
            command.extend(
                (
                    "--mount",
                    f"type=bind,source={item.root},target=/capabilities/{item.mount_index},readonly",
                )
            )
        command.extend(
            (self.image, "node", "/opt/mangrove-host/capability-host-server.mjs")
        )
        lease = CapabilityHostLease(
            container_name=container_name,
            relay_url=f"http://{container_name}:8765",
            relay_token=token,
            capability_names=tuple(item.manifest.name for item in mounted),
            capability_kinds=tuple(
                (item.manifest.name, item.manifest.kind) for item in mounted
            ),
            runtime_dir=runtime_dir,
        )
        try:
            # identity 绑定 Owner/Task/revision/run；恢复同一 Run 时只替换该确定性 Host。
            await self._docker(("docker", "rm", "-f", container_name))
            result = await self._docker(tuple(command))
            if result.returncode != 0:
                raise RuntimeError(
                    "无法启动 Capability Host：" + result.stderr.strip()[:300]
                )
            await self._wait_until_ready(lease)
            return lease
        except Exception as error:
            logs: DockerCommandResult | None = None
            try:
                logs = await self._docker(("docker", "logs", container_name))
            except Exception:
                pass
            try:
                await self.stop(lease)
            except Exception:
                pass
            detail = (
                (logs.stdout + logs.stderr).strip()[-1500:]
                if logs is not None
                else ""
            )
            if detail:
                raise RuntimeError(f"{error}；Host 日志：{detail}") from error
            raise

    async def _wait_until_ready(self, lease: CapabilityHostLease) -> None:
        probe = (
            "docker", "exec", lease.container_name, "node", "-e",
            "fetch('http://127.0.0.1:8765/health',{headers:{authorization:'Bearer ' + "
            "process.env.MANGROVE_CAPABILITY_TOKEN}}).then(r=>{if(!r.ok)process.exit(1)})",
        )
        for _ in range(80):
            result = await self._docker(probe)
            if result.returncode == 0:
                return
            await asyncio.sleep(0.25)
        raise RuntimeError("Capability Host 健康检查超时")

    async def stop(self, lease: CapabilityHostLease) -> None:
        stop_error = ""
        try:
            stopped = await self._docker(
                ("docker", "stop", "--time", "3", lease.container_name)
            )
            if stopped.returncode != 0:
                stop_error = stopped.stderr.strip()
        except Exception as error:
            stop_error = str(error)
        try:
            removed = await self._docker(
                ("docker", "rm", "-f", lease.container_name)
            )
        except Exception as error:
            raise RuntimeError(
                f"无法清理 Capability Host：{error}；停止结果：{stop_error}"
            ) from error
        removal_error = removed.stderr.strip()
        if removed.returncode != 0 and "no such container" not in removal_error.casefold():
            raise RuntimeError(
                f"无法清理 Capability Host：{removal_error or 'docker rm 失败'}；"
                f"停止结果：{stop_error}"
            )
        runtime_dir = lease.runtime_dir.resolve()
        if self.execution_root in runtime_dir.parents:
            # 恢复/取消可能重复清理同一确定性 Lease；目录已不存在仍属于幂等成功。
            try:
                shutil.rmtree(runtime_dir)
            except FileNotFoundError:
                pass

    async def cancel(self, lease: CapabilityHostLease) -> None:
        await self.stop(lease)
