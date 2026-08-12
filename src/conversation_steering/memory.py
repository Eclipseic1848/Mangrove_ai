# -*- coding: utf-8 -*-
"""冻结契约的内存 Adapter；供测试和原型穿过与生产相同的 Interface。"""
from __future__ import annotations

from .models import (
    AcquisitionRun,
    AutomationProcedure,
    CapabilityPack,
    ContextDelta,
    RawUserTurn,
    RevisionDecision,
    RevisionDecisionStatus,
    RevisionProposal,
    SteeringResult,
)


class InMemorySteeringRepository:
    def __init__(self) -> None:
        self.turns: dict[tuple[str, str], RawUserTurn] = {}
        self.turn_keys: dict[tuple[str, str, str], str] = {}
        self.deltas: dict[tuple[str, str], ContextDelta] = {}
        self.delta_turns: dict[tuple[str, str], str] = {}
        self.proposals: dict[tuple[str, str], RevisionProposal] = {}
        self.decisions: dict[tuple[str, str], RevisionDecision] = {}
        self.decision_proposals: dict[tuple[str, str], str] = {}
        self.results: dict[tuple[str, str], SteeringResult] = {}
        self.result_turns: dict[tuple[str, str], str] = {}

    def save_turn(self, turn: RawUserTurn) -> RawUserTurn:
        if turn.idempotency_key:
            key = (turn.owner_id, turn.task_id, turn.idempotency_key)
            existing_id = self.turn_keys.get(key)
            if existing_id:
                existing = self.turns[(turn.owner_id, existing_id)]
                if existing.revision != turn.revision or existing.text != turn.text:
                    raise ValueError("相同幂等键不能提交不同的用户原话")
                return existing
            self.turn_keys[key] = turn.turn_id
        key = (turn.owner_id, turn.turn_id)
        if key in self.turns:
            raise ValueError("用户回合已存在，禁止覆盖")
        self.turns[key] = turn
        return turn

    def get_turn(self, owner_id: str, turn_id: str) -> RawUserTurn | None:
        return self.turns.get((owner_id, turn_id))

    def list_turns(
        self,
        owner_id: str,
        task_id: str,
        *,
        revision: int | None = None,
    ) -> tuple[RawUserTurn, ...]:
        return tuple(
            sorted(
                (
                    turn
                    for (owner, _), turn in self.turns.items()
                    if owner == owner_id
                    and turn.task_id == task_id
                    and (revision is None or turn.revision == revision)
                ),
                key=lambda item: (item.created_at, item.turn_id),
            )
        )

    def save_delta(self, turn_id: str, delta: ContextDelta) -> ContextDelta:
        turn_key = (delta.owner_id, turn_id)
        existing_id = self.delta_turns.get(turn_key)
        if existing_id:
            existing = self.deltas[(delta.owner_id, existing_id)]
            if existing == delta:
                return existing
            raise ValueError("上下文变更草案已存在，禁止覆盖")
        self.deltas[(delta.owner_id, delta.delta_id)] = delta
        self.delta_turns[turn_key] = delta.delta_id
        return delta

    def get_delta_for_turn(self, owner_id: str, turn_id: str) -> ContextDelta | None:
        delta_id = self.delta_turns.get((owner_id, turn_id))
        return self.get_delta(owner_id, delta_id) if delta_id else None

    def get_delta(self, owner_id: str, delta_id: str) -> ContextDelta | None:
        return self.deltas.get((owner_id, delta_id))

    def save_proposal(self, proposal: RevisionProposal) -> RevisionProposal:
        key = (proposal.owner_id, proposal.proposal_id)
        if key in self.proposals:
            raise ValueError("Revision 草案已存在，禁止覆盖")
        self.proposals[key] = proposal
        return proposal

    def get_proposal(self, owner_id: str, proposal_id: str) -> RevisionProposal | None:
        return self.proposals.get((owner_id, proposal_id))

    def list_proposals(
        self,
        owner_id: str,
        task_id: str,
    ) -> tuple[RevisionProposal, ...]:
        return tuple(
            sorted(
                (
                    proposal
                    for (owner, _), proposal in self.proposals.items()
                    if owner == owner_id and proposal.task_id == task_id
                ),
                key=lambda item: (item.created_at, item.proposal_id),
            )
        )

    def update_proposal(self, proposal: RevisionProposal) -> RevisionProposal:
        key = (proposal.owner_id, proposal.proposal_id)
        if key not in self.proposals:
            raise KeyError("Revision 草案不存在或无权访问")
        self.proposals[key] = proposal
        return proposal

    def save_decision(self, decision: RevisionDecision) -> RevisionDecision:
        proposal_key = (decision.owner_id, decision.proposal_id)
        existing_id = self.decision_proposals.get(proposal_key)
        if existing_id:
            return self.decisions[(decision.owner_id, existing_id)]
        self.decisions[(decision.owner_id, decision.decision_id)] = decision
        self.decision_proposals[proposal_key] = decision.decision_id
        return decision

    def get_decision(self, owner_id: str, decision_id: str) -> RevisionDecision | None:
        return self.decisions.get((owner_id, decision_id))

    def waiting_decision(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> RevisionDecision | None:
        candidates = [
            item
            for (owner, _), item in self.decisions.items()
            if owner == owner_id
            and item.task_id == task_id
            and item.base_revision == revision
            and item.status is RevisionDecisionStatus.WAITING_SAFE_POINT
        ]
        return max(candidates, key=lambda item: item.updated_at) if candidates else None

    def update_decision(self, decision: RevisionDecision) -> RevisionDecision:
        key = (decision.owner_id, decision.decision_id)
        if key not in self.decisions:
            raise KeyError("Revision 决策不存在或无权访问")
        self.decisions[key] = decision
        return decision

    def save_result(self, result: SteeringResult) -> SteeringResult:
        turn_key = (result.owner_id, result.turn_id)
        existing_id = self.result_turns.get(turn_key)
        if existing_id:
            return self.results[(result.owner_id, existing_id)]
        self.results[(result.owner_id, result.result_id)] = result
        self.result_turns[turn_key] = result.result_id
        return result

    def get_result_for_turn(self, owner_id: str, turn_id: str) -> SteeringResult | None:
        result_id = self.result_turns.get((owner_id, turn_id))
        return self.get_result(owner_id, result_id) if result_id else None

    def get_result(self, owner_id: str, result_id: str) -> SteeringResult | None:
        return self.results.get((owner_id, result_id))


class InMemoryContractRegistry:
    """AC-00 只验证作用域与不可变性，不实现 AC-04 的目录解析。"""

    def __init__(self) -> None:
        self.capabilities: dict[tuple[str | None, str, str], CapabilityPack] = {}
        self.procedures: dict[tuple[str | None, str, str], AutomationProcedure] = {}
        self.acquisitions: dict[tuple[str, str], AcquisitionRun] = {}

    def save_capability(self, item: CapabilityPack) -> CapabilityPack:
        key = (item.owner_id, item.pack_id, item.version)
        if key in self.capabilities and self.capabilities[key] != item:
            raise ValueError("能力版本不可覆盖")
        self.capabilities[key] = item
        return item

    def save_procedure(self, item: AutomationProcedure) -> AutomationProcedure:
        key = (item.owner_id, item.procedure_id, item.version)
        if key in self.procedures and self.procedures[key] != item:
            raise ValueError("自动化方案版本不可覆盖")
        self.procedures[key] = item
        return item

    def save_acquisition(self, item: AcquisitionRun) -> AcquisitionRun:
        self.acquisitions[(item.owner_id, item.acquisition_id)] = item
        return item
