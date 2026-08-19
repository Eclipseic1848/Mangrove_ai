# -*- coding: utf-8 -*-
"""AC07-02 验证运行的生产执行器与可恢复轮询器。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Protocol
import uuid

from src.agentic_runtime.document_retrieval import DocumentRetrievalModule
from src.agentic_runtime.document_tools import DocumentToolBroker
from src.agentic_runtime.models import RuntimeStatus, VerificationStatus
from src.agentic_runtime.pi_runtime import PiRuntime
from src.capability_adapters import load_runtime_manifests
from src.capability_catalog import CatalogActor
from src.capability_host import CapabilityHost, CapabilityHostLease, CapabilityHostRequest
from src.config.settings import settings
from src.model_connections import get_default_broker

from .models import (
    CapabilityGovernanceTarget,
    CapabilitySupplyChainEvidence,
    CapabilityValidationRun,
    ValidationEvidence,
    ValidationRunStatus,
    ValidationStep,
    ValidationStepStatus,
)
from .service import CapabilityGovernance, CapabilityValidationExecutor
from .task_replay import ValidationTaskResolver


logger = logging.getLogger(__name__)


class SupplyChainEvidenceCollector(Protocol):
    def requires_collection(
        self,
        target: CapabilityGovernanceTarget,
    ) -> bool: ...

    def collect(
        self,
        target: CapabilityGovernanceTarget,
        subject_root: str | Path,
    ) -> CapabilitySupplyChainEvidence: ...


def _hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _target_mounts(
    run: CapabilityValidationRun,
    mounts: tuple,
) -> tuple[Path, ...]:
    """验证只能装载目标 digest，不能让同 Revision 的其他能力替代它完成任务。"""

    selected: list[Path] = []
    for raw_path in mounts:
        path = Path(raw_path).resolve()
        marker = path / ".mangrove-capability-digest"
        if (
            marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == run.target.digest
        ):
            selected.append(path)
    if len(selected) != 1:
        raise RuntimeError("无法把验证目标解析为唯一的精确 digest 挂载")
    return tuple(selected)


class PiTaskReplayRunner:
    """在独立临时状态域中重新运行冻结任务，并要求新候选再次通过 Verifier。"""

    def __init__(
        self,
        *,
        task_resolver: ValidationTaskResolver,
        capability_mounts: Callable[[str, str, int], tuple],
        execution_root: str | Path,
        cancel_requested: Callable[[], bool],
        cleanup_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        grant_revoker: Callable[[str, str, int, str], object] | None = None,
        replay_guard: Callable[[CapabilityValidationRun], None] | None = None,
    ) -> None:
        self._task_resolver = task_resolver
        self._capability_mounts = capability_mounts
        self._execution_root = Path(execution_root).resolve()
        self._cancel_requested = cancel_requested
        self._cleanup_runner = cleanup_runner or self._docker_cleanup
        self._grant_revoker = grant_revoker or self._revoke_grants
        self._replay_guard = replay_guard

    @staticmethod
    def _actor(run: CapabilityValidationRun) -> CatalogActor:
        return CatalogActor(owner_id=run.owner_id, role=run.actor_role)

    @staticmethod
    def _remove_tree(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _docker_cleanup(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("docker", *arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )

    @staticmethod
    def _revoke_grants(
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> object:
        return get_default_broker().revoke_run_grants(
            owner_id,
            task_id,
            revision,
            run_id,
            reason="validation_replay_cleanup",
        )

    def _cleanup_runtime_resources(
        self,
        run: CapabilityValidationRun,
        inner_run_id: str,
    ) -> None:
        task_id = run.task_ref.task_id
        revision = run.task_ref.revision
        safe_pi_task = re.sub(r"[^a-z0-9-]", "-", task_id.lower())[-24:]
        pi_container = f"mangrove-pi-{safe_pi_task}-r{revision}-{inner_run_id[-6:]}"[:63]
        egress_identity = hashlib.sha256(
            f"{run.owner_id}:{task_id}:{revision}:{inner_run_id}:business_execution".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        safe_egress_task = re.sub(r"[^a-z0-9-]", "-", task_id.lower())[-16:]
        network = f"mangrove-pi-net-{safe_egress_task}-{egress_identity}"[:63]
        proxy = f"mangrove-pi-proxy-{safe_egress_task}-{egress_identity}"[:63]
        host_identity = hashlib.sha256(
            f"{run.owner_id}:{task_id}:{revision}:{inner_run_id}".encode("utf-8")
        ).hexdigest()[:12]
        safe_host_task = re.sub(r"[^a-z0-9-]", "-", task_id.casefold())[-16:]
        host = f"mangrove-cap-host-{safe_host_task}-{host_identity}"[:63]
        errors: list[str] = []
        for name in (pi_container, proxy, host):
            try:
                result = self._cleanup_runner("container", "rm", "-f", name)
                detail = (result.stdout + result.stderr).casefold()
                if result.returncode != 0 and "no such container" not in detail:
                    errors.append("容器清理失败")
            except Exception:
                errors.append("容器清理异常")
        try:
            result = self._cleanup_runner("network", "rm", network)
            detail = (result.stdout + result.stderr).casefold()
            if (
                result.returncode != 0
                and "not found" not in detail
                and "no such network" not in detail
            ):
                errors.append("网络清理失败")
        except Exception:
            errors.append("网络清理异常")
        try:
            self._grant_revoker(run.owner_id, task_id, revision, inner_run_id)
        except Exception:
            errors.append("模型授权撤销失败")
        if errors:
            raise RuntimeError("真实任务重放资源清理不完整：" + "、".join(errors))

    def cleanup(self, run: CapabilityValidationRun) -> None:
        identity = hashlib.sha256(run.run_id.encode("utf-8")).hexdigest()[:16]
        inner_run_id = f"pi_validation_{identity}"
        replay_root = (self._execution_root / "task-replays" / identity).resolve()
        try:
            self._cleanup_runtime_resources(run, inner_run_id)
        finally:
            self._remove_tree(replay_root)

    def __call__(self, run: CapabilityValidationRun) -> dict[str, object]:
        actor = self._actor(run)
        if self._replay_guard is not None:
            # 重放启动前再查一次治理投影：装载之后被隔离/撤销的目标
            # 不得继续重放执行（draft 验证目标不受影响）。
            self._replay_guard(run)
        request = self._task_resolver.load_replay_request(
            actor,
            run.target,
            run.task_ref,
        )
        mounts = _target_mounts(
            run,
            self._capability_mounts(
                run.owner_id,
                run.task_ref.task_id,
                run.task_ref.revision,
            ),
        )
        identity = hashlib.sha256(run.run_id.encode("utf-8")).hexdigest()[:16]
        inner_run_id = f"pi_validation_{identity}"
        replay_root = (self._execution_root / "task-replays" / identity).resolve()
        if self._execution_root not in replay_root.parents:
            raise RuntimeError("真实任务重放目录越界")
        self.cleanup(run)
        replay_root.mkdir(parents=True, exist_ok=False)
        replay_request = request
        runtime = PiRuntime(
            execution_root=replay_root,
            capability_mount_resolver=lambda *_args: tuple(mounts),
            capability_host=CapabilityHost(
                image=settings.pi_capability_host_image,
                execution_root=replay_root / "capability-hosts",
            ),
            document_tool_broker=DocumentToolBroker(
                retriever=DocumentRetrievalModule(),
                ttl_seconds=settings.pi_runtime_timeout_seconds,
            ),
            configure_as_default_document_broker=False,
        )

        completed_tools: set[str] = set()

        async def run_replay():
            async def collect_event(event) -> None:
                if event.event_type == "tool.completed":
                    tool = str(event.details.get("tool") or "")
                    if tool:
                        completed_tools.add(tool)

            task = asyncio.create_task(
                runtime.start(
                    replay_request,
                    on_event=collect_event,
                    run_id=inner_run_id,
                )
            )
            while not task.done():
                await asyncio.wait({task}, timeout=0.5)
                if self._cancel_requested() and not task.done():
                    await runtime.cancel(
                        replay_request.user_id,
                        replay_request.task_id,
                        replay_request.revision,
                    )
                    try:
                        await task
                    except Exception:
                        pass
                    return None
            return await task

        try:
            result = asyncio.run(run_replay())
            if result is None:
                return {"cancelled": True}
            if (
                result.status is not RuntimeStatus.CANDIDATE_READY
                or result.verification is None
                or result.verification.status is not VerificationStatus.PASSED
                or not result.candidates
            ):
                raise RuntimeError("真实任务重放未形成通过独立验证的新候选")
            target_tools = {
                "capability_"
                + re.sub(r"[^a-z0-9_]+", "_", item.manifest.name.lower())[:53]
                for item in load_runtime_manifests(mounts)
            }
            if not completed_tools.intersection(target_tools):
                raise RuntimeError("真实任务重放未实际调用目标 digest 的能力")
            return {
                "cancelled": False,
                "candidate_hashes": sorted(item.sha256 for item in result.candidates),
                "verification": result.verification.model_dump(mode="json"),
                "target_invocation": sorted(completed_tools.intersection(target_tools)),
            }
        finally:
            # 重放只保留受控摘要；候选、会话、临时数据库和日志全部属于验证临时制品。
            self.cleanup(run)


class TaskEvidenceValidationExecutor(CapabilityValidationExecutor):
    """运行真实 Sidecar Smoke，并重开任务来源、交付与独立 Verifier。"""

    def __init__(
        self,
        *,
        task_resolver: ValidationTaskResolver,
        capability_mounts: Callable[[str, str, int], tuple],
        capability_host: CapabilityHost,
        execution_root: str | Path,
        task_replay: Callable[[CapabilityValidationRun], dict[str, object]],
    ) -> None:
        self._task_resolver = task_resolver
        self._capability_mounts = capability_mounts
        self._capability_host = capability_host
        self._execution_root = Path(execution_root).resolve()
        self._task_replay = task_replay
        self._lease: CapabilityHostLease | None = None

    @staticmethod
    def _actor(run: CapabilityValidationRun) -> CatalogActor:
        return CatalogActor(owner_id=run.owner_id, role=run.actor_role)

    def _evidence(
        self,
        run: CapabilityValidationRun,
        step: ValidationStep,
        value: object,
    ) -> ValidationEvidence:
        return ValidationEvidence(
            step=step,
            status=ValidationStepStatus.PASSED,
            evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
            evidence_sha256=_hash(value),
            summary="验证步骤已通过",
        )

    @staticmethod
    def _identity(run: CapabilityValidationRun) -> str:
        return hashlib.sha256(
            f"{run.owner_id}:{run.task_ref.task_id}:{run.task_ref.revision}:{run.run_id}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]

    def _network_name(self, run: CapabilityValidationRun) -> str:
        return f"mangrove-capval-{hashlib.sha256(run.run_id.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _verify_declared_permissions(mounts: tuple[Path, ...]) -> None:
        mounted = load_runtime_manifests(mounts)
        if not mounted:
            raise RuntimeError("冻结能力制品缺少可验证运行清单")
        for item in mounted:
            required = {"network:none"}
            if item.manifest.entrypoint is not None:
                required.add("process:child")
            if item.manifest.kind == "mcp_local":
                required.add("mcp:stdio")
            missing = required - set(item.manifest.permissions)
            if missing:
                # 能力不能依赖实际运行时隐式赋予、却未在冻结清单声明的权限。
                raise RuntimeError("能力运行清单存在未声明的必需权限")

    @staticmethod
    def _same_host_path(value: object, expected: Path) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            return Path(value).resolve() == expected.resolve()
        except OSError:
            return False

    def _verify_isolation(
        self,
        run: CapabilityValidationRun,
        capability_mounts: tuple[Path, ...],
    ) -> None:
        if self._lease is None:
            self._lease = self._stale_lease(run)
        inspected = self._docker(
            "container",
            "inspect",
            self._lease.container_name,
            "--format",
            "{{json .}}",
        )
        if inspected.returncode != 0:
            raise RuntimeError("Capability Host 隔离配置不可判定")
        try:
            payload = json.loads(inspected.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("Capability Host 隔离配置不可判定") from error
        host_config = payload.get("HostConfig") if isinstance(payload, dict) else None
        mounts = payload.get("Mounts") if isinstance(payload, dict) else None
        network_settings = (
            payload.get("NetworkSettings") if isinstance(payload, dict) else None
        )
        attached_networks = (
            network_settings.get("Networks")
            if isinstance(network_settings, dict)
            else None
        )
        if (
            not isinstance(host_config, dict)
            or not isinstance(mounts, list)
            or not isinstance(attached_networks, dict)
        ):
            raise RuntimeError("Capability Host 隔离配置不可判定")
        cap_drop = {str(value).casefold() for value in (host_config.get("CapDrop") or [])}
        security_options = {
            str(value).casefold()
            for value in (host_config.get("SecurityOpt") or [])
        }
        tmpfs = host_config.get("Tmpfs")
        tmp_options = (
            {
                value.strip().casefold()
                for value in str(tmpfs.get("/tmp") or "").split(",")
                if value.strip()
            }
            if isinstance(tmpfs, dict)
            else set()
        )
        expected_network = self._network_name(run)
        # 容器只能附着本次验证专属的 internal 网络；第二网络会绕过 network:none 与治理出口边界。
        if (
            host_config.get("ReadonlyRootfs") is not True
            or host_config.get("Privileged") is not False
            or "all" not in cap_drop
            or not any(
                value.startswith("no-new-privileges")
                for value in security_options
            )
            or not isinstance(tmpfs, dict)
            or set(tmpfs) != {"/tmp"}
            or tmp_options != {"rw", "noexec", "nosuid", "size=64m"}
            or host_config.get("PidsLimit") != 128
            or host_config.get("Memory") != 2 * 1024**3
            or host_config.get("NanoCpus") != 2 * 10**9
            or host_config.get("NetworkMode") != expected_network
            or set(attached_networks) != {expected_network}
        ):
            raise RuntimeError("Capability Host 隔离硬门未通过")
        # 只读额外挂载仍可能注入其他 Owner 或未冻结内容，因此必须与本次 digest 请求一一对应。
        mounted_capabilities = load_runtime_manifests(capability_mounts)
        expected_mounts = {
            "/opt/mangrove-host": self._lease.runtime_dir,
            **{
                f"/capabilities/{item.mount_index}": item.root
                for item in mounted_capabilities
            },
        }
        if len(mounts) != len(expected_mounts):
            raise RuntimeError("Capability Host 挂载集合与冻结请求不一致")
        remaining_destinations = set(expected_mounts)
        for mount in mounts:
            if not isinstance(mount, dict):
                raise RuntimeError("Capability Host 挂载配置不可判定")
            destination = str(mount.get("Destination") or "")
            source = mount.get("Source")
            if "docker.sock" in str(source or "").casefold() or (
                "docker.sock" in destination.casefold()
            ):
                raise RuntimeError("Capability Host 禁止挂载 Docker Socket")
            if (
                destination not in remaining_destinations
                or mount.get("Type") != "bind"
                or mount.get("RW") is not False
                or not self._same_host_path(source, expected_mounts[destination])
            ):
                raise RuntimeError("Capability Host 挂载身份与冻结请求不一致")
            remaining_destinations.remove(destination)
        if remaining_destinations:
            raise RuntimeError("Capability Host 缺少冻结请求挂载")
        network = self._docker(
            "network",
            "inspect",
            expected_network,
            "--format",
            "{{json .Internal}}",
        )
        if network.returncode != 0 or network.stdout.strip().casefold() != "true":
            raise RuntimeError("Capability Host 未使用 internal 隔离网络")

    def _stale_lease(self, run: CapabilityValidationRun) -> CapabilityHostLease:
        identity = self._identity(run)
        safe_task = re.sub(
            r"[^a-z0-9-]",
            "-",
            run.task_ref.task_id.casefold(),
        )[-16:]
        return CapabilityHostLease(
            container_name=f"mangrove-cap-host-{safe_task}-{identity}"[:63],
            relay_url="http://invalid:8765",
            relay_token="stale-validation-token",
            capability_names=(),
            capability_kinds=(),
            runtime_dir=(self._execution_root / identity).resolve(),
        )

    @staticmethod
    def _smoke_mcp_invoke(
        container_name: str,
        capability: str,
        arguments: dict,
        tool: str = "echo",
    ) -> str:
        """容器内真实 MCP 工具调用（Smoke 的 mcp_local 增量，#16）。

        通过 docker exec + node fetch 调 Host /invoke；Host 启动时已完成
        协议握手与 list_tools 冻结。echo 返回必须含 "Echo:" 才通过
        （server-everything echo 工具的输出模式）。
        """
        payload = json.dumps(
            {"capability": capability, "tool": tool, "arguments": arguments}
        )
        # Windows 侧 docker.exe 对含空格/引号的参数重新拼接命令行，内联 JS
        # 的任何引号字面量都会被破坏（node 语法错误 [eval]:1）。可靠路径：
        # 脚本 base64 编码后经单行 wrapper 传递（#16 阶段 2 真实首跑暴露）。
        import base64

        script = (
            "fetch('http://127.0.0.1:8765/invoke',"
            "{method:'POST',headers:{'content-type':'application/json',"
            "authorization:'Bearer '+process.env.MANGROVE_CAPABILITY_TOKEN},"
            "body:JSON.stringify({capability:process.argv[1],tool:process.argv[2],"
            "arguments:JSON.parse(process.argv[3])})})"
            ".then(r=>r.text()).then(t=>{if(!t.includes('Echo:'))process.exit(1);"
            "process.stdout.write(t)})"
        )
        script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        wrapper = f"eval(Buffer.from('{script_b64}','base64').toString())"
        completed = subprocess.run(
            (
                "docker", "exec", container_name, "node", "-e", wrapper,
                capability, tool, json.dumps(arguments),
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"MCP 合成 Smoke 调用失败：{completed.stdout.strip()[-200:]}"
            )
        return completed.stdout.strip()[-200:]

    @staticmethod
    def _docker(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ("docker", *arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
        if check and completed.returncode != 0:
            raise RuntimeError("Docker 验证环境操作失败")
        return completed

    def _cleanup(self, run: CapabilityValidationRun) -> None:
        lease = self._lease or self._stale_lease(run)
        errors: list[Exception] = []
        try:
            asyncio.run(self._capability_host.stop(lease))
        except Exception as error:
            errors.append(error)
        try:
            removed = self._docker("network", "rm", self._network_name(run))
            remove_detail = (removed.stdout + removed.stderr).casefold()
            if (
                removed.returncode != 0
                and "not found" not in remove_detail
                and "no such network" not in remove_detail
            ):
                errors.append(RuntimeError("能力验证网络删除失败"))
            try:
                shutil.rmtree(lease.runtime_dir)
            except FileNotFoundError:
                pass
            self._lease = None
        except Exception as error:
            errors.append(error)
        replay_cleanup = getattr(self._task_replay, "cleanup", None)
        if callable(replay_cleanup):
            try:
                replay_cleanup(run)
            except Exception as error:
                errors.append(error)
        container = self._docker("container", "inspect", lease.container_name)
        network = self._docker("network", "inspect", self._network_name(run))
        container_detail = (container.stdout + container.stderr).casefold()
        network_detail = (network.stdout + network.stderr).casefold()
        container_absent = container.returncode != 0 and (
            "no such object" in container_detail
            or "no such container" in container_detail
            or "not found" in container_detail
        )
        network_absent = network.returncode != 0 and (
            "no such network" in network_detail or "not found" in network_detail
        )
        if (
            not container_absent
            or not network_absent
            or lease.runtime_dir.exists()
        ):
            errors.append(RuntimeError("能力验证临时容器、网络或运行目录清理不完整"))
        if errors:
            raise RuntimeError("能力验证资源清理不完整") from errors[0]

    def execute(
        self,
        run: CapabilityValidationRun,
        step: ValidationStep,
    ) -> ValidationEvidence:
        actor = self._actor(run)
        if step is ValidationStep.SYNTHETIC_SMOKE:
            # 恢复时先精确清理同一 Run 的旧资源；名称均由 run_id 与冻结任务身份确定。
            self._cleanup(run)
            mounts = _target_mounts(
                run,
                self._capability_mounts(
                    run.owner_id,
                    run.task_ref.task_id,
                    run.task_ref.revision,
                ),
            )
            manifests = load_runtime_manifests(tuple(mounts))
            if not mounts or not manifests:
                raise RuntimeError("冻结能力制品无法装载或缺少运行清单")
            network_name = self._network_name(run)
            self._docker("network", "create", "--internal", network_name, check=True)
            self._lease = asyncio.run(
                self._capability_host.start(
                    CapabilityHostRequest(
                        user_id=run.owner_id,
                        task_id=run.task_ref.task_id,
                        revision=run.task_ref.revision,
                        run_id=run.run_id,
                        network_name=network_name,
                        capability_dirs=tuple(mounts),
                    )
                )
            )
            # #16 增量：mcp_local 能力真实 MCP 工具调用（协议握手与
            # list_tools 在 Host 启动时已完成；这里真实 invoke echo）。
            mcp_calls = []
            for item in manifests:
                if item.manifest.kind == "mcp_local":
                    echoed = self._smoke_mcp_invoke(
                        self._lease.container_name,
                        item.manifest.name,
                        {"message": "ac07-11-smoke"},
                    )
                    mcp_calls.append(
                        {"capability": item.manifest.name, "echo": echoed}
                    )
            return self._evidence(
                run,
                step,
                {
                    "digest": run.target.digest,
                    "manifests": [
                        (item.manifest.name, item.manifest.version, item.manifest.kind)
                        for item in manifests
                    ],
                    "mcp_smoke": mcp_calls,
                },
            )
        if step is ValidationStep.OWNER_TASK_REPLAY:
            current = self._task_resolver.verify(actor, run.target, run.task_ref)
            replay = self._task_replay(run)
            if replay.get("cancelled"):
                return ValidationEvidence(
                    step=step,
                    status=ValidationStepStatus.CANCELLED,
                    evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                    evidence_sha256=_hash({"cancelled": True}),
                    summary="验证运行已取消",
                )
            return self._evidence(
                run,
                step,
                {"task_ref": current.model_dump(mode="json"), "replay": replay},
            )
        if step is ValidationStep.FAIL_CLOSED:
            mounts = _target_mounts(
                run,
                self._capability_mounts(
                    run.owner_id,
                    run.task_ref.task_id,
                    run.task_ref.revision,
                ),
            )
            self._verify_declared_permissions(mounts)
            altered = run.task_ref.model_copy(update={"output_sha256": "0" * 64})
            try:
                self._task_resolver.verify(actor, run.target, altered)
            except (PermissionError, ValueError):
                pass
            else:
                raise RuntimeError("篡改后的真实任务引用未被拒绝")
            other = CatalogActor(owner_id=f"other-{run.owner_id}", role="user")
            try:
                self._task_resolver.verify(other, run.target, run.task_ref)
            except (PermissionError, ValueError):
                pass
            else:
                raise RuntimeError("跨 Owner 的真实任务引用未被拒绝")
            if self._lease is None:
                self._lease = self._stale_lease(run)
            unauthorized = self._docker(
                "exec",
                self._lease.container_name,
                "node",
                "-e",
                "fetch('http://127.0.0.1:8765/health',{headers:{authorization:'Bearer invalid'}})"
                ".then(r=>process.exit(r.status===401?0:1)).catch(()=>process.exit(2))",
            )
            if unauthorized.returncode != 0:
                raise RuntimeError("Capability Host 未正确拒绝无效 Token")
            self._verify_isolation(run, mounts)
            return self._evidence(
                run,
                step,
                {
                    "tamper": "rejected",
                    "cross_owner": "rejected",
                    "invalid_token": "rejected",
                    "permissions": "declared",
                    "isolation": "verified",
                },
            )
        if step is ValidationStep.VERIFIER:
            replay_evidence = next(
                (
                    item
                    for item in run.evidence
                    if item.step is ValidationStep.OWNER_TASK_REPLAY
                    and item.status is ValidationStepStatus.PASSED
                ),
                None,
            )
            if replay_evidence is None:
                raise RuntimeError("本次真实任务重放缺少独立 Verifier 通过证据")
            return self._evidence(
                run,
                step,
                {"replay_evidence_sha256": replay_evidence.evidence_sha256},
            )
        self._cleanup(run)
        return self._evidence(run, step, {"temporary_resources": 0, "cleanup": "verified"})


class CapabilityValidationManager:
    """单进程可恢复 worker；持久化 Lease 负责跨进程串行化同一 digest。"""

    def __init__(
        self,
        governance: CapabilityGovernance,
        executor_factory: Callable[[CapabilityValidationRun], CapabilityValidationExecutor],
        *,
        supply_chain_evidence: SupplyChainEvidenceCollector | None = None,
        capability_mounts: Callable[[str, str, int], tuple] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self._governance = governance
        self._executor_factory = executor_factory
        self._supply_chain_evidence = supply_chain_evidence
        self._capability_mounts = capability_mounts
        self._poll_seconds = poll_seconds
        self._worker_id = f"capval-worker-{uuid.uuid4().hex[:12]}"
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(),
                name="capability-validation-worker",
            )

    def notify(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> int:
        pending = tuple(
            run
            for run in self._governance.list_all_validations_for_worker()
            if run.status
            in {
                ValidationRunStatus.QUEUED,
                ValidationRunStatus.RUNNING,
                ValidationRunStatus.CANCELLING,
            }
        )
        for run in pending:
            actor = CatalogActor(owner_id=run.owner_id, role=run.actor_role)

            def collect_supply_chain_evidence(
                current: CapabilityValidationRun,
            ) -> None:
                if (
                    self._supply_chain_evidence is None
                    or self._capability_mounts is None
                ):
                    return
                try:
                    if not self._supply_chain_evidence.requires_collection(
                        current.target
                    ):
                        return
                    mounts = _target_mounts(
                        current,
                        self._capability_mounts(
                            current.owner_id,
                            current.task_ref.task_id,
                            current.task_ref.revision,
                        ),
                    )
                    self._supply_chain_evidence.collect(current.target, mounts[0])
                    # 供应链证据落库是晋级判定的第二触发时点；验证未完成时
                    # 命令保持 held 且不写事件，终态时点会再次判定。
                    self._governance.maybe_promote(current.target, actor=actor)
                except Exception:
                    # 供应链证据是独立硬门；采集失败留待晋级门处理，不篡改五步运行结果。
                    logger.exception("能力供应链证据采集失败：%s", current.run_id)

            try:
                completed = await asyncio.to_thread(
                    self._governance.execute_validation,
                    actor,
                    run.run_id,
                    worker_id=self._worker_id,
                    executor=self._executor_factory(run),
                    lease_guarded_preflight=collect_supply_chain_evidence,
                )
                if completed.status is ValidationRunStatus.SUCCEEDED:
                    # 验证终态是晋级判定的第一触发时点；全部证据通过时确定性晋级。
                    self._governance.maybe_promote(completed.target, actor=actor)
            except Exception:
                # 单条坏记录不能杀死恢复循环；运行本身仍保持持久化状态供下一轮接管。
                logger.exception("能力验证运行执行失败：%s", run.run_id)
        return len(pending)

    async def _run(self) -> None:
        while True:
            await self.run_once()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass
            self._wake.clear()
