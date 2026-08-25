# -*- coding: utf-8 -*-
"""统一承接候选验证生命周期与兼容投影。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Protocol

from src.agentic_runtime.models import (
    CandidateArtifact,
    PiRuntimeRequest,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from src.model_connections import ProviderOutcomeUnknownError

from .models import (
    AttemptReason,
    AttemptStatus,
    ReverificationBlocker,
    ReverificationOffer,
    RulesetIdentityStatus,
    VerificationAttempt,
    VerifierRulesetBinding,
)
from .repository import SqliteCandidateVerificationRepository


class CandidateVerifierPort(Protocol):
    async def verify(
        self,
        *,
        request: PiRuntimeRequest,
        candidates: tuple[CandidateArtifact, ...],
        manifest_path: Path,
    ) -> VerificationReport: ...

    async def retry_semantic_verification(
        self,
        *,
        request: PiRuntimeRequest,
        candidates: tuple[CandidateArtifact, ...],
        manifest_path: Path,
        previous_report: VerificationReport,
    ) -> VerificationReport: ...


class RulesetResolverPort(Protocol):
    def resolve(self, verifier: CandidateVerifierPort) -> VerifierRulesetBinding: ...

    def resolve_target(self) -> VerifierRulesetBinding: ...


class P0ReaderPort(Protocol):
    def __call__(self, request: PiRuntimeRequest) -> bool: ...


class BrokerAdapterPort(Protocol):
    def assert_verifier_binding(
        self,
        request: PiRuntimeRequest,
        run_id: str,
        verifier: CandidateVerifierPort,
    ) -> None: ...


class VerificationEventWriterPort(Protocol):
    def __call__(
        self,
        event_type: str,
        attempt: VerificationAttempt,
    ) -> None: ...


class ProviderGrantRevokerPort(Protocol):
    def __call__(self, provider_attempt_id: str, reason: str) -> bool: ...


class SemanticVerifierFactoryPort(Protocol):
    def __call__(
        self,
        request: PiRuntimeRequest,
        run_id: str,
        provider_attempt_id: str | None = None,
    ) -> CandidateVerifierPort: ...


class ReverificationAuthorityPort(Protocol):
    def blockers(
        self,
        request: PiRuntimeRequest,
        run_id: str,
    ) -> tuple[ReverificationBlocker, ...]: ...


class ReverificationUnavailableError(RuntimeError):
    """当前服务环境无法可靠冻结或执行重验。"""


class ReverificationContractError(ValueError):
    """冻结候选或任务契约缺失、损坏，调用方必须先修复数据。"""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate_set_hash(candidates: tuple[CandidateArtifact, ...]) -> str:
    payload = [
        {
            "artifact_id": item.artifact_id,
            "filename": item.filename,
            "format": item.format,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in sorted(candidates, key=lambda value: value.artifact_id)
    ]
    return _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _request_contract_hashes(request: PiRuntimeRequest) -> tuple[str, str]:
    goal_payload = {
        "owner_id": request.user_id,
        "task_id": request.task_id,
        "revision": request.revision,
        "objective_text": request.objective_text,
        "permission_profile": request.permission_profile.value,
        "external_api_confirmed": request.external_api_confirmed,
        "model_connection_id": request.model_connection_id,
        "model_connection_version": request.model_connection_version,
        "model_connection_model": request.model_connection_model,
        "local_model": request.model,
        "local_base_url_hash": (
            _sha256_bytes(request.base_url.encode("utf-8"))
            if request.base_url is not None
            else None
        ),
        "sources": [
            {
                "upload_id": source.upload_id,
                "original_name": source.original_name,
                "sha256": source.sha256,
            }
            for source in request.sources
        ],
    }
    delivery_payload = {
        "requested_output_formats": request.requested_output_formats,
        "table_output_contracts": [
            item.model_dump(mode="json") for item in request.table_output_contracts
        ],
    }
    encode = lambda value: json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encode(goal_payload)), _sha256_bytes(
        encode(delivery_payload)
    )


def _manifest_identity_matches(
    manifest_bytes: bytes,
    request: PiRuntimeRequest,
    candidates: tuple[CandidateArtifact, ...],
) -> bool:
    """复核 Manifest 内部身份，不能只信任初验时冻结的整文件哈希。"""

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        artifacts = manifest["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            return False
        manifest_map = {str(item["filename"]): item for item in artifacts}
        candidate_map = {item.filename: item for item in candidates}
        if (
            len(manifest_map) != len(artifacts)
            or len(candidate_map) != len(candidates)
            or set(manifest_map) != set(candidate_map)
        ):
            return False
        allowed_sources = {
            value
            for source in request.sources
            for value in (source.upload_id, source.original_name)
        }
        for filename, candidate in candidate_map.items():
            artifact = manifest_map[filename]
            evidence = artifact["evidence"]
            if str(artifact["format"]).lower() != candidate.format:
                return False
            if not isinstance(evidence, list) or not evidence:
                return False
            if any(
                not isinstance(item, dict)
                or str(item.get("source") or "") not in allowed_sources
                or not str(item.get("locator") or "").strip()
                or not str(item.get("quote") or "").strip()
                for item in evidence
            ):
                return False
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


class CandidateVerificationService:
    """把 Attempt 生命周期和旧 Runtime 投影收口到一个提交边界。"""

    def __init__(
        self,
        *,
        repository: SqliteCandidateVerificationRepository,
        ruleset_resolver: RulesetResolverPort,
        p0_reader: P0ReaderPort,
        broker_adapter: BrokerAdapterPort,
        event_writer: VerificationEventWriterPort,
        reverification_authority: ReverificationAuthorityPort | None = None,
        provider_grant_revoker: ProviderGrantRevokerPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._ruleset_resolver = ruleset_resolver
        self._p0_reader = p0_reader
        self._broker_adapter = broker_adapter
        self._event_writer = event_writer
        self._reverification_authority = reverification_authority
        self._provider_grant_revoker = provider_grant_revoker
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def verify_initial(
        self,
        *,
        request: PiRuntimeRequest,
        run_id: str,
        candidates: tuple[CandidateArtifact, ...],
        manifest_path: Path,
        verifier: CandidateVerifierPort,
        actor_id: str,
        idempotency_key: str,
        goal_contract_hash: str,
        delivery_spec_hash: str,
    ) -> VerificationAttempt:
        async def operation() -> VerificationReport:
            return await verifier.verify(
                request=request,
                candidates=candidates,
                manifest_path=manifest_path,
            )

        return await self._execute(
            request=request,
            run_id=run_id,
            candidates=candidates,
            manifest_path=manifest_path,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            goal_contract_hash=goal_contract_hash,
            delivery_spec_hash=delivery_spec_hash,
            reason=AttemptReason.INITIAL,
            previous_attempt_id=None,
            verifier=verifier,
            operation=operation,
        )

    async def verify_initial_current(
        self,
        *,
        request: PiRuntimeRequest,
        run_id: str,
        candidates: tuple[CandidateArtifact, ...],
        manifest_path: Path,
        verifier: CandidateVerifierPort,
        actor_id: str,
    ) -> VerificationAttempt:
        goal_hash, delivery_hash = _request_contract_hashes(request)
        candidate_hash = _candidate_set_hash(candidates)
        return await self.verify_initial(
            request=request,
            run_id=run_id,
            candidates=candidates,
            manifest_path=manifest_path,
            verifier=verifier,
            actor_id=actor_id,
            idempotency_key=f"initial:{run_id}:{candidate_hash}",
            goal_contract_hash=goal_hash,
            delivery_spec_hash=delivery_hash,
        )

    async def retry_current_semantic(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
        verifier_factory: SemanticVerifierFactoryPort,
    ) -> VerificationAttempt:
        context = self._repository.get_runtime_context(
            owner_id,
            task_id,
            revision,
        )
        if (
            context is None
            or context["runtime_version"] != "pi"
            or context["status"] != "candidate_ready"
            or not context["run_id"]
            or not context["workspace_root"]
            or not context["request_json"]
            or not context["candidates_json"]
            or not context["verification_json"]
        ):
            raise ValueError("当前任务没有可重新验证的候选")
        try:
            request_values = json.loads(str(context["request_json"]))
            if not request_values.get("model_connection_id"):
                # 本地直连密钥从不落库；重验时只恢复固定占位值。
                request_values["api_key"] = "local-runtime"
            request = PiRuntimeRequest.model_validate(request_values)
            candidates = tuple(
                CandidateArtifact.model_validate(item)
                for item in json.loads(str(context["candidates_json"]))
            )
            previous_report = VerificationReport.model_validate_json(
                str(context["verification_json"])
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("候选缺少冻结运行信息，不能重新验证") from exc
        if previous_report.status is not VerificationStatus.INCONCLUSIVE:
            raise ValueError("当前任务没有可重新验证的候选")
        run_id = str(context["run_id"])
        candidate_hash = _candidate_set_hash(candidates)
        if context["verified_candidate_set_hash"] != candidate_hash:
            raise ValueError("候选集合已漂移，不能重新验证")
        report_hash = _sha256_bytes(
            str(context["verification_json"]).encode("utf-8")
        )
        history = self._repository.list_for_candidate(
            owner_id,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            candidate_set_hash=candidate_hash,
        )
        previous = next(
            (
                item
                for item in reversed(history)
                if item.report_hash == report_hash
                and item.status is AttemptStatus.INCONCLUSIVE
            ),
            None,
        )
        if previous is None:
            raise ValueError("当前验证投影缺少精确 Attempt 依据")
        goal_hash, delivery_hash = _request_contract_hashes(request)
        verifier = verifier_factory(request, run_id)
        return await self.retry_semantic(
            request=request,
            run_id=run_id,
            candidates=candidates,
            manifest_path=(
                Path(str(context["workspace_root"]))
                / "output"
                / "candidate-manifest.json"
            ),
            verifier=verifier,
            previous_report=previous_report,
            previous_attempt_id=previous.attempt_id,
            actor_id=owner_id,
            idempotency_key=(
                f"semantic:{run_id}:{candidate_hash}:{previous.report_hash}"
            ),
            goal_contract_hash=goal_hash,
            delivery_spec_hash=delivery_hash,
        )

    async def inspect_reverification(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> ReverificationOffer:
        return await asyncio.to_thread(
            self.inspect_reverification_sync,
            owner_id=owner_id,
            task_id=task_id,
            revision=revision,
        )

    def inspect_reverification_sync(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> ReverificationOffer:
        """只读计算当前 CandidateSet 是否具备同 Run 重验资格。"""

        context = self._repository.get_runtime_context(owner_id, task_id, revision)
        if (
            context is None
            or context["runtime_version"] != "pi"
            or context["status"] != "candidate_ready"
            or not context["run_id"]
        ):
            raise ValueError("当前任务没有可检查的候选")
        if not context["request_json"] or not context["candidates_json"]:
            raise ReverificationContractError(
                "候选缺少冻结运行信息，不能检查重验资格"
            )
        try:
            request_values = json.loads(str(context["request_json"]))
            if not request_values.get("model_connection_id"):
                request_values["api_key"] = "local-runtime"
            request = PiRuntimeRequest.model_validate(request_values)
            candidates = tuple(
                CandidateArtifact.model_validate(item)
                for item in json.loads(str(context["candidates_json"]))
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ReverificationContractError(
                "候选缺少冻结运行信息，不能检查重验资格"
            ) from exc
        blockers: list[ReverificationBlocker] = []
        if self._reverification_authority is None:
            blockers.append(ReverificationBlocker.AUTHORITY_UNAVAILABLE)
        else:
            blockers.extend(
                self._reverification_authority.blockers(
                    request,
                    str(context["run_id"]),
                )
            )
        if self._p0_reader(request):
            # P0 是生产硬门；只读 Offer 也不能暗示可以绕过它启动新 Attempt。
            blockers.append(ReverificationBlocker.P0_BLOCKED)
        delivery_exists = self._repository.has_succeeded_delivery(
            owner_id,
            str(context["run_id"]),
        )
        if delivery_exists:
            # 新旧任一正式 Delivery 都代表候选已发布，禁止重复重验覆盖业务语义。
            blockers.append(ReverificationBlocker.DELIVERY_EXISTS)
        # Offer 必须重新读取当前文件字节；数据库中的旧摘要不是继续授权的充分依据。
        try:
            candidate_drift = any(
                item.host_path.stat().st_size != item.size_bytes
                or _sha256_bytes(item.host_path.read_bytes()) != item.sha256
                for item in candidates
            )
        except OSError:
            candidate_drift = True
        if candidate_drift:
            blockers.append(ReverificationBlocker.CANDIDATE_DRIFT)
        try:
            source_drift = any(
                _sha256_bytes(source.host_path.read_bytes()) != source.sha256
                for source in request.sources
            )
        except OSError:
            source_drift = True
        if source_drift:
            blockers.append(ReverificationBlocker.SOURCE_DRIFT)
        candidate_hash = _candidate_set_hash(candidates)
        verified_candidate_hash = str(
            context["verified_candidate_set_hash"] or ""
        )
        if (
            verified_candidate_hash
            and verified_candidate_hash != candidate_hash
            and ReverificationBlocker.CANDIDATE_DRIFT not in blockers
        ):
            blockers.append(ReverificationBlocker.CANDIDATE_DRIFT)
        history = self._repository.list_for_candidate(
            owner_id,
            task_id=task_id,
            revision=revision,
            run_id=str(context["run_id"]),
            candidate_set_hash=verified_candidate_hash or candidate_hash,
        )
        if not history:
            raise ValueError("当前验证投影缺少精确 Attempt 依据")
        previous = history[-1]
        if previous.status in {AttemptStatus.REQUESTED, AttemptStatus.RUNNING}:
            # 活动 Attempt 的结论尚未冻结，再开放写入口会制造并发双真相。
            blockers.append(ReverificationBlocker.ACTIVE_ATTEMPT)
        elif previous.status is AttemptStatus.OUTCOME_UNKNOWN:
            # Provider 是否已执行无法确认时不得自动重发，必须先由 Owner 核对。
            blockers.append(ReverificationBlocker.OUTCOME_UNKNOWN)
        if previous.ruleset_identity_status is RulesetIdentityStatus.LEGACY_UNVERSIONED:
            # 历史记录没有可证明的规则和契约身份，不能把“未知”伪装成漂移或未变化。
            blockers.append(ReverificationBlocker.LEGACY_UNVERSIONED)
        else:
            try:
                manifest_path = (
                    Path(str(context["workspace_root"]))
                    / "output"
                    / "candidate-manifest.json"
                )
                manifest_bytes = manifest_path.read_bytes()
                manifest_drift = (
                    previous.manifest_hash is None
                    or _sha256_bytes(manifest_bytes) != previous.manifest_hash
                    or not _manifest_identity_matches(
                        manifest_bytes,
                        request,
                        candidates,
                    )
                )
            except OSError:
                manifest_drift = True
            if manifest_drift:
                blockers.append(ReverificationBlocker.MANIFEST_DRIFT)
            # Task 目标与输出契约任一改变都应创建新 Revision，不能复用旧 Candidate。
            goal_hash, delivery_hash = _request_contract_hashes(request)
            if previous.goal_contract_hash != goal_hash:
                blockers.append(ReverificationBlocker.GOAL_CONTRACT_DRIFT)
            if previous.delivery_spec_hash != delivery_hash:
                blockers.append(ReverificationBlocker.DELIVERY_SPEC_DRIFT)
        try:
            target = self._ruleset_resolver.resolve_target()
        except RuntimeError:
            # 普通用户只需要知道当前规则身份不可证明，不能泄露源码或依赖细节。
            blockers.append(ReverificationBlocker.RULESET_UNAVAILABLE)
            ruleset_changed = None
        else:
            ruleset_changed = (
                None
                if previous.ruleset_identity_status
                is RulesetIdentityStatus.LEGACY_UNVERSIONED
                else previous.verifier_ruleset_hash
                != target.verifier_ruleset_hash
            )
        semantic_retry = previous.status is AttemptStatus.INCONCLUSIVE
        ruleset_retry = (
            previous.status is AttemptStatus.FAILED and ruleset_changed is True
        )
        eligible = (semantic_retry or ruleset_retry) and not blockers
        reason = (
            AttemptReason.SEMANTIC_INCONCLUSIVE
            if semantic_retry
            else AttemptReason.RULESET_CHANGED if ruleset_retry else None
        )
        if not eligible:
            reason = None
        if blockers:
            final_blockers = tuple(dict.fromkeys(blockers))
        elif semantic_retry or ruleset_retry:
            final_blockers = ()
        elif previous.status is AttemptStatus.PASSED:
            final_blockers = (ReverificationBlocker.ALREADY_PASSED,)
        elif previous.status is AttemptStatus.CANCELLED:
            final_blockers = (ReverificationBlocker.PREVIOUS_CANCELLED,)
        else:
            final_blockers = (ReverificationBlocker.RULESET_UNCHANGED,)
        return ReverificationOffer(
            eligible=eligible,
            reason=reason,
            blockers=final_blockers,
            previous_attempt_id=previous.attempt_id,
            previous_status=previous.status,
            previous_reason=previous.reason_code,
            ruleset_identity_status=previous.ruleset_identity_status,
            ruleset_changed=ruleset_changed,
            ruleset_change_summary=(
                "当前验证规则已更新"
                if ruleset_changed is True
                else "当前验证规则与上次相同"
                if ruleset_changed is False
                else "当前验证规则身份暂时无法证明"
            ),
            candidate_count=len(candidates),
            candidate_formats=tuple(sorted({item.format for item in candidates})),
            requires_provider=bool(request.model_connection_id),
            connection_id=request.model_connection_id,
            model_id=request.model_connection_model or request.model,
            egress_categories=(
                ("task_goal", "candidate_previews", "source_evidence")
                if request.model_connection_id
                else ()
            ),
            egress_summary=(
                "将外发任务目标、候选预览和来源证据"
                if request.model_connection_id
                else "本次不外发"
            ),
            awaiting_publication=(
                previous.status is AttemptStatus.PASSED
                and not delivery_exists
                and not blockers
            ),
        )

    def get_attempt(
        self,
        *,
        owner_id: str,
        attempt_id: str,
    ) -> VerificationAttempt:
        """按 Owner 读取精确 Attempt，不向其他 Owner 泄露其存在性。"""

        attempt = self._repository.get(owner_id, attempt_id)
        if attempt is None:
            raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
        return attempt

    def prepare_publication(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
        attempt_id: str,
    ) -> VerificationAttempt:
        """在产生新发布意图前，重新验证精确 passed Attempt 与当前资格。"""

        attempt = self.get_attempt(
            owner_id=owner_id,
            attempt_id=attempt_id,
        )
        if attempt.task_id != task_id or attempt.revision != revision:
            raise ValueError("候选验证 Attempt 与当前任务修订不一致")
        if attempt.status is not AttemptStatus.PASSED:
            raise ValueError("只有 passed 候选验证 Attempt 可以发布")
        if (
            attempt.ruleset_identity_status
            is not RulesetIdentityStatus.VERSIONED
        ):
            raise ValueError("候选验证 Attempt 缺少可证明的规则身份")
        if attempt.report_json is None or attempt.report_hash is None:
            raise ValueError("候选验证 Attempt 缺少冻结报告")
        if (
            _sha256_bytes(attempt.report_json.encode("utf-8"))
            != attempt.report_hash
        ):
            raise ValueError("候选验证 Attempt 报告哈希不一致")
        try:
            report = VerificationReport.model_validate_json(attempt.report_json)
        except ValueError as exc:
            raise ValueError("候选验证 Attempt 报告无效") from exc
        if report.status is not VerificationStatus.PASSED:
            raise ValueError("候选验证 Attempt 报告未通过")

        offer = self.inspect_reverification_sync(
            owner_id=owner_id,
            task_id=task_id,
            revision=revision,
        )
        if offer.previous_attempt_id != attempt_id:
            raise ValueError("候选验证 Attempt 已不是当前精确结果")
        if not offer.awaiting_publication:
            raise ValueError("当前候选不具备显式发布资格")
        if offer.ruleset_changed is not False:
            raise ValueError("当前验证规则身份已变化或无法证明")
        return attempt

    async def retry_semantic(
        self,
        *,
        request: PiRuntimeRequest,
        run_id: str,
        candidates: tuple[CandidateArtifact, ...],
        manifest_path: Path,
        verifier: CandidateVerifierPort,
        previous_report: VerificationReport,
        previous_attempt_id: str,
        actor_id: str,
        idempotency_key: str,
        goal_contract_hash: str,
        delivery_spec_hash: str,
    ) -> VerificationAttempt:
        if previous_report.status is not VerificationStatus.INCONCLUSIVE:
            raise ValueError("语义重试只接受 inconclusive 前序报告")

        async def operation() -> VerificationReport:
            return await verifier.retry_semantic_verification(
                request=request,
                candidates=candidates,
                manifest_path=manifest_path,
                previous_report=previous_report,
            )

        return await self._execute(
            request=request,
            run_id=run_id,
            candidates=candidates,
            manifest_path=manifest_path,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            goal_contract_hash=goal_contract_hash,
            delivery_spec_hash=delivery_spec_hash,
            reason=AttemptReason.SEMANTIC_INCONCLUSIVE,
            previous_attempt_id=previous_attempt_id,
            verifier=verifier,
            operation=operation,
        )

    async def request_reverification(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
        expected_previous_attempt_id: str,
        external_api_confirmed: bool,
        accept_duplicate_provider_cost: bool = False,
        idempotency_key: str,
        verifier_factory: SemanticVerifierFactoryPort,
    ) -> VerificationAttempt:
        """资格 CAS 后持久化 requested；实际完整验证由 Worker 另行认领。"""

        existing = self._repository.get_by_idempotency(owner_id, idempotency_key)
        if existing is not None:
            existing_previous = (
                self._repository.get(owner_id, existing.previous_attempt_id)
                if existing.previous_attempt_id is not None
                else None
            )
            duplicate_cost_required = (
                existing_previous is not None
                and existing_previous.status is AttemptStatus.OUTCOME_UNKNOWN
            )
            if (
                existing.task_id != task_id
                or existing.revision != revision
                or existing.previous_attempt_id != expected_previous_attempt_id
                or bool(existing.egress_confirmed_at) != external_api_confirmed
                or duplicate_cost_required != accept_duplicate_provider_cost
            ):
                raise ValueError("幂等键已绑定其他候选验证请求")
            return existing
        offer = await self.inspect_reverification(
            owner_id=owner_id,
            task_id=task_id,
            revision=revision,
        )
        recovering_unknown = offer.previous_status is AttemptStatus.OUTCOME_UNKNOWN
        if accept_duplicate_provider_cost and not recovering_unknown:
            raise ReverificationContractError(
                "只有恢复 Provider 未知结果时才能确认重复费用风险"
            )
        if recovering_unknown and not accept_duplicate_provider_cost:
            raise ReverificationContractError(
                "Provider 结果未知；创建恢复 Attempt 前必须确认重复费用风险"
            )
        recoverable_unknown = (
            recovering_unknown
            and external_api_confirmed
            and set(offer.blockers) == {ReverificationBlocker.OUTCOME_UNKNOWN}
            and offer.previous_reason is not None
        )
        if not offer.eligible and not recoverable_unknown:
            if ReverificationBlocker.PROVIDER_BINDING_FORBIDDEN in offer.blockers:
                raise PermissionError("模型连接不存在或不属于当前 Owner")
            unavailable = {
                ReverificationBlocker.AUTHORITY_UNAVAILABLE,
                ReverificationBlocker.RULESET_UNAVAILABLE,
            }
            if unavailable.intersection(offer.blockers):
                raise ReverificationUnavailableError("候选重验服务暂时不可用")
            raise ValueError("当前候选不具备完整重验资格")
        if offer.previous_attempt_id != expected_previous_attempt_id:
            raise ValueError("前序验证 Attempt 已变化，请查看最新结果")
        context, request, candidates, previous = self._load_reverification_context(
            owner_id=owner_id,
            task_id=task_id,
            revision=revision,
            expected_previous_attempt_id=expected_previous_attempt_id,
        )
        uses_provider = request.model_connection_id is not None
        if uses_provider and not external_api_confirmed:
            raise ReverificationContractError("本次 Provider 外发必须由 Owner 重新确认")
        if not uses_provider and external_api_confirmed:
            raise ValueError("本地完整重验不接受外发确认")
        run_id = str(context["run_id"])
        reason = offer.reason or offer.previous_reason
        if reason is None:
            raise ValueError("当前候选没有可恢复的重验原因")
        verifier = verifier_factory(request, run_id)
        self._broker_adapter.assert_verifier_binding(request, run_id, verifier)
        try:
            ruleset = await asyncio.to_thread(
                self._ruleset_resolver.resolve,
                verifier,
            )
        except RuntimeError as exc:
            raise ReverificationUnavailableError(
                "候选重验服务暂时不可用"
            ) from exc
        manifest_path = (
            Path(str(context["workspace_root"]))
            / "output"
            / "candidate-manifest.json"
        )
        manifest_hash = _sha256_bytes(manifest_path.read_bytes())
        candidate_hash = _candidate_set_hash(candidates)
        goal_hash, delivery_hash = _request_contract_hashes(request)
        request_payload = {
            "owner_id": owner_id,
            "task_id": task_id,
            "revision": revision,
            "run_id": run_id,
            "reason_code": reason.value,
            "previous_attempt_id": previous.attempt_id,
            "candidate_set_hash": candidate_hash,
            "manifest_hash": manifest_hash,
            "goal_contract_hash": goal_hash,
            "delivery_spec_hash": delivery_hash,
            "verifier_ruleset_hash": ruleset.verifier_ruleset_hash,
            "external_api_confirmed": external_api_confirmed,
            "accept_duplicate_provider_cost": accept_duplicate_provider_cost,
        }
        request_hash = _sha256_bytes(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        attempt_id = "verification_" + _sha256_bytes(
            f"{owner_id}\x1f{idempotency_key}".encode("utf-8")
        )[:32]
        provider_attempt_id = (
            "grant_cv_" + _sha256_bytes(attempt_id.encode("utf-8"))[:32]
            if uses_provider
            else None
        )
        attempt, created = self._repository.create_with_result(
            VerificationAttempt(
                attempt_id=attempt_id,
                owner_id=owner_id,
                task_id=task_id,
                revision=revision,
                run_id=run_id,
                previous_attempt_id=previous.attempt_id,
                reason_code=reason,
                candidate_set_hash=candidate_hash,
                manifest_hash=manifest_hash,
                goal_contract_hash=goal_hash,
                delivery_spec_hash=delivery_hash,
                verifier_ruleset_hash=ruleset.verifier_ruleset_hash,
                verifier_code_commit=ruleset.verifier_code_commit,
                verifier_source_hash=ruleset.verifier_source_hash,
                verifier_execution_identity_hash=(
                    ruleset.verifier_execution_identity_hash
                ),
                verifier_ruleset_manifest_json=(
                    ruleset.verifier_ruleset_manifest_json
                ),
                actor_id=owner_id,
                connection_id=request.model_connection_id,
                connection_version=request.model_connection_version,
                model_id=request.model_connection_model or request.model,
                egress_confirmed_at=(self._clock() if uses_provider else None),
                provider_attempt_id=provider_attempt_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status=AttemptStatus.REQUESTED,
                created_at=self._clock(),
            )
        )
        if created:
            self._event_writer("candidate_verification_attempt_requested", attempt)
        return attempt

    async def execute_requested_reverification(
        self,
        *,
        owner_id: str,
        attempt_id: str,
        verifier_factory: SemanticVerifierFactoryPort,
    ) -> VerificationAttempt:
        """Worker 认领 requested Attempt，并只对冻结 Candidate 执行完整验证。"""

        requested = self._repository.get(owner_id, attempt_id)
        if requested is None:
            raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
        context, request, candidates, _previous = self._load_reverification_context(
            owner_id=owner_id,
            task_id=requested.task_id,
            revision=requested.revision,
            expected_previous_attempt_id=requested.previous_attempt_id,
        )
        candidate_hash = _candidate_set_hash(candidates)
        manifest_path = (
            Path(str(context["workspace_root"]))
            / "output"
            / "candidate-manifest.json"
        )
        manifest_bytes = manifest_path.read_bytes()
        goal_hash, delivery_hash = _request_contract_hashes(request)
        if (
            candidate_hash != requested.candidate_set_hash
            or _sha256_bytes(manifest_bytes) != requested.manifest_hash
            or not _manifest_identity_matches(manifest_bytes, request, candidates)
            or goal_hash != requested.goal_contract_hash
            or delivery_hash != requested.delivery_spec_hash
        ):
            raise RuntimeError("资格预检后冻结候选或契约已漂移")
        if any(
            item.host_path.stat().st_size != item.size_bytes
            or _sha256_bytes(item.host_path.read_bytes()) != item.sha256
            for item in candidates
        ):
            raise RuntimeError("资格预检后 Candidate 文件已漂移")
        if any(
            _sha256_bytes(source.host_path.read_bytes()) != source.sha256
            for source in request.sources
        ):
            raise RuntimeError("资格预检后来源文件已漂移")
        if self._reverification_authority is None or self._reverification_authority.blockers(
            request,
            requested.run_id,
        ):
            raise PermissionError("资格预检后任务权威身份已漂移")
        verifier = (
            verifier_factory(
                request,
                requested.run_id,
                requested.provider_attempt_id,
            )
            if requested.provider_attempt_id is not None
            else verifier_factory(request, requested.run_id)
        )
        self._broker_adapter.assert_verifier_binding(
            request,
            requested.run_id,
            verifier,
        )
        ruleset = await asyncio.to_thread(self._ruleset_resolver.resolve, verifier)
        if (
            ruleset.verifier_ruleset_hash != requested.verifier_ruleset_hash
            or ruleset.verifier_execution_identity_hash
            != requested.verifier_execution_identity_hash
        ):
            raise RuntimeError("资格预检后 Verifier 执行身份已漂移")
        running, claimed, cancelled_now = (
            self._repository.start_requested_if_current(
            owner_id,
            attempt_id,
            started_at=self._clock(),
            expected_workspace_root=str(context["workspace_root"]),
            expected_request_json=str(context["request_json"]),
            expected_candidates_json=str(context["candidates_json"]),
            expected_verification_json=str(context["verification_json"]),
            )
        )
        if not claimed:
            if cancelled_now:
                self._event_writer(
                    "candidate_verification_attempt_finished",
                    running,
                )
            return running

        async def operation() -> VerificationReport:
            return await verifier.verify(
                request=request,
                candidates=candidates,
                manifest_path=manifest_path,
            )

        return await self._complete_running(
            running=running,
            request=request,
            candidates=candidates,
            candidate_hash=candidate_hash,
            operation=operation,
        )

    def close_unstarted_reverification(
        self,
        *,
        owner_id: str,
        attempt_id: str,
    ) -> VerificationAttempt:
        """Worker 在认领前失败时留下 cancelled 终态，避免永久占据活动槽。"""

        cancelled, transitioned = self._repository.cancel_requested(
            owner_id,
            attempt_id,
            finished_at=self._clock(),
        )
        if transitioned:
            self._event_writer(
                "candidate_verification_attempt_finished",
                cancelled,
            )
        return cancelled

    def list_requested_reverifications(self) -> tuple[VerificationAttempt, ...]:
        return self._repository.list_requested_local()

    def list_running_reverifications(self) -> tuple[VerificationAttempt, ...]:
        return self._repository.list_running_local()

    def recover_interrupted_reverification(
        self,
        attempt: VerificationAttempt,
    ) -> VerificationAttempt:
        """仅在调用方持有该 Attempt 租约时收口中断的 running。"""

        if attempt.provider_attempt_id is not None:
            # running 后无法证明 Provider 尚未收到请求，必须按可能计费失败关闭。
            return self._finish_outcome_unknown(attempt)
        return self._finish_inconclusive(
            attempt,
            code="verification_worker_recovery",
            summary="候选重验 Worker 中断，未形成可靠结论",
            candidates_json="[]",
            candidate_set_hash=attempt.candidate_set_hash,
            project_runtime=False,
        )

    def ensure_requested_event(self, attempt: VerificationAttempt) -> None:
        if attempt.status is AttemptStatus.REQUESTED:
            self._event_writer("candidate_verification_attempt_requested", attempt)

    def _load_reverification_context(
        self,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
        expected_previous_attempt_id: str | None,
    ) -> tuple[
        dict[str, object],
        PiRuntimeRequest,
        tuple[CandidateArtifact, ...],
        VerificationAttempt,
    ]:
        context = self._repository.get_runtime_context(
            owner_id,
            task_id,
            revision,
        )
        if (
            context is None
            or context["runtime_version"] != "pi"
            or context["status"] != "candidate_ready"
            or not context["run_id"]
        ):
            raise ValueError("当前任务没有可重新验证的候选")
        if (
            not context["workspace_root"]
            or not context["request_json"]
            or not context["candidates_json"]
        ):
            raise ReverificationContractError(
                "候选缺少冻结运行信息，不能重新验证"
            )
        try:
            request_values = json.loads(str(context["request_json"]))
            if not request_values.get("model_connection_id"):
                request_values["api_key"] = "local-runtime"
            request = PiRuntimeRequest.model_validate(request_values)
            candidates = tuple(
                CandidateArtifact.model_validate(item)
                for item in json.loads(str(context["candidates_json"]))
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ReverificationContractError(
                "候选缺少冻结运行信息，不能重新验证"
            ) from exc
        history = self._repository.list_for_candidate(
            owner_id,
            task_id=task_id,
            revision=revision,
            run_id=str(context["run_id"]),
            candidate_set_hash=_candidate_set_hash(candidates),
        )
        previous = next(
            (
                item
                for item in reversed(history)
                if item.attempt_id == expected_previous_attempt_id
            ),
            None,
        )
        if previous is None:
            raise ValueError("当前验证投影缺少精确 Attempt 依据")
        return context, request, candidates, previous

    async def _execute(
        self,
        *,
        request: PiRuntimeRequest,
        run_id: str,
        candidates: tuple[CandidateArtifact, ...],
        manifest_path: Path,
        actor_id: str,
        idempotency_key: str,
        goal_contract_hash: str,
        delivery_spec_hash: str,
        reason: AttemptReason,
        previous_attempt_id: str | None,
        verifier: CandidateVerifierPort,
        operation: Callable[[], Awaitable[VerificationReport]],
    ) -> VerificationAttempt:
        if not candidates:
            raise ValueError("候选验证必须绑定非空 CandidateSet")
        if self._p0_reader(request):
            raise PermissionError("P0/Gate 当前阻断新的候选验证 Attempt")
        if reason is AttemptReason.INITIAL:
            self._repository.claim_runtime_binding(
                request.user_id,
                request.task_id,
                request.revision,
                run_id,
            )
        else:
            self._repository.assert_runtime_binding(
                request.user_id,
                request.task_id,
                request.revision,
                run_id,
            )
        manifest_hash = _sha256_bytes(manifest_path.read_bytes())
        candidate_hash = _candidate_set_hash(candidates)
        self._broker_adapter.assert_verifier_binding(request, run_id, verifier)
        ruleset = await asyncio.to_thread(self._ruleset_resolver.resolve, verifier)
        request_payload = {
            "owner_id": request.user_id,
            "task_id": request.task_id,
            "revision": request.revision,
            "run_id": run_id,
            "reason_code": reason.value,
            "previous_attempt_id": previous_attempt_id,
            "candidate_set_hash": candidate_hash,
            "manifest_hash": manifest_hash,
            "goal_contract_hash": goal_contract_hash,
            "delivery_spec_hash": delivery_spec_hash,
            "verifier_ruleset_hash": ruleset.verifier_ruleset_hash,
        }
        request_hash = _sha256_bytes(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        attempt_id = "verification_" + _sha256_bytes(
            f"{request.user_id}\x1f{idempotency_key}".encode("utf-8")
        )[:32]
        created_at = self._clock()
        running = self._repository.create_and_start_if_p0_allowed(
            VerificationAttempt(
                attempt_id=attempt_id,
                owner_id=request.user_id,
                task_id=request.task_id,
                revision=request.revision,
                run_id=run_id,
                previous_attempt_id=previous_attempt_id,
                reason_code=reason,
                candidate_set_hash=candidate_hash,
                manifest_hash=manifest_hash,
                goal_contract_hash=goal_contract_hash,
                delivery_spec_hash=delivery_spec_hash,
                verifier_ruleset_hash=ruleset.verifier_ruleset_hash,
                verifier_code_commit=ruleset.verifier_code_commit,
                verifier_source_hash=ruleset.verifier_source_hash,
                verifier_execution_identity_hash=(
                    ruleset.verifier_execution_identity_hash
                ),
                verifier_ruleset_manifest_json=(
                    ruleset.verifier_ruleset_manifest_json
                ),
                actor_id=actor_id,
                connection_id=request.model_connection_id,
                connection_version=request.model_connection_version,
                model_id=request.model_connection_model or request.model,
                egress_confirmed_at=(
                    created_at if request.external_api_confirmed else None
                ),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status=AttemptStatus.REQUESTED,
                created_at=created_at,
            ),
            started_at=self._clock(),
        )
        if running.status is not AttemptStatus.RUNNING:
            return running
        return await self._complete_running(
            running=running,
            request=request,
            candidates=candidates,
            candidate_hash=candidate_hash,
            operation=operation,
        )

    async def _complete_running(
        self,
        *,
        running: VerificationAttempt,
        request: PiRuntimeRequest,
        candidates: tuple[CandidateArtifact, ...],
        candidate_hash: str,
        operation: Callable[[], Awaitable[VerificationReport]],
    ) -> VerificationAttempt:
        """统一完成已认领 Attempt，并原子更新兼容 Runtime 投影。"""

        candidates_json = json.dumps(
            [item.model_dump(mode="json") for item in candidates],
            ensure_ascii=False,
        )
        try:
            self._event_writer("candidate_verification_attempt_started", running)
            report = await operation()
        except asyncio.CancelledError:
            if running.provider_attempt_id is not None:
                # 取消只能终止本地等待，不能证明 Provider 没有处理或计费。
                self._finish_outcome_unknown(running)
            else:
                self._finish_cancelled(running)
            raise
        except ProviderOutcomeUnknownError:
            return self._finish_outcome_unknown(running)
        except Exception:
            self._finish_inconclusive(
                running,
                code="verification_execution",
                summary="候选验证执行异常，未形成可靠结论",
                candidates_json=candidates_json,
                candidate_set_hash=candidate_hash,
                project_runtime=True,
            )
            raise
        status = {
            VerificationStatus.PASSED: AttemptStatus.PASSED,
            VerificationStatus.FAILED: AttemptStatus.FAILED,
            VerificationStatus.INCONCLUSIVE: AttemptStatus.INCONCLUSIVE,
        }[report.status]
        report_json = report.model_dump_json()
        try:
            self._revoke_provider_grant(running, "candidate_verify_finished")
            finished = self._repository.finish_with_runtime_projection(
                request.user_id,
                running.attempt_id,
                status=status,
                report_json=report_json,
                report_hash=_sha256_bytes(report_json.encode("utf-8")),
                finished_at=self._clock(),
                candidates_json=candidates_json,
                candidate_set_hash=candidate_hash,
            )
        except Exception:
            if running.provider_attempt_id is not None:
                self._finish_outcome_unknown(running)
            else:
                self._finish_inconclusive(
                    running,
                    code="verification_persistence",
                    summary="候选验证结论未能完整持久化，未形成可靠结论",
                    candidates_json=candidates_json,
                    candidate_set_hash=candidate_hash,
                    project_runtime=False,
                )
            raise
        self._event_writer("candidate_verification_attempt_finished", finished)
        return finished

    def _finish_outcome_unknown(
        self,
        running: VerificationAttempt,
    ) -> VerificationAttempt:
        """外发结果不确定是独立终态，不能伪装成可普通重试的 inconclusive。"""

        self._revoke_provider_grant(running, "candidate_verify_outcome_unknown")
        finished = self._repository.finish(
            running.owner_id,
            running.attempt_id,
            status=AttemptStatus.OUTCOME_UNKNOWN,
            report_json=None,
            report_hash=None,
            finished_at=self._clock(),
        )
        self._event_writer("candidate_verification_attempt_finished", finished)
        return finished

    def _finish_cancelled(
        self,
        running: VerificationAttempt,
    ) -> VerificationAttempt:
        """协程取消是显式终态；不得伪造成验证报告或遗留活动 Attempt。"""

        self._revoke_provider_grant(running, "candidate_verify_cancelled")
        finished = self._repository.finish(
            running.owner_id,
            running.attempt_id,
            status=AttemptStatus.CANCELLED,
            report_json=None,
            report_hash=None,
            finished_at=self._clock(),
        )
        self._event_writer("candidate_verification_attempt_finished", finished)
        return finished

    def _finish_inconclusive(
        self,
        running: VerificationAttempt,
        *,
        code: str,
        summary: str,
        candidates_json: str,
        candidate_set_hash: str,
        project_runtime: bool,
    ) -> VerificationAttempt:
        """异常必须留下可重放终态，不能让 Attempt 永久占据 running。"""

        self._revoke_provider_grant(running, "candidate_verify_inconclusive")

        report = VerificationReport(
            status=VerificationStatus.INCONCLUSIVE,
            summary=summary,
            checks=(
                VerificationCheck(
                    code=code,
                    passed=False,
                    summary=summary,
                ),
            ),
            evidence_count=0,
            formal_delivery_eligible=False,
        )
        report_json = report.model_dump_json()
        finish = (
            self._repository.finish_with_runtime_projection
            if project_runtime
            else self._repository.finish
        )
        arguments = {
            "status": AttemptStatus.INCONCLUSIVE,
            "report_json": report_json,
            "report_hash": _sha256_bytes(report_json.encode("utf-8")),
            "finished_at": self._clock(),
        }
        if project_runtime:
            arguments.update(
                {
                    "candidates_json": candidates_json,
                    "candidate_set_hash": candidate_set_hash,
                }
            )
        finished = finish(
            running.owner_id,
            running.attempt_id,
            **arguments,
        )
        self._event_writer("candidate_verification_attempt_finished", finished)
        return finished

    def _revoke_provider_grant(
        self,
        attempt: VerificationAttempt,
        reason: str,
    ) -> None:
        if (
            attempt.provider_attempt_id is not None
            and self._provider_grant_revoker is not None
        ):
            self._provider_grant_revoker(attempt.provider_attempt_id, reason)
