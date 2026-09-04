# -*- coding: utf-8 -*-
"""锁定 CoreMind Protocol v2 的最小 AgentKernel Adapter。"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Callable, Mapping
import uuid

from .candidate_manifest_tool import (
    add_evidence,
    add_qualified_omission,
    add_result_item,
    initialize_manifest,
    mark_result_search_complete,
)
from .candidate_qa import inspect_candidates
from .candidate_verifier import load_qualified_omissions, load_result_items
from .coremind_events import project_coremind_event
from .coremind_worker_launcher import sanitized_worker_environment
from .coverage import assess_web_candidate
from .kernel import (
    AGENT_KERNEL_EVENT_SCHEMA_VERSION,
    AGENT_KERNEL_PROTOCOL_VERSION,
    AgentKernelCapabilityError,
    AgentKernelCapabilityManifest,
    AgentKernelError,
    AgentKernelResultUnknownError,
    EventSink,
    RuntimeBinding,
)
from .models import (
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    PiRuntimeResult,
    PermissionProfile,
    RuntimeStatus,
    VerificationReport,
    VerificationStatus,
)
from src.model_connections import (
    ConnectionBroker,
    ProviderOutcomeUnknownError,
    get_default_broker,
)


COREMIND_VERSION = "0.7.1"
COREMIND_SOURCE_COMMIT = "75b706a20ca4cdddef71cbcc0dd90b8b424ddd99"
COREMIND_REVIEWED_COMMIT = "e1d4c3aee76d9ff41b7fb7dcf241707fcd3ef7b6"
COREMIND_MERGE_COMMIT = "bd72bc6ba9dccd157c3b48a3db4490eabd1aff17"
COREMIND_WHEEL_SHA256 = "3fa5301c444da2e3bdaca51bd4800b1bdbcb6dc68e3abef4b39197bde3625e74"
COREMIND_WORKER_SHA256 = "ba4590a68841e520dcd3a91e206ca9e346d10fd9a23b3ed4c560f59707cfa71e"
COREMIND_WORKER_MANIFEST_SHA256 = "fcc625cc41d7960a55f63af1eb862e1634b9844f3b922c4774d73c77f9a70190"
COREMIND_PROVENANCE_SHA256 = "7e081c66858f1edddc7daa176b0839a1e7b8858798f7214ff99ed034fd3e0f03"
COREMIND_SDK_TREE_SHA256 = "812258edd429587ba01a31101c64fc74ed110b5d91d1d0330044eae9039a2488"
COREMIND_PROTOCOL_FINGERPRINT = "sha256:94c8e093979be73a13ecc1090167454567d0602a70b065ceffeed4cb1eca4ce3"
_BASE_RUNTIME_ARTIFACT = (
    f"source-commit={COREMIND_SOURCE_COMMIT};"
    f"reviewed-commit={COREMIND_REVIEWED_COMMIT};"
    f"merge-commit={COREMIND_MERGE_COMMIT};"
    f"coremind-ai-wheel=sha256:{COREMIND_WHEEL_SHA256};"
    f"worker=sha256:{COREMIND_WORKER_SHA256};"
    f"worker-manifest=sha256:{COREMIND_WORKER_MANIFEST_SHA256};"
    f"provenance=sha256:{COREMIND_PROVENANCE_SHA256};"
    f"protocol-v2={COREMIND_PROTOCOL_FINGERPRINT}"
)

ClientFactory = Callable[..., Any]
CandidateVerifierFactory = Callable[[PiRuntimeRequest, str], Any]
_MAX_TEXT_TOOL_BYTES = 2 * 1024 * 1024
_WORKER_START_LOCK = threading.Lock()
_WORKER_STATE = ".mangrove-worker-state.json"


class _WorkerSubprocess:
    """只给锁定 SDK 的 Worker 创建调用注入隔离环境。"""

    def __init__(self, module: Any, environment: Mapping[str, str]) -> None:
        self._module = module
        self._environment = dict(environment)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    def Popen(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["env"] = self._environment
        return self._module.Popen(*args, **kwargs)


def _tool_definitions() -> tuple[dict[str, Any], ...]:
    return (
        {
            "schemaVersion": 1,
            "registrationId": "mangrove-read-source-v1",
            "definitionVersion": 1,
            "toolId": "mangrove-read-source",
            "name": "mangrove_read_source",
            "label": "读取冻结来源",
            "description": "按来源 ID 读取当前 Mangrove Run 已冻结的 UTF-8 来源。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "minLength": 1},
                },
                "required": ["source_id"],
                "additionalProperties": False,
            },
            "effect": {"operations": ["read"], "reversible": True},
            "capability": {
                "effect": "none",
                "replay": "safe",
                "concurrency": "parallel",
                "checkpoint": "none",
                "durability": "ordinary",
            },
        },
        {
            "schemaVersion": 1,
            "registrationId": "mangrove-submit-candidate-v1",
            "definitionVersion": 1,
            "toolId": "mangrove-submit-candidate",
            "name": "mangrove_submit_candidate",
            "label": "提交候选结果",
            "description": "把一个文本候选及逐项来源证据写入当前 Run 的候选区。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "minLength": 1},
                    "format": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                    "result_items": {"type": "array", "items": {"type": "object"}},
                    "qualified_omissions": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "result_search_complete": {"type": "boolean"},
                },
                "required": [
                    "filename",
                    "format",
                    "content",
                    "description",
                    "evidence",
                    "result_items",
                    "result_search_complete",
                ],
                "additionalProperties": False,
            },
            "effect": {
                "operations": ["write"],
                "reversible": True,
                "pathFields": ["filename"],
            },
            "capability": {
                "effect": "workspace",
                "replay": "idempotent",
                "concurrency": "workspace_exclusive",
                "checkpoint": "required",
                "durability": "critical",
            },
        },
    )


def _execution_contract(timeout_seconds: float) -> dict[str, Any]:
    timeout_ms = max(1_000, int(timeout_seconds * 1_000))
    return {
        "tools": _tool_definitions(),
        "permissionProfile": PermissionProfile.STANDARD.value,
        "permissions": {
            "mode": "ask",
            "workspaceOnly": True,
            "network": "deny",
            "allow": ["mangrove_read_source", "mangrove_submit_candidate"],
        },
        "runtime": {
            "maxTurns": 12,
            "maxRetries": 0,
            "runTimeoutMs": timeout_ms,
        },
        "loop": {
            "maxIterations": 3,
            "maxRepairs": 2,
            "verificationTimeoutMs": timeout_ms,
        },
    }


class CoreMindAgentKernelAdapter:
    """把固定 CoreMind SDK 收窄到 Mangrove AgentKernel Interface。"""

    def __init__(
        self,
        *,
        execution_root: str | Path,
        client_factory: ClientFactory | None = None,
        candidate_verifier_factory: CandidateVerifierFactory | None = None,
        connection_broker: ConnectionBroker | None = None,
        relay_base_url: str | None = None,
        tool_names: Mapping[str, str] | None = None,
        poll_interval_seconds: float = 0.05,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.execution_root = Path(execution_root)
        self._client_factory = client_factory
        self._candidate_verifier_factory = candidate_verifier_factory
        self._connection_broker = connection_broker
        self._relay_base_url = relay_base_url
        self._candidate_verification: Any | None = None
        self._tool_names = {
            "mangrove_read_source": "读取冻结来源",
            "mangrove_submit_candidate": "提交候选结果",
            **dict(tool_names or {}),
        }
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._clients: dict[tuple[str, str, int], tuple[str, Any, Path]] = {}
        contract_digest = hashlib.sha256(
            json.dumps(
                _execution_contract(timeout_seconds),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.manifest = AgentKernelCapabilityManifest(
            adapter_id="coremind-runtime",
            adapter_version="0.1.0",
            runtime_artifact=(
                f"{_BASE_RUNTIME_ARTIFACT};execution-contract=sha256:{contract_digest}"
            ),
            protocol_version=AGENT_KERNEL_PROTOCOL_VERSION,
            event_schema_version=AGENT_KERNEL_EVENT_SCHEMA_VERSION,
            runtime_version=f"0.7.1+{COREMIND_SOURCE_COMMIT}",
            runtime_protocol_version="2.0",
            runtime_event_schema_version=COREMIND_PROTOCOL_FINGERPRINT,
            required_capabilities=("start", "resume", "cancel"),
            optional_capabilities=(
                "steer",
                "events",
                "query",
                "host_verification",
                "checkpoint_operations",
                "effect_receipts",
                "provider_usage",
                "tool_approval",
            ),
            available_capabilities=(
                "start",
                "resume",
                "cancel",
                "steer",
                "events",
                "query",
                "host_verification",
                "checkpoint_operations",
                "effect_receipts",
                "provider_usage",
                "tool_approval",
            ),
        )

    async def prepare_manifest(self) -> AgentKernelCapabilityManifest:
        """默认工厂使用前核验实际安装 SDK 与随包 Worker 身份。"""

        if self._client_factory is not None:
            return self.manifest
        module = importlib.import_module("coremind")
        if getattr(module, "__version__", None) != COREMIND_VERSION:
            raise AgentKernelCapabilityError("CoreMind SDK 版本与冻结候选不一致")
        package_root = Path(module.__file__).resolve().parent
        worker = package_root / "_worker" / "coremind-worker.mjs"
        manifest_path = worker.with_name("manifest.json")
        try:
            direct_url = json.loads(
                metadata.distribution("coremind-ai").read_text("direct_url.json")
                or "{}"
            )
            worker_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            worker_sha256 = hashlib.sha256(worker.read_bytes()).hexdigest()
            manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            sdk_tree_sha256 = self._sdk_tree_sha256(package_root)
        except (OSError, ValueError) as exc:
            raise AgentKernelCapabilityError("CoreMind Worker 身份无法核验") from exc
        archive_info = (
            direct_url.get("archive_info") if isinstance(direct_url, Mapping) else None
        )
        hashes = archive_info.get("hashes") if isinstance(archive_info, Mapping) else None
        installed_wheel_sha256 = (
            hashes.get("sha256") if isinstance(hashes, Mapping) else None
        )
        if (
            installed_wheel_sha256 != COREMIND_WHEEL_SHA256
            or worker_sha256 != COREMIND_WORKER_SHA256
            or manifest_sha256 != COREMIND_WORKER_MANIFEST_SHA256
            or sdk_tree_sha256 != COREMIND_SDK_TREE_SHA256
            or worker_manifest.get("bundleSha256") != COREMIND_WORKER_SHA256
            or worker_manifest.get("protocolV2Version") != "2.0"
            or worker_manifest.get("protocolV2SchemaFingerprint")
            != COREMIND_PROTOCOL_FINGERPRINT
        ):
            raise AgentKernelCapabilityError("CoreMind Worker 或 Protocol 身份漂移")
        return self.manifest

    @staticmethod
    def _sdk_tree_sha256(package_root: Path) -> str:
        """核对实际导入包，而不是只信可被篡改的安装来源记录。"""

        root = package_root.parent
        files = sorted(
            path
            for path in package_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )
        digest = hashlib.sha256()
        for path in files:
            name = path.relative_to(root).as_posix()
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    def new_external_run_id(self) -> str:
        return f"cm_run_{uuid.uuid4().hex[:16]}"

    async def start(
        self,
        request: PiRuntimeRequest,
        *,
        binding: RuntimeBinding,
        on_event: EventSink,
    ) -> PiRuntimeResult:
        self._assert_binding(binding)
        self._assert_request_policy(request)
        client, run_root = self._new_client(request, binding)
        key = (request.user_id, request.task_id, request.revision)
        self._clients[key] = (binding.external_run_id, client, run_root)
        try:
            self._write_worker_state(run_root, binding.external_run_id, client, "active")
            self._prepare_workspace(request, run_root)
            self._register_tools(client)
            handle = await asyncio.to_thread(
                client.run,
                self._runtime_prompt(request),
                run_id=binding.external_run_id,
            )
            self._assert_handle(handle, binding.external_run_id)
            return await self._wait_for_terminal(
                client,
                request=request,
                binding=binding,
                run_root=run_root,
                on_event=on_event,
            )
        except asyncio.CancelledError:
            raise
        except (AgentKernelError, AgentKernelCapabilityError):
            raise
        except (TimeoutError, OSError) as exc:
            raise AgentKernelResultUnknownError(
                "CoreMind 请求结果不确定，禁止自动重试"
            ) from exc
        except Exception as exc:
            if exc.__class__.__name__ == "WorkerExitedError":
                raise AgentKernelResultUnknownError(
                    "CoreMind Worker 异常退出，结果未知且禁止自动重试"
                ) from exc
            raise
        finally:
            self._clients.pop(key, None)
            await self._close_client(
                client,
                run_root,
                request,
                binding,
                reason="run_closed",
            )

    async def resume(
        self,
        request: PiRuntimeRequest,
        *,
        binding: RuntimeBinding,
        checkpoint: PiRuntimeCheckpoint,
        on_event: EventSink,
    ) -> PiRuntimeResult:
        self._assert_binding(binding)
        self._assert_request_policy(request)
        if checkpoint.run_id != binding.external_run_id:
            raise AgentKernelError("CoreMind 恢复身份与冻结 RuntimeBinding 不一致")
        self._revoke_model_grants(request, binding, reason="run_resumed")
        client, run_root = self._new_client(request, binding)
        if checkpoint.workspace_root.resolve() != run_root.resolve():
            raise AgentKernelError("CoreMind 恢复工作区不属于当前用户、任务或版本")
        key = (request.user_id, request.task_id, request.revision)
        self._clients[key] = (binding.external_run_id, client, run_root)
        try:
            self._write_worker_state(run_root, binding.external_run_id, client, "active")
            self._prepare_workspace(request, run_root)
            self._register_tools(client)
            handle = await asyncio.to_thread(
                client.resume_run,
                binding.external_run_id,
            )
            self._assert_handle(handle, binding.external_run_id)
            return await self._wait_for_terminal(
                client,
                request=request,
                binding=binding,
                run_root=run_root,
                on_event=on_event,
                recover_persisted_candidate=True,
            )
        except asyncio.CancelledError:
            raise
        except (AgentKernelError, AgentKernelCapabilityError):
            raise
        except (TimeoutError, OSError) as exc:
            raise AgentKernelResultUnknownError(
                "CoreMind 恢复结果不确定，禁止自动重试"
            ) from exc
        except Exception as exc:
            if exc.__class__.__name__ == "WorkerExitedError":
                raise AgentKernelResultUnknownError(
                    "CoreMind Worker 异常退出，恢复结果未知且禁止自动重试"
                ) from exc
            raise
        finally:
            self._clients.pop(key, None)
            await self._close_client(
                client,
                run_root,
                request,
                binding,
                reason="run_closed",
            )

    async def steer(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        instruction: str,
    ) -> Any:
        entry = self._clients.get((user_id, task_id, revision))
        if entry is None:
            raise AgentKernelError("CoreMind Run 当前不在本进程执行")
        run_id, client, _run_root = entry
        control_id = uuid.uuid4().hex
        receipt = await asyncio.to_thread(
            client.control,
            {
                "schemaVersion": 1,
                "controlId": control_id,
                "runId": run_id,
                "type": "steer",
                "input": instruction,
            },
        )
        self._assert_control_receipt(receipt, run_id, control_id)
        return receipt

    async def cancel(self, user_id: str, task_id: str, revision: int) -> None:
        entry = self._clients.get((user_id, task_id, revision))
        if entry is None:
            self._assert_persisted_worker_closed(user_id, task_id, revision)
            return
        run_id, client, run_root = entry
        try:
            await asyncio.to_thread(client.cancel, run_id)
        finally:
            # 控制回执不等于静止；关闭独立 Worker 才是本切片的硬停边界。
            self._clients.pop((user_id, task_id, revision), None)
            await self._close_client_for_identity(
                client,
                run_root,
                user_id=user_id,
                task_id=task_id,
                revision=revision,
                run_id=run_id,
                reason="run_cancelled",
            )

    def bind_candidate_verification(self, service: Any) -> None:
        """绑定 Mangrove 独立验证；CoreMind 的自报成功不取得发布权。"""

        if self._candidate_verifier_factory is None:
            raise AgentKernelCapabilityError(
                "CoreMind 文本尚未绑定真实 CandidateSet，拒绝接入独立验证"
            )
        if (
            self._candidate_verification is not None
            and self._candidate_verification is not service
        ):
            raise AgentKernelCapabilityError("CoreMind Adapter 已绑定其他验证 Module")
        self._candidate_verification = service

    def _new_client(
        self,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
    ) -> tuple[Any, Path]:
        owner = hashlib.sha256(request.user_id.encode("utf-8")).hexdigest()[:16]
        run_root = (
            self.execution_root
            / "coremind"
            / owner
            / request.task_id
            / f"r{request.revision}"
            / binding.external_run_id
        )
        run_root.mkdir(parents=True, exist_ok=True)
        factory = self._client_factory or self._default_client_factory
        model_route = self._model_route(request, binding)
        try:
            client = factory(
                request=request,
                binding=binding,
                run_root=run_root,
                model_route=model_route,
            )
        except Exception:
            self._revoke_model_grants(request, binding, reason="start_failed")
            raise
        return client, run_root

    def _model_route(
        self,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
    ) -> dict[str, str]:
        if request.model_connection_id is None:
            assert request.model is not None
            assert request.base_url is not None
            assert request.api_key is not None
            return {
                "model": request.model,
                "base_url": request.base_url,
                "api_key": request.api_key,
            }
        assert request.model_connection_version is not None
        if not self._relay_base_url:
            raise AgentKernelCapabilityError("CoreMind 模型 Relay 地址尚未配置")
        grant = self._broker().issue_grant(
            owner_user_id=request.user_id,
            connection_id=request.model_connection_id,
            connection_version=request.model_connection_version,
            model_id=binding.model,
            task_id=request.task_id,
            revision=request.revision,
            run_id=binding.external_run_id,
            purpose="agent_inference",
            ttl_seconds=max(1, int(self._timeout_seconds)),
        )
        if grant.api_format != "openai_chat_completions":
            self._revoke_model_grants(request, binding, reason="unsupported_protocol")
            raise AgentKernelCapabilityError("所选连接协议不受当前 CoreMind Runtime 支持")
        return {
            "model": grant.model,
            "base_url": self._relay_base_url.rstrip("/"),
            "api_key": grant.token,
        }

    def _broker(self) -> ConnectionBroker:
        if self._connection_broker is None:
            self._connection_broker = get_default_broker()
        return self._connection_broker

    def _revoke_model_grants(
        self,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        *,
        reason: str,
    ) -> None:
        if request.model_connection_id is None:
            return
        self._revoke_model_grants_for_identity(
            request.user_id,
            request.task_id,
            request.revision,
            binding.external_run_id,
            reason=reason,
        )

    def _revoke_model_grants_for_identity(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        *,
        reason: str,
    ) -> None:
        if self._connection_broker is None:
            return
        self._connection_broker.revoke_run_grants(
            user_id,
            task_id,
            revision,
            run_id,
            reason=reason,
        )

    async def _close_client(
        self,
        client: Any,
        run_root: Path,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        *,
        reason: str,
    ) -> None:
        await self._close_client_for_identity(
            client,
            run_root,
            user_id=request.user_id,
            task_id=request.task_id,
            revision=request.revision,
            run_id=binding.external_run_id,
            reason=reason,
        )

    async def _close_client_for_identity(
        self,
        client: Any,
        run_root: Path,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        reason: str,
    ) -> None:
        stopped = False
        cancelled = False
        try:
            close_task = asyncio.create_task(asyncio.to_thread(client.close))
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                # 取消不能跳过 Worker 清理；清理完成后仍保留原取消语义。
                cancelled = True
                await close_task
            stopped = True
        except Exception:
            stopped = await asyncio.to_thread(self._force_stop_process, client)
        state_saved = False
        if stopped:
            try:
                self._write_worker_state(run_root, run_id, client, "closed")
                state_saved = True
            except OSError:
                pass
        revoke_error = False
        try:
            self._revoke_model_grants_for_identity(
                user_id,
                task_id,
                revision,
                run_id,
                reason=reason,
            )
        except Exception:
            revoke_error = True
        if not stopped or not state_saved or revoke_error:
            raise AgentKernelResultUnknownError(
                "CoreMind Worker 或临时模型授权未能证明已清理"
            )
        if cancelled:
            raise asyncio.CancelledError()

    @staticmethod
    def _force_stop_process(client: Any) -> bool:
        process = getattr(client, "_process", None)
        if process is None:
            return False
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except (TimeoutError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
            return process.poll() is not None
        except (OSError, TimeoutError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _write_worker_state(
        run_root: Path,
        run_id: str,
        client: Any,
        status: str,
    ) -> None:
        process = getattr(client, "_process", None)
        pid = getattr(process, "pid", None)
        payload = json.dumps(
            {"run_id": run_id, "status": status, "pid": pid},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with tempfile.NamedTemporaryFile(
            "w",
            dir=run_root,
            prefix=".mangrove-worker-state-",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(run_root / _WORKER_STATE)

    def _assert_persisted_worker_closed(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> None:
        owner = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        revision_root = (
            self.execution_root / "coremind" / owner / task_id / f"r{revision}"
        )
        states = list(revision_root.glob(f"*/{_WORKER_STATE}"))
        if not states:
            raise AgentKernelError("CoreMind Worker 静止状态无法证明")
        for state_path in states:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AgentKernelError("CoreMind Worker 静止状态无法证明") from exc
            if state.get("status") == "closed":
                continue
            pid = state.get("pid")
            if isinstance(pid, int) and pid > 0 and not self._pid_exists(pid):
                continue
            raise AgentKernelError("CoreMind Worker 仍可能运行，取消失败关闭")

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (OSError, PermissionError):
            return True
        return True

    def _prepare_workspace(self, request: PiRuntimeRequest, run_root: Path) -> None:
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "output").mkdir(exist_ok=True)
        for source in request.sources:
            if self._file_sha256(source.host_path) != source.sha256:
                raise AgentKernelCapabilityError("CoreMind 冻结来源内容哈希不一致")

    @staticmethod
    def _assert_request_policy(request: PiRuntimeRequest) -> None:
        if request.permission_profile is not PermissionProfile.STANDARD:
            raise AgentKernelCapabilityError(
                f"权限档位 {request.permission_profile.value} 尚未配置 CoreMind 授权范围"
            )

    @staticmethod
    def _approval_decision(event: Mapping[str, Any], run_id: str) -> str:
        """只批准当前 Run 冻结目录内、语义未漂移的工具。"""

        definitions = {item["name"]: item for item in _tool_definitions()}
        definition = definitions.get(str(event.get("tool") or ""))
        if (
            event.get("type") != "approval_required"
            or event.get("runId") != run_id
            or definition is None
            or not isinstance(event.get("args"), Mapping)
            or event.get("effect") != definition["effect"]
            or event.get("capability") != definition["capability"]
        ):
            return "deny"
        return "allow"

    def _register_tools(self, client: Any) -> None:
        if self._candidate_verification is None:
            return
        register = getattr(client, "register_tool_definition", None)
        if not callable(register):
            raise AgentKernelCapabilityError("CoreMind Worker 不支持声明式工具")
        for definition in _tool_definitions():
            receipt = register(definition)
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("registrationId") != definition["registrationId"]
                or receipt.get("toolId") != definition["toolId"]
                or receipt.get("status") not in {"registered", "duplicate"}
            ):
                raise AgentKernelCapabilityError("CoreMind 工具目录冻结失败")

    @staticmethod
    def _runtime_prompt(request: PiRuntimeRequest) -> str:
        payload = {
            "objective": request.objective_text,
            "requested_output_formats": list(request.requested_output_formats),
            "sources": [
                {
                    "source_id": source.upload_id,
                    "name": source.original_name,
                    "media_type": source.media_type,
                    "sha256": source.sha256,
                }
                for source in request.sources
            ],
            "goal_contract": request.goal_contract,
            "compiled_context": (
                request.compiled_context.content
                if request.compiled_context is not None
                else None
            ),
            "source_coverage": request.source_coverage,
            "table_output_contracts": [
                contract.model_dump(mode="json")
                for contract in request.table_output_contracts
            ],
            "permission_profile": request.permission_profile.value,
        }
        return (
            "执行以下冻结的 Mangrove 任务。来源内容必须通过 mangrove_read_source 读取；"
            "结果必须通过 mangrove_submit_candidate 提交。不得扩大来源、模型或权限。\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _default_client_factory(
        self,
        *,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        run_root: Path,
        model_route: Mapping[str, str],
    ) -> Any:
        coremind = importlib.import_module("coremind")
        package_root = Path(coremind.__file__).resolve().parent
        worker = package_root / "_worker" / "coremind-worker.mjs"
        node = shutil.which("node")
        if node is None:
            raise AgentKernelCapabilityError("CoreMind Worker 需要已安装的 Node")
        grant_env_name = (
            "MANGROVE_COREMIND_RUN_GRANT_"
            + hashlib.sha256(binding.external_run_id.encode("utf-8")).hexdigest()[:16]
        )
        contract = _execution_contract(self._timeout_seconds)
        config = {
            "schemaVersion": 2,
            "name": "mangrove-agent-kernel",
            "permissions": contract["permissions"],
            "provider": {
                "baseUrl": model_route["base_url"],
                "model": model_route["model"],
                "apiKeyEnv": "MANGROVE_COREMIND_MODEL_GRANT",
            },
            "agents": {"main": {"systemPrompt": "执行冻结的 Mangrove 目标。", "tools": []}},
            "runtime": contract["runtime"],
            "loop": {
                "execute": {"agent": "main", "input": "{{prompt}}"},
                "verify": {
                    "mode": "host",
                    "timeoutMs": contract["loop"]["verificationTimeoutMs"],
                },
                "repair": {
                    "agent": "main",
                    "input": "按 Mangrove 宿主验证反馈修正：{{verification.text}}",
                },
                "maxIterations": contract["loop"]["maxIterations"],
                "maxRepairs": contract["loop"]["maxRepairs"],
            },
        }
        client = coremind.CoreMindClient(
            config,
            config_dir=run_root,
            cwd=run_root,
            worker_command=(str(Path(node).resolve()), str(worker)),
            approval_handler=lambda event: self._approval_decision(
                event,
                binding.external_run_id,
            ),
            request_timeout=self._timeout_seconds,
            protocol_version="2.0",
        )
        environment = sanitized_worker_environment(
            {**os.environ, grant_env_name: model_route["api_key"]},
            grant_env_name=grant_env_name,
            runtime_root=run_root,
            node_path=node,
        )
        # 固定 SDK 尚无 Worker env 参数；只替换其模块内的创建接缝，不能清空宿主环境。
        with _WORKER_START_LOCK:
            client_module = importlib.import_module("coremind.client")
            original_subprocess = client_module.subprocess
            client_module.subprocess = _WorkerSubprocess(
                original_subprocess,
                environment,
            )
            try:
                client.start()
            finally:
                client_module.subprocess = original_subprocess
        return client

    async def _wait_for_terminal(
        self,
        client: Any,
        *,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        run_root: Path,
        on_event: EventSink,
        recover_persisted_candidate: bool = False,
    ) -> PiRuntimeResult:
        cursor = 0
        tool_cursor = 0
        verification_cursor = 0
        verified_result: PiRuntimeResult | None = None
        required_effect_calls: set[str] = set()
        checkpointed_calls: set[str] = set()
        committed_effect_calls: set[str] = set()
        async with asyncio.timeout(self._timeout_seconds):
            while True:
                tool_calls = getattr(client, "received_tool_calls", ())
                while tool_cursor < len(tool_calls):
                    await self._answer_tool_call(
                        client,
                        request=request,
                        binding=binding,
                        run_root=run_root,
                        call=tool_calls[tool_cursor],
                    )
                    if tool_calls[tool_cursor].get("name") == "mangrove_submit_candidate":
                        required_effect_calls.add(str(tool_calls[tool_cursor]["callId"]))
                    tool_cursor += 1
                verification_requests = getattr(
                    client,
                    "received_verification_requests",
                    (),
                )
                while verification_cursor < len(verification_requests):
                    verification = verification_requests[verification_cursor]
                    verified_result, decision, feedback = await self._verify_candidate(
                        request=request,
                        binding=binding,
                        run_root=run_root,
                        verification=verification,
                    )
                    control_id = self._stable_id(
                        "verification",
                        binding.external_run_id,
                        verification["requestId"],
                    )
                    receipt = await asyncio.to_thread(
                        client.submit_verification,
                        binding.external_run_id,
                        verification["requestId"],
                        verification["candidateSha256"],
                        decision=decision,
                        feedback=feedback,
                        control_id=control_id,
                    )
                    self._assert_control_receipt(
                        receipt,
                        binding.external_run_id,
                        control_id,
                    )
                    if receipt.get("status") not in {"applied", "duplicate"}:
                        raise AgentKernelResultUnknownError(
                            "CoreMind 宿主验证结果不确定，禁止自动放行"
                        )
                    verification_cursor += 1
                try:
                    page = await asyncio.to_thread(
                        client.events,
                        binding.external_run_id,
                        after_sequence=cursor,
                        limit=1000,
                    )
                except Exception as exc:
                    # v2 RunHandle 可早于首条持久事实返回；只重查，不重发 run。
                    if getattr(exc, "coremind_code", None) == "unknown_run":
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                    raise
                for raw_event in page.get("events", ()):
                    payload = raw_event.get("payload") or {}
                    if isinstance(payload, Mapping):
                        call_id = payload.get("callId")
                        if payload.get("type") == "checkpoint_created" and call_id:
                            checkpointed_calls.add(str(call_id))
                        if payload.get("type") == "effect_receipt" and call_id:
                            if payload.get("status") == "unknown":
                                raise AgentKernelResultUnknownError(
                                    "CoreMind 副作用结果不确定，禁止自动重放"
                                )
                            if payload.get("status") == "committed":
                                committed_effect_calls.add(str(call_id))
                    event = project_coremind_event(
                        raw_event,
                        run_id=binding.external_run_id,
                        model=binding.model,
                        tool_names=self._tool_names,
                    )
                    if event is not None:
                        await on_event(event)
                cursor = int(page.get("nextCursor", cursor))
                try:
                    snapshot = await asyncio.to_thread(
                        client.query,
                        binding.external_run_id,
                    )
                except Exception as exc:
                    if getattr(exc, "coremind_code", None) == "unknown_run":
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                    raise
                projection = snapshot.get("projection") or {}
                if projection.get("status") in {"finished", "paused"}:
                    succeeded = (
                        (projection.get("outcome") or {}).get("status") == "succeeded"
                    )
                    if succeeded and verified_result is None and recover_persisted_candidate:
                        verified_result, decision, _feedback = (
                            await self._verify_current_candidate(
                                request=request,
                                binding=binding,
                                run_root=run_root,
                            )
                        )
                        if decision != "accept":
                            verified_result = None
                    if succeeded and verified_result is not None:
                        if (
                            recover_persisted_candidate
                            and not checkpointed_calls.intersection(
                                committed_effect_calls
                            )
                        ):
                            raise AgentKernelResultUnknownError(
                                "CoreMind 恢复缺少已提交副作用的成对证据"
                            )
                        if not required_effect_calls.issubset(checkpointed_calls):
                            raise AgentKernelResultUnknownError(
                                "CoreMind 工作区副作用缺少 Checkpoint 证据"
                            )
                        if not required_effect_calls.issubset(
                            committed_effect_calls
                        ):
                            raise AgentKernelResultUnknownError(
                                "CoreMind 工作区副作用缺少 EffectReceipt 证据"
                            )
                        return verified_result
                    return self._result_from_projection(
                        binding.external_run_id,
                        run_root,
                        projection,
                    )
                await asyncio.sleep(self._poll_interval_seconds)

    async def _answer_tool_call(
        self,
        client: Any,
        *,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        run_root: Path,
        call: Mapping[str, Any],
    ) -> None:
        definitions = {item["name"]: item for item in _tool_definitions()}
        definition = definitions.get(str(call.get("name") or ""))
        if (
            definition is None
            or call.get("runId") != binding.external_run_id
            or call.get("registrationId") != definition["registrationId"]
            or call.get("toolId") != definition["toolId"]
            or not isinstance(call.get("args"), Mapping)
        ):
            raise AgentKernelCapabilityError("CoreMind 工具调用不属于冻结目录")
        try:
            if definition["name"] == "mangrove_read_source":
                result = self._read_source(request, call["args"])
            else:
                result = self._submit_candidate(request, run_root, call["args"])
            error = None
        except (OSError, UnicodeError, ValueError):
            result = None
            error = "Mangrove 隔离来源或候选参数无效"
        result_id = self._stable_id(
            "tool-result",
            binding.external_run_id,
            str(call["callId"]),
        )
        values = {"result_id": result_id}
        if error is None:
            values["result"] = result
        else:
            values["error"] = error
        receipt = await asyncio.to_thread(
            client.submit_tool_result,
            binding.external_run_id,
            call["callId"],
            call["registrationId"],
            **values,
        )
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("resultId") != result_id
            or receipt.get("runId") != binding.external_run_id
            or receipt.get("callId") != call["callId"]
            or receipt.get("registrationId") != call["registrationId"]
        ):
            raise AgentKernelCapabilityError("CoreMind 工具结果回执身份无效")
        if receipt.get("status") not in {"accepted", "duplicate"}:
            raise AgentKernelResultUnknownError(
                "CoreMind 工具结果不确定，禁止自动重放副作用"
            )

    def _read_source(
        self,
        request: PiRuntimeRequest,
        args: Mapping[str, Any],
    ) -> dict[str, Any]:
        source_id = str(args.get("source_id") or "")
        source = next(
            (item for item in request.sources if item.upload_id == source_id),
            None,
        )
        if source is None:
            raise ValueError("来源不存在或不属于当前 Run")
        if self._file_sha256(source.host_path) != source.sha256:
            raise ValueError("冻结来源内容哈希已漂移")
        raw = source.host_path.read_bytes()
        if len(raw) > _MAX_TEXT_TOOL_BYTES:
            raise ValueError("冻结来源超过当前文本工具上限")
        return {
            "source_id": source.upload_id,
            "content": raw.decode("utf-8-sig"),
            "sha256": source.sha256,
        }

    def _submit_candidate(
        self,
        request: PiRuntimeRequest,
        run_root: Path,
        args: Mapping[str, Any],
    ) -> dict[str, Any]:
        filename = str(args.get("filename") or "")
        if Path(filename).name != filename or filename == "candidate-manifest.json":
            raise ValueError("候选文件名必须是不含路径的普通文件名")
        output_format = str(args.get("format") or "").lower().lstrip(".")
        normalized = {"markdown" if item == "md" else item for item in request.requested_output_formats}
        if ("markdown" if output_format == "md" else output_format) not in normalized:
            raise ValueError("候选格式不属于冻结交付格式")
        content = args.get("content")
        description = str(args.get("description") or "").strip()
        if not isinstance(content, str) or not content.strip() or not description:
            raise ValueError("候选内容和说明不能为空")
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_TEXT_TOOL_BYTES:
            raise ValueError("候选内容超过当前文本工具上限")
        output_dir = run_root / "output"
        path = output_dir / filename
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=output_dir,
            prefix=".coremind-candidate-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            temporary = Path(handle.name)
        temporary.replace(path)
        initialize_manifest(
            output_dir=output_dir,
            filename=filename,
            output_format=output_format,
            description=description,
        )
        for evidence in self._records(args.get("evidence"), "evidence"):
            add_evidence(
                output_dir=output_dir,
                filename=filename,
                source=str(evidence.get("source") or ""),
                locator=str(evidence.get("locator") or ""),
                quote=str(evidence.get("quote") or ""),
            )
        for item in self._records(args.get("result_items"), "result_items"):
            add_result_item(
                output_dir=output_dir,
                result_id=str(item.get("result_id") or ""),
                label=str(item.get("label") or ""),
                source=str(item.get("source") or ""),
                locator=str(item.get("locator") or ""),
                quote=str(item.get("quote") or ""),
            )
        for item in self._records(
            args.get("qualified_omissions", ()),
            "qualified_omissions",
        ):
            add_qualified_omission(
                output_dir=output_dir,
                result_id=str(item.get("result_id") or ""),
                label=str(item.get("label") or ""),
                source=str(item.get("source") or ""),
                locator=str(item.get("locator") or ""),
                quote=str(item.get("quote") or ""),
            )
        if args.get("result_search_complete") is True:
            mark_result_search_complete(output_dir=output_dir)
        return {"status": "candidate_staged", "filename": filename}

    async def _verify_candidate(
        self,
        *,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        run_root: Path,
        verification: Mapping[str, Any],
    ) -> tuple[PiRuntimeResult | None, str, str]:
        candidate = verification.get("candidate")
        if (
            verification.get("runId") != binding.external_run_id
            or not isinstance(candidate, str)
            or hashlib.sha256(candidate.encode("utf-8")).hexdigest()
            != verification.get("candidateSha256")
        ):
            raise AgentKernelCapabilityError("CoreMind 宿主验证请求身份无效")
        return await self._verify_current_candidate(
            request=request,
            binding=binding,
            run_root=run_root,
        )

    async def _verify_current_candidate(
        self,
        *,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        run_root: Path,
    ) -> tuple[PiRuntimeResult | None, str, str]:
        """只按当前冻结来源复验落盘候选，不信任 Runtime 的成功状态。"""

        if (
            self._candidate_verification is None
            or self._candidate_verifier_factory is None
        ):
            return None, "reject", "Mangrove 独立 Candidate 验证尚未绑定"
        output_dir = run_root / "output"
        try:
            candidates = inspect_candidates(output_dir, request.requested_output_formats)
            verifier = self._candidate_verifier_factory(
                request,
                binding.external_run_id,
            )
            attempt = await self._candidate_verification.verify_initial_current(
                request=request,
                run_id=binding.external_run_id,
                candidates=candidates,
                manifest_path=output_dir / "candidate-manifest.json",
                verifier=verifier,
                actor_id=request.user_id,
            )
            attempt_status = getattr(attempt.status, "value", attempt.status)
            if attempt_status == "outcome_unknown":
                raise AgentKernelResultUnknownError(
                    "CoreMind 候选验证结果不确定，禁止自动重试"
                )
            report = VerificationReport.model_validate_json(attempt.report_json)
        except AgentKernelResultUnknownError:
            raise
        except ProviderOutcomeUnknownError as exc:
            raise AgentKernelResultUnknownError(
                "CoreMind 候选验证结果不确定，禁止自动重试"
            ) from exc
        except Exception:
            return None, "reject", "Mangrove 独立候选验证未通过"
        if report.status is not VerificationStatus.PASSED:
            return None, "reject", report.summary[:500]
        coverage = self._candidate_coverage(
            request,
            output_dir / "candidate-manifest.json",
            report,
        )
        if coverage is not None and coverage.same_run_repair_allowed:
            return None, "reject", coverage.conclusion.reason[:500]
        return (
            PiRuntimeResult(
                status=RuntimeStatus.CANDIDATE_READY,
                run_id=binding.external_run_id,
                workspace_root=run_root,
                summary="CoreMind 候选已通过 Mangrove 独立验证",
                candidates=candidates,
                verification=report,
                candidate_coverage=coverage,
            ),
            "accept",
            "",
        )

    @staticmethod
    def _candidate_coverage(
        request: PiRuntimeRequest,
        manifest_path: Path,
        report: VerificationReport,
    ):
        if request.goal_contract is None or request.source_coverage is None:
            return None
        coverage = request.goal_contract.get("coverage") or {}
        source = request.source_coverage
        result_search_verified = any(
            check.code == "coverage_scope_review" and check.passed
            for check in report.checks
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return assess_web_candidate(
            result_items=load_result_items(manifest_path, require_explicit=True),
            qualified_omissions=(
                load_qualified_omissions(manifest_path, require_explicit=True)
                if result_search_verified
                else ()
            ),
            target_result_count=coverage.get("target_result_count"),
            strict=coverage.get("strictness") == "strict",
            require_all=bool(coverage.get("require_all")),
            scope_complete=source.get("status") == "scope_complete",
            failed_page_count=int(source.get("failed_page_count") or 0),
            coverage_unknown=(
                source.get("status") == "coverage_unknown"
                or bool(source.get("limit_reached"))
            ),
            result_search_complete=bool(manifest.get("result_search_complete"))
            and result_search_verified,
            observed_page_count=int(source.get("valid_page_count") or 0),
        )

    @staticmethod
    def _records(value: Any, field: str) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, Mapping) for item in value
        ):
            raise ValueError(f"{field} 必须是对象数组")
        return tuple(value)

    @staticmethod
    def _stable_id(kind: str, run_id: str, value: str) -> str:
        digest = hashlib.sha256(f"{run_id}\0{value}".encode("utf-8")).hexdigest()
        return f"mangrove-{kind}-{digest[:32]}"

    @staticmethod
    def _assert_control_receipt(
        receipt: Any,
        run_id: str,
        control_id: str,
    ) -> None:
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("runId") != run_id
            or receipt.get("controlId") != control_id
        ):
            raise AgentKernelCapabilityError("CoreMind 控制回执身份无效")

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _result_from_projection(
        run_id: str,
        run_root: Path,
        projection: Mapping[str, Any],
    ) -> PiRuntimeResult:
        status = projection.get("status")
        outcome = projection.get("outcome") or {}
        outcome_status = outcome.get("status")
        if status == "paused":
            return PiRuntimeResult(
                status=RuntimeStatus.NEEDS_INPUT,
                run_id=run_id,
                workspace_root=run_root,
                summary="CoreMind Run 已暂停，等待明确控制",
            )
        if outcome_status in {"aborted", "cancelled"}:
            return PiRuntimeResult(
                status=RuntimeStatus.CANCELLED,
                run_id=run_id,
                workspace_root=run_root,
            )
        error_code = (
            "COREMIND_CANDIDATE_NOT_MAPPED"
            if outcome_status == "succeeded"
            else "COREMIND_EXECUTION_FAILED"
        )
        summary = (
            "CoreMind 已结束，但真实 CandidateSet 尚未接入 Mangrove 独立验证"
            if outcome_status == "succeeded"
            else "CoreMind Run 未成功完成"
        )
        runtime_error = outcome.get("error")
        runtime_error_code = (
            str(runtime_error.get("code"))[:120]
            if isinstance(runtime_error, Mapping) and runtime_error.get("code")
            else None
        )
        return PiRuntimeResult(
            status=RuntimeStatus.FAILED,
            run_id=run_id,
            workspace_root=run_root,
            summary=summary,
            failure={
                "error_code": error_code,
                "cause_summary": summary,
                "runtime_status": str(status or "unknown")[:40],
                "runtime_outcome": str(outcome_status or "unknown")[:40],
                **(
                    {"runtime_error_code": runtime_error_code}
                    if runtime_error_code
                    else {}
                ),
            },
        )

    def _assert_binding(self, binding: RuntimeBinding) -> None:
        manifest = self.manifest
        if (
            binding.adapter_id != manifest.adapter_id
            or binding.adapter_version != manifest.adapter_version
            or binding.runtime_artifact != manifest.runtime_artifact
            or binding.protocol_version != manifest.protocol_version
            or binding.event_schema_version != manifest.event_schema_version
            or binding.capability_digest != manifest.digest
        ):
            raise AgentKernelCapabilityError("CoreMind 身份与冻结 RuntimeBinding 不一致")

    @staticmethod
    def _assert_handle(handle: Any, run_id: str) -> None:
        if not isinstance(handle, Mapping) or handle.get("runId") != run_id:
            raise AgentKernelCapabilityError("CoreMind RunHandle 身份不一致")
