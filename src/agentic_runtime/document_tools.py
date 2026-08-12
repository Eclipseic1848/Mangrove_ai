# -*- coding: utf-8 -*-
"""Pi 文档能力工具的任务绑定授权与调用 Seam。"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import threading
from typing import Any, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field

from .coverage import (
    CandidateRejection,
    CoverageContract,
    CoverageContractDraft,
    EvidenceBinding,
    CoverageLedger,
    ProposedResult,
    freeze_contract,
    verify_coverage,
)
from .models import SourceInput


class DocumentToolError(RuntimeError):
    """文档能力调用不满足授权或 Interface 契约。"""


class DocumentToolGrant(BaseModel):
    """单次 Run、单一用途、短时有效的文档能力使用权。"""

    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(min_length=1)
    token: str = Field(min_length=32)
    owner_user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    purpose: str = "document_retrieval"
    owner_binding: str = Field(min_length=16)
    expires_at: datetime


class DocumentToolClaims(BaseModel):
    """Relay 请求必须重复携带的 Run 绑定，避免 Token 被错配到别的上下文。"""

    model_config = ConfigDict(extra="forbid")

    grant_id: str
    owner_binding: str
    task_id: str
    revision: int = Field(ge=1)
    run_id: str
    purpose: str


@dataclass
class _GrantState:
    grant_id: str
    owner_user_id: str
    owner_binding: str
    task_id: str
    revision: int
    run_id: str
    purpose: str
    expires_at: datetime
    sources: dict[str, SourceInput]
    inspected_units: dict[str, tuple[str, ...]] = field(default_factory=dict)
    contract: CoverageContract | None = None
    ledger: CoverageLedger | None = None
    clarification: dict[str, str] | None = None
    revoked: bool = False
    revoked_reason: str | None = None


class DocumentRetriever(Protocol):
    """文档检索深 Module 的能力级 Interface。"""

    async def inspect(
        self,
        source: SourceInput,
        *,
        owner_key: str = "anonymous",
    ) -> dict[str, object]: ...

    async def discover(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        query: str,
        unit_ids: tuple[str, ...],
    ) -> dict[str, object]: ...

    async def read(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        unit_ids: tuple[str, ...],
        needs: tuple[str, ...],
    ) -> dict[str, object]: ...


class CoverageStateStore(Protocol):
    """覆盖状态持久化 Seam；实现必须按 Owner/Run 精确隔离。"""

    def save_coverage(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        contract: CoverageContract,
        ledger: CoverageLedger,
    ) -> None: ...

    def get_coverage(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> tuple[CoverageContract, CoverageLedger] | None: ...


class DocumentToolBroker:
    """把不可信 Pi 工具调用收口到当前 Run 获准来源。"""

    def __init__(
        self,
        *,
        retriever: DocumentRetriever,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = 900,
        state_store: CoverageStateStore | None = None,
    ) -> None:
        self._retriever = retriever
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ttl_seconds = ttl_seconds
        self._state_store = state_store
        self._grants: dict[str, _GrantState] = {}
        self._grant_keys: dict[str, str] = {}
        self._active_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._cancel_events: dict[str, set[threading.Event]] = {}

    def issue_grant(
        self,
        *,
        owner_user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        sources: tuple[SourceInput, ...],
        ttl_seconds: int | None = None,
    ) -> DocumentToolGrant:
        if revision < 1 or not sources:
            raise DocumentToolError("文档工具 Grant 缺少有效来源或 revision")
        effective_ttl = ttl_seconds or self._ttl_seconds
        if effective_ttl <= 0:
            raise DocumentToolError("文档工具 Grant TTL 必须大于 0")
        token = secrets.token_urlsafe(32)
        grant_id = f"document_grant_{uuid.uuid4().hex}"
        expires_at = self._clock() + timedelta(seconds=effective_ttl)
        token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        owner_binding = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()
        restored = (
            self._state_store.get_coverage(
                user_id=owner_user_id,
                task_id=task_id,
                revision=revision,
                run_id=run_id,
            )
            if self._state_store is not None
            else None
        )
        self._grants[token_key] = _GrantState(
            grant_id=grant_id,
            owner_user_id=owner_user_id,
            owner_binding=owner_binding,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            purpose="document_retrieval",
            expires_at=expires_at,
            sources={source.upload_id: source for source in sources},
            contract=restored[0] if restored else None,
            ledger=restored[1] if restored else None,
        )
        self._grant_keys[grant_id] = token_key
        return DocumentToolGrant(
            grant_id=grant_id,
            token=token,
            owner_user_id=owner_user_id,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            owner_binding=owner_binding,
            expires_at=expires_at,
        )

    def revoke_grant(self, grant_id: str, reason: str) -> None:
        """撤销指定 Grant；重复撤销保持幂等且不泄露其是否曾存在。"""

        token_key = self._grant_keys.get(grant_id)
        grant = self._grants.get(token_key or "")
        if grant is not None:
            grant.revoked = True
            grant.revoked_reason = reason[:200]
            for task in tuple(self._active_tasks.get(grant_id, set())):
                task.cancel()
            for event in tuple(self._cancel_events.get(grant_id, set())):
                event.set()

    def revoke_run_grants(
        self,
        owner_user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        *,
        reason: str,
    ) -> None:
        for grant in tuple(self._grants.values()):
            if (
                grant.owner_user_id == owner_user_id
                and grant.task_id == task_id
                and grant.revision == revision
                and grant.run_id == run_id
            ):
                self.revoke_grant(grant.grant_id, reason)

    def revoke_revision_grants(
        self,
        owner_user_id: str,
        task_id: str,
        revision: int,
        *,
        reason: str,
    ) -> None:
        for grant in tuple(self._grants.values()):
            if (
                grant.owner_user_id == owner_user_id
                and grant.task_id == task_id
                and grant.revision == revision
            ):
                self.revoke_grant(grant.grant_id, reason)

    def completion_state(
        self,
        grant_id: str,
    ) -> tuple[CoverageContract, CoverageLedger] | None:
        token_key = self._grant_keys.get(grant_id)
        grant = self._grants.get(token_key or "")
        if grant is None:
            return None
        contract = grant.contract
        ledger = grant.ledger
        if isinstance(contract, CoverageContract) and isinstance(
            ledger, CoverageLedger
        ):
            return contract, ledger
        return None

    def clarification_state(
        self,
        grant_id: str,
    ) -> dict[str, str] | None:
        token_key = self._grant_keys.get(grant_id)
        grant = self._grants.get(token_key or "")
        clarification = grant.clarification if grant else None
        return dict(clarification) if clarification else None

    async def read_for_verification(
        self,
        grant_id: str,
        source_ref: str,
        locator: str,
    ) -> str:
        """在同一 Owner/Run 授权内重读已入账页面，供独立验证器核对逐字证据。"""

        token_key = self._grant_keys.get(grant_id)
        grant = self._grants.get(token_key or "")
        if (
            grant is None
            or grant.revoked
            or grant.expires_at <= self._clock()
        ):
            raise DocumentToolError("文档验证 Grant 无效或已过期")
        source = self._resolve_source(grant, source_ref)
        if source is None:
            raise DocumentToolError("验证证据引用了任务外来源")
        match = re.fullmatch(
            r"\s*(?:page|页)\s*[:：]?\s*(\d+)\s*",
            locator,
            re.I,
        )
        if match is None:
            raise DocumentToolError("PDF 验证证据缺少合法页码")
        unit_id = f"{source.upload_id}:page:{int(match.group(1))}"
        _, ledger = self._coverage(grant)
        if unit_id not in ledger.authoritatively_read_unit_ids:
            raise DocumentToolError("验证证据页面尚未经过权威读取")
        result = await self._invoke(
            grant,
            self._retriever.read(
                source,
                owner_key=grant.owner_user_id,
                unit_ids=(unit_id,),
                needs=("text", "layout"),
            ),
        )
        items = tuple(
            item
            for item in result.get("items", [])
            if isinstance(item, dict) and item.get("unit_id") == unit_id
        )
        if (
            len(items) != 1
            or items[0].get("quality_status") != "trusted"
        ):
            raise DocumentToolError("验证证据页面未形成可信权威读取结果")
        return str(items[0].get("text") or "")

    async def call(
        self,
        *,
        grant_token: str,
        operation: str,
        payload: dict[str, object],
        claims: DocumentToolClaims | None = None,
    ) -> dict[str, object]:
        grant = self._resolve(grant_token, claims)
        if operation == "freeze_coverage":
            return self._freeze_coverage(grant, payload)
        if operation == "request_clarification":
            return self._request_clarification(grant, payload)
        if operation == "propose_completion":
            return self._propose_completion(grant, payload)
        source_id = str(payload.get("source_id") or "")
        source = self._resolve_source(grant, source_id)
        if source is None:
            # 不区分来源不存在和越权，避免向不可信 Runtime 泄露来源身份。
            raise DocumentToolError("来源不存在或不属于当前 Run")
        source_id = source.upload_id
        if operation == "inspect_source":
            result = await self._invoke(
                grant,
                self._retriever.inspect(
                    source,
                    owner_key=grant.owner_user_id,
                ),
            )
            unit_ids = tuple(
                str(item["unit_id"])
                for item in result.get("units", [])
                if isinstance(item, dict) and item.get("unit_id")
            )
            grant.inspected_units[source_id] = unit_ids
            return result
        contract, ledger = self._coverage(grant)
        requested_units = tuple(
            str(value) for value in payload.get("unit_ids", [])
        )
        self._assert_units_authorized(ledger, requested_units)
        if operation == "discover_content":
            result = await self._invoke(
                grant,
                self._retriever.discover(
                    source,
                    owner_key=grant.owner_user_id,
                    query=str(payload.get("query") or ""),
                    unit_ids=requested_units,
                ),
            )
            self._record_discovery(grant, ledger, result)
            return {
                **result,
                "coverage": grant.ledger.public_progress(),
            }
        if operation == "read_evidence":
            result = await self._invoke(
                grant,
                self._retriever.read(
                    source,
                    owner_key=grant.owner_user_id,
                    unit_ids=requested_units,
                    needs=tuple(
                        str(value)
                        for value in payload.get("needs", [])
                    ),
                ),
            )
            self._record_read(grant, ledger, result)
            return {
                **result,
                "coverage": grant.ledger.public_progress(),
            }
        raise DocumentToolError("未知的文档能力操作")

    @staticmethod
    def _request_clarification(
        grant: _GrantState,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if grant.contract is not None:
            raise DocumentToolError("覆盖契约冻结后不能再改为需求澄清")
        question = str(payload.get("question") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not question or len(question) > 500:
            raise DocumentToolError("澄清问题必须为 1–500 字")
        grant.clarification = {
            "question": question,
            "reason": reason[:500],
        }
        return {
            "status": "needs_input",
            "question": question,
        }

    async def _invoke(
        self,
        grant: _GrantState,
        operation: Any,
    ) -> dict[str, object]:
        from .document_retrieval import (
            bind_document_retrieval_cancel_event,
            reset_document_retrieval_cancel_event,
        )

        cancel_event = threading.Event()

        async def run_bound_operation() -> dict[str, object]:
            token = bind_document_retrieval_cancel_event(cancel_event)
            try:
                return await operation
            finally:
                reset_document_retrieval_cancel_event(token)

        task = asyncio.create_task(run_bound_operation())
        active = self._active_tasks.setdefault(grant.grant_id, set())
        events = self._cancel_events.setdefault(grant.grant_id, set())
        active.add(task)
        events.add(cancel_event)
        try:
            return await task
        finally:
            active.discard(task)
            events.discard(cancel_event)

    @staticmethod
    def _coverage(
        grant: _GrantState,
    ) -> tuple[CoverageContract, CoverageLedger]:
        contract = grant.contract
        ledger = grant.ledger
        if not isinstance(contract, CoverageContract) or not isinstance(
            ledger, CoverageLedger
        ):
            raise DocumentToolError("必须先冻结覆盖契约")
        return contract, ledger

    @staticmethod
    def _assert_units_authorized(
        ledger: CoverageLedger,
        unit_ids: tuple[str, ...],
    ) -> None:
        selected = set(unit_ids) or set(ledger.authorized_unit_ids)
        if not selected <= set(ledger.authorized_unit_ids):
            raise DocumentToolError("内容单元超出冻结的获准范围")

    def _replace_ledger(
        self,
        grant: _GrantState,
        ledger: CoverageLedger,
        **changes: object,
    ) -> None:
        grant.ledger = ledger.model_copy(update=changes)
        self._persist(grant)

    def _persist(self, grant: _GrantState) -> None:
        if self._state_store is None:
            return
        contract = grant.contract
        ledger = grant.ledger
        if not isinstance(contract, CoverageContract) or not isinstance(
            ledger, CoverageLedger
        ):
            return
        self._state_store.save_coverage(
            user_id=grant.owner_user_id,
            task_id=grant.task_id,
            revision=grant.revision,
            run_id=grant.run_id,
            contract=contract,
            ledger=ledger,
        )

    def _freeze_coverage(
        self,
        grant: _GrantState,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if grant.contract is not None:
            raise DocumentToolError("覆盖契约已经冻结，不可改写")
        try:
            normalized_payload = dict(payload)
            raw_scope = payload.get("authorized_scope")
            if isinstance(raw_scope, dict):
                normalized_scope = dict(raw_scope)
                raw_source_ids = raw_scope.get("source_ids")
                if isinstance(raw_source_ids, (list, tuple)):
                    normalized_source_ids: list[str] = []
                    for source_ref in raw_source_ids:
                        source = self._resolve_source(
                            grant,
                            str(source_ref or ""),
                        )
                        if source is None:
                            raise DocumentToolError(
                                "来源不存在或不属于当前 Run"
                            )
                        normalized_source_ids.append(source.upload_id)
                    normalized_scope["source_ids"] = normalized_source_ids
                    normalized_payload["authorized_scope"] = normalized_scope
            draft = CoverageContractDraft.model_validate(normalized_payload)
            inspected = tuple(
                unit_id
                for source_id in draft.authorized_scope.source_ids
                for unit_id in grant.inspected_units.get(source_id, ())
            )
            contract, ledger = freeze_contract(
                draft,
                bound_source_ids=set(grant.sources),
                inspected_unit_ids=inspected,
            )
        except ValueError as exc:
            raise DocumentToolError(str(exc)) from exc
        grant.contract = contract
        grant.ledger = ledger
        self._persist(grant)
        return {
            "contract": contract.model_dump(mode="json"),
            "coverage": ledger.public_progress(),
        }

    @staticmethod
    def _resolve_source(
        grant: _GrantState,
        source_ref: str,
    ) -> SourceInput | None:
        exact = grant.sources.get(source_ref)
        if exact is not None:
            return exact
        runtime_name = source_ref.removeprefix("/workspace/input/")
        visible_name = runtime_name if "/" not in runtime_name else ""
        matches = tuple(
            candidate
            for candidate in grant.sources.values()
            if visible_name and candidate.original_name == visible_name
        )
        # Pi 只能看到容器内文件名；只在当前 Grant 内唯一匹配时转换为内部 ID，
        # 重名、越权或任意其他路径仍统一失败关闭。
        return matches[0] if len(matches) == 1 else None

    def _record_discovery(
        self,
        grant: _GrantState,
        ledger: CoverageLedger,
        result: dict[str, object],
    ) -> None:
        observed = set(ledger.observed_unit_ids) | set(
            str(value) for value in result.get("observed_unit_ids", [])
        )
        candidates = set(ledger.discovered_candidate_unit_ids) | set(
            str(value) for value in result.get("candidate_unit_ids", [])
        )
        low = set(ledger.low_quality_units) | set(
            str(value) for value in result.get("low_quality_units", [])
        )
        unknown = (
            set(ledger.unknown_units) - observed
        ) | set(str(value) for value in result.get("unknown_units", []))
        versions = set(ledger.parser_versions) | set(
            str(value) for value in result.get("parser_versions", [])
        )
        self._replace_ledger(
            grant,
            ledger,
            observed_unit_ids=tuple(sorted(observed)),
            discovered_candidate_unit_ids=tuple(sorted(candidates)),
            low_quality_units=tuple(sorted(low)),
            unknown_units=tuple(sorted(unknown)),
            parser_versions=tuple(sorted(versions)),
            cache_hits=ledger.cache_hits + int(result.get("cache_hits", 0)),
        )

    def _record_read(
        self,
        grant: _GrantState,
        ledger: CoverageLedger,
        result: dict[str, object],
    ) -> None:
        read = set(ledger.authoritatively_read_unit_ids) | set(
            str(value) for value in result.get("source_unit_ids", [])
        )
        observed = set(ledger.observed_unit_ids) | read
        unknown = set(ledger.unknown_units) - read
        bindings = {
            (binding.evidence_ref, binding.unit_id)
            for binding in ledger.evidence_bindings
        }
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            evidence_ref = str(item.get("evidence_ref") or "")
            unit_id = str(item.get("unit_id") or "")
            if evidence_ref and unit_id:
                bindings.add((evidence_ref, unit_id))
        versions = set(ledger.parser_versions) | set(
            str(value)
            for value in result.get("authoritative_parser_versions", [])
        )
        low = set(ledger.low_quality_units)
        trusted_units = {
            str(item.get("unit_id"))
            for item in result.get("items", [])
            if isinstance(item, dict)
            and item.get("quality_status") == "trusted"
            and item.get("unit_id")
        }
        low -= trusted_units
        # 每次工具调用只评价本次返回的内容单元；累计 read 中的历史可信页
        # 不在本次响应里，不能因此被重新降级为低质量。
        current_units = {
            str(value) for value in result.get("source_unit_ids", [])
        }
        low |= current_units - trusted_units
        self._replace_ledger(
            grant,
            ledger,
            observed_unit_ids=tuple(sorted(observed)),
            authoritatively_read_unit_ids=tuple(sorted(read)),
            low_quality_units=tuple(sorted(low)),
            unknown_units=tuple(sorted(unknown)),
            evidence_bindings=tuple(
                EvidenceBinding(evidence_ref=ref, unit_id=unit_id)
                for ref, unit_id in sorted(bindings)
            ),
            parser_versions=tuple(sorted(versions)),
            cache_hits=ledger.cache_hits + int(result.get("cache_hits", 0)),
        )

    def _propose_completion(
        self,
        grant: _GrantState,
        payload: dict[str, object],
    ) -> dict[str, object]:
        contract, ledger = self._coverage(grant)
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise DocumentToolError("结果对象必须是数组")
        try:
            proposed_results = tuple(
                ProposedResult.model_validate(item) for item in raw_results
            )
            raw_rejections = payload.get("rejected_candidates", [])
            if not isinstance(raw_rejections, list):
                raise ValueError("排除候选必须是数组")
            proposed_rejections = tuple(
                CandidateRejection.model_validate(item)
                for item in raw_rejections
            )
        except ValueError as exc:
            raise DocumentToolError(f"结果对象无效：{exc}") from exc
        updated = ledger.model_copy(
            update={
                "agent_stop_proposal": str(
                    payload.get("summary") or "Pi 提议任务完成"
                )[:1000],
                "ordering_proof": tuple(
                    str(value)
                    for value in payload.get("ordering_proof", [])
                ),
                "proposed_results": proposed_results,
                "proposed_candidate_rejections": proposed_rejections,
                "result_empty_confirmed": bool(
                    payload.get("result_empty_confirmed", False)
                ),
            }
        )
        decision = verify_coverage(contract, updated)
        grant.ledger = updated.model_copy(
            update={
                "verifier_decision": decision.decision,
                "verifier_gaps": decision.gaps,
            }
        )
        self._persist(grant)
        return {
            "decision": decision.model_dump(mode="json"),
            "coverage": grant.ledger.model_dump(mode="json"),
        }

    def _resolve(
        self,
        token: str,
        claims: DocumentToolClaims | None,
    ) -> _GrantState:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        grant = self._grants.get(key)
        if grant is None or grant.revoked:
            raise DocumentToolError("文档工具 Grant 无效或已撤销")
        if grant.expires_at <= self._clock():
            raise DocumentToolError("文档工具 Grant 已过期")
        if claims is not None and (
            claims.grant_id != grant.grant_id
            or claims.owner_binding != grant.owner_binding
            or claims.task_id != grant.task_id
            or claims.revision != grant.revision
            or claims.run_id != grant.run_id
            or claims.purpose != grant.purpose
        ):
            raise DocumentToolError("文档工具 Grant 与当前 Run 绑定不一致")
        return grant


_DEFAULT_BROKER: DocumentToolBroker | None = None


def configure_default_document_tool_broker(
    broker: DocumentToolBroker,
) -> None:
    """Runtime 与内部 Relay 必须共享同一个进程内 Grant Broker。"""

    global _DEFAULT_BROKER
    _DEFAULT_BROKER = broker


def get_default_document_tool_broker() -> DocumentToolBroker:
    if _DEFAULT_BROKER is None:
        raise DocumentToolError("文档能力工具尚未配置")
    return _DEFAULT_BROKER
