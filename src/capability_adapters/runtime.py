# -*- coding: utf-8 -*-
"""任务容器内复用的命令与本地 stdio MCP 生命周期。"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress
from datetime import timedelta
import os
from pathlib import Path, PurePosixPath
import signal
import shutil
import subprocess
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from .models import CapabilityRuntimeManifest, RuntimeCommand


_SAFE_INHERITED_ENVIRONMENT = {
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONUTF8",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
}


class CapabilityProcessResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    returncode: int
    stdout: str
    stderr: str


def _resolve_program(
    root: Path,
    program: str,
    aliases: Mapping[str, str],
) -> str:
    if program in aliases:
        return aliases[program]
    normalized = PurePosixPath(program.replace("\\", "/"))
    if len(normalized.parts) > 1:
        raw_candidate = root / Path(*normalized.parts)
        if raw_candidate.is_symlink():
            raise ValueError("能力入口不得是符号链接")
        candidate = raw_candidate.resolve(strict=True)
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError("能力入口越过冻结目录")
        return str(candidate)
    executable = shutil.which(program)
    if executable is None:
        raise ValueError(f"任务镜像缺少运行时：{program}")
    return executable


def _resolve_cwd(root: Path, relative: str) -> Path:
    normalized = PurePosixPath(relative.replace("\\", "/"))
    candidate = (root / Path(*normalized.parts)).resolve(strict=True)
    if candidate != root and root not in candidate.parents:
        raise ValueError("能力工作目录越过冻结目录")
    if not candidate.is_dir():
        raise ValueError("能力工作目录不存在")
    return candidate


def _runtime_environment(command: RuntimeCommand) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _SAFE_INHERITED_ENVIRONMENT
    }
    environment.update(dict(command.environment))
    return environment


def _root_directory(path: str | Path) -> Path:
    original = Path(path)
    if original.is_symlink():
        raise ValueError("能力根目录不得是符号链接")
    root = original.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("能力根目录必须是实体目录")
    return root


class CommandCapabilityAdapter:
    """Python、Node 与 CLI 共用的直接 argv 执行接口。"""

    _MAX_OUTPUT_BYTES = 1024 * 1024

    def __init__(
        self,
        root: str | Path,
        manifest: CapabilityRuntimeManifest,
        *,
        runtime_aliases: Mapping[str, str] | None = None,
    ) -> None:
        if manifest.kind not in {"python", "node", "cli"}:
            raise ValueError("命令 Adapter 只接受 Python、Node 或 CLI 清单")
        self.root = _root_directory(root)
        self.manifest = manifest
        self._aliases = dict(runtime_aliases or {})
        self._active_process: asyncio.subprocess.Process | None = None
        self._active_task: asyncio.Task[CapabilityProcessResult] | None = None
        self._lock = asyncio.Lock()

    @property
    def active_pid(self) -> int | None:
        process = self._active_process
        return process.pid if process is not None else None

    async def prepare(self) -> "CommandCapabilityAdapter":
        assert self.manifest.entrypoint is not None
        _resolve_program(self.root, self.manifest.entrypoint.program, self._aliases)
        _resolve_cwd(self.root, self.manifest.entrypoint.working_directory)
        if self.manifest.healthcheck is not None:
            _resolve_program(
                self.root,
                self.manifest.healthcheck.program,
                self._aliases,
            )
        return self

    async def _terminate_process(self) -> None:
        process = self._active_process
        if process is None or process.returncode is not None:
            return
        if os.name == "nt":
            # Windows 没有 POSIX SIGTERM；taskkill /T 是不引入额外依赖的进程树硬回收门。
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            await process.wait()
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    async def _run(
        self,
        command: RuntimeCommand,
        extra_arguments: tuple[str, ...] = (),
    ) -> CapabilityProcessResult:
        async with self._lock:
            owner = asyncio.current_task()
            assert owner is not None
            self._active_task = owner
            executable = _resolve_program(
                self.root,
                command.program,
                self._aliases,
            )
            cwd = _resolve_cwd(self.root, command.working_directory)
            environment = _runtime_environment(command)
            try:
                process_options = (
                    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == "nt"
                    else {"start_new_session": True}
                )
                self._active_process = await asyncio.create_subprocess_exec(
                    executable,
                    *command.arguments,
                    *extra_arguments,
                    cwd=str(cwd),
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **process_options,
                )
                try:
                    stdout_task = asyncio.create_task(
                        self._read_limited(self._active_process.stdout)
                    )
                    stderr_task = asyncio.create_task(
                        self._read_limited(self._active_process.stderr)
                    )
                    stdout, stderr, _ = await asyncio.wait_for(
                        asyncio.gather(
                            stdout_task,
                            stderr_task,
                            self._active_process.wait(),
                        ),
                        timeout=command.timeout_seconds,
                    )
                except (asyncio.CancelledError, TimeoutError, RuntimeError):
                    await self._terminate_process()
                    raise
                result = CapabilityProcessResult(
                    returncode=self._active_process.returncode or 0,
                    stdout=stdout.decode("utf-8", errors="strict"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"能力进程退出码 {result.returncode}：{result.stderr[:500]}"
                    )
                return result
            finally:
                self._active_process = None
                self._active_task = None

    async def _read_limited(
        self,
        stream: asyncio.StreamReader | None,
    ) -> bytes:
        if stream is None:
            return b""
        chunks: list[bytes] = []
        size = 0
        while chunk := await stream.read(64 * 1024):
            size += len(chunk)
            if size > self._MAX_OUTPUT_BYTES:
                raise RuntimeError("能力输出超过 1 MiB 上限")
            chunks.append(chunk)
        return b"".join(chunks)

    async def health(self) -> CapabilityProcessResult:
        command = self.manifest.healthcheck or self.manifest.entrypoint
        assert command is not None
        return await self._run(command)

    async def invoke(
        self,
        arguments: tuple[str, ...] = (),
    ) -> CapabilityProcessResult:
        assert self.manifest.entrypoint is not None
        return await self._run(self.manifest.entrypoint, arguments)

    async def cancel(self) -> None:
        task = self._active_task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def cleanup(self) -> None:
        await self.cancel()
        await self._terminate_process()


class LocalMcpAdapter:
    """使用官方 MCP Python SDK，按任务复用一个 stdio Session。"""

    def __init__(
        self,
        root: str | Path,
        manifest: CapabilityRuntimeManifest,
        *,
        runtime_aliases: Mapping[str, str] | None = None,
    ) -> None:
        if manifest.kind != "mcp_local":
            raise ValueError("本地 MCP Adapter 只接受 mcp_local 清单")
        self.root = _root_directory(root)
        self.manifest = manifest
        self._aliases = dict(runtime_aliases or {})
        self._stack: AsyncExitStack | None = None
        self._session = None
        self._start_lock = asyncio.Lock()
        self._call_lock = asyncio.Lock()
        self._active_task: asyncio.Task[object] | None = None

    @property
    def session_identity(self) -> int | None:
        return id(self._session) if self._session is not None else None

    async def prepare(self) -> "LocalMcpAdapter":
        async with self._start_lock:
            if self._session is not None:
                return self
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            command = self.manifest.entrypoint
            assert command is not None
            executable = _resolve_program(self.root, command.program, self._aliases)
            cwd = _resolve_cwd(self.root, command.working_directory)
            stack = AsyncExitStack()
            try:
                streams = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=executable,
                            args=list(command.arguments),
                            cwd=cwd,
                            env=_runtime_environment(command),
                        )
                    )
                )
                session = await stack.enter_async_context(
                    ClientSession(
                        streams[0],
                        streams[1],
                        read_timeout_seconds=timedelta(
                            seconds=command.timeout_seconds
                        ),
                    )
                )
                await session.initialize()
                await session.send_ping()
                await session.list_tools()
            except Exception:
                await stack.aclose()
                raise
            self._stack = stack
            self._session = session
            return self

    async def health(self) -> bool:
        await self.prepare()
        await self._session.send_ping()
        await self._session.list_tools()
        return True

    async def list_tools(self) -> tuple[str, ...]:
        await self.prepare()
        listed = await self._session.list_tools()
        return tuple(sorted(item.name for item in listed.tools))

    async def invoke(self, tool_name: str, arguments: dict[str, object]) -> object:
        await self.prepare()
        async with self._call_lock:
            owner = asyncio.current_task()
            assert owner is not None
            self._active_task = owner
            try:
                result = await self._session.call_tool(tool_name, arguments)
                return result.model_dump(mode="json")
            finally:
                self._active_task = None

    async def cancel(self) -> None:
        task = self._active_task
        if task is None:
            return
        # 官方 SDK 会把 Python 任务取消映射为 MCP 请求取消；任务终止再由 cleanup 回收进程。
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def cleanup(self) -> None:
        await self.cancel()
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.aclose()
