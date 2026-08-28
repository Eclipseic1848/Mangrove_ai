# -*- coding: utf-8 -*-
"""Phase 4B 批次 7：单机持久化工作台任务编排。"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any

from filelock import FileLock, Timeout

from src.agentic_runtime.models import (
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    RuntimeEvent,
    RuntimeStatus,
    RuntimeVersion,
    SourceInput,
    VerificationReport,
    VerificationStatus,
)
from src.agentic_runtime.candidate_verifier import (
    BrokerSemanticJudge,
    CandidateVerifier,
    LocalModelSemanticJudge,
)
from src.agentic_runtime.kernel import AgentKernel, PiAgentKernelAdapter
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
from src.candidate_verification import (
    AttemptStatus,
    CandidateVerificationService,
    CurrentVerifierRulesetResolver,
    HistoricalReverificationBinding,
    HistoricalAuthorityRecoveryConfirmation,
    HistoricalReverificationEvidence,
    HistoricalReverificationPurpose,
    ReverificationBlocker,
    SqliteCandidateVerificationRepository,
    parse_frozen_runtime_request,
)
from src.conversation_steering import (
    ConversationSteering,
    SqliteSteeringRepository,
)
from src.delivery_publishing.models import PublicationGate
from src.delivery_publishing.pi_adapter import PiCandidateAdapter
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher
from src.runtime_routing import RuntimeAssignment, runtime_routing_is_p0_blocked
from src.semantic_harness.compiler_models import ClarificationResolution
from src.semantic_harness.harness_models import HarnessResume
from src.semantic_harness.inspectors import UploadSourceInspector
from src.semantic_harness.models import SemanticTaskPlan
from src.observability.workspace_telemetry import (
    workspace_stage_span,
    workspace_task_span,
)
from src.services.managed_paths import ManagedPathCodec
from src.source_acquisition import SourceAcquisitionRepository
from src.task_context import TaskContextRepository
from src.services.upload_store import UploadStore
from src.model_connections import get_default_broker


_LOGGER = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    "completed",
    "candidate_ready",
    "failed",
    "cancelled",
}
# 运行期治理监督的投影检查节奏（秒）。
RUNTIME_GATE_POLL_SECONDS = 30
REVERIFICATION_RECOVERY_POLL_SECONDS = 5
WORKSPACE_PURGE_INTERVAL_SECONDS = 3600


class _GateViolationAbort(RuntimeError):
    """运行期治理门命中：任务已标记取消，调用方不得再覆盖状态。"""


class _DeliveryRetryPending(RuntimeError):
    """候选已冻结，后续恢复只能重试 Publisher。"""


class _CandidateVerificationBrokerAdapter:
    """确认实际 Verifier 使用冻结的本地或 Broker 路由。"""

    def assert_verifier_binding(self, request, run_id, verifier) -> None:
        if type(verifier) is not CandidateVerifier:
            raise RuntimeError("候选验证器不是当前受控实现")
        judge = verifier._semantic_judge
        if request.model_connection_id is not None:
            if type(judge) is not BrokerSemanticJudge or (
                judge._owner_user_id,
                judge._connection_id,
                judge._connection_version,
                judge._model_id,
                judge._task_id,
                judge._revision,
                judge._run_id,
            ) != (
                request.user_id,
                request.model_connection_id,
                request.model_connection_version,
                request.model_connection_model,
                request.task_id,
                request.revision,
                run_id,
            ):
                raise RuntimeError("Verifier 的 Broker 路由与冻结任务不一致")
            return
        if type(judge) is not LocalModelSemanticJudge or (
            judge.model,
            judge.base_url,
            judge.api_key,
        ) != (request.model, request.base_url.rstrip("/"), request.api_key):
            raise RuntimeError("Verifier 的本地模型路由与冻结任务不一致")


class _WorkspaceReverificationAuthority:
    """只读重开权威门：复核任务、路由与连接，不签发 Grant。"""

    def blockers(self, request, run_id) -> tuple[ReverificationBlocker, ...]:
        blockers: list[ReverificationBlocker] = []
        database = Path(settings.webui_db_path)
        try:
            with sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=30,
            ) as connection:
                connection.row_factory = sqlite3.Row
                revision = connection.execute(
                    "SELECT r.run_id, r.objective_text, r.output_formats_json, "
                    "r.table_output_contracts_json, t.upload_ids_json, "
                    "t.active_revision "
                    "FROM semantic_workspace_revisions AS r "
                    "JOIN semantic_workspace_tasks AS t ON t.task_id=r.task_id "
                    "AND t.user_id=r.user_id "
                    "WHERE r.user_id=? AND r.task_id=? AND r.revision=?",
                    (request.user_id, request.task_id, request.revision),
                ).fetchone()
                assignment_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='runtime_assignments'"
                ).fetchone()
                assignment = (
                    connection.execute(
                        "SELECT payload_json, runtime_version, rollout_mode, "
                        "gate_snapshot_id, assigned_at "
                        "FROM runtime_assignments WHERE owner_id=? "
                        "AND task_id=? AND revision=?",
                        (request.user_id, request.task_id, request.revision),
                    ).fetchone()
                    if assignment_table is not None
                    else None
                )
        except (sqlite3.DatabaseError, OSError):
            return (ReverificationBlocker.AUTHORITY_UNAVAILABLE,)

        try:
            revision_matches = (
                revision is not None
                and revision["active_revision"] == request.revision
                and revision["run_id"] == run_id
                and revision["objective_text"] == request.objective_text
                and tuple(json.loads(revision["output_formats_json"]))
                == request.requested_output_formats
                and tuple(json.loads(revision["table_output_contracts_json"]))
                == tuple(
                    item.model_dump(mode="json")
                    for item in request.table_output_contracts
                )
            )
        except (TypeError, json.JSONDecodeError):
            revision_matches = False
        if not revision_matches:
            # Runtime 行不是 TaskRevision 权威；孤儿或串线记录必须失败关闭。
            blockers.append(ReverificationBlocker.TASK_REVISION_DRIFT)
        if revision is not None:
            try:
                upload_ids = tuple(json.loads(revision["upload_ids_json"]))
            except (TypeError, json.JSONDecodeError):
                upload_ids = ()
            if upload_ids != tuple(source.upload_id for source in request.sources):
                # 来源顺序和身份都属于冻结输入，不能用 Runtime 自报字段替代。
                blockers.append(ReverificationBlocker.SOURCE_BINDING_DRIFT)

        try:
            frozen_assignment = (
                RuntimeAssignment.model_validate_json(assignment["payload_json"])
                if assignment
                else None
            )
            assignment_matches = (
                frozen_assignment is not None
                and assignment["runtime_version"] == "pi"
                and frozen_assignment.runtime_version.value == "pi"
                and assignment["rollout_mode"]
                == frozen_assignment.rollout_mode.value
                and assignment["gate_snapshot_id"]
                == frozen_assignment.gate_snapshot_id
                and assignment["assigned_at"]
                == frozen_assignment.assigned_at.isoformat()
                and frozen_assignment.task_revision.owner_id == request.user_id
                and frozen_assignment.task_revision.task_id == request.task_id
                and frozen_assignment.task_revision.revision == request.revision
            )
        except (KeyError, TypeError, ValueError):
            assignment_matches = False
        if not assignment_matches:
            # RuntimeAssignment 是不可变路由凭据；缺失时不能只相信可漂移 Runtime 行。
            blockers.append(ReverificationBlocker.RUNTIME_ASSIGNMENT_DRIFT)

        if request.model_connection_id:
            provider_blocker = self._provider_binding_blocker(database, request)
            if provider_blocker is not None:
                # 只读复核 Owner、版本和模型；不签发 Grant，也不读取 Secret。
                blockers.append(provider_blocker)
        return tuple(dict.fromkeys(blockers))

    def historical_recovery_evidence(
        self,
        request: PiRuntimeRequest,
        run_id: str,
        binding: HistoricalReverificationBinding,
    ) -> HistoricalReverificationEvidence | None:
        """只为迁移前的精确缺口构造摘要；不会写库或伪造 Assignment。"""

        database = Path(settings.webui_db_path)
        try:
            with sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=30,
            ) as connection:
                connection.row_factory = sqlite3.Row
                revision = connection.execute(
                    "SELECT r.run_id, r.objective_text, r.output_formats_json, "
                    "r.table_output_contracts_json, t.upload_ids_json, "
                    "t.active_revision "
                    "FROM semantic_workspace_revisions AS r "
                    "JOIN semantic_workspace_tasks AS t ON t.task_id=r.task_id "
                    "AND t.user_id=r.user_id "
                    "WHERE r.user_id=? AND r.task_id=? AND r.revision=?",
                    (request.user_id, request.task_id, request.revision),
                ).fetchone()
                runtime = connection.execute(
                    "SELECT run_id, runtime_version, status, request_json, "
                    "external_api_confirmed, created_at "
                    "FROM agentic_runtime_runs WHERE user_id=? "
                    "AND task_id=? AND revision=?",
                    (request.user_id, request.task_id, request.revision),
                ).fetchone()
                migration = connection.execute(
                    "SELECT migration_id, backup_sha256, applied_at "
                    "FROM runtime_routing_migrations "
                    "WHERE migration_id='0001_runtime_routing'",
                ).fetchone()
                assignment = connection.execute(
                    "SELECT 1 FROM runtime_assignments WHERE owner_id=? "
                    "AND task_id=? AND revision=?",
                    (request.user_id, request.task_id, request.revision),
                ).fetchone()
                events = connection.execute(
                    "SELECT sequence, event_id, event_type, created_at "
                    "FROM agentic_runtime_events WHERE user_id=? AND task_id=? "
                    "AND revision=? ORDER BY sequence",
                    (request.user_id, request.task_id, request.revision),
                ).fetchall()
        except (sqlite3.DatabaseError, OSError):
            return None

        if (
            revision is None
            or runtime is None
            or migration is None
            or assignment is not None
            or runtime["run_id"] != run_id
            or runtime["runtime_version"] != "pi"
            or runtime["status"] != "candidate_ready"
            or revision["run_id"] != run_id
            or revision["active_revision"] != request.revision
            or revision["objective_text"] != request.objective_text
        ):
            return None
        try:
            output_formats = tuple(json.loads(revision["output_formats_json"]))
            table_contracts = tuple(
                json.loads(revision["table_output_contracts_json"])
            )
            upload_ids = tuple(json.loads(revision["upload_ids_json"]))
            frozen_request, _used_legacy_confirmation = (
                parse_frozen_runtime_request(
                    request_json=runtime["request_json"],
                    external_api_confirmed=runtime[
                        "external_api_confirmed"
                    ],
                )
            )
            runtime_created_at = datetime.fromisoformat(runtime["created_at"])
            migration_applied_at = datetime.fromisoformat(migration["applied_at"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            runtime_created_at.tzinfo is None
            or migration_applied_at.tzinfo is None
            or runtime_created_at >= migration_applied_at
            or output_formats != request.requested_output_formats
            or table_contracts
            != tuple(
                item.model_dump(mode="json")
                for item in request.table_output_contracts
            )
            or upload_ids != tuple(source.upload_id for source in request.sources)
            or frozen_request.model_dump(mode="json", exclude={"api_key"})
            != request.model_dump(mode="json", exclude={"api_key"})
            or not request.model_connection_id
            or not request.model_connection_version
            or not request.model_connection_model
        ):
            return None
        required_events = {
            "runtime.preparing",
            "agent.started",
            "verification.completed",
            "candidate.ready",
        }
        if not required_events.issubset({row["event_type"] for row in events}):
            return None
        backup_sha256 = str(migration["backup_sha256"])
        if len(backup_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in backup_sha256
        ):
            return None

        def digest(payload: object) -> str:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()

        source_binding = [
            {
                "upload_id": source.upload_id,
                "original_name": source.original_name,
                "sha256": source.sha256,
                "media_type": source.media_type,
            }
            for source in request.sources
        ]
        return HistoricalReverificationEvidence(
            owner_id=request.user_id,
            task_id=request.task_id,
            revision=request.revision,
            run_id=run_id,
            purpose=(
                HistoricalReverificationPurpose
                .SEMANTIC_INCONCLUSIVE_REVERIFICATION
            ),
            legacy_runtime_created_at=runtime_created_at,
            runtime_routing_migration_id="0001_runtime_routing",
            runtime_routing_applied_at=migration_applied_at,
            runtime_routing_backup_sha256=backup_sha256,
            runtime_request_hash=digest(
                frozen_request.model_dump(mode="json", exclude={"api_key"})
            ),
            task_revision_hash=digest(
                {
                    "owner_id": request.user_id,
                    "task_id": request.task_id,
                    "revision": request.revision,
                    "run_id": run_id,
                    "objective_text": request.objective_text,
                    "output_formats": output_formats,
                    "table_output_contracts": table_contracts,
                }
            ),
            source_binding_hash=digest(source_binding),
            runtime_event_chain_hash=digest(
                [
                    {
                        "sequence": row["sequence"],
                        "event_id": row["event_id"],
                        "event_type": row["event_type"],
                        "created_at": row["created_at"],
                    }
                    for row in events
                ]
            ),
            candidate_set_hash=binding.candidate_set_hash,
            candidate_manifest_hash=binding.candidate_manifest_hash,
            goal_contract_hash=binding.goal_contract_hash,
            delivery_spec_hash=binding.delivery_spec_hash,
            previous_attempt_id=binding.previous_attempt_id,
            previous_report_hash=binding.previous_report_hash,
            connection_id=request.model_connection_id,
            connection_version=request.model_connection_version,
            model_id=request.model_connection_model,
        )

    @staticmethod
    def _provider_binding_blocker(
        database: Path,
        request,
    ) -> ReverificationBlocker | None:
        """用 SQLite 只读 URI 复算冻结版本，冷查询不得初始化 Broker/Vault。"""

        try:
            with sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=30,
            ) as connection:
                connection.row_factory = sqlite3.Row
                frozen = connection.execute(
                    "SELECT connection_id, model, secret_id, owner_scope, "
                    "status, "
                    "owner_user_id "
                    "FROM model_connections WHERE connection_id=?",
                    (request.model_connection_id,),
                ).fetchone()
        except (sqlite3.DatabaseError, OSError):
            return ReverificationBlocker.AUTHORITY_UNAVAILABLE
        if frozen is None:
            return ReverificationBlocker.PROVIDER_BINDING_UNAVAILABLE
        if (
            frozen["owner_scope"] == "user_personal"
            and frozen["owner_user_id"] != request.user_id
        ):
            # 冻结请求来自服务端，不接受客户端替换；命中他 Owner 时显式拒绝。
            return ReverificationBlocker.PROVIDER_BINDING_FORBIDDEN
        if frozen["status"] != "verified":
            return ReverificationBlocker.PROVIDER_BINDING_UNAVAILABLE
        try:
            with sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=30,
            ) as connection:
                model = connection.execute(
                    "SELECT 1 FROM model_connection_models "
                    "WHERE connection_id=? AND model_id=? "
                    "AND status='available' AND enabled=1",
                    (
                        request.model_connection_id,
                        request.model_connection_model,
                    ),
                ).fetchone()
        except (sqlite3.DatabaseError, OSError):
            return ReverificationBlocker.AUTHORITY_UNAVAILABLE
        if model is None:
            return ReverificationBlocker.PROVIDER_BINDING_UNAVAILABLE
        secret_version = str(frozen["secret_id"] or frozen["connection_id"])
        version = hashlib.sha256(
            f"{secret_version}\0{frozen['model']}".encode("utf-8")
        ).hexdigest()
        return (
            None
            if version == request.model_connection_version
            else ReverificationBlocker.PROVIDER_BINDING_UNAVAILABLE
        )


def _write_candidate_verification_event(event_type, attempt) -> None:
    store = get_store()
    event_digest = hashlib.sha256(
        (
            f"{attempt.owner_id}\0{attempt.task_id}\0{attempt.attempt_id}\0"
            f"{event_type}\0{attempt.status.value}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    store.append_semantic_workspace_event(
        attempt.owner_id,
        attempt.task_id,
        event_id=f"workspace_event_cv_{event_digest}",
        stage="verify",
        event_type=event_type,
        summary=(
            "候选验证请求已记录"
            if attempt.status.value == "requested"
            else "候选验证已开始"
            if attempt.status.value == "running"
            else "候选验证已形成不可变结论"
        ),
        details={
            "attempt_id": attempt.attempt_id,
            "revision": attempt.revision,
            "run_id": attempt.run_id,
            "reason": attempt.reason_code.value,
            "status": attempt.status.value,
            "ruleset_hash": attempt.verifier_ruleset_hash,
            "report_hash": attempt.report_hash,
            "external_api_confirmed": attempt.egress_confirmed_at is not None,
            "provider_attempt_id": attempt.provider_attempt_id,
        },
    )


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
        agent_kernel: AgentKernel | None = None,
        pi_runtime: PiRuntime | None = None,
        candidate_verification: CandidateVerificationService | None = None,
    ) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._maintenance: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}
        self._reverification_tasks: set[asyncio.Task[None]] = set()
        self._reverification_task_context: dict[
            asyncio.Task[None],
            tuple[str, str],
        ] = {}
        self._queued: set[str] = set()
        self._deferred_requeue: set[str] = set()
        self._delivery_retry_attempts: dict[str, int] = {}
        self._delivery_retry_after: dict[str, float] = {}
        self._heavy = asyncio.Semaphore(1)
        self._candidate_verification = candidate_verification
        self._agent_kernel = agent_kernel
        if agent_kernel is None:
            runtime = pi_runtime or PiRuntime(
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
                candidate_verification=candidate_verification,
            )
            self._agent_kernel = AgentKernel(
                adapter=PiAgentKernelAdapter(runtime),
                repository=lambda: AgenticRuntimeRepository(
                    settings.webui_db_path
                ),
            )

    def _kernel(self) -> AgentKernel:
        """首次需要 Runtime 时才验证数据库 Schema 并建立 Kernel。"""

        assert self._agent_kernel is not None
        return self._agent_kernel

    async def prepare_runtime_binding(
        self,
        *,
        model_connection_id: str | None,
        model_connection_version: str | None,
        model: str,
    ):
        """在任务聚合事务前解析并验证完整 RuntimeBinding。"""

        return await self._kernel().prepare_binding(
            model_connection_id=model_connection_id,
            model_connection_version=model_connection_version,
            model=model,
        )

    def _candidate_verification_module(self) -> CandidateVerificationService:
        if self._candidate_verification is None:
            self._candidate_verification = CandidateVerificationService(
                repository=SqliteCandidateVerificationRepository(
                    settings.webui_db_path
                ),
                ruleset_resolver=CurrentVerifierRulesetResolver(
                    Path(__file__).resolve().parents[2]
                ),
                p0_reader=lambda _request: runtime_routing_is_p0_blocked(
                    settings.webui_db_path
                ),
                broker_adapter=_CandidateVerificationBrokerAdapter(),
                event_writer=_write_candidate_verification_event,
                reverification_authority=_WorkspaceReverificationAuthority(),
                provider_grant_revoker=lambda provider_attempt_id, reason: (
                    get_default_broker().revoke_grant(provider_attempt_id, reason)
                ),
            )
        bind_candidate_verification = getattr(
            self._kernel(),
            "bind_candidate_verification",
            None,
        )
        if bind_candidate_verification is None:
            raise RuntimeError("Pi Runtime 未提供 CandidateVerification 绑定接缝")
        bind_candidate_verification(self._candidate_verification)
        return self._candidate_verification

    def inspect_candidate_reverification(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ):
        """为同步任务详情提供只读 Offer，不触发 Pi 或 Provider。"""

        return self._candidate_verification_module().inspect_reverification_sync(
            owner_id=owner_id,
            task_id=task_id,
            revision=revision,
        )

    async def request_candidate_reverification(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        expected_previous_attempt_id: str,
        expected_candidate_set_hash: str | None,
        expected_target_ruleset_hash: str | None,
        legacy_ruleset_unknown_acknowledged: bool,
        authorization_text_version: str | None,
        external_api_confirmed: bool,
        accept_duplicate_provider_cost: bool,
        historical_authority_recovery: (
            HistoricalAuthorityRecoveryConfirmation | None
        ),
        idempotency_key: str,
    ):
        """先落 requested Attempt，再交给后台任务执行完整验证。"""

        task = get_store().get_semantic_workspace_task(owner_id, task_id)
        if task is None:
            raise KeyError("工作台任务不存在或无权访问")
        module = self._candidate_verification_module()
        verifier_factory = self._build_full_candidate_verifier
        attempt = await module.request_reverification(
            owner_id=owner_id,
            task_id=task_id,
            revision=expected_revision,
            expected_previous_attempt_id=expected_previous_attempt_id,
            expected_candidate_set_hash=expected_candidate_set_hash,
            expected_target_ruleset_hash=expected_target_ruleset_hash,
            legacy_ruleset_unknown_acknowledged=(
                legacy_ruleset_unknown_acknowledged
            ),
            authorization_text_version=authorization_text_version,
            external_api_confirmed=external_api_confirmed,
            accept_duplicate_provider_cost=accept_duplicate_provider_cost,
            historical_authority_recovery=historical_authority_recovery,
            idempotency_key=idempotency_key,
            verifier_factory=verifier_factory,
        )
        if attempt.status is AttemptStatus.REQUESTED:
            self._schedule_candidate_reverification(
                owner_id=owner_id,
                attempt_id=attempt.attempt_id,
                verifier_factory=verifier_factory,
            )
        return attempt

    @staticmethod
    def _require_candidate_delivery_eligible(
        *,
        repository: AgenticRuntimeRepository,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> None:
        """所有发布入口共用 PartialCandidate 失败关闭门。"""

        runtime = repository.get(user_id, task_id, revision)
        if runtime is None or not runtime.get("verified_candidate_set_hash"):
            return
        assessment = repository.get_candidate_coverage(
            user_id=user_id,
            task_id=task_id,
            revision=revision,
            candidate_set_hash=str(runtime["verified_candidate_set_hash"]),
        )
        if assessment is None:
            web_contract = get_store().get_web_task_contract(
                user_id,
                task_id,
                revision,
            )
            if web_contract is not None and (
                web_contract.get("goal_contract", {}).get("coverage")
            ):
                raise ValueError(
                    "网页 Candidate 缺少冻结覆盖结论，禁止正式发布"
                )
        if assessment is not None and not assessment.formal_delivery_eligible:
            raise ValueError(
                "当前结果是 PartialCandidate；原版本覆盖缺口未解决，禁止正式发布"
            )

    async def publish_candidate_verification(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        attempt_id: str,
        idempotency_key: str,
    ):
        """以精确 passed Attempt 显式发布；重放沿用既有发布意图。"""

        store = get_store()
        task = store.get_semantic_workspace_task(owner_id, task_id)
        if task is None:
            raise KeyError("工作台任务不存在或无权访问")
        if int(task["active_revision"]) != expected_revision:
            raise ValueError("活动版本已变化，请查看最新结果后再发布")

        runtime_repository = AgenticRuntimeRepository(settings.webui_db_path)
        self._require_candidate_delivery_eligible(
            repository=runtime_repository,
            user_id=owner_id,
            task_id=task_id,
            revision=expected_revision,
        )

        module = self._candidate_verification_module()
        attempt = module.get_attempt(
            owner_id=owner_id,
            attempt_id=attempt_id,
        )
        adapter = PiCandidateAdapter(
            runtime_repository=runtime_repository,
            workspace_store=store,
            upload_store=_upload_store(),
        )
        command = adapter.build_command(
            owner_id=owner_id,
            task_id=task_id,
            revision=expected_revision,
            verification_attempt=attempt,
            request_idempotency_key=idempotency_key,
        )
        delivery_repository = DeliveryPublishingRepository(
            settings.webui_db_path,
            semantic_paths=ManagedPathCodec(
                settings.semantic_execution_root,
                legacy_anchor=("data", "semantic-executions"),
            ),
        )
        existing_intent = delivery_repository.get_intent(
            command.publication_key
        )
        def has_crossed_commit_point(intent) -> bool:
            return bool(
                intent
                and intent["status"] in {"committing", "published"}
            )

        crossed_commit_point = has_crossed_commit_point(existing_intent)
        if not crossed_commit_point:
            # failed/aborted/staging 都仍在提交点前，重试必须重新计算完整资格。
            try:
                module.prepare_publication(
                    owner_id=owner_id,
                    task_id=task_id,
                    revision=expected_revision,
                    attempt_id=attempt_id,
                )
            except ValueError:
                # Publisher 文件锁外的快照可能刚被同键并发请求推进到提交点。
                refreshed = delivery_repository.get_intent(
                    command.publication_key
                )
                if not has_crossed_commit_point(refreshed):
                    raise
                crossed_commit_point = True

        def publication_gate(_command) -> PublicationGate:
            if not crossed_commit_point:
                # QA 前后都重开精确资格，避免 failed/staging 重试绕过 Ruleset/latest CAS。
                module.prepare_publication(
                    owner_id=owner_id,
                    task_id=task_id,
                    revision=expected_revision,
                    attempt_id=attempt_id,
                )
            current = store.get_semantic_workspace_task(owner_id, task_id)
            return PublicationGate(
                cancel_requested=bool(
                    current and current.get("cancel_requested")
                ),
                p0_blocked=runtime_routing_is_p0_blocked(
                    settings.webui_db_path
                ),
                revision_current=bool(
                    current
                    and int(current["active_revision"]) == expected_revision
                ),
            )

        publisher = DeliveryPublisher(
            repository=delivery_repository,
            output_root=Path(settings.semantic_execution_root),
            candidate_resolver=adapter.resolve_candidates,
            gate_reader=publication_gate,
        )
        try:
            delivery = await asyncio.to_thread(
                publisher.publish,
                command,
                actor_id=owner_id,
            )
        except sqlite3.OperationalError:
            if existing_intent is None:
                store.append_semantic_workspace_event(
                    owner_id,
                    task_id,
                    stage="deliver",
                    event_type="delivery_failed",
                    summary="显式正式交付发布失败，请重试或查看任务状态",
                    details={
                        "attempt_id": attempt_id,
                        "formal_delivery": False,
                    },
                )
            _LOGGER.exception("显式正式交付数据库操作失败")
            raise
        except Exception as exc:
            if existing_intent is None:
                store.append_semantic_workspace_event(
                    owner_id,
                    task_id,
                    stage="deliver",
                    event_type="delivery_failed",
                    summary="显式正式交付发布失败，请重试或查看任务状态",
                    details={
                        "attempt_id": attempt_id,
                        "formal_delivery": False,
                    },
                )
            _LOGGER.exception("显式正式交付发布失败")
            raise ValueError("显式正式交付发布失败，请查看任务状态") from exc

        current = store.get_semantic_workspace_task(owner_id, task_id)
        if current is not None and current["status"] != "completed":
            try:
                store.update_semantic_workspace_task(
                    owner_id,
                    task_id,
                    expected_active_revision=expected_revision,
                    status="completed",
                    summary=(
                        "候选通过完整重验，已发布 "
                        f"{len(delivery.outputs)} 个正式文件"
                    ),
                    error=None,
                    failure=None,
                    question=None,
                    cancel_requested=False,
                )
            except RuntimeError:
                # Delivery 已按旧 Revision 提交；新 Revision 的任务状态不得被旧结果覆盖。
                return delivery
            store.update_semantic_workspace_revision(
                owner_id,
                task_id,
                expected_revision,
                status="completed",
                summary="候选通过完整重验并发布正式交付",
            )
            store.append_semantic_workspace_event(
                owner_id,
                task_id,
                stage="deliver",
                event_type="task_completed",
                summary=(
                    "完整重验与独立 QA 通过，已发布 "
                    f"{len(delivery.outputs)} 个正式文件"
                ),
                details={
                    "attempt_id": attempt_id,
                    "delivery_id": delivery.delivery_id,
                    "publication_key": command.publication_key,
                    "formal_delivery": True,
                },
            )
        return delivery

    def _schedule_candidate_reverification(
        self,
        *,
        owner_id: str,
        attempt_id: str,
        verifier_factory,
    ) -> None:
        if (owner_id, attempt_id) in self._reverification_task_context.values():
            return
        worker = asyncio.create_task(
            self._execute_candidate_reverification(
                owner_id=owner_id,
                attempt_id=attempt_id,
                verifier_factory=verifier_factory,
            ),
            name=f"candidate-reverification-{attempt_id}",
        )
        self._reverification_tasks.add(worker)
        self._reverification_task_context[worker] = (owner_id, attempt_id)
        worker.add_done_callback(self._candidate_reverification_done)

    def _candidate_reverification_done(self, task: asyncio.Task[None]) -> None:
        self._reverification_tasks.discard(task)
        context = self._reverification_task_context.pop(task, None)
        if task.cancelled():
            if context is not None:
                owner_id, attempt_id = context
                try:
                    self._candidate_verification_module().close_unstarted_reverification(
                        owner_id=owner_id,
                        attempt_id=attempt_id,
                    )
                except Exception:
                    _LOGGER.exception(
                        "候选重验后台任务取消后未能安全收口：%s",
                        task.get_name(),
                    )
            return
        error = task.exception()
        if error is not None:
            _LOGGER.error(
                "候选重验后台任务未能安全收口：%s",
                task.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _execute_candidate_reverification(
        self,
        *,
        owner_id: str,
        attempt_id: str,
        verifier_factory,
    ) -> None:
        lease = self._candidate_reverification_lease(attempt_id)
        try:
            lease.acquire(timeout=0)
        except Timeout:
            # 另一 Web 进程仍在处理同一 Attempt；本进程不得重复执行或收口。
            return
        try:
            try:
                await self._candidate_verification_module().execute_requested_reverification(
                    owner_id=owner_id,
                    attempt_id=attempt_id,
                    verifier_factory=verifier_factory,
                )
            except asyncio.CancelledError:
                self._candidate_verification_module().close_unstarted_reverification(
                    owner_id=owner_id,
                    attempt_id=attempt_id,
                )
                raise
            except Exception:
                # 进程崩溃会保留 running 供租约恢复；预执行失败需释放 requested 活动槽。
                self._candidate_verification_module().close_unstarted_reverification(
                    owner_id=owner_id,
                    attempt_id=attempt_id,
                )
                raise
        finally:
            lease.release()

    @staticmethod
    def _candidate_reverification_lease(attempt_id: str) -> FileLock:
        database = Path(settings.webui_db_path)
        lock_path = database.with_name(
            f"{database.name}.candidate-reverification-{attempt_id}.lock"
        )
        return FileLock(str(lock_path))

    def _recover_interrupted_candidate_reverifications(self, module) -> None:
        try:
            attempts = module.list_running_reverifications()
        except Exception:
            _LOGGER.exception("候选重验 running 扫描暂时失败，下一轮将重试")
            return
        for attempt in attempts:
            lease = self._candidate_reverification_lease(attempt.attempt_id)
            try:
                lease.acquire(timeout=0)
            except Timeout:
                # 滚动启动期间旧进程仍持有租约，不能把活跃 Worker 误判为崩溃。
                continue
            try:
                try:
                    module.recover_interrupted_reverification(attempt)
                except Exception:
                    _LOGGER.exception(
                        "候选重验 running 收口失败，下一轮将重试：%s",
                        attempt.attempt_id,
                    )
            finally:
                lease.release()

    def _resume_requested_candidate_reverifications(self, module) -> None:
        try:
            attempts = module.list_requested_reverifications()
        except Exception:
            _LOGGER.exception("候选重验 requested 扫描暂时失败，下一轮将重试")
            return
        for attempt in attempts:
            try:
                module.ensure_requested_event(attempt)
                self._schedule_candidate_reverification(
                    owner_id=attempt.owner_id,
                    attempt_id=attempt.attempt_id,
                    verifier_factory=self._build_full_candidate_verifier,
                )
            except Exception:
                _LOGGER.exception(
                    "候选重验 requested 接管失败，下一轮将重试：%s",
                    attempt.attempt_id,
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
        try:
            module = self._candidate_verification_module()
        except RuntimeError as exc:
            # 显式迁移前不创建表；没有 CandidateVerification Schema 时只跳过恢复。
            _LOGGER.info("候选重验恢复尚不可用：%s", exc)
        else:
            self._recover_interrupted_candidate_reverifications(module)
            self._resume_requested_candidate_reverifications(module)
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

    def workers_ready(self) -> bool:
        """两个工作器都已启动且未退出时，进程才可以继续接单。"""

        return len(self._workers) == 2 and all(
            not worker.done() for worker in self._workers
        )

    async def stop(self) -> None:
        reverification_tasks = tuple(self._reverification_tasks)
        for task in reverification_tasks:
            task.cancel()
        for task in self._active.values():
            task.cancel()
        for task in self._workers:
            task.cancel()
        if self._maintenance is not None:
            self._maintenance.cancel()
        tasks = [*self._active.values(), *self._workers]
        if self._maintenance is not None:
            tasks.append(self._maintenance)
        for task in reverification_tasks:
            # 异常已由专用 done callback 记录；关闭流程只负责消费，不能二次击穿 lifespan。
            with suppress(asyncio.CancelledError, Exception):
                await task
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._active.clear()
        self._reverification_tasks.clear()
        self._reverification_task_context.clear()
        self._workers.clear()
        self._maintenance = None
        self._queued.clear()
        self._deferred_requeue.clear()
        self._delivery_retry_attempts.clear()
        self._delivery_retry_after.clear()

    async def _maintenance_loop(self) -> None:
        """持续接管重验孤儿，并每小时清理一次到期回收站记录。"""

        last_purge = time.monotonic()
        while True:
            await asyncio.sleep(REVERIFICATION_RECOVERY_POLL_SECONDS)
            try:
                module = self._candidate_verification_module()
            except RuntimeError:
                module = None
            if module is not None:
                try:
                    self._recover_interrupted_candidate_reverifications(module)
                    self._resume_requested_candidate_reverifications(module)
                except Exception:
                    # 单轮意外失败不得杀死维护任务；下一轮继续接管。
                    _LOGGER.exception("候选重验维护轮次失败，下一轮将重试")
            # worker 异常退出或 Publisher 暂时失败后，持久化的非终态任务由
            # 当前进程重新接管；enqueue 会跳过仍在执行的同一 Task。
            try:
                pending_tasks = get_store().list_pending_semantic_workspace_tasks()
                now = time.monotonic()
                for pending in pending_tasks:
                    if self._delivery_retry_after.get(
                        pending["task_id"], 0.0
                    ) > now:
                        continue
                    self.enqueue(pending["user_id"], pending["task_id"])
            except Exception:
                _LOGGER.exception("工作台非终态任务接管失败，下一轮将重试")
            if time.monotonic() - last_purge >= WORKSPACE_PURGE_INTERVAL_SECONDS:
                try:
                    get_store().purge_expired_semantic_workspace_tasks()
                except Exception:
                    _LOGGER.exception("工作台到期清理暂时失败，下一轮将重试")
                else:
                    last_purge = time.monotonic()

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
                await self._kernel().cancel(user_id, task_id, revision)
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
            await self._kernel().cancel(
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
        provider_attempt_id: str | None = None,
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
                provider_attempt_id=provider_attempt_id,
                allow_response_retry=provider_attempt_id is None,
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

    def _build_full_candidate_verifier(
        self,
        request: PiRuntimeRequest,
        run_id: str,
        provider_attempt_id: str | None = None,
    ) -> CandidateVerifier:
        return CandidateVerifier(
            semantic_judge=self._build_retry_semantic_judge(
                request,
                run_id,
                provider_attempt_id,
            )
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
        candidate_verification = self._candidate_verification_module()
        store.append_semantic_workspace_event(
            user_id,
            task_id,
            stage="verify",
            event_type="candidate_verification_retry_started",
            summary="正在重新验证现有候选，不会重新读取来源或生成文件",
            details={"formal_delivery": False},
        )
        attempt = await candidate_verification.retry_current_semantic(
            owner_id=user_id,
            task_id=task_id,
            revision=revision,
            verifier_factory=lambda request, run_id: CandidateVerifier(
                semantic_judge=self._build_retry_semantic_judge(
                    request,
                    run_id,
                )
            ),
        )
        assert attempt.report_json is not None
        verification = VerificationReport.model_validate_json(
            attempt.report_json
        )
        repository = AgenticRuntimeRepository(settings.webui_db_path)
        runtime = repository.get(user_id, task_id, revision)
        assert runtime is not None
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
        self._require_candidate_delivery_eligible(
            repository=repository,
            user_id=user_id,
            task_id=task_id,
            revision=revision,
        )
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
            # 迁移激活后只读中央 Rollout；Agent 与发布命令都不能覆盖 P0 结论。
            return PublicationGate(
                cancel_requested=bool(
                    current and current.get("cancel_requested")
                ),
                p0_blocked=runtime_routing_is_p0_blocked(settings.webui_db_path),
            )

        publisher = DeliveryPublisher(
            repository=DeliveryPublishingRepository(
                settings.webui_db_path,
                semantic_paths=ManagedPathCodec(
                    settings.semantic_execution_root,
                    legacy_anchor=("data", "semantic-executions"),
                ),
            ),
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
            retryable = self._delivery_error_is_retryable(exc)
            store.append_semantic_workspace_event(
                user_id,
                task_id,
                stage="deliver",
                event_type="delivery_failed",
                summary=f"正式交付发布失败：{str(exc)[:300]}",
                details={
                    "formal_delivery": False,
                    "retryable": retryable,
                },
            )
            message = f"正式交付发布失败：{str(exc) or exc.__class__.__name__}"
            if retryable:
                raise _DeliveryRetryPending(message) from exc
            raise ValueError(message) from exc
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
        self._delivery_retry_attempts.pop(task_id, None)
        self._delivery_retry_after.pop(task_id, None)

    @staticmethod
    def _delivery_error_is_retryable(exc: Exception) -> bool:
        """只重试明确的锁、数据库暂态和文件系统 I/O 故障。"""

        current: BaseException | None = exc
        while current is not None:
            if isinstance(current, (sqlite3.OperationalError, OSError, Timeout)):
                return True
            current = current.__cause__
        return False

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

    async def _apply_waiting_revision_at_safe_point(
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

        await _apply_confirmed_steering_revision(
            get_store().get_user(user_id)
            or {"user_id": user_id, "role": "user"},
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
                if await self._apply_waiting_revision_at_safe_point(
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
            if await self._apply_waiting_revision_at_safe_point(
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
        except _DeliveryRetryPending as exc:
            # Candidate/Verification 已经持久化，不能把任务标成普通失败后再跑 Agent。
            # 保持可恢复状态；服务重启或维护轮次只会重试同一 PublicationKey。
            store.update_semantic_workspace_task(
                user_id,
                task_id,
                status="running",
                summary=str(exc),
                error=None,
                failure=None,
            )
            store.update_semantic_workspace_revision(
                user_id,
                task_id,
                revision,
                status="running",
                summary="候选已冻结，正式交付等待恢复",
            )
            attempts = self._delivery_retry_attempts.get(task_id, 0) + 1
            self._delivery_retry_attempts[task_id] = attempts
            self._delivery_retry_after[task_id] = time.monotonic() + min(
                60.0,
                2.0 * (2 ** (attempts - 1)),
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
        if (
            runtime["status"] is RuntimeStatus.CANDIDATE_READY
            and runtime["verification"] is not None
            and runtime["verification"].status is VerificationStatus.PASSED
        ):
            await self._publish_verified_candidates(
                user_id=user_id,
                task_id=task_id,
                revision=revision,
                repository=repository,
                upload_store=_upload_store(),
            )
            return
        checkpoint = None
        if (
            runtime["status"]
            in {
                RuntimeStatus.PREPARING,
                RuntimeStatus.RUNNING,
                RuntimeStatus.NEEDS_INPUT,
            }
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
        web_repository = SourceAcquisitionRepository(settings.webui_db_path)
        for source_ref in task_revision.get("source_refs", []):
            if source_ref.get("kind") != "web_artifact":
                continue
            artifact = web_repository.get_artifact(
                user_id,
                str(source_ref["artifact_id"]),
                include_content=True,
            )
            if (
                artifact is None
                or artifact["snapshot_id"] != source_ref.get("snapshot_id")
                or artifact["content_sha256"] != source_ref.get("sha256")
            ):
                raise ValueError("冻结网页来源身份不存在或与任务修订不一致")
            content = bytes(artifact["content_blob"])
            if hashlib.sha256(content).hexdigest() != artifact["content_sha256"]:
                raise ValueError("冻结网页来源内容哈希校验失败")
            owner_key = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
            artifact_key = hashlib.sha256(
                artifact["artifact_id"].encode("utf-8")
            ).hexdigest()[:24]
            source_path = (
                Path(settings.semantic_execution_root)
                / "frozen-web-sources"
                / owner_key
                / f"{artifact_key}.html"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            if (
                not source_path.exists()
                or hashlib.sha256(source_path.read_bytes()).hexdigest()
                != artifact["content_sha256"]
            ):
                temporary = source_path.with_suffix(".tmp")
                temporary.write_bytes(content)
                temporary.replace(source_path)
            sources.append(
                SourceInput(
                    upload_id=artifact["artifact_id"],
                    original_name=(artifact["title"] or artifact["final_url"]),
                    host_path=source_path,
                    sha256=artifact["content_sha256"],
                    media_type=artifact["media_type"],
                )
            )
        web_contract = get_store().get_web_task_contract(
            user_id,
            task_id,
            revision,
        )
        frozen_context = TaskContextRepository(
            settings.webui_db_path
        ).get_frozen(user_id, task_id, revision)
        web_snapshot = (
            web_repository.get_snapshot(
                user_id,
                web_contract["source_snapshot_id"],
            )
            if web_contract is not None
            else None
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
            "goal_contract": (
                web_contract["goal_contract"]
                if web_contract is not None
                else None
            ),
            "compiled_context": (
                frozen_context.compiled_context
                if frozen_context is not None
                else None
            ),
            "source_coverage": (
                {
                    **(web_snapshot.get("coverage") or {}),
                    "valid_page_count": int(web_snapshot["valid_page_count"]),
                    "failed_page_count": int(web_snapshot["failed_page_count"]),
                }
                if web_snapshot is not None
                else None
            ),
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
        self._candidate_verification_module()
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
                and await self._apply_waiting_revision_at_safe_point(
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
                    self._kernel().resume(
                        request,
                        checkpoint=checkpoint,
                        on_event=on_event,
                    )
                )
            else:
                execution = asyncio.ensure_future(
                    self._kernel().start(
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
            await self._kernel().cancel(user_id, task_id, revision)
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
        if result.verification is None:
            raise ValueError("Pi Runtime 未形成独立验证结论")
        repository.update(
            user_id,
            task_id,
            revision,
            run_id=result.run_id,
            container_name=result.container_name,
            workspace_root=result.workspace_root,
            session_file=result.session_file,
        )
        verification = result.verification
        saved_runtime = repository.update(
            user_id,
            task_id,
            revision,
            status=result.status,
            candidates=result.candidates,
            verification=verification,
        )
        candidate_coverage = result.candidate_coverage
        candidate_set_hash = saved_runtime.get("verified_candidate_set_hash")
        if candidate_coverage is not None:
            if not candidate_set_hash:
                raise ValueError("Candidate 覆盖结论缺少冻结候选集合身份")
            repository.save_candidate_coverage(
                user_id=user_id,
                task_id=task_id,
                revision=revision,
                run_id=result.run_id,
                candidate_set_hash=str(candidate_set_hash),
                assessment=candidate_coverage,
            )
        verification_passed = bool(
            verification and verification.status.value == "passed"
            and (
                candidate_coverage is None
                or candidate_coverage.formal_delivery_eligible
            )
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
                candidate_coverage.conclusion.reason
                if candidate_coverage is not None
                and candidate_coverage.conclusion is not None
                else verification.summary
                if verification is not None
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
                candidate_coverage.conclusion.reason
                if candidate_coverage is not None
                and candidate_coverage.conclusion is not None
                else verification.summary
                if verification is not None
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
                "candidate_kind": (
                    "partial_candidate"
                    if candidate_coverage is not None
                    and candidate_coverage.is_partial
                    else "candidate"
                ),
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
            external_provider = bool(runtime.get("model_connection_id"))
            outcome_unknown = (
                "模型请求结果不确定" in message
                or (
                    external_provider
                    and any(
                        marker in message
                        for marker in (
                            "Pi 执行超过",
                            "Pi RPC 在任务稳定结束前退出",
                        )
                    )
                )
            )
            failure = {
                "error_code": (
                    "MODEL_OUTCOME_UNKNOWN"
                    if outcome_unknown
                    else "PI_RUNTIME_FAILED"
                ),
                "stage": "execute",
                "cause_summary": message[:500],
                "attempt_count": 1,
                "elapsed_ms": elapsed_ms,
                "source_read": source_read,
                "intermediate_created": _pi_has_user_output(runtime),
                "delivery_published": False,
                "next_actions": (
                    [
                        "由你决定是否创建新版本重新执行",
                        "取消并保留当前失败记录",
                    ]
                    if outcome_unknown
                    else [
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


_manager: SemanticWorkspaceManager | None = None


def get_semantic_workspace_manager() -> SemanticWorkspaceManager:
    global _manager
    if _manager is None:
        # 模块导入只注册路由；真实应用 lifespan 首次取用时才验证 Schema 并失败关闭。
        _manager = SemanticWorkspaceManager()
    return _manager
