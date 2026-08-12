# -*- coding: utf-8 -*-
"""运行中追问的非破坏性转向 Interface。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
import uuid

from .models import (
    ContextDelta,
    RawUserTurn,
    RevisionDecision,
    RevisionDecisionStatus,
    RevisionProposal,
    SteeringAction,
    SteeringRequest,
    SteeringResult,
    TurnIntent,
    RevisionSwitchMode,
)
from typing import Any


class ContextRewriter(Protocol):
    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        """外部模型边界：只返回结构化语义，不拥有任务修改权。"""


class SemanticDiffGate:
    """只检查结构化差异，不用关键词替代模型理解。"""

    _ANSWER_INTENTS = {
        TurnIntent.STATUS_QUESTION,
        TurnIntent.RATIONALE_QUESTION,
    }

    @classmethod
    def material_changes(cls, delta: ContextDelta) -> tuple[str, ...]:
        changes: list[str] = []
        if delta.goal_delta:
            changes.append("goal")
        if delta.source_scope_delta:
            changes.append("source_scope")
        if delta.selection_delta:
            changes.append("selection")
        if delta.coverage_delta:
            changes.append("coverage")
        if delta.field_semantics_delta:
            changes.append("field_semantics")
        if delta.output_delta:
            changes.append("output")
        if delta.permission_delta:
            changes.append("permission")
        return tuple(changes)

    @classmethod
    def classify(cls, delta: ContextDelta) -> SteeringAction:
        if delta.intent is TurnIntent.NEW_TASK:
            return SteeringAction.NEW_TASK_PROPOSAL
        if delta.intent is TurnIntent.PERMISSION_REQUEST:
            return SteeringAction.PERMISSION_REQUEST
        # “问状态”只是模型对意图的判断，不能盖过结构化差异。只要同时改变
        # 业务语义，就必须失败关闭为待确认草案，避免混合输入绕过人工确认。
        if cls.material_changes(delta):
            return SteeringAction.REVISION_PROPOSAL
        if delta.intent in cls._ANSWER_INTENTS:
            return SteeringAction.ANSWER_ONLY
        return SteeringAction.NORMALIZED_NO_MATERIAL_CHANGE


class ConversationSteering:
    def __init__(
        self,
        repository: Any,
        rewriter: ContextRewriter | None,
    ) -> None:
        self._repository = repository
        self._rewriter = rewriter

    async def handle_turn(self, request: SteeringRequest) -> SteeringResult:
        if self._rewriter is None:
            raise RuntimeError("ConversationSteering 未配置 ContextRewriter")
        submitted = RawUserTurn(
            turn_id=f"turn_{uuid.uuid4().hex[:16]}",
            owner_id=request.owner_id,
            task_id=request.task_id,
            revision=request.revision,
            text=request.text.strip(),
            idempotency_key=request.idempotency_key,
        )
        turn = self._repository.save_turn(submitted)
        existing = self._repository.get_result_for_turn(
            request.owner_id,
            turn.turn_id,
        )
        if existing is not None:
            return existing

        delta = await self._rewriter.rewrite(turn, request)
        if (
            delta.owner_id != turn.owner_id
            or delta.task_id != turn.task_id
            or delta.source_turn_ids != (turn.turn_id,)
            or delta.inherited_revision != turn.revision
        ):
            raise ValueError("ContextDelta 与原始回合或冻结 revision 不一致")
        self._repository.save_delta(turn.turn_id, delta)
        action = SemanticDiffGate.classify(delta)
        proposal = None
        if action is SteeringAction.REVISION_PROPOSAL:
            proposal = RevisionProposal(
                proposal_id=f"proposal_{uuid.uuid4().hex[:16]}",
                owner_id=turn.owner_id,
                task_id=turn.task_id,
                base_revision=turn.revision,
                delta_id=delta.delta_id,
                summary=delta.normalized_text,
                material_changes=SemanticDiffGate.material_changes(delta),
            )
            self._repository.save_proposal(proposal)

        acknowledgement = {
            SteeringAction.ANSWER_ONLY: "已回答，不影响当前任务",
            SteeringAction.NORMALIZED_NO_MATERIAL_CHANGE: "已按你的表达更新理解，不影响当前任务",
            SteeringAction.REVISION_PROPOSAL: "已形成修改草案，等待确认",
            SteeringAction.NEW_TASK_PROPOSAL: "检测到独立目标，建议创建新任务",
            SteeringAction.PERMISSION_REQUEST: "需要新的权限或外部数据处理授权",
        }[action]
        result = SteeringResult(
            result_id=f"steering_{uuid.uuid4().hex[:16]}",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            turn_id=turn.turn_id,
            delta_id=delta.delta_id,
            action=action,
            acknowledgement=acknowledgement,
            answer=delta.direct_answer,
            proposal_id=proposal.proposal_id if proposal else None,
            run_id=request.run_id,
            revision=request.revision,
        )
        return self._repository.save_result(result)

    def decide_proposal(
        self,
        owner_id: str,
        proposal_id: str,
        mode: RevisionSwitchMode,
        *,
        external_api_confirmed: bool = False,
    ) -> RevisionDecision:
        proposal = self._repository.get_proposal(owner_id, proposal_id)
        if proposal is None:
            raise KeyError("Revision 草案不存在或无权访问")
        status = {
            RevisionSwitchMode.CANCEL_NOW: RevisionDecisionStatus.READY_TO_APPLY,
            RevisionSwitchMode.AFTER_SAFE_POINT: RevisionDecisionStatus.WAITING_SAFE_POINT,
            RevisionSwitchMode.NEW_TASK: RevisionDecisionStatus.NEW_TASK_REQUIRED,
        }[mode]
        decision = RevisionDecision(
            decision_id=f"decision_{uuid.uuid4().hex[:16]}",
            proposal_id=proposal.proposal_id,
            owner_id=proposal.owner_id,
            task_id=proposal.task_id,
            base_revision=proposal.base_revision,
            mode=mode,
            status=status,
            external_api_confirmed=external_api_confirmed,
        )
        return self._repository.save_decision(decision)

    def mark_safe_point(
        self,
        owner_id: str,
        task_id: str,
        *,
        revision: int,
        safe_point: str,
    ) -> RevisionDecision | None:
        decision = self._repository.waiting_decision(
            owner_id,
            task_id,
            revision,
        )
        if decision is None:
            return None
        ready = decision.model_copy(
            update={
                "status": RevisionDecisionStatus.READY_TO_APPLY,
                "safe_point": safe_point,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return self._repository.update_decision(ready)
