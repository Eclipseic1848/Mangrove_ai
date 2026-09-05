# -*- coding: utf-8 -*-
"""Smokescreen + BuildKit + ORAS 的真实能力获取 Adapter。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from filelock import FileLock, Timeout as FileLockTimeout

from src.agentic_runtime.egress_policy import (
    EgressLease,
    EgressPolicy,
    SmokescreenEgressController,
)
from src.capability_catalog import OrasOciLayoutStore
from src.conversation_steering import AcquisitionBudget

from .models import (
    AcquisitionCandidate,
    AcquisitionRequest,
    PreparedCapability,
    ResolvedCandidate,
)


@dataclass(frozen=True, slots=True)
class DockerAcquisitionLease:
    acquisition_id: str
    workspace: Path
    egress: EgressLease


@dataclass(frozen=True, slots=True)
class DockerExecutionClaim:
    acquisition_id: str
    lock: FileLock
    cancel_marker: Path


async def _run_command(
    command: tuple[str, ...],
    *,
    cancel_marker: Path | None = None,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        communication = asyncio.create_task(process.communicate())
        while not communication.done():
            if cancel_marker is not None and cancel_marker.is_file():
                process.terminate()
                await process.wait()
                communication.cancel()
                raise asyncio.CancelledError
            await asyncio.sleep(0.05)
        stdout, stderr = await communication
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        raise
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


_RESOLVE_SCRIPT = r"""
import json, sys, urllib.request
chain = [sys.argv[1]]
class Recorder(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
request = urllib.request.Request(sys.argv[1], method="HEAD")
with urllib.request.build_opener(Recorder()).open(request, timeout=30) as response:
    print(json.dumps({"final_uri": response.geturl(), "redirect_chain": chain}))
""".strip()


_DOWNLOAD_SCRIPT = r"""
import hashlib, json, os, sys, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("redirect_not_allowed")
url, limit_text, output = sys.argv[1:4]
limit = int(limit_text)
total = 0
digest = hashlib.sha256()
temporary = output + ".partial"
try:
    with urllib.request.build_opener(NoRedirect()).open(url, timeout=60) as response:
        with open(temporary, "wb") as stream:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise RuntimeError("download_budget_exceeded")
                digest.update(chunk)
                stream.write(chunk)
    os.replace(temporary, output)
    print(json.dumps({"bytes": total, "digest": "sha256:" + digest.hexdigest()}))
finally:
    if os.path.exists(temporary):
        os.remove(temporary)
""".strip()


class DockerBuildkitAcquisitionEnvironment:
    """联网下载与离线构建分离；业务来源和业务 Secret 没有传入入口。"""

    _ARTIFACT_TYPE = "application/vnd.mangrove.capability.v1"

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        cache_root: str | Path,
        model_base_url: str,
        downloader_image: str,
        egress_controller: SmokescreenEgressController,
        artifact_store: OrasOciLayoutStore,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._cache_root = Path(cache_root).resolve()
        self._model_base_url = model_base_url
        self._downloader_image = downloader_image
        self._egress_controller = egress_controller
        self._artifact_store = artifact_store
        self._containers: dict[str, str] = {}
        self._operations: dict[str, asyncio.Task[object]] = {}
        self._cancel_markers: dict[str, Path] = {}

    def _artifact_name(self, candidate: AcquisitionCandidate) -> str:
        identity = hashlib.sha256(
            f"{candidate.source_uri}\0{candidate.version}".encode("utf-8")
        ).hexdigest()[:20]
        return f"cap-{identity}"

    def _execution_identity(self, acquisition_id: str) -> str:
        return hashlib.sha256(acquisition_id.encode("utf-8")).hexdigest()

    def _cancel_marker_for(self, acquisition_id: str) -> Path:
        return (
            self._cache_root
            / "cancellations"
            / self._execution_identity(acquisition_id)
        )

    async def claim_execution(
        self,
        request: AcquisitionRequest,
        cancel_event: asyncio.Event,
    ) -> DockerExecutionClaim:
        """同一 acquisition_id 只允许一个进程恢复或执行。"""

        identity = self._execution_identity(request.acquisition_id)
        path = self._cache_root / "executions" / f"{identity}.lock"
        cancel_marker = self._cancel_marker_for(request.acquisition_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        claim = FileLock(path, thread_local=False)
        while True:
            try:
                await asyncio.to_thread(claim.acquire, timeout=0)
                cancel_marker.parent.mkdir(parents=True, exist_ok=True)
                cancel_marker.unlink(missing_ok=True)
                self._cancel_markers[request.acquisition_id] = cancel_marker
                return DockerExecutionClaim(
                    acquisition_id=request.acquisition_id,
                    lock=claim,
                    cancel_marker=cancel_marker,
                )
            except FileLockTimeout:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                await asyncio.sleep(0.05)

    async def release_execution(self, claim: object) -> None:
        if not isinstance(claim, DockerExecutionClaim):
            raise TypeError("能力获取执行 Claim 类型不正确")
        claim.cancel_marker.unlink(missing_ok=True)
        self._cancel_markers.pop(claim.acquisition_id, None)
        if claim.lock.is_locked:
            claim.lock.release()

    def _workspace_for(self, acquisition_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", acquisition_id)[:80]
        workspace = (self._workspace_root / safe_id).resolve()
        if self._workspace_root not in workspace.parents:
            raise ValueError("能力获取工作目录越界")
        return workspace

    async def recover(self, request: AcquisitionRequest) -> None:
        """清理由同一 Acquisition 身份遗留的确定性资源。"""

        workspace = self._workspace_for(request.acquisition_id)
        policy = EgressPolicy.for_dependency_acquisition(
            model_base_url=self._model_base_url,
        )
        lease = self._egress_controller.lease_for(
            policy=policy,
            user_id=request.owner_id,
            task_id=request.acquisition_id,
            revision=1,
            run_id=request.acquisition_id,
            policy_dir=workspace / "policy",
        )
        for prefix in ("mangrove-acq-resolve-", "mangrove-acq-download-"):
            name = f"{prefix}{request.acquisition_id}"[:63]
            await _run_command(("docker", "rm", "-f", name))
        await self._egress_controller.stop(lease)
        if workspace.exists():
            shutil.rmtree(workspace)

    async def start(
        self,
        request: AcquisitionRequest,
        allowed_domains: tuple[str, ...],
    ) -> DockerAcquisitionLease:
        workspace = self._workspace_for(request.acquisition_id)
        workspace.mkdir(parents=True, exist_ok=False)
        policy = EgressPolicy.for_dependency_acquisition(
            model_base_url=self._model_base_url,
            additional_domains=allowed_domains,
        )
        if policy.mount_sources:
            raise RuntimeError("依赖获取策略不得挂载用户来源")
        try:
            egress = await self._egress_controller.start(
                policy=policy,
                user_id=request.owner_id,
                task_id=request.acquisition_id,
                revision=1,
                run_id=request.acquisition_id,
                policy_dir=workspace / "policy",
            )
        except Exception:
            shutil.rmtree(workspace)
            raise
        return DockerAcquisitionLease(
            acquisition_id=request.acquisition_id,
            workspace=workspace,
            egress=egress,
        )

    def _container_command(
        self,
        lease: DockerAcquisitionLease,
        *,
        name: str,
        script: str,
        args: tuple[str, ...],
        mount_workspace: bool,
    ) -> tuple[str, ...]:
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            lease.egress.network_name,
            "--label",
            "mangrove.agentic-runtime=true",
            "--env",
            f"HTTP_PROXY={lease.egress.proxy_url}",
            "--env",
            f"HTTPS_PROXY={lease.egress.proxy_url}",
            "--env",
            f"http_proxy={lease.egress.proxy_url}",
            "--env",
            f"https_proxy={lease.egress.proxy_url}",
        ]
        if mount_workspace:
            command.extend(
                (
                    "--mount",
                    f"type=bind,source={lease.workspace},target=/work",
                )
            )
        command.extend((self._downloader_image, "python", "-I", "-c", script, *args))
        return tuple(command)

    async def resolve(
        self,
        lease: DockerAcquisitionLease,
        candidate: AcquisitionCandidate,
    ) -> ResolvedCandidate:
        task = asyncio.current_task()
        assert task is not None
        self._operations[lease.acquisition_id] = task
        try:
            name = f"mangrove-acq-resolve-{lease.acquisition_id}"[:63]
            self._containers[lease.acquisition_id] = name
            code, stdout, stderr = await _run_command(
                self._container_command(
                    lease,
                    name=name,
                    script=_RESOLVE_SCRIPT,
                    args=(candidate.source_uri,),
                    mount_workspace=False,
                ),
                cancel_marker=self._cancel_markers.get(lease.acquisition_id),
            )
        finally:
            self._containers.pop(lease.acquisition_id, None)
            if self._operations.get(lease.acquisition_id) is task:
                self._operations.pop(lease.acquisition_id, None)
        if code != 0:
            raise RuntimeError(f"来源解析失败：{stderr.strip()[-500:]}")
        payload = json.loads(stdout.strip().splitlines()[-1])
        return ResolvedCandidate(
            candidate=candidate,
            final_uri=payload["final_uri"],
            redirect_chain=tuple(payload["redirect_chain"]),
        )

    async def lookup(
        self,
        resolved: ResolvedCandidate,
    ) -> PreparedCapability | None:
        expected = resolved.candidate.expected_sha256
        if expected is None:
            return None
        artifact_name = self._artifact_name(resolved.candidate)
        descriptor = self._artifact_store.lookup_file(
            artifact_name=artifact_name,
            version=resolved.candidate.version,
            source_digest=expected,
            artifact_type=self._ARTIFACT_TYPE,
        )
        if descriptor is None:
            return None
        return PreparedCapability(
            pack_id=artifact_name,
            version=resolved.candidate.version,
            digest=descriptor.digest,
            oci_reference=descriptor.reference,
            source_uri=resolved.candidate.source_uri,
            final_uri=resolved.final_uri,
            download_bytes=0,
            unpacked_bytes=0,
            reused=True,
        )

    async def _prepare_once(
        self,
        lease: DockerAcquisitionLease,
        resolved: ResolvedCandidate,
        budget: AcquisitionBudget,
        cancel_event: asyncio.Event,
    ) -> PreparedCapability:
        context = lease.workspace / "build-context"
        assembled = lease.workspace / "assembled"
        # 每次重试都从空的短期上下文开始；共享 BuildKit cache 独立保留。
        for path in (context, assembled):
            if path.exists():
                shutil.rmtree(path)
        context.mkdir(parents=True, exist_ok=False)
        output_path = context / "payload"
        name = f"mangrove-acq-download-{lease.acquisition_id}"[:63]
        self._containers[lease.acquisition_id] = name
        code, stdout, stderr = await _run_command(
            self._container_command(
                lease,
                name=name,
                script=_DOWNLOAD_SCRIPT,
                args=(
                    resolved.final_uri,
                    str(budget.max_download_bytes),
                    "/work/build-context/payload",
                ),
                mount_workspace=True,
            ),
            cancel_marker=self._cancel_markers.get(lease.acquisition_id),
        )
        self._containers.pop(lease.acquisition_id, None)
        if cancel_event.is_set():
            raise asyncio.CancelledError
        if code != 0:
            raise RuntimeError(f"能力下载失败：{stderr.strip()[-500:]}")
        download = json.loads(stdout.strip().splitlines()[-1])
        if (
            resolved.candidate.expected_sha256 is not None
            and download["digest"] != resolved.candidate.expected_sha256
        ):
            raise ValueError("下载内容 digest 与冻结来源不一致")
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY payload /capability/payload\n",
            encoding="utf-8",
        )
        cache_dir = self._cache_root / "buildkit"
        cache_dir.mkdir(parents=True, exist_ok=True)
        build_parts = [
            "docker",
            "buildx",
            "build",
            "--network",
            "none",
            "--progress",
            "plain",
            "--output",
            f"type=local,dest={assembled}",
        ]
        if (cache_dir / "index.json").is_file():
            build_parts.extend(("--cache-from", f"type=local,src={cache_dir}"))
        build_parts.extend(
            (
                "--cache-to",
                f"type=local,dest={cache_dir},mode=max",
                str(context),
            )
        )
        try:
            code, _stdout, stderr = await _run_command(
                tuple(build_parts),
                cancel_marker=self._cancel_markers.get(lease.acquisition_id),
            )
            if cancel_event.is_set():
                raise asyncio.CancelledError
            if code != 0:
                raise RuntimeError(f"BuildKit 离线构建失败：{stderr.strip()[-500:]}")
            built_payload = assembled / "capability" / "payload"
            if not built_payload.is_file():
                raise RuntimeError("BuildKit 未产出预期能力文件")
            artifact_name = self._artifact_name(resolved.candidate)
            descriptor = self._artifact_store.push_file(
                built_payload,
                artifact_name=artifact_name,
                version=resolved.candidate.version,
                artifact_type=self._ARTIFACT_TYPE,
            )
            return PreparedCapability(
                pack_id=artifact_name,
                version=resolved.candidate.version,
                digest=descriptor.digest,
                oci_reference=descriptor.reference,
                source_uri=resolved.candidate.source_uri,
                final_uri=resolved.final_uri,
                download_bytes=int(download["bytes"]),
                unpacked_bytes=built_payload.stat().st_size,
                reused=False,
            )
        finally:
            self._containers.pop(lease.acquisition_id, None)

    async def prepare(
        self,
        lease: DockerAcquisitionLease,
        resolved: ResolvedCandidate,
        budget: AcquisitionBudget,
        cancel_event: asyncio.Event,
    ) -> PreparedCapability:
        task = asyncio.current_task()
        assert task is not None
        self._operations[lease.acquisition_id] = task
        lock_key = hashlib.sha256(
            (
                f"{resolved.candidate.kind.value}\0"
                f"{resolved.candidate.source_uri}\0"
                f"{resolved.candidate.version}\0"
                f"{resolved.candidate.expected_sha256 or ''}"
            ).encode("utf-8")
        ).hexdigest()
        lock_path = self._cache_root / "singleflight" / f"{lock_key}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        cross_process_lock = FileLock(
            lock_path,
            timeout=budget.max_duration_seconds,
            thread_local=False,
        )
        try:
            while True:
                try:
                    await asyncio.to_thread(cross_process_lock.acquire, timeout=0)
                    break
                except FileLockTimeout:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    await asyncio.sleep(0.05)
            cached = await self.lookup(resolved)
            if cached is not None:
                return cached
            return await self._prepare_once(
                lease,
                resolved,
                budget,
                cancel_event,
            )
        finally:
            if cross_process_lock.is_locked:
                cross_process_lock.release()
            self._containers.pop(lease.acquisition_id, None)
            if self._operations.get(lease.acquisition_id) is task:
                self._operations.pop(lease.acquisition_id, None)

    async def cancel(self, acquisition_id: str) -> None:
        marker = self._cancel_marker_for(acquisition_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("cancelled", encoding="utf-8")
        operation = self._operations.get(acquisition_id)
        if operation is not None and operation is not asyncio.current_task():
            operation.cancel()
        names = {
            self._containers.get(acquisition_id),
            f"mangrove-acq-resolve-{acquisition_id}"[:63],
            f"mangrove-acq-download-{acquisition_id}"[:63],
        }
        for name in names:
            if name:
                await _run_command(("docker", "rm", "-f", name))

    async def cleanup(self, lease: DockerAcquisitionLease) -> None:
        workspace = lease.workspace.resolve()
        if self._workspace_root not in workspace.parents:
            raise RuntimeError("拒绝清理能力获取根目录之外的路径")
        await self._egress_controller.stop(lease.egress)
        # 撤权失败时保留清理身份和证据，供恢复重试。
        shutil.rmtree(workspace)
