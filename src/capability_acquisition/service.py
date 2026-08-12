# -*- coding: utf-8 -*-
"""独立能力获取状态机；调用者不感知 Docker、BuildKit 或 ORAS。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
import logging
from typing import Protocol
from urllib.parse import urlsplit

from src.conversation_steering import AcquisitionBudget, AcquisitionStatus

from .models import (
    AcquisitionCandidate,
    AcquisitionEvent,
    AcquisitionRecord,
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionSourceKind,
    PreparedCapability,
    ResolvedCandidate,
)
from .repository import AcquisitionRepository


EventSink = Callable[[AcquisitionEvent], object | Awaitable[object]]
logger = logging.getLogger(__name__)


class AcquisitionEnvironment(Protocol):
    async def claim_execution(
        self,
        request: AcquisitionRequest,
        cancel_event: asyncio.Event,
    ) -> object: ...

    async def release_execution(self, claim: object) -> None: ...

    async def recover(self, request: AcquisitionRequest) -> None: ...

    async def start(
        self,
        request: AcquisitionRequest,
        allowed_domains: tuple[str, ...],
    ) -> object: ...

    async def resolve(
        self,
        lease: object,
        candidate: AcquisitionCandidate,
    ) -> ResolvedCandidate: ...

    async def lookup(
        self,
        resolved: ResolvedCandidate,
    ) -> PreparedCapability | None: ...

    async def prepare(
        self,
        lease: object,
        resolved: ResolvedCandidate,
        budget: AcquisitionBudget,
        cancel_event: asyncio.Event,
    ) -> PreparedCapability: ...

    async def cancel(self, acquisition_id: str) -> None: ...

    async def cleanup(self, lease: object) -> None: ...


@dataclass(frozen=True, slots=True)
class SourceAuthorization:
    allowed: bool
    requires_permission: bool
    allowed_hosts: tuple[str, ...] = ()


class AcquisitionFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SourcePolicy:
    _HOSTS = {
        AcquisitionSourceKind.PYPI: ("pypi.org", "files.pythonhosted.org"),
        AcquisitionSourceKind.NPM: ("registry.npmjs.org",),
        AcquisitionSourceKind.GITHUB_RELEASE: (
            "github.com",
            "objects.githubusercontent.com",
            "githubusercontent.com",
        ),
        AcquisitionSourceKind.OFFICIAL_GIT: (
            "github.com",
            "githubusercontent.com",
        ),
    }

    def __init__(
        self,
        permission_checker: Callable[[str, str, str], bool] | None = None,
        registered_source_resolver: (
            Callable[[str, str, str], tuple[str, ...] | None] | None
        ) = None,
    ) -> None:
        self._permission_checker = permission_checker or (
            lambda _owner_id, _grant_id, _uri: False
        )
        self._registered_source_resolver = registered_source_resolver or (
            lambda _owner_id, _registration_id, _uri: None
        )

    def _has_permission(
        self,
        request: AcquisitionRequest,
        candidate: AcquisitionCandidate,
    ) -> bool:
        grant_id = candidate.permission_grant_id
        return bool(
            grant_id
            and self._permission_checker(
                request.owner_id,
                grant_id,
                candidate.source_uri,
            )
        )

    def authorize(
        self,
        request: AcquisitionRequest,
        candidate: AcquisitionCandidate,
    ) -> SourceAuthorization:
        parsed = urlsplit(candidate.source_uri)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if candidate.kind is AcquisitionSourceKind.UNKNOWN_URL:
            permitted = self._has_permission(request, candidate)
            return SourceAuthorization(
                allowed=permitted,
                requires_permission=not permitted,
                allowed_hosts=(host,) if permitted else (),
            )
        if candidate.kind in {
            AcquisitionSourceKind.REGISTERED_MCP,
            AcquisitionSourceKind.REGISTERED_SKILL,
        }:
            registration_id = candidate.source_registration_id
            assert registration_id is not None
            resolved = self._registered_source_resolver(
                request.owner_id,
                registration_id,
                candidate.source_uri,
            )
            hosts = tuple(item.casefold().rstrip(".") for item in (resolved or ()))
            return SourceAuthorization(
                allowed=bool(hosts) and host in hosts,
                requires_permission=False,
                allowed_hosts=hosts,
            )
        hosts = self._HOSTS.get(candidate.kind, ())
        return SourceAuthorization(
            allowed=any(host == item or host.endswith(f".{item}") for item in hosts),
            requires_permission=False,
            allowed_hosts=hosts,
        )

    def allows(
        self,
        request: AcquisitionRequest,
        candidate: AcquisitionCandidate,
        uri: str,
    ) -> bool:
        parsed = urlsplit(uri)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        host = parsed.hostname.casefold().rstrip(".")
        authorization = self.authorize(request, candidate)
        if not authorization.allowed:
            return False
        if candidate.kind is AcquisitionSourceKind.UNKNOWN_URL:
            return uri == candidate.source_uri
        allowed = authorization.allowed_hosts
        return any(host == item or host.endswith(f".{item}") for item in allowed)


class CapabilityAcquisition:
    """用两个方法隐藏来源策略、预算、恢复、缓存和清理。"""

    _TERMINAL = {
        AcquisitionStatus.READY,
        AcquisitionStatus.FAILED,
        AcquisitionStatus.CANCELLED,
    }

    def __init__(
        self,
        repository: AcquisitionRepository,
        environment: AcquisitionEnvironment,
        source_policy: SourcePolicy,
        *,
        platform_max_concurrency: int = 4,
    ) -> None:
        self._repository = repository
        self._environment = environment
        self._source_policy = source_policy
        self._platform_max_concurrency = platform_max_concurrency
        self._semaphore = asyncio.Semaphore(platform_max_concurrency)
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._candidate_locks: dict[str, asyncio.Lock] = {}

    def _candidate_key(self, candidate: AcquisitionCandidate) -> str:
        return "\0".join(
            (
                candidate.kind.value,
                candidate.source_uri,
                candidate.version,
                candidate.expected_sha256 or "",
            )
        )

    def _cancel_requested(
        self,
        acquisition_id: str,
        local_event: asyncio.Event,
    ) -> bool:
        record = self._repository.get(acquisition_id)
        return local_event.is_set() or bool(
            record is not None and record.cancel_requested
        )

    async def _emit(
        self,
        record: AcquisitionRecord,
        status: AcquisitionStatus,
        summary: str,
        sink: EventSink,
    ) -> AcquisitionRecord:
        event = AcquisitionEvent(
            acquisition_id=record.request.acquisition_id,
            owner_id=record.request.owner_id,
            sequence=len(record.events) + 1,
            status=status,
            summary=summary,
        )
        record = record.model_copy(
            update={"status": status, "events": (*record.events, event)}
        )
        saved = self._repository.save(record)
        if saved != record:
            return saved
        delivered = sink(event)
        if inspect.isawaitable(delivered):
            await delivered
        return record

    def _finish(
        self,
        record: AcquisitionRecord,
        result: AcquisitionResult,
    ) -> AcquisitionResult:
        saved = self._repository.save(
            record.model_copy(update={"status": result.status, "result": result})
        )
        return saved.result or result

    async def _fail(
        self,
        record: AcquisitionRecord,
        *,
        code: str,
        summary: str,
        sink: EventSink,
    ) -> AcquisitionResult:
        record = await self._emit(
            record,
            AcquisitionStatus.FAILED,
            summary,
            sink,
        )
        return self._finish(
            record,
            AcquisitionResult(
                acquisition_id=record.request.acquisition_id,
                owner_id=record.request.owner_id,
                status=AcquisitionStatus.FAILED,
                failure_code=code,
            ),
        )

    async def acquire(
        self,
        request: AcquisitionRequest,
        on_event: EventSink,
    ) -> AcquisitionResult:
        if request.budget.max_concurrency > self._platform_max_concurrency:
            raise ValueError("请求并发预算超过平台上限")
        lock = self._locks.setdefault(request.acquisition_id, asyncio.Lock())
        async with lock:
            record = self._repository.create(request)
            if record.result is not None and record.status in self._TERMINAL:
                return record.result
            cancel_event = self._cancel_events.setdefault(
                request.acquisition_id,
                asyncio.Event(),
            )
            execution_claim: object | None = None
            try:
                async with self._semaphore:
                    async with asyncio.timeout(request.budget.max_duration_seconds):
                        execution_claim = await self._environment.claim_execution(
                            request,
                            cancel_event,
                        )
                        latest = self._repository.get(request.acquisition_id)
                        if (
                            latest is not None
                            and latest.result is not None
                            and latest.status in self._TERMINAL
                        ):
                            return latest.result
                        if latest is not None:
                            record = latest
                        if record.cancel_requested:
                            raise asyncio.CancelledError
                        if (
                            latest is not None
                            and latest.status is not AcquisitionStatus.AWAITING_PERMISSION
                        ):
                            await self._environment.recover(request)
                        record = await self._emit(
                            record,
                            AcquisitionStatus.DISCOVERING,
                            "正在检查能力来源与缓存",
                            on_event,
                        )
                        last_error: Exception | None = None
                        for candidate in request.candidates:
                            authorization = self._source_policy.authorize(
                                request,
                                candidate,
                            )
                            if authorization.requires_permission:
                                record = await self._emit(
                                    record,
                                    AcquisitionStatus.AWAITING_PERMISSION,
                                    "陌生来源需要用户确认",
                                    on_event,
                                )
                                return self._finish(
                                    record,
                                    AcquisitionResult(
                                        acquisition_id=request.acquisition_id,
                                        owner_id=request.owner_id,
                                        status=AcquisitionStatus.AWAITING_PERMISSION,
                                        failure_code="SOURCE_PERMISSION_REQUIRED",
                                        message="陌生 URL 尚未获得权限 Grant",
                                    ),
                                )
                            if not authorization.allowed:
                                last_error = AcquisitionFailure(
                                    "SOURCE_NOT_ALLOWED",
                                    "能力来源不在批准策略内",
                                )
                                continue
                            lease: object | None = None
                            try:
                                if self._cancel_requested(
                                    request.acquisition_id,
                                    cancel_event,
                                ):
                                    raise asyncio.CancelledError
                                lease = await self._environment.start(
                                    request,
                                    authorization.allowed_hosts,
                                )
                                resolved = await self._environment.resolve(
                                    lease,
                                    candidate,
                                )
                                if self._cancel_requested(
                                    request.acquisition_id,
                                    cancel_event,
                                ):
                                    raise asyncio.CancelledError
                                if not self._source_policy.allows(
                                    request,
                                    candidate,
                                    resolved.final_uri,
                                ):
                                    raise AcquisitionFailure(
                                        "FINAL_SOURCE_NOT_ALLOWED",
                                        "重定向后的最终来源不在批准策略内",
                                    )
                                candidate_lock = self._candidate_locks.setdefault(
                                    self._candidate_key(candidate),
                                    asyncio.Lock(),
                                )
                                async with candidate_lock:
                                    prepared = await self._environment.lookup(resolved)
                                    if prepared is None:
                                        record = await self._emit(
                                            record,
                                            AcquisitionStatus.ACQUIRING,
                                            "正在获取冻结来源",
                                            on_event,
                                        )
                                        record = await self._emit(
                                            record,
                                            AcquisitionStatus.BUILDING,
                                            "正在隔离构建能力制品",
                                            on_event,
                                        )
                                        retry_error: Exception | None = None
                                        for attempt in range(
                                            request.budget.max_retries_per_source + 1
                                        ):
                                            try:
                                                prepared = await self._environment.prepare(
                                                    lease,
                                                    resolved,
                                                    request.budget,
                                                    cancel_event,
                                                )
                                                break
                                            except asyncio.CancelledError:
                                                raise
                                            except Exception as exc:
                                                if self._cancel_requested(
                                                    request.acquisition_id,
                                                    cancel_event,
                                                ):
                                                    raise asyncio.CancelledError
                                                retry_error = exc
                                                if attempt < request.budget.max_retries_per_source:
                                                    record = await self._emit(
                                                        record,
                                                        AcquisitionStatus.BUILDING,
                                                        (
                                                            f"候选 {candidate.candidate_id} "
                                                            f"第 {attempt + 1} 次尝试失败，准备重试"
                                                        ),
                                                        on_event,
                                                    )
                                        else:
                                            assert retry_error is not None
                                            raise retry_error
                                if prepared.download_bytes > request.budget.max_download_bytes:
                                    raise AcquisitionFailure(
                                        "DOWNLOAD_BUDGET_EXCEEDED",
                                        "下载量达到获取预算",
                                    )
                                if prepared.unpacked_bytes > request.budget.max_unpacked_bytes:
                                    raise AcquisitionFailure(
                                        "UNPACKED_BUDGET_EXCEEDED",
                                        "解包量达到获取预算",
                                    )
                                if self._cancel_requested(
                                    request.acquisition_id,
                                    cancel_event,
                                ):
                                    raise asyncio.CancelledError
                                record = await self._emit(
                                    record,
                                    AcquisitionStatus.VALIDATING,
                                    "正在核对 digest 与隔离门",
                                    on_event,
                                )
                                # READY 只能在 Lease、进程和临时目录全部回收后出现。
                                await self._environment.cleanup(lease)
                                lease = None
                                result = AcquisitionResult(
                                    acquisition_id=request.acquisition_id,
                                    owner_id=request.owner_id,
                                    status=AcquisitionStatus.READY,
                                    pack_ref=(
                                        f"{prepared.pack_id}@{prepared.version}@"
                                        f"{prepared.digest}"
                                    ),
                                    digest=prepared.digest,
                                    reused=prepared.reused,
                                )
                                ready_event = AcquisitionEvent(
                                    acquisition_id=request.acquisition_id,
                                    owner_id=request.owner_id,
                                    sequence=len(record.events) + 1,
                                    status=AcquisitionStatus.READY,
                                    summary="能力制品已冻结，可供后续任务复用",
                                )
                                ready_record = record.model_copy(
                                    update={
                                        "status": AcquisitionStatus.READY,
                                        "events": (*record.events, ready_event),
                                        "result": result,
                                    }
                                )
                                record = self._repository.finalize_ready(ready_record)
                                if record is not None and record != ready_record:
                                    if record.result is not None:
                                        return record.result
                                    raise RuntimeError("终态记录缺少结果")
                                if record is None or cancel_event.is_set():
                                    latest = self._repository.get(request.acquisition_id)
                                    if latest is not None:
                                        record = latest
                                    raise asyncio.CancelledError
                                try:
                                    delivered = on_event(ready_event)
                                    if inspect.isawaitable(delivered):
                                        await delivered
                                except Exception:
                                    # READY 已原子持久化；投影失败由刷新恢复，不能反转业务终态。
                                    logger.warning(
                                        "能力获取 READY 事件投影失败",
                                        exc_info=True,
                                    )
                                return result
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                if self._cancel_requested(
                                    request.acquisition_id,
                                    cancel_event,
                                ):
                                    raise asyncio.CancelledError
                                last_error = exc
                                record = await self._emit(
                                    record,
                                    AcquisitionStatus.DISCOVERING,
                                    f"候选 {candidate.candidate_id} 未通过，已记录本次尝试",
                                    on_event,
                                )
                            finally:
                                if lease is not None:
                                    await self._environment.cleanup(lease)
                        assert last_error is not None
                        raise last_error
            except (asyncio.CancelledError, TimeoutError):
                latest = self._repository.get(request.acquisition_id)
                cancelled = cancel_event.is_set() or bool(
                    latest is not None and latest.cancel_requested
                )
                if latest is not None:
                    record = latest
                status = (
                    AcquisitionStatus.CANCELLED
                    if cancelled
                    else AcquisitionStatus.FAILED
                )
                code = "CANCELLED" if cancelled else "DURATION_BUDGET_EXCEEDED"
                record = await self._emit(record, status, "能力获取已停止", on_event)
                return self._finish(
                    record,
                    AcquisitionResult(
                        acquisition_id=request.acquisition_id,
                        owner_id=request.owner_id,
                        status=status,
                        failure_code=code,
                    ),
                )
            except AcquisitionFailure as exc:
                return await self._fail(
                    record,
                    code=exc.code,
                    summary=str(exc),
                    sink=on_event,
                )
            except Exception as exc:
                record = await self._emit(
                    record,
                    AcquisitionStatus.FAILED,
                    "能力获取失败并已关闭环境",
                    on_event,
                )
                return self._finish(
                    record,
                    AcquisitionResult(
                        acquisition_id=request.acquisition_id,
                        owner_id=request.owner_id,
                        status=AcquisitionStatus.FAILED,
                        failure_code="ACQUISITION_FAILED",
                        message=str(exc)[:500],
                    ),
                )
            finally:
                if execution_claim is not None:
                    await self._environment.release_execution(execution_claim)
                self._cancel_events.pop(request.acquisition_id, None)

    async def cancel(self, acquisition_id: str, owner_id: str) -> None:
        record = self._repository.request_cancel(acquisition_id, owner_id)
        if record.status in self._TERMINAL:
            return
        event = self._cancel_events.setdefault(acquisition_id, asyncio.Event())
        event.set()
        await self._environment.cancel(acquisition_id)
