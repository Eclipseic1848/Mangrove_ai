# -*- coding: utf-8 -*-
"""Mangrove 自有 AgentKernel 合同与 Runtime Adapter 接缝。"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..database_migrations import SchemaNotCurrentError
from .models import (
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeEvent,
    RuntimeStatus,
)


AGENT_KERNEL_ID = "mangrove-agent-kernel"
AGENT_KERNEL_VERSION = "1.0.0"
AGENT_KERNEL_PROTOCOL_VERSION = "mangrove.agent-kernel.v1"
AGENT_KERNEL_EVENT_SCHEMA_VERSION = "mangrove.runtime-event.v1"
_REQUIRED_ADAPTER_CAPABILITIES = frozenset({"start", "resume", "cancel"})
_QUIESCENT_RESULT_STATUSES = frozenset(
    {
        RuntimeStatus.NEEDS_INPUT,
        RuntimeStatus.CANDIDATE_READY,
        RuntimeStatus.FAILED,
        RuntimeStatus.CANCELLED,
    }
)

EventSink = Callable[[RuntimeEvent], Awaitable[None]]


class AgentKernelError(RuntimeError):
    """AgentKernel 失败基类。"""


class AgentKernelCapabilityError(AgentKernelError):
    """Runtime Adapter 合同缺失或不兼容。"""


class AgentKernelResultUnknownError(AgentKernelError):
    """外部请求是否形成结果无法证明，禁止自动重试。"""


class AgentKernelCapabilityManifest(BaseModel):
    """Adapter 对 AgentKernel 声明的冻结能力合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_id: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    runtime_artifact: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    event_schema_version: str = Field(min_length=1)
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...] = ()
    available_capabilities: tuple[str, ...]

    @model_validator(mode="after")
    def validate_capability_sets(self) -> "AgentKernelCapabilityManifest":
        sets = (
            self.required_capabilities,
            self.optional_capabilities,
            self.available_capabilities,
        )
        if any(len(values) != len(set(values)) for values in sets):
            raise ValueError("AgentKernel 能力清单不得包含重复项")
        overlap = set(self.required_capabilities) & set(self.optional_capabilities)
        if overlap:
            raise ValueError("必需能力与可选能力不得重叠")
        return self

    @property
    def digest(self) -> str:
        """返回与字段顺序无关的能力清单摘要。"""

        payload = self.model_dump(mode="json")
        for key in (
            "required_capabilities",
            "optional_capabilities",
            "available_capabilities",
        ):
            payload[key] = sorted(payload[key])
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class RuntimeBinding(BaseModel):
    """一个 Run 在启动前冻结的精确 Runtime 身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kernel_id: str = AGENT_KERNEL_ID
    kernel_version: str = AGENT_KERNEL_VERSION
    adapter_id: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    runtime_artifact: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    event_schema_version: str = Field(min_length=1)
    capability_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_run_id: str = Field(min_length=1)
    model_connection_id: str | None = None
    model_connection_version: str | None = None
    model: str = Field(min_length=1)


class AgentKernelRunSnapshot(BaseModel):
    """通过 query 返回的 Run 结果、静止与绑定事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding: RuntimeBinding
    status: RuntimeStatus
    result_known: bool
    quiescent: bool
    last_event_sequence: int | None = None


class AgentKernelRuntimeAdapter(Protocol):
    """AgentKernel 后方可替换的 Runtime Adapter 接口。"""

    manifest: AgentKernelCapabilityManifest

    def new_external_run_id(self) -> str: ...

    async def start(
        self,
        request: PiRuntimeRequest,
        *,
        binding: RuntimeBinding,
        on_event: EventSink,
    ) -> PiRuntimeResult: ...

    async def resume(
        self,
        request: PiRuntimeRequest,
        *,
        binding: RuntimeBinding,
        checkpoint: PiRuntimeCheckpoint,
        on_event: EventSink,
    ) -> PiRuntimeResult: ...

    async def cancel(self, user_id: str, task_id: str, revision: int) -> None: ...


class PiAgentKernelAdapter:
    """把现有 PiRuntime 收窄到 Mangrove AgentKernel Adapter 接口。"""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        runtime_artifact = self._initial_runtime_artifact(runtime)
        capabilities = ["start", "resume", "cancel"]
        if callable(getattr(runtime, "steer", None)):
            capabilities.append("steer")
        self.manifest = AgentKernelCapabilityManifest(
            adapter_id="pi-runtime",
            adapter_version="1.0.0",
            runtime_artifact=runtime_artifact,
            protocol_version=AGENT_KERNEL_PROTOCOL_VERSION,
            event_schema_version=AGENT_KERNEL_EVENT_SCHEMA_VERSION,
            required_capabilities=("start", "resume", "cancel"),
            optional_capabilities=("steer",),
            available_capabilities=tuple(capabilities),
        )

    async def prepare_manifest(self) -> AgentKernelCapabilityManifest:
        """在创建外部 Run 前解析不可变 Runtime 内容身份。"""

        resolver = getattr(self._runtime, "resolve_runtime_artifact", None)
        if callable(resolver):
            runtime_artifact = str(await resolver()).strip()
        else:
            runtime_artifact = self._initial_runtime_artifact(self._runtime)
        if not self._artifact_is_immutable(runtime_artifact):
            raise AgentKernelCapabilityError(
                "Pi Runtime 未提供不可变的内容摘要"
            )
        self.manifest = self.manifest.model_copy(
            update={"runtime_artifact": runtime_artifact}
        )
        return self.manifest

    def new_external_run_id(self) -> str:
        return f"pi_run_{uuid.uuid4().hex[:16]}"

    def bind_candidate_verification(self, service: Any) -> None:
        bind = getattr(self._runtime, "bind_candidate_verification", None)
        if not callable(bind):
            raise AgentKernelCapabilityError(
                "Pi Runtime 未提供 CandidateVerification 绑定接缝"
            )
        bind(service)

    async def start(
        self,
        request: PiRuntimeRequest,
        *,
        binding: RuntimeBinding,
        on_event: EventSink,
    ) -> PiRuntimeResult:
        await self._assert_binding_artifact(binding)
        try:
            return await self._runtime.start(
                request,
                on_event=on_event,
                run_id=binding.external_run_id,
            )
        except Exception as exc:
            self._raise_unknown(exc)
            raise

    async def resume(
        self,
        request: PiRuntimeRequest,
        *,
        binding: RuntimeBinding,
        checkpoint: PiRuntimeCheckpoint,
        on_event: EventSink,
    ) -> PiRuntimeResult:
        if checkpoint.run_id != binding.external_run_id:
            raise AgentKernelError("Pi 恢复检查点与冻结 RuntimeBinding 不一致")
        await self._assert_binding_artifact(binding)
        try:
            return await self._runtime.resume(
                request,
                checkpoint=checkpoint,
                on_event=on_event,
            )
        except Exception as exc:
            self._raise_unknown(exc)
            raise

    async def steer(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        instruction: str,
    ) -> Any:
        steer = getattr(self._runtime, "steer", None)
        if not callable(steer):
            raise AgentKernelCapabilityError("Pi Runtime 不支持直接运行中 steer")
        return await steer(user_id, task_id, revision, instruction)

    async def cancel(self, user_id: str, task_id: str, revision: int) -> None:
        await self._runtime.cancel(user_id, task_id, revision)

    @staticmethod
    def _raise_unknown(exc: Exception) -> None:
        if "模型请求结果不确定" in str(exc):
            raise AgentKernelResultUnknownError(str(exc)) from exc

    async def _assert_binding_artifact(self, binding: RuntimeBinding) -> None:
        manifest = await self.prepare_manifest()
        if manifest.runtime_artifact != binding.runtime_artifact:
            raise AgentKernelCapabilityError(
                "Pi Runtime 内容身份与冻结 RuntimeBinding 不一致"
            )

    @staticmethod
    def _initial_runtime_artifact(runtime: Any) -> str:
        image = str(getattr(runtime, "image", "") or "").strip()
        digest = str(
            getattr(runtime, "runtime_artifact_digest", "") or ""
        ).strip()
        if image and digest:
            return f"oci-image-ref={image};content-digest={digest}"
        if image:
            return f"unresolved-oci-image-ref={image}"
        identity = f"{type(runtime).__module__}.{type(runtime).__qualname__}"
        try:
            source = inspect.getsource(type(runtime))
        except (OSError, TypeError):
            source = identity
        source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return f"python-runtime={identity};content-digest=sha256:{source_digest}"

    @staticmethod
    def _artifact_is_immutable(runtime_artifact: str) -> bool:
        marker = "content-digest=sha256:"
        if marker not in runtime_artifact:
            return False
        digest = runtime_artifact.rsplit(marker, 1)[1]
        return len(digest) == 64 and all(
            character in "0123456789abcdef" for character in digest
        )


class AgentKernel:
    """验证合同后驱动唯一 Runtime Adapter 的 Mangrove 执行 Module。"""

    def __init__(self, *, adapter: AgentKernelRuntimeAdapter, repository: Any) -> None:
        self._adapter = adapter
        self._repository = None if callable(repository) else repository
        self._repository_factory = repository if callable(repository) else None
        self._quiescent: set[tuple[str, str, int]] = set()
        self._cancel_requests: dict[
            tuple[str, str, int], asyncio.Event
        ] = {}
        self._cancelled: set[tuple[str, str, int]] = set()

    def _assert_compatible(self) -> None:
        manifest = self._adapter.manifest
        if manifest.protocol_version != AGENT_KERNEL_PROTOCOL_VERSION:
            raise AgentKernelCapabilityError(
                "Runtime Adapter 的 AgentKernel 协议版本不兼容"
            )
        if manifest.event_schema_version != AGENT_KERNEL_EVENT_SCHEMA_VERSION:
            raise AgentKernelCapabilityError(
                "Runtime Adapter 的事件 Schema 版本不兼容"
            )
        if set(manifest.required_capabilities) != _REQUIRED_ADAPTER_CAPABILITIES:
            raise AgentKernelCapabilityError(
                "Runtime Adapter 必须声明 start、resume、cancel 为必需能力"
            )
        missing = set(manifest.required_capabilities) - set(
            manifest.available_capabilities
        )
        if missing:
            raise AgentKernelCapabilityError(
                f"Runtime Adapter 缺少必需能力：{', '.join(sorted(missing))}"
            )

    async def start(
        self,
        request: PiRuntimeRequest,
        *,
        on_event: EventSink,
    ) -> PiRuntimeResult:
        """在创建外部 Run 前验证 Adapter 合同。"""

        await self._prepare_adapter_manifest()
        if self._find_binding(request.user_id, request.task_id, request.revision):
            raise AgentKernelError("同一 Run 已冻结 RuntimeBinding，不能重新启动或改绑")
        binding = self._build_binding(
            request,
            external_run_id=self._adapter.new_external_run_id(),
        )
        self._persist_binding(request, binding, adopted_existing_run=False)
        key = (request.user_id, request.task_id, request.revision)
        self._quiescent.discard(key)
        try:
            result = await self._adapter.start(
                request,
                binding=binding,
                on_event=self._persisting_sink(request, on_event),
            )
            return await self._accept_result(request, binding, result)
        except AgentKernelResultUnknownError as exc:
            if not await self._cancel_won(key):
                self._persist_failure(
                    request,
                    error_code="MODEL_OUTCOME_UNKNOWN",
                    cause=exc,
                )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not await self._cancel_won(key):
                self._persist_failure(
                    request,
                    error_code="ADAPTER_EXECUTION_FAILED",
                    cause=exc,
                )
            raise

    async def resume(
        self,
        request: PiRuntimeRequest,
        *,
        checkpoint: PiRuntimeCheckpoint,
        on_event: EventSink,
    ) -> PiRuntimeResult:
        """只沿已冻结绑定恢复同一外部 Run。"""

        await self._prepare_adapter_manifest()
        binding = self._find_binding(
            request.user_id,
            request.task_id,
            request.revision,
        )
        if binding is None:
            row = self._repo().get(
                request.user_id,
                request.task_id,
                request.revision,
            )
            if row is None or row.get("run_id") != checkpoint.run_id:
                raise AgentKernelError("历史 Run 身份无法与恢复检查点互证")
            binding = self._build_binding(
                request,
                external_run_id=checkpoint.run_id,
            )
            # 兼容 AgentKernel 引入前已经持久化的 Run：只接管原身份，不能生成新 Run。
            self._persist_binding(request, binding, adopted_existing_run=True)
        self._assert_binding_matches_manifest(binding)
        self._assert_request_matches_binding(request, binding)
        if checkpoint.run_id != binding.external_run_id:
            raise AgentKernelError("恢复检查点与冻结 RuntimeBinding 不一致")
        key = (request.user_id, request.task_id, request.revision)
        self._quiescent.discard(key)
        try:
            result = await self._adapter.resume(
                request,
                binding=binding,
                checkpoint=checkpoint,
                on_event=self._persisting_sink(request, on_event),
            )
            return await self._accept_result(request, binding, result)
        except AgentKernelResultUnknownError as exc:
            if not await self._cancel_won(key):
                self._persist_failure(
                    request,
                    error_code="MODEL_OUTCOME_UNKNOWN",
                    cause=exc,
                )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not await self._cancel_won(key):
                self._persist_failure(
                    request,
                    error_code="ADAPTER_EXECUTION_FAILED",
                    cause=exc,
                )
            raise

    async def steer(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        instruction: str,
    ) -> Any:
        """向声明支持 steer 的 Adapter 发送运行中指令。"""

        await self._prepare_adapter_manifest()
        if "steer" not in self._adapter.manifest.available_capabilities:
            raise AgentKernelCapabilityError("当前 Runtime Adapter 不支持直接 steer")
        steer = getattr(self._adapter, "steer", None)
        if not callable(steer):
            raise AgentKernelCapabilityError("Runtime Adapter 的 steer 声明与实现不一致")
        return await steer(user_id, task_id, revision, instruction)

    async def cancel(self, user_id: str, task_id: str, revision: int) -> None:
        """终止 Adapter；返回后不再接受本 Run 的迟到事件。"""

        key = (user_id, task_id, revision)
        done = asyncio.Event()
        self._cancel_requests[key] = done
        try:
            await self._adapter.cancel(user_id, task_id, revision)
            try:
                repository = self._repo()
            except SchemaNotCurrentError:
                # 治理硬停不能被未迁移的状态库阻断；真实启动
                # 流程会先经 startup preflight，此分支只保留失败关闭的硬停。
                repository = None
            if repository is not None:
                existing = repository.get(user_id, task_id, revision)
                if existing is not None:
                    repository.update(
                        user_id,
                        task_id,
                        revision,
                        status=RuntimeStatus.CANCELLED,
                        clear_failure=True,
                    )
        except BaseException:
            self._cancel_requests.pop(key, None)
            done.set()
            raise
        self._cancelled.add(key)
        self._quiescent.add(key)
        self._cancel_requests.pop(key, None)
        done.set()

    def events(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> list[dict[str, Any]]:
        """按持久 sequence 返回脱离 Adapter 私有类型的原始事件。"""

        return self._repo().list_events(user_id, task_id, revision)

    def query(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> AgentKernelRunSnapshot:
        """返回已知结果、静止状态与冻结绑定。"""

        row = self._repo().get(user_id, task_id, revision)
        if row is None:
            raise KeyError("AgentKernel Run 不存在或无权访问")
        status = row["status"]
        failure = row.get("failure") or {}
        events = self.events(user_id, task_id, revision)
        return AgentKernelRunSnapshot(
            binding=self._binding(user_id, task_id, revision),
            status=status,
            result_known=failure.get("error_code") != "MODEL_OUTCOME_UNKNOWN",
            quiescent=status in _QUIESCENT_RESULT_STATUSES,
            last_event_sequence=(events[-1]["sequence"] if events else None),
        )

    def bind_candidate_verification(self, service: Any) -> None:
        """保持 CandidateVerification 属于 Mangrove，Adapter 只接受绑定。"""

        bind = getattr(self._adapter, "bind_candidate_verification", None)
        if not callable(bind):
            raise AgentKernelCapabilityError(
                "Runtime Adapter 未提供 CandidateVerification 绑定接缝"
            )
        bind(service)

    def _persisting_sink(
        self,
        request: PiRuntimeRequest,
        downstream: EventSink,
    ) -> EventSink:
        key = (request.user_id, request.task_id, request.revision)

        async def persist(event: RuntimeEvent) -> None:
            if (
                key in self._quiescent
                or key in self._cancel_requests
                or key in self._cancelled
            ):
                return
            public_details = {
                name: value
                for name, value in event.details.items()
                if not name.startswith("_")
            }
            self._repo().append_event(
                request.user_id,
                request.task_id,
                request.revision,
                event_type=event.event_type,
                summary=event.summary,
                details=public_details,
            )
            await downstream(event)

        return persist

    async def _accept_result(
        self,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        result: PiRuntimeResult,
    ) -> PiRuntimeResult:
        key = (request.user_id, request.task_id, request.revision)
        if result.run_id != binding.external_run_id:
            raise AgentKernelError(
                "Runtime Adapter 返回的 Run 身份与冻结绑定不一致"
            )
        if await self._cancel_won(key):
            return result.model_copy(update={"status": RuntimeStatus.CANCELLED})
        self._assert_result_quiescent(result)
        update: dict[str, Any] = {
            "status": result.status,
            "clear_failure": result.failure is None,
        }
        if result.failure is not None:
            update["failure"] = result.failure
        self._repo().update(
            request.user_id,
            request.task_id,
            request.revision,
            **update,
        )
        self._quiescent.add(key)
        return result

    async def _cancel_won(self, key: tuple[str, str, int]) -> bool:
        pending = self._cancel_requests.get(key)
        if pending is not None:
            await pending.wait()
        return key in self._cancelled

    def _persist_failure(
        self,
        request: PiRuntimeRequest,
        *,
        error_code: str,
        cause: Exception,
    ) -> None:
        key = (request.user_id, request.task_id, request.revision)
        self._repo().update(
            request.user_id,
            request.task_id,
            request.revision,
            status=RuntimeStatus.FAILED,
            failure={
                "error_code": error_code,
                "cause_summary": str(cause)[:500],
            },
        )
        self._quiescent.add(key)

    def _binding(self, user_id: str, task_id: str, revision: int) -> RuntimeBinding:
        binding = self._find_binding(user_id, task_id, revision)
        if binding is None:
            raise AgentKernelError("Run 尚未冻结 RuntimeBinding")
        return binding

    def _find_binding(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> RuntimeBinding | None:
        events = self._repo().list_events(user_id, task_id, revision)
        frozen = next(
            (
                event
                for event in events
                if event["event_type"] == "kernel.binding.frozen"
            ),
            None,
        )
        if frozen is None:
            return None
        return RuntimeBinding.model_validate(frozen["details"]["binding"])

    def _build_binding(
        self,
        request: PiRuntimeRequest,
        *,
        external_run_id: str,
    ) -> RuntimeBinding:
        manifest = self._adapter.manifest
        model = request.model_connection_model or request.model
        if model is None:
            # RuntimeBinding 必须冻结用户选择的精确模型，不能用占位值掩盖缺失。
            raise AgentKernelError("Runtime 请求缺少可冻结的模型身份")
        return RuntimeBinding(
            adapter_id=manifest.adapter_id,
            adapter_version=manifest.adapter_version,
            runtime_artifact=manifest.runtime_artifact,
            protocol_version=manifest.protocol_version,
            event_schema_version=manifest.event_schema_version,
            capability_digest=manifest.digest,
            external_run_id=external_run_id,
            model_connection_id=request.model_connection_id,
            model_connection_version=request.model_connection_version,
            model=model,
        )

    def _persist_binding(
        self,
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
        *,
        adopted_existing_run: bool,
    ) -> None:
        self._repo().freeze_runtime_binding(
            request.user_id,
            request.task_id,
            request.revision,
            run_id=binding.external_run_id,
            binding=binding.model_dump(mode="json"),
            capability_manifest=self._adapter.manifest.model_dump(mode="json"),
            adopted_existing_run=adopted_existing_run,
        )

    def _repo(self) -> Any:
        if self._repository is None:
            if self._repository_factory is None:
                raise AgentKernelError("AgentKernel 缺少 Runtime Repository")
            self._repository = self._repository_factory()
        return self._repository

    async def _prepare_adapter_manifest(self) -> None:
        # 先检查静态协议/能力，避免不兼容 Adapter 触发外部解析。
        self._assert_compatible()
        prepare = getattr(self._adapter, "prepare_manifest", None)
        if callable(prepare):
            manifest = await prepare()
            if manifest != self._adapter.manifest:
                raise AgentKernelCapabilityError(
                    "Runtime Adapter 返回的能力清单与当前清单不一致"
                )
        self._assert_compatible()

    def _assert_binding_matches_manifest(self, binding: RuntimeBinding) -> None:
        manifest = self._adapter.manifest
        expected = (
            AGENT_KERNEL_ID,
            AGENT_KERNEL_VERSION,
            manifest.adapter_id,
            manifest.adapter_version,
            manifest.runtime_artifact,
            manifest.protocol_version,
            manifest.event_schema_version,
            manifest.digest,
        )
        actual = (
            binding.kernel_id,
            binding.kernel_version,
            binding.adapter_id,
            binding.adapter_version,
            binding.runtime_artifact,
            binding.protocol_version,
            binding.event_schema_version,
            binding.capability_digest,
        )
        if actual != expected:
            raise AgentKernelCapabilityError(
                "当前 Runtime Adapter 与冻结 RuntimeBinding 不一致"
            )

    @staticmethod
    def _assert_request_matches_binding(
        request: PiRuntimeRequest,
        binding: RuntimeBinding,
    ) -> None:
        requested = (
            request.model_connection_id,
            request.model_connection_version,
            request.model_connection_model or request.model,
        )
        frozen = (
            binding.model_connection_id,
            binding.model_connection_version,
            binding.model,
        )
        if requested != frozen:
            # 恢复必须沿用启动时冻结的精确模型与连接，禁止隐式 failover。
            raise AgentKernelError("恢复请求的模型或连接与冻结绑定不一致")

    @staticmethod
    def _assert_result_quiescent(result: PiRuntimeResult) -> None:
        if result.status not in _QUIESCENT_RESULT_STATUSES:
            raise AgentKernelError(
                f"Runtime Adapter 在非静止状态 {result.status.value} 提前返回"
            )
