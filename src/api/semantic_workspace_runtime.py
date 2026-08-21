# -*- coding: utf-8 -*-
"""Phase 4B 批次 7：单机持久化工作台任务编排。"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
import time
from typing import Any

from src.agentic_runtime.models import (
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    RuntimeEvent,
    RuntimeStatus,
    RuntimeVersion,
    SourceInput,
    VerificationStatus,
)
from src.agentic_runtime.candidate_verifier import (
    BrokerSemanticJudge,
    CandidateVerifier,
    LocalModelSemanticJudge,
)
from src.agentic_runtime.pi_runtime import PiRuntime
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_store
from src.capability_catalog import DefaultCapabilityMounts
from src.capability_host import CapabilityHost
from src.api.routes.semantic_bindings import _run_and_save as bind_and_save
from src.api.routes.semantic_harness import (
    HarnessRunCreateIn,
    create_run_record,
    invoke_run_record,
)
from src.api.routes.semantic_plans import (
    SemanticCompileIn,
    _compile_request,
    _run_and_save as compile_and_save,
)
from src.config.settings import settings
from src.conversation_steering import (
    ConversationSteering,
    SqliteSteeringRepository,
)
from src.delivery_publishing.models import PublicationGate
from src.delivery_publishing.pi_adapter import PiCandidateAdapter
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher
from src.semantic_harness.compiler_models import ClarificationResolution
from src.semantic_harness.harness_models import HarnessResume
from src.semantic_harness.inspectors import UploadSourceInspector
from src.semantic_harness.models import SemanticTaskPlan
from src.observability.workspace_telemetry import (
    workspace_stage_span,
    workspace_task_span,
)
from src.services.upload_store import UploadStore
from src.model_connections import get_default_broker


_TERMINAL_STATUSES = {
    "completed",
    "candidate_ready",
    "failed",
    "cancelled",
}
# 运行期治理监督的投影检查节奏（秒）。
RUNTIME_GATE_POLL_SECONDS = 30


class _GateViolationAbort(RuntimeError):
    """运行期治理门命中：任务已标记取消，调用方不得再覆盖状态。"""


def _capability_selections_table_exists() -> bool:
    """只读检查目录冻结选择表是否存在；监督路径零 DDL。"""
    import sqlite3

    if not Path(settings.webui_db_path).is_file():
        return False
    with sqlite3.connect(settings.webui_db_path, timeout=30) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='capability_selections'"
        ).fetchone()
    return row is not None
_HEAVY_FORMATS = {"docx", "pdf", "pptx"}
_HEAVY_SUFFIXES = {
    ".pdf",
    ".docx",
    ".pptx",
    ".html",
    ".htm",
    ".md",
    ".markdown",
    ".txt",
    ".xml",
}


class _RevisionSwitchAtSafePoint(Exception):
    """内部控制流：当前原子工具已结束，可以切换冻结版本。"""


def _upload_store() -> UploadStore:
    return UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )


def _platform_signing_runtime_factory():
    """#13 装载验签的签名运行时；延迟装配避免 API 模块装载环。"""
    from src.api.capability_governance_runtime import (
        get_platform_signing_runtime,
    )

    return get_platform_signing_runtime()


def _platform_oras_executable() -> str:
    """平台 Layout 物化用的锁定 ORAS 路径；延迟装配避免 API 模块装载环。"""
    from src.api.capability_governance_runtime import (
        get_locked_signing_toolchain,
    )

    return str(get_locked_signing_toolchain().oras_executable)


def _resolve_actor_role(owner_id: str) -> str:
    """装载门的真实角色解析；未知角色失败关闭为普通用户。"""
    user = get_store().get_user(owner_id)
    role = str((user or {}).get("role") or "user")
    if role == "super_admin":
        return "superadmin"
    return role if role in {"user", "admin"} else "user"


def _is_heavy(task: dict[str, Any]) -> bool:
    if set(task["output_formats"]) & _HEAVY_FORMATS:
        return True
    store = _upload_store()
    for upload_id in task["upload_ids"]:
        with suppress(Exception):
            item = store.resolve(task.get("user_id", ""), upload_id)
            if Path(item.original_name).suffix.lower() in _HEAVY_SUFFIXES:
                return True
    return False


def _pi_has_user_output(runtime: dict[str, Any]) -> bool:
    """工作区存在不等于产出了结果；只认非空的用户候选文件。"""

    workspace_root = str(runtime.get("workspace_root") or "").strip()
    if not workspace_root:
        return False
    output_dir = Path(workspace_root) / "output"
    with suppress(OSError):
        return any(
            path.is_file()
            and not path.is_symlink()
            and path.name != "candidate-manifest.json"
            and path.stat().st_size > 0
            for path in output_dir.iterdir()
        )
    return False


class SemanticWorkspaceManager:
    """两个普通 worker + 一个重任务信号量；不引入外部队列服务。"""

    def __init__(
        self,
        *,
        pi_runtime: PiRuntime | None = None,
    ) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._maintenance: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}
        self._queued: set[str] = set()
        self._deferred_requeue: set[str] = set()
        self._heavy = asyncio.Semaphore(1)
        self._pi_runtime = pi_runtime or PiRuntime(
            capability_mount_resolver=DefaultCapabilityMounts(
                db_path=settings.webui_db_path,
                oci_layout_path=settings.capability_oci_layout_path,
                mount_root=settings.capability_mount_cache_path,
                platform_oci_layout_path=(
                    settings.capability_platform_oci_layout_path
                ),
                platform_oras_executable_factory=_platform_oras_executable,
                platform_signing_public_key_path=(
                    settings.capability_platform_signing_public_key
                ),
                signing_runtime_factory=_platform_signing_runtime_factory,
                actor_role_resolver=_resolve_actor_role,
            ),
            capability_host=(
                CapabilityHost(
                    image=settings.pi_capability_host_image,
                    execution_root=(
                        Path(settings.semantic_execution_root)
                        / "capability-hosts"
                    ),
                )
                if settings.pi_capability_host_enabled
                else None
            ),
        )

    def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(
                self._worker(index),
                name=f"semantic-workspace-worker-{index}",
            )
            for index in range(2)
        ]
        store = get_store()
        store.purge_expired_semantic_workspace_tasks()
        self._maintenance = asyncio.create_task(
            self._maintenance_loop(),
            name="semantic-workspace-maintenance",
        )
        # 恢复必须由 Web 服务进程接管，确保 Runtime 与文档 Relay 共享同一 Grant 域。
        for task in store.list_pending_semantic_workspace_tasks():
            if task["status"] == "cancelling":
                store.update_semantic_workspace_task(
                    task["user_id"],
                    task["task_id"],
                    status="cancelled",
                    cancel_requested=True,
                )
                continue
            store.update_semantic_workspace_task(
                task["user_id"],
                task["task_id"],
                status="queued",
            )
            self.enqueue(task["user_id"], task["task_id"])

    async def stop(self) -> None:
        for task in self._active.values():
            task.cancel()
        for task in self._workers:
            task.cancel()
        if self._maintenance is not None:
            self._maintenance.cancel()
        tasks = [*self._active.values(), *self._workers]
        if self._maintenance is not None:
            tasks.append(self._maintenance)
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._active.clear()
        self._workers.clear()
        self._maintenance = None
        self._queued.clear()
        self._deferred_requeue.clear()

    async def _maintenance_loop(self) -> None:
        """每小时清理一次到期回收站记录。"""
        while True:
            await asyncio.sleep(3600)
            get_store().purge_expired_semantic_workspace_tasks()

    async def _selection_has_capabilities(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> bool:
        """运行期监督的前置短路：无能力任务零负担（AC6）。"""
        from src.capability_catalog import (
            CapabilityCatalog,
            CatalogActor,
            SqliteCapabilityCatalogRepository,
        )

        if not _capability_selections_table_exists():
            # 零 DDL 读取路径：目录表不存在即无冻结选择，无能力任务。
            return False
        catalog = CapabilityCatalog(
            SqliteCapabilityCatalogRepository(
                settings.webui_db_path,
                initialize_schema=False,
            )
        )
        selection = catalog.resolve_selection(
            CatalogActor(owner_id=user_id, role=_resolve_actor_role(user_id)),
            task_id=task_id,
            revision=revision,
        )
        return bool(selection is not None and selection.pack_refs)

    async def _runtime_gate_violation(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> bool:
        """运行期只读投影检查：隔离或撤销返回 True；不写事件、不写投影。"""
        from src.capability_catalog import (
            CapabilityCatalog,
            CatalogActor,
            SqliteCapabilityCatalogRepository,
        )
        from src.capability_governance import (
            CapabilityEligibility,
            CapabilityGovernance,
            CapabilityLifecycle,
            SqliteCapabilityGovernanceRepository,
        )

        if not _capability_selections_table_exists():
            # 零 DDL 读取路径：目录表不存在即无冻结选择，无违规可判定。
            return False
        catalog = CapabilityCatalog(
            SqliteCapabilityCatalogRepository(
                settings.webui_db_path,
                initialize_schema=False,
            )
        )
        actor = CatalogActor(
            owner_id=user_id, role=_resolve_actor_role(user_id)
        )
        selection = catalog.resolve_selection(
            actor,
            task_id=task_id,
            revision=revision,
        )
        if selection is None:
            return False
        governance = CapabilityGovernance(
            catalog,
            SqliteCapabilityGovernanceRepository(settings.webui_db_path),
        )
        for ref in selection.pack_refs:
            pack = catalog.resolve_pack(actor, ref.pack_id, ref.version)
            if pack is None or pack.digest != ref.digest:
                # 身份失配是硬门；运行中失配同样失败关闭。
                return True
            projection = governance.runtime_projection_for_pack(pack)
            if projection.lifecycle is CapabilityLifecycle.REVOKED:
                return True
            if projection.eligibility is CapabilityEligibility.QUARANTINED:
                return True
        return False

    async def _supervise_runtime_gate(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        execution: asyncio.Future,
    ) -> bool:
        """运行期治理监督：命中隔离/撤销 → 停容器与 Sidecar + 标记取消。

        返回 True 表示硬门命中（已标记取消并取消执行）；False 表示执行已
        正常结束。「当前原子调用完成后停止后续调用」由 Sidecar 停止提供；
        违反硬门时立即取消并禁止发布 Candidate/Delivery（复用 _mark_cancelled）。
        """
        while True:
            await asyncio.sleep(RUNTIME_GATE_POLL_SECONDS)
            if execution.done():
                return False
            try:
                violated = await self._runtime_gate_violation(
                    user_id,
                    task_id,
                    revision,
                )
            except Exception:
                # 投影读取异常不是确定性违反：跳过本轮继续监督，
                # 避免数据库瞬时故障误杀正常任务。
                continue
            if not violated:
                continue
            # 硬门命中：先停容器与 Sidecar（阻断后续能力调用），
            # 再标记取消，最后取消执行协程并等待其清理收尾。
            try:
                await self._pi_runtime.cancel(user_id, task_id, revision)
            except Exception as error:
                # 容器清理失败不能掩盖治理取消事实；留痕供审计。
                get_store().append_semantic_workspace_event(
                    user_id,
                    task_id,
                    stage="cancelled",
                    event_type="gate_cancel_cleanup_failed",
                    summary="治理门取消：容器清理失败",
                    details={
                        "error": str(error) or type(error).__name__,
                    },
                )
            self._mark_cancelled(user_id, task_id, revision)
            execution.cancel()
            with suppress(asyncio.CancelledError):
                await execution
            return True

    async def _await_with_gate_supervision(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        execution: asyncio.Future,
    ) -> object:
        """并发等待执行与运行期监督；无能力任务直接等待执行。"""
        if not await self._selection_has_capabilities(
            user_id,
            task_id,
            revision,
        ):
            return await execution
        supervisor = asyncio.ensure_future(
            self._supervise_runtime_gate(
                user_id,
                task_id,
                revision,
                execution,
            )
        )
        try:
            done, _pending = await asyncio.wait(
                {execution, supervisor},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not supervisor.done():
                supervisor.cancel()
            with suppress(asyncio.CancelledError):
                await supervisor
        if supervisor in done:
            # 监督先完成即监督优先：命中硬门（True）或监督异常都失败关闭，
            # 即使 cancel 期间执行恰好正常完成也不返回其结果（竞态防护）。
            try:
                hit = supervisor.result()
            except BaseException:
                hit = True
            if hit:
                raise _GateViolationAbort(
                    "能力运行期治理门触发：已被隔离或撤销"
                )
        return execution.result()

    def enqueue(self, user_id: str, task_id: str) -> None:
        active = self._active.get(task_id)
        if active is not None and active.done():
            self._active.pop(task_id, None)
        if task_id in self._queued or task_id in self._active:
            return
        self._queued.add(task_id)
        self._queue.put_nowait((user_id, task_id))

    async def cancel(self, user_id: str, task_id: str) -> dict[str, Any]:
        store = get_store()
        task = store.get_semantic_workspace_task(user_id, task_id)
        if task is None:
            raise KeyError("工作台任务不存在或无权访问")
        if task["status"] in _TERMINAL_STATUSES:
            return task
        running = self._active.get(task_id)
        if running is None:
            self._queued.discard(task_id)
            # 排队阶段也必须同步 vNext Run 状态，否则详情会同时显示
            # “任务已取消”和“Runtime 仍在排队”两种相互冲突的事实。
            self._mark_cancelled(
                user_id,
                task_id,
                task["active_revision"],
            )
            return (
                store.get_semantic_workspace_task(user_id, task_id)
                or task
            )
        saved = store.update_semantic_workspace_task(
            user_id,
            task_id,
            status="cancelling",
            cancel_requested=True,
        )
        runtime = AgenticRuntimeRepository(
            settings.webui_db_path
        ).get(user_id, task_id, task["active_revision"])
        if (
            runtime is not None
            and runtime["runtime_version"] is RuntimeVersion.PI
        ):
            # 先显式终止容器，再取消编排协程；不能依赖 CancelledError
            # 恰好传播到底层，否则第三方 Adapter 可能留下继续运行的子进程。
            await self._pi_runtime.cancel(
                user_id,
                task_id,
                task["active_revision"],
            )
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running
        return (
            store.get_semantic_workspace_task(user_id, task_id)
            or saved
        )

    async def answer(
        self,
        user_id: str,
        task_id: str,
        answer: str,
    ) -> dict[str, Any]:
        store = get_store()
        task = store.get_semantic_workspace_task(user_id, task_id)
        if task is None:
            raise KeyError("工作台任务不存在或无权访问")
        question = task["question"]
        if task["status"] != "needs_input" or not question:
            raise ValueError("当前任务没有待回答问题")
        kind = question.get("kind")
        allowed = {
            str(option["value"])
            for option in question.get("options", [])
        }
        if (
            allowed
            and answer not in allowed
            and not question.get("allow_free_text", False)
        ):
            raise ValueError("回答不在当前允许选项中")

        if kind == "external":
            if answer == "cancel":
                return await self.cancel(user_id, task_id)
            changes: dict[str, Any] = {
                "status": "queued",
                "question": None,
                "cancel_requested": False,
            }
            if answer == "confirm":
                changes["external_api_confirmed"] = True
            elif answer == "local":
                changes["provider"] = "local"
                changes["model"] = None
                changes["external_api_confirmed"] = False
            else:
                raise ValueError("外发问题只接受确认、本地执行或取消")
            saved = store.update_semantic_workspace_task(
                user_id, task_id, **changes
            )
        elif kind == "plan":
            objective = (
                f"{task['objective_text']}\n用户补充：{answer.strip()}"
            )
            next_question = dict(question)
            next_question["answer"] = answer.strip()
            saved = store.update_semantic_workspace_task(
                user_id,
                task_id,
                objective_text=objective,
                status="queued",
                question=next_question,
                cancel_requested=False,
            )
        elif kind in {"binding", "harness"}:
            next_question = dict(question)
            next_question["answer"] = answer
            saved = store.update_semantic_workspace_task(
                user_id,
                task_id,
                status="queued",
                question=next_question,
                cancel_requested=False,
            )
        else:
            raise ValueError("未知的工作台问题类型")
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="needs_input",
            event_type="question_answered",
            summary="已收到补充信息，继续执行",
        )
        self.enqueue(user_id, task_id)
        return saved

    def _build_retry_semantic_judge(
        self,
        request: PiRuntimeRequest,
        run_id: str,
    ):
        if request.model_connection_id is not None:
            assert request.model_connection_version is not None
            return BrokerSemanticJudge(
                broker=get_default_broker(),
                owner_user_id=request.user_id,
                connection_id=request.model_connection_id,
                connection_version=request.model_connection_version,
                model_id=request.model_connection_model,
                task_id=request.task_id,
                revision=request.revision,
                run_id=run_id,
            )
        assert request.model is not None
        assert request.base_url is not None
        assert request.api_key is not None
        return LocalModelSemanticJudge(
            model=request.model,
            base_url=request.base_url,
            api_key=request.api_key,
            timeout_seconds=180,
        )

    async def retry_candidate_verification(
        self,
        user_id: str,
        task_id: str,
    ) -> None:
        """复用已通过的文件/来源门，只重跑瞬时失败的语义验证和发布。"""

        store = get_store()
        task = store.get_semantic_workspace_task(user_id, task_id)
        if task is None:
            raise KeyError("工作台任务不存在或无权访问")
        revision = int(task["active_revision"])
        repository = AgenticRuntimeRepository(settings.webui_db_path)
        runtime = repository.get(user_id, task_id, revision)
        if (
            task["status"] != "candidate_ready"
            or runtime is None
            or runtime["runtime_version"] is not RuntimeVersion.PI
            or runtime["status"] is not RuntimeStatus.CANDIDATE_READY
            or not runtime["candidates"]
            or runtime["verification"] is None
            or runtime["verification"].status is not VerificationStatus.INCONCLUSIVE
        ):
            raise ValueError("当前任务没有可重新验证的候选")
        if not runtime["request"] or not runtime["run_id"] or not runtime["workspace_root"]:
            raise ValueError("候选缺少冻结运行信息，不能重新验证")
        request_values = dict(runtime["request"])
        if not request_values.get("model_connection_id"):
            # 本地直连密钥从不落库；重验时只恢复固定占位值。
            request_values["api_key"] = "local-runtime"
        request = PiRuntimeRequest.model_validate(request_values)
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="verify",
            event_type="candidate_verification_retry_started",
            summary="正在重新验证现有候选，不会重新读取来源或生成文件",
            details={"formal_delivery": False},
        )
        verifier = CandidateVerifier(
            semantic_judge=self._build_retry_semantic_judge(
                request,
                runtime["run_id"],
            )
        )
        verification = await verifier.retry_semantic_verification(
            request=request,
            candidates=runtime["candidates"],
            manifest_path=(
                Path(runtime["workspace_root"])
                / "output"
                / "candidate-manifest.json"
            ),
            previous_report=runtime["verification"],
        )
        repository.update(
            user_id,
            task_id,
            revision,
            candidates=runtime["candidates"],
            verification=verification,
        )
        if verification.status is not VerificationStatus.PASSED:
            store.update_semantic_workspace_task(
                user_id,
                task_id,
                summary=verification.summary,
            )
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="verify",
                event_type="candidate_verification_retry_inconclusive",
                summary=verification.summary,
                details={
                    "verification_status": verification.status.value,
                    "formal_delivery": False,
                },
            )
            return
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="verify",
            event_type="candidate_verified",
            summary="候选已通过重新验证",
            details={
                "candidate_count": len(runtime["candidates"]),
                "verification_status": "passed",
                "formal_delivery": False,
            },
        )
        await self._publish_verified_candidates(
            user_id=user_id,
            task_id=task_id,
            revision=revision,
            repository=repository,
            upload_store=_upload_store(),
        )

    async def _publish_verified_candidates(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        repository: AgenticRuntimeRepository,
        upload_store: UploadStore,
    ) -> None:
        store = get_store()
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="deliver",
            event_type="stage_started",
            summary="正在独立重开候选并发布正式交付",
        )
        adapter = PiCandidateAdapter(
            runtime_repository=repository,
            workspace_store=store,
            upload_store=upload_store,
        )
        command = adapter.build_command(
            owner_id=user_id,
            task_id=task_id,
            revision=revision,
        )

        def publication_gate(_command) -> PublicationGate:
            current = store.get_semantic_workspace_task(user_id, task_id)
            # Rollout P0 状态尚未实施；保留显式门位，不能由 Agent 自行传值。
            return PublicationGate(
                cancel_requested=bool(
                    current and current.get("cancel_requested")
                ),
                p0_blocked=False,
            )

        publisher = DeliveryPublisher(
            repository=DeliveryPublishingRepository(settings.webui_db_path),
            output_root=Path(settings.semantic_execution_root),
            candidate_resolver=adapter.resolve_candidates,
            gate_reader=publication_gate,
        )
        try:
            delivery = await asyncio.to_thread(
                publisher.publish,
                command,
                actor_id=user_id,
            )
        except Exception as exc:
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="deliver",
                event_type="delivery_failed",
                summary=f"正式交付发布失败：{str(exc)[:300]}",
                details={"formal_delivery": False},
            )
            raise ValueError(
                f"正式交付发布失败：{str(exc) or exc.__class__.__name__}"
            ) from exc
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            status="completed",
            summary=f"候选通过独立验证，已发布 {len(delivery.outputs)} 个正式文件",
            error=None,
            failure=None,
            question=None,
            cancel_requested=False,
        )
        store.update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            status="completed",
            summary="候选已通过独立验证并发布正式交付",
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="deliver",
            event_type="task_completed",
            summary=f"独立 QA 通过，已发布 {len(delivery.outputs)} 个正式文件",
            details={
                "delivery_id": delivery.delivery_id,
                "publication_key": command.publication_key,
                "formal_delivery": True,
            },
        )

    async def _worker(self, _index: int) -> None:
        while True:
            user_id, task_id = await self._queue.get()
            self._queued.discard(task_id)
            task = get_store().get_semantic_workspace_task(
                user_id, task_id
            )
            if (
                task is None
                or task["deleted_at"] is not None
                or task["status"] in _TERMINAL_STATUSES
                or task["cancel_requested"]
            ):
                self._queue.task_done()
                continue
            try:
                if _is_heavy({**task, "user_id": user_id}):
                    async with self._heavy:
                        await self._launch_job(user_id, task_id)
                else:
                    await self._launch_job(user_id, task_id)
            finally:
                self._queue.task_done()

    async def _launch_job(self, user_id: str, task_id: str) -> None:
        """取得重任务令牌后再启动，避免任务越过并发限制。"""
        task = get_store().get_semantic_workspace_task(user_id, task_id)
        if (
            task is None
            or task["deleted_at"] is not None
            or task["status"] in _TERMINAL_STATUSES
            or task["cancel_requested"]
        ):
            return
        job = asyncio.create_task(
            self._run_task(user_id, task_id),
            name=f"semantic-workspace-job-{task_id}",
        )
        self._active[task_id] = job
        try:
            await job
        finally:
            self._active.pop(task_id, None)
            if task_id in self._deferred_requeue:
                self._deferred_requeue.discard(task_id)
                self.enqueue(user_id, task_id)

    def _apply_waiting_revision_at_safe_point(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        safe_point: str,
    ) -> bool:
        coordinator = ConversationSteering(
            SqliteSteeringRepository(settings.webui_db_path),
            None,
        )
        decision = coordinator.mark_safe_point(
            user_id,
            task_id,
            revision=revision,
            safe_point=safe_point,
        )
        if decision is None:
            return False
        # 路由与 worker 使用同一个应用函数，防止两条路径形成不同的
        # revision/外发冻结语义。延迟导入用于避开现有 API 模块装载环。
        from src.api.routes.semantic_workspace import (  # noqa: PLC0415
            _apply_confirmed_steering_revision,
        )

        _apply_confirmed_steering_revision(
            user_id,
            task_id,
            decision.decision_id,
            external_api_confirmed=decision.external_api_confirmed,
        )
        get_store().append_semantic_workspace_event(
            user_id,
            task_id,
            stage="queued",
            event_type="revision.safe_point_applied",
            summary="当前原子步骤已结束，已切换到确认后的新版本",
            details={
                "decision_id": decision.decision_id,
                "safe_point": safe_point,
                "base_revision": revision,
            },
        )
        self._deferred_requeue.add(task_id)
        return True

    async def _run_task(self, user_id: str, task_id: str) -> None:
        task = get_store().get_semantic_workspace_task(user_id, task_id)
        if task is None:
            return
        source_types: list[str] = []
        upload_store = _upload_store()
        for upload_id in task["upload_ids"]:
            with suppress(Exception):
                upload = upload_store.resolve(user_id, upload_id)
                source_types.append(
                    Path(upload.original_name).suffix.lower().lstrip(".")
                    or "unknown"
                )
        with workspace_task_span(
            task_id=task_id,
            revision=task["active_revision"],
            source_types=source_types,
            source_count=len(task["upload_ids"]),
            output_formats=task["output_formats"],
            provider=task["provider"],
            model=task["model"],
        ) as span:
            await self._run_task_inner(user_id, task_id)
            final = get_store().get_semantic_workspace_task(
                user_id,
                task_id,
            )
            if final is not None and span is not None:
                span.set_attribute("workspace.status", final["status"])
                if final["failure"]:
                    span.set_attribute(
                        "workspace.error_code",
                        final["failure"].get("error_code", ""),
                    )

    async def _run_task_inner(self, user_id: str, task_id: str) -> None:
        store = get_store()
        task = store.get_semantic_workspace_task(user_id, task_id)
        if task is None:
            return
        revision = task["active_revision"]
        started = time.monotonic()
        try:
            store.update_semantic_workspace_task(
                user_id,
                task_id,
                status="running",
                error=None,
                failure=None,
            )
            store.update_semantic_workspace_revision(
                user_id,
                task_id,
                revision,
                status="running",
            )
            runtime = AgenticRuntimeRepository(
                settings.webui_db_path
            ).get(user_id, task_id, revision)
            if (
                runtime is not None
                and runtime["runtime_version"] is RuntimeVersion.PI
            ):
                await self._run_pi_task(
                    user_id,
                    task_id,
                    revision,
                    task,
                )
                return
            question = task["question"] or {}
            if question.get("kind") == "harness" and question.get("answer"):
                await self._resume_harness(
                    user_id, task_id, revision, task, question
                )
                return
            if task["run_id"]:
                run = store.get_semantic_harness_run(
                    user_id, task["run_id"]
                )
                if run and run["status"] == "running":
                    await self._execute_harness(
                        user_id, task_id, revision, task["run_id"]
                    )
                    return

            if task["provider"] != "local" and not task[
                "external_api_confirmed"
            ]:
                await self._pause_for_external(user_id, task_id)
                return

            if question.get("kind") == "binding" and question.get("answer"):
                plan_row = store.latest_semantic_plan_revision(
                    user_id, task["plan_id"]
                )
                if plan_row is None or plan_row["plan"] is None:
                    raise ValueError("待恢复的语义计划不存在")
            else:
                plan_row = await self._compile(
                    user_id, task_id, task
                )
                if plan_row["status"] == "failed":
                    self._mark_compile_failed(
                        user_id,
                        task_id,
                        revision,
                        plan_row,
                    )
                    return
                if plan_row["status"] == "needs_user":
                    await self._pause_for_plan(
                        user_id, task_id, revision, plan_row
                    )
                    return
                if plan_row["status"] != "ready":
                    raise ValueError(
                        f"未知语义计划状态：{plan_row['status']}"
                    )
                if self._apply_waiting_revision_at_safe_point(
                    user_id,
                    task_id,
                    revision,
                    "understanding_compiled",
                ):
                    return

            with workspace_stage_span("inspect"):
                binding_row = await self._bind(
                    user_id,
                    task_id,
                    plan_row,
                    question=question,
                )
            if binding_row["status"] == "needs_user":
                await self._pause_for_binding(
                    user_id,
                    task_id,
                    revision,
                    binding_row,
                )
                return
            if binding_row["status"] != "ready":
                raise ValueError("来源检查未形成可执行绑定")
            if self._apply_waiting_revision_at_safe_point(
                user_id,
                task_id,
                revision,
                "sources_bound",
            ):
                return

            run = create_run_record(
                user_id,
                HarnessRunCreateIn(
                    plan_id=plan_row["plan_id"],
                    logical_revision=plan_row["revision"],
                    binding_revision=binding_row["binding_revision"],
                ),
            )
            store.update_semantic_workspace_task(
                user_id,
                task_id,
                plan_id=plan_row["plan_id"],
                logical_revision=plan_row["revision"],
                binding_revision=binding_row["binding_revision"],
                run_id=run["run_id"],
                question=None,
            )
            store.update_semantic_workspace_revision(
                user_id,
                task_id,
                revision,
                plan_id=plan_row["plan_id"],
                logical_revision=plan_row["revision"],
                binding_revision=binding_row["binding_revision"],
                run_id=run["run_id"],
                summary=plan_row["summary"],
            )
            await self._execute_harness(
                user_id, task_id, revision, run["run_id"]
            )
        except asyncio.CancelledError:
            self._mark_cancelled(user_id, task_id, revision)
        except Exception as exc:  # noqa: BLE001
            failure = self._runtime_failure(
                user_id,
                task_id,
                str(exc) or exc.__class__.__name__,
                elapsed_ms=max(
                    0,
                    int((time.monotonic() - started) * 1000),
                ),
            )
            self._mark_failed(
                user_id,
                task_id,
                revision,
                str(exc) or exc.__class__.__name__,
                stage=failure["stage"],
                failure=failure,
            )

    async def _run_pi_task(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        task: dict[str, Any],
    ) -> None:
        """执行 TaskRevision 已冻结的标准 vNext 容器任务。"""

        repository = AgenticRuntimeRepository(settings.webui_db_path)
        runtime = repository.get(user_id, task_id, revision)
        if runtime is None:
            raise ValueError("Pi Runtime 配置不存在")
        task_revision = get_store().get_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
        )
        if task_revision is None:
            raise ValueError("Pi Runtime 对应的冻结 TaskRevision 不存在")
        checkpoint = None
        if (
            runtime["status"]
            in {RuntimeStatus.PREPARING, RuntimeStatus.RUNNING}
            and runtime["run_id"]
            and runtime["workspace_root"]
        ):
            checkpoint = PiRuntimeCheckpoint(
                run_id=runtime["run_id"],
                workspace_root=Path(runtime["workspace_root"]),
                container_name=runtime["container_name"],
                session_file=runtime["session_file"],
            )
        upload_store = _upload_store()
        sources: list[SourceInput] = []
        for upload_id in task["upload_ids"]:
            upload = upload_store.resolve(user_id, upload_id)
            sources.append(
                SourceInput(
                    upload_id=upload.upload_id,
                    original_name=upload.original_name or upload.upload_id,
                    host_path=Path(upload.storage_path),
                    sha256=upload.sha256,
                    media_type=upload.media_type,
                )
            )
        request_values: dict[str, Any] = {
            "user_id": user_id,
            "task_id": task_id,
            "revision": revision,
            "objective_text": task_revision["objective_text"],
            "requested_output_formats": tuple(task_revision["output_formats"]),
            "table_output_contracts": tuple(
                task_revision["table_output_contracts"]
            ),
            "sources": tuple(sources),
            "permission_profile": runtime["permission_profile"],
            # 外部 Provider 只能使用创建运行记录时已经冻结的用户确认，不能在执行时推断。
            "external_api_confirmed": bool(
                runtime.get("external_api_confirmed", False)
            ),
        }
        if runtime["model_connection_id"]:
            request_values["model_connection_id"] = runtime[
                "model_connection_id"
            ]
            request_values["model_connection_version"] = runtime[
                "model_connection_version"
            ]
            request_values["model_connection_model"] = runtime[
                "model_connection_model"
            ]
        else:
            request_values.update(
                {
                    "model": task["model"] or settings.llm_model_name,
                    "base_url": settings.llm_base_url,
                    # 本地直连没有 Provider Secret，固定占位值也不写入运行台账。
                    "api_key": "local-runtime",
                }
            )
        request = PiRuntimeRequest(**request_values)
        repository.update(
            user_id,
            task_id,
            revision,
            status=RuntimeStatus.PREPARING,
            clear_failure=True,
            request=request.model_dump(
                mode="json",
                exclude={"api_key"},
                exclude_none=True,
            ),
        )
        store = get_store()
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="goal_interpretation",
            event_type="stage_started",
            summary="正在理解任务范围和结果要求",
            details={"runtime_version": "pi"},
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="source_probe",
            event_type="source.observed",
            summary=f"Pi 将按任务需要检查 {len(sources)} 个真实来源",
            details={
                "runtime_version": "pi",
                "source_count": len(sources),
            },
        )
        current_progress_stage = "goal_interpretation"
        coverage_frozen = False

        async def on_event(event: RuntimeEvent) -> None:
            nonlocal current_progress_stage, coverage_frozen
            checkpoint_data = event.details.get("_checkpoint")
            if isinstance(checkpoint_data, dict):
                repository.update(
                    user_id,
                    task_id,
                    revision,
                    run_id=str(checkpoint_data.get("run_id") or ""),
                    container_name=str(
                        checkpoint_data.get("container_name") or ""
                    ),
                    workspace_root=str(
                        checkpoint_data.get("workspace_root") or ""
                    ),
                    session_file=(
                        str(checkpoint_data["session_file"])
                        if checkpoint_data.get("session_file")
                        else None
                    ),
                )
            public_details = {
                key: value
                for key, value in event.details.items()
                if not key.startswith("_")
            }
            repository.append_event(
                user_id,
                task_id,
                revision,
                event_type=event.event_type,
                summary=event.summary,
                details=public_details,
            )
            tool_name = str(public_details.get("tool") or "")
            tool_stages = {
                "freeze_coverage": "goal_interpretation",
                "inspect_source": "source_probe",
                "discover_content": "source_discovery",
                "read_evidence": "evidence_read",
                "propose_completion": "verify_coverage",
            }
            progress_event_type = "action_progress"
            if event.event_type.startswith("capability."):
                stage = "prepare_capabilities"
                progress_event_type = (
                    "stage_completed"
                    if event.event_type.endswith(".completed")
                    else "stage_started"
                )
            elif event.event_type == "candidate.ready":
                stage = "verify"
            elif tool_name in tool_stages:
                requested_stage = tool_stages[tool_name]
                observing_before_freeze = (
                    not coverage_frozen
                    and tool_name != "freeze_coverage"
                )
                # Pi 可以在冻结目标前先观察来源；这仍属于理解要求，不能让
                # 普通用户看到“读取来源/处理数据”越过尚未完成的目标理解。
                stage = (
                    current_progress_stage
                    if observing_before_freeze
                    else requested_stage
                )
                if observing_before_freeze:
                    progress_event_type = (
                        "action_warning"
                        if event.event_type == "tool.failed"
                        else "action_progress"
                    )
                elif event.event_type == "tool.started":
                    progress_event_type = "stage_started"
                    current_progress_stage = stage
                elif event.event_type == "tool.completed":
                    progress_event_type = "stage_completed"
                    if tool_name == "freeze_coverage":
                        coverage_frozen = True
                # 单次工具失败是可恢复动作，只有任务终止才是业务阶段失败。
                elif event.event_type == "tool.failed":
                    progress_event_type = "action_warning"
            else:
                stage = current_progress_stage
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage=stage,
                event_type=progress_event_type,
                summary=event.summary,
                details={
                    **public_details,
                    "source": "pi-runtime",
                    "runtime_event_type": event.event_type,
                },
            )
            if (
                event.event_type == "tool.completed"
                and self._apply_waiting_revision_at_safe_point(
                    user_id,
                    task_id,
                    revision,
                    f"pi_tool_completed:{tool_name or 'unknown'}",
                )
            ):
                raise _RevisionSwitchAtSafePoint

        repository.update(
            user_id,
            task_id,
            revision,
            status=RuntimeStatus.RUNNING,
        )
        try:
            if checkpoint is not None:
                execution: asyncio.Future = asyncio.ensure_future(
                    self._pi_runtime.resume(
                        request,
                        checkpoint=checkpoint,
                        on_event=on_event,
                    )
                )
            else:
                execution = asyncio.ensure_future(
                    self._pi_runtime.start(
                        request,
                        on_event=on_event,
                    )
                )
            result = await self._await_with_gate_supervision(
                user_id,
                task_id,
                revision,
                execution,
            )
        except _RevisionSwitchAtSafePoint:
            # 新 revision 已冻结后，显式终止旧版本容器；旧工作区仍保留为
            # 审计证据，但不会被登记成新版本候选或正式交付。
            await self._pi_runtime.cancel(user_id, task_id, revision)
            return
        except _GateViolationAbort:
            # 治理门命中：状态已由监督标记为 cancelled，静默退出；
            # 不覆盖状态，也不发布 Candidate/Delivery。
            return
        if result.status is RuntimeStatus.NEEDS_INPUT:
            repository.update(
                user_id,
                task_id,
                revision,
                status=RuntimeStatus.NEEDS_INPUT,
                run_id=result.run_id,
                workspace_root=result.workspace_root,
                session_file=result.session_file,
            )
            clarification = result.clarification or {}
            self._set_needs_input(
                user_id,
                task_id,
                {
                    "kind": "plan",
                    "question_id": f"pi:{result.run_id}",
                    "prompt": clarification.get("question")
                    or "请补充会影响结果范围的信息",
                    "reason": clarification.get("reason")
                    or "不同解释会改变结果或处理范围",
                    "affected_scope": ["覆盖范围", "结果数量"],
                    "options": [],
                    "allow_free_text": True,
                },
            )
            return
        if result.status is not RuntimeStatus.CANDIDATE_READY:
            raise ValueError("Pi Runtime 未形成候选结果")
        repository.update(
            user_id,
            task_id,
            revision,
            status=result.status,
            run_id=result.run_id,
            container_name=result.container_name,
            workspace_root=result.workspace_root,
            session_file=result.session_file,
            candidates=result.candidates,
            verification=result.verification,
        )
        verification = result.verification
        verification_passed = bool(
            verification and verification.status.value == "passed"
        )
        # Task 的 run_id 指向真实 Pi Run，公共 Delivery 查询不再依赖伪造 Legacy Harness。
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            run_id=result.run_id,
        )
        store.update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            run_id=result.run_id,
        )
        if verification_passed:
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="verify",
                event_type="candidate_verified",
                summary="候选已通过文件、来源和目标语义验证",
                details={
                    "candidate_count": len(result.candidates),
                    "verification_status": "passed",
                    "formal_delivery": False,
                },
            )
            await self._publish_verified_candidates(
                user_id=user_id,
                task_id=task_id,
                revision=revision,
                repository=repository,
                upload_store=upload_store,
            )
            return

        # 未通过独立验证的 Candidate 仅供诊断，不进入 Publisher。
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            status="candidate_ready",
            summary=(
                verification.summary
                if verification
                else "候选尚未形成独立验证结论"
            ),
            error=None,
            failure=None,
            question=None,
            cancel_requested=False,
        )
        store.update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            status="candidate_ready",
            summary="Pi 候选未通过独立验证",
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="verify",
            event_type="candidate_verification_failed",
            summary=(
                verification.summary
                if verification
                else "候选没有独立验证结论"
            ),
            details={
                "candidate_count": len(result.candidates),
                "verification_status": (
                    verification.status.value
                    if verification
                    else "inconclusive"
                ),
                "next_actions": ["查看失败原因", "创建新版本修改目标", "停止"],
                "formal_delivery": False,
            },
        )

    async def _compile(
        self,
        user_id: str,
        task_id: str,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        store = get_store()
        started = time.monotonic()
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="interpret",
            event_type="stage_started",
            summary="正在理解任务范围和输出要求",
        )
        plan_id = task["plan_id"]
        latest = (
            store.latest_semantic_plan_revision(user_id, plan_id)
            if plan_id
            else None
        )
        logical_revision = int(latest["revision"]) + 1 if latest else 1
        payload = SemanticCompileIn(
            task_id=task_id,
            objective_text=task["objective_text"],
            artifact_ids=tuple(task["upload_ids"]),
            requested_output_formats=tuple(task["output_formats"]),
            provider=task["provider"],
            model=task["model"],
            external_api_confirmed=task["external_api_confirmed"],
        )
        request = _compile_request(payload)
        question = task.get("question") or {}
        if (
            latest
            and latest.get("plan")
            and question.get("kind") == "plan"
            and question.get("answer")
        ):
            request = request.model_copy(
                update={
                    "prior_plan": SemanticTaskPlan.model_validate(
                        latest["plan"]
                    ),
                    "clarification": ClarificationResolution(
                        ambiguity_id=str(question["question_id"]),
                        question=str(question["prompt"]),
                        answer=str(question["answer"]),
                    ),
                }
            )
        with workspace_stage_span("compile"):
            row = await compile_and_save(
                request,
                user_id=user_id,
                plan_id=plan_id,
                revision=logical_revision,
            )
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            plan_id=row["plan_id"],
            logical_revision=row["revision"],
            summary=row["summary"],
        )
        if row["status"] == "ready":
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="interpret",
                event_type="stage_completed",
                summary="已形成可执行任务理解",
                details={"plan_summary": row["summary"]},
            )
        elif row["status"] == "needs_user":
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="interpret",
                event_type="stage_waiting",
                summary="任务理解需要一项补充",
                details={"plan_summary": row["summary"]},
            )
        return {
            **row,
            "_elapsed_ms": max(
                0,
                int((time.monotonic() - started) * 1000),
            ),
        }

    async def _bind(
        self,
        user_id: str,
        task_id: str,
        plan_row: dict[str, Any],
        *,
        question: dict[str, Any],
    ) -> dict[str, Any]:
        store = get_store()
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="inspect",
            event_type="stage_started",
            summary=f"正在检查 {len(plan_row['request']['artifact_ids'])} 个来源文件",
        )
        plan = SemanticTaskPlan.model_validate(plan_row["plan"])
        previous = store.latest_semantic_binding_revision(
            user_id, plan.plan_id
        )
        binding_revision = (
            int(previous["binding_revision"]) + 1 if previous else 1
        )
        resolutions: dict[str, str] = {}
        if (
            question.get("kind") == "binding"
            and question.get("answer")
            and previous is not None
        ):
            resolutions = dict(previous["resolutions"])
            resolutions[question["ambiguity_id"]] = question["answer"]
        with workspace_stage_span("bind"):
            row = await bind_and_save(
                user_id=user_id,
                plan=plan,
                binding_revision=binding_revision,
                resolutions=resolutions,
                use_local_semantics=True,
            )
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            binding_revision=row["binding_revision"],
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="bind",
            event_type="stage_completed",
            summary=(
                "来源和目标字段已绑定"
                if row["status"] == "ready"
                else "来源绑定需要一项确认"
            ),
        )
        return row

    async def _execute_harness(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        *,
        resume: HarnessResume | None = None,
    ) -> None:
        store = get_store()
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="execute",
            event_type="stage_started",
            summary="正在执行、验证并生成正式交付",
        )
        with workspace_stage_span("execute"):
            await invoke_run_record(user_id, run_id, resume=resume)
        run = store.get_semantic_harness_run(user_id, run_id)
        if run is None:
            raise ValueError("Harness run 执行后记录丢失")
        if run["status"] == "needs_user":
            await self._pause_for_harness(
                user_id, task_id, revision, run
            )
            return
        with workspace_stage_span("verify", status=run["status"]):
            if run["status"] != "succeeded":
                verification = run.get("final_verification") or {}
                issues = verification.get("issues") or []
                message = (
                    issues[0].get("message")
                    if issues and isinstance(issues[0], dict)
                    else "执行未通过最终验证"
                )
                raise ValueError(message)
        with workspace_stage_span("publish"):
            delivery = store.latest_semantic_delivery(user_id, run_id)
            if delivery is None:
                raise ValueError("执行成功但正式交付记录不存在")
            store.update_semantic_workspace_task(
                user_id,
                task_id,
                status="completed",
                question=None,
                cancel_requested=False,
                error=None,
                failure=None,
            )
            store.update_semantic_workspace_revision(
                user_id,
                task_id,
                revision,
                status="completed",
            )
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="deliver",
                event_type="task_completed",
                summary=(
                    f"检查通过，已生成 {len(delivery['outputs'])} 个正式文件"
                ),
                details={"delivery_id": delivery["delivery_id"]},
            )

    async def _resume_harness(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        task: dict[str, Any],
        question: dict[str, Any],
    ) -> None:
        if not task["run_id"]:
            raise ValueError("待恢复的 Harness run 不存在")
        resume = HarnessResume(
            question_id=question["question_id"],
            resume_token=question["resume_token"],
            answer=question["answer"],
        )
        get_store().update_semantic_workspace_task(
            user_id,
            task_id,
            question=None,
        )
        await self._execute_harness(
            user_id,
            task_id,
            revision,
            task["run_id"],
            resume=resume,
        )

    async def _pause_for_external(
        self,
        user_id: str,
        task_id: str,
    ) -> None:
        task = get_store().get_semantic_workspace_task(user_id, task_id)
        assert task is not None
        question = {
            "kind": "external",
            "question_id": f"external:{task_id}",
            "prompt": f"是否允许将必要数据发送到 {task['provider']}？",
            "reason": "当前选择的是外部 OpenAPI",
            "affected_scope": "任务要求和被选中的证据片段",
            "external_service": task["provider"],
            "outbound_data": ["任务要求", "被选中的证据片段"],
            "purpose": "完成用户要求的语义处理",
            "risk": "数据将离开本机或局域网",
            "options": [
                {"value": "confirm", "label": "仅本次允许"},
                {"value": "local", "label": "改用本地模型"},
                {"value": "cancel", "label": "取消任务"},
            ],
            "allow_free_text": False,
        }
        self._set_needs_input(user_id, task_id, question)

    async def _pause_for_plan(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        row: dict[str, Any],
    ) -> None:
        clarification = row["clarification"]
        if clarification is None:
            raise ValueError("语义计划编译失败且没有可回答问题")
        question = {
            "kind": "plan",
            "question_id": clarification["ambiguity_id"],
            "prompt": clarification["question"],
            "reason": "该信息会实质改变任务结果",
            "affected_scope": "任务范围或输出语义",
            "options": [
                {"value": item, "label": item}
                for item in clarification.get("candidates", [])
            ],
            "allow_free_text": True,
        }
        get_store().update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            plan_id=row["plan_id"],
            logical_revision=row["revision"],
            status="needs_input",
            summary=row["summary"],
        )
        self._set_needs_input(user_id, task_id, question)

    async def _pause_for_binding(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        row: dict[str, Any],
    ) -> None:
        clarification = row["result"].get("clarification")
        if clarification is None:
            raise ValueError("来源绑定暂停但没有可回答问题")
        ambiguity_id = clarification["ambiguity_id"]
        semantic_ref = ambiguity_id.split("|", 1)[0]
        options = [
            {
                "value": item["physical_ref"],
                "label": item["semantic_label"],
                "description": "；".join(item["evidence_reasons"]),
            }
            for item in row["result"].get("candidates", [])
            if item["semantic_ref"] == semantic_ref
        ]
        question = {
            "kind": "binding",
            "question_id": ambiguity_id,
            "ambiguity_id": ambiguity_id,
            "prompt": clarification["question"],
            "reason": "多个来源位置的匹配度接近",
            "affected_scope": "来源字段或文档段落",
            "options": options,
            "allow_free_text": False,
        }
        get_store().update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            binding_revision=row["binding_revision"],
            status="needs_input",
        )
        self._set_needs_input(user_id, task_id, question)

    async def _pause_for_harness(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        run: dict[str, Any],
    ) -> None:
        raw = run["question"]
        if raw is None:
            raise ValueError("Harness 暂停但没有可回答问题")
        question = {
            "kind": "harness",
            **raw,
            "prompt": raw.get("prompt", "请选择下一步"),
        }
        get_store().update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            status="needs_input",
        )
        self._set_needs_input(user_id, task_id, question)

    def _set_needs_input(
        self,
        user_id: str,
        task_id: str,
        question: dict[str, Any],
    ) -> None:
        store = get_store()
        task = store.update_semantic_workspace_task(
            user_id,
            task_id,
            status="needs_input",
            question=question,
            failure=None,
        )
        store.update_semantic_workspace_revision(
            user_id,
            task_id,
            task["active_revision"],
            status="needs_input",
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="needs_input",
            event_type="question_required",
            summary=question["prompt"],
            details={
                "reason": question.get("reason"),
                "affected_scope": question.get("affected_scope"),
            },
        )

    def _mark_cancelled(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> None:
        store = get_store()
        task = store.get_semantic_workspace_task(user_id, task_id)
        if task is None:
            return
        runtime_repository = AgenticRuntimeRepository(
            settings.webui_db_path
        )
        runtime = runtime_repository.get(
            user_id, task_id, revision
        )
        if (
            runtime is not None
            and runtime["runtime_version"] is RuntimeVersion.PI
        ):
            runtime_repository.update(
                user_id,
                task_id,
                revision,
                status=RuntimeStatus.CANCELLED,
            )
        if task["run_id"]:
            run = store.get_semantic_harness_run(
                user_id, task["run_id"]
            )
            if run and run["status"] == "running":
                store.update_semantic_harness_run(
                    user_id,
                    task["run_id"],
                    status="cancelled",
                    current_node=run["current_node"],
                    repair_rounds=run["repair_rounds"],
                    semantic_replans=run["semantic_replans"],
                    transient_retries=run["transient_retries"],
                    same_failure_count=run["same_failure_count"],
                    last_failure_fingerprint=run[
                        "last_failure_fingerprint"
                    ],
                    question=None,
                    final_verification=run["final_verification"],
                    eligible_for_delivery=False,
                )
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            status="cancelled",
            cancel_requested=True,
            question=None,
            failure=None,
        )
        store.update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            status="cancelled",
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="cancelled",
            event_type="task_cancelled",
            summary="任务已取消，未发布新的正式交付",
        )

    def _mark_compile_failed(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        row: dict[str, Any],
    ) -> None:
        diagnostics = list(row.get("diagnostics") or [])
        truncated = sum(
            1
            for item in diagnostics
            if any(
                marker in str(item.get("message", "")).lower()
                for marker in (
                    "max_tokens",
                    "length limit",
                    "incompleteoutput",
                )
            )
        )
        invalid = any(
            item.get("code") == "invalid_plan"
            for item in diagnostics
        )
        if truncated == 2 and invalid:
            cause = (
                "本地模型两次输出被截断，最后生成的计划未通过校验"
            )
        elif truncated and invalid:
            cause = (
                f"本地模型 {truncated} 次输出被截断，"
                "最后生成的计划未通过校验"
            )
        elif truncated:
            cause = f"本地模型连续 {truncated} 次输出被截断"
        else:
            cause = "语义计划在有界尝试后仍未通过校验"
        attempts = (
            max(int(item.get("attempt", 0)) for item in diagnostics) + 1
            if diagnostics
            else int(
                (row.get("provenance") or {}).get(
                    "repair_attempts",
                    0,
                )
            )
            + 1
        )
        failure = {
            "error_code": "STP_COMPILE_FAILED",
            "stage": "interpret",
            "cause_summary": cause,
            "attempt_count": attempts,
            "elapsed_ms": int(row.get("_elapsed_ms") or 0),
            "source_read": False,
            "intermediate_created": False,
            "delivery_published": False,
            "next_actions": ["修改要求后重试", "检查本地模型配置"],
            "diagnostic_ref": row.get("plan_id"),
        }
        get_store().update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            plan_id=row.get("plan_id"),
            logical_revision=row.get("revision"),
            summary=row.get("summary") or "",
        )
        self._mark_failed(
            user_id,
            task_id,
            revision,
            cause,
            stage="interpret",
            failure=failure,
        )

    @staticmethod
    def _runtime_failure(
        user_id: str,
        task_id: str,
        message: str,
        *,
        elapsed_ms: int,
    ) -> dict[str, Any]:
        """把 Harness/转换器错误归一为普通用户可理解的失败说明。"""
        store = get_store()
        task = store.get_semantic_workspace_task(user_id, task_id)
        assert task is not None
        runtime = AgenticRuntimeRepository(
            settings.webui_db_path
        ).get(user_id, task_id, task["active_revision"])
        if (
            runtime is not None
            and runtime["runtime_version"] is RuntimeVersion.PI
        ):
            if "扫描 PDF OCR 服务不可用" in message:
                failure = {
                    "error_code": "SOURCE_OCR_UNAVAILABLE",
                    "stage": "inspect",
                    "cause_summary": message[:500],
                    "attempt_count": 1,
                    "elapsed_ms": elapsed_ms,
                    "source_read": True,
                    "intermediate_created": False,
                    "delivery_published": False,
                    "next_actions": [
                        "检查 MinerU 或 PaddleOCR-VL 文档解析服务",
                        "服务恢复后重试任务",
                    ],
                    "diagnostic_ref": runtime.get("run_id") or task_id,
                }
                AgenticRuntimeRepository(
                    settings.webui_db_path
                ).update(
                    user_id,
                    task_id,
                    task["active_revision"],
                    status=RuntimeStatus.FAILED,
                    failure=failure,
                )
                return failure
            runtime_events = AgenticRuntimeRepository(
                settings.webui_db_path
            ).list_events(
                user_id,
                task_id,
                task["active_revision"],
            )
            source_read = any(
                event["event_type"] == "tool.completed"
                and str(event["details"].get("tool") or "") == "read"
                for event in runtime_events
            )
            infrastructure_failure = any(
                marker in message.lower()
                for marker in ("docker", "runtime 镜像", "image inspect")
            )
            failure = {
                "error_code": "PI_RUNTIME_FAILED",
                "stage": "execute",
                "cause_summary": message[:500],
                "attempt_count": 1,
                "elapsed_ms": elapsed_ms,
                "source_read": source_read,
                "intermediate_created": _pi_has_user_output(runtime),
                "delivery_published": False,
                "next_actions": (
                    [
                        "检查 Docker Desktop 和 Pi Runtime 镜像",
                        "服务恢复后重试任务",
                        "停止任务",
                    ]
                    if infrastructure_failure
                    else [
                        "查看任务执行记录",
                        "修改要求后创建新版本",
                        "停止任务",
                    ]
                ),
                "diagnostic_ref": runtime.get("run_id") or task_id,
            }
            AgenticRuntimeRepository(
                settings.webui_db_path
            ).update(
                user_id,
                task_id,
                task["active_revision"],
                status=RuntimeStatus.FAILED,
                failure=failure,
            )
            return failure
        attempts = (
            store.list_semantic_harness_attempts(
                user_id,
                task["run_id"],
            )
            if task["run_id"]
            else []
        )
        technical_messages = [message]
        for attempt in attempts:
            tool_result = attempt.get("tool_result") or {}
            if tool_result.get("error_message"):
                technical_messages.append(
                    str(tool_result["error_message"])
                )
        if task["run_id"]:
            for event in store.list_semantic_harness_events(
                user_id,
                task["run_id"],
            ):
                if event.get("event_type") in {
                    "delivery_failed",
                    "attempt_failed",
                }:
                    technical_messages.append(str(event.get("summary") or ""))
        combined = "\n".join(technical_messages).lower()
        if any(
            marker in combined
            for marker in ("renderer", "转换器", "render")
        ):
            error_code = "DELIVERY_RENDER_FAILED"
            stage = "deliver"
            cause = "结果已生成，但正式文件转换失败"
            next_actions = ["重试生成正式文件", "检查转换器配置"]
        elif any(
            marker in combined
            for marker in ("publisher", "正式交付发布")
        ):
            error_code = "DELIVERY_PUBLISH_FAILED"
            stage = "deliver"
            cause = "候选已生成，但正式交付发布失败"
            next_actions = ["重试正式发布", "查看发布与 QA 记录"]
        elif any(
            marker in combined
            for marker in ("corrupt", "损坏", "parse_failed")
        ):
            error_code = "SOURCE_INVALID"
            stage = "inspect"
            cause = "原始资料无法可靠读取"
            next_actions = ["重新上传可打开的原文件", "检查文件格式"]
        else:
            error_code = "WORKSPACE_EXECUTION_FAILED"
            stage = "execute"
            cause = message
            next_actions = ["修改要求后重试", "查看错误代码并检查配置"]
        delivery = (
            store.latest_semantic_delivery(user_id, task["run_id"])
            if task["run_id"]
            else None
        )
        return {
            "error_code": error_code,
            "stage": stage,
            "cause_summary": cause[:500],
            "attempt_count": max(1, len(attempts)),
            "elapsed_ms": elapsed_ms,
            "source_read": task["binding_revision"] is not None,
            "intermediate_created": bool(task["run_id"]),
            "delivery_published": delivery is not None,
            "next_actions": next_actions,
            "diagnostic_ref": task["run_id"] or task["plan_id"] or task_id,
        }

    def _mark_failed(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        message: str,
        *,
        stage: str = "failed",
        failure: dict[str, Any] | None = None,
    ) -> None:
        store = get_store()
        store.update_semantic_workspace_task(
            user_id,
            task_id,
            status="failed",
            error=message[:1000],
            failure=failure,
            question=None,
        )
        store.update_semantic_workspace_revision(
            user_id,
            task_id,
            revision,
            status="failed",
        )
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage=stage,
            event_type=(
                "stage_failed" if stage != "failed" else "task_failed"
            ),
            summary=f"任务失败：{message[:300]}",
            details={
                "impact": "未发布新的正式交付",
                "next_actions": ["重试", "修改要求", "停止"],
                "error_code": (
                    failure.get("error_code") if failure else None
                ),
            },
        )


_manager = SemanticWorkspaceManager()


def get_semantic_workspace_manager() -> SemanticWorkspaceManager:
    return _manager
