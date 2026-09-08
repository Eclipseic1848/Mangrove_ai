# -*- coding: utf-8 -*-
"""对话转向记录的 Owner 隔离 SQLite Repository。"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import threading
import json

from .models import (
    ContextDelta,
    RawUserTurn,
    RevisionDecision,
    RevisionDecisionStatus,
    RevisionSwitchMode,
    RevisionProposal,
    SteeringResult,
)
from src.database_migrations import DatabaseTarget, inspect_database


class ResultContextConflict(ValueError):
    """同一回合不得更换或移除已冻结的结果引用。"""




class SqliteSteeringRepository:
    """只暴露领域对象，不向调用方泄漏表结构。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        inspect_database(
            DatabaseTarget(profile="webui", path=Path(self._db_path))
        ).require_current()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _turn(row: sqlite3.Row | None) -> RawUserTurn | None:
        if row is None:
            return None
        return RawUserTurn(
            turn_id=row["turn_id"],
            owner_id=row["owner_id"],
            task_id=row["task_id"],
            revision=row["revision"],
            text=row["text"],
            idempotency_key=row["idempotency_key"],
            result_context=json.loads(row["result_context_json"]) if row["result_context_json"] else None,
            created_at=row["created_at"],
        )

    def save_turn(self, turn: RawUserTurn) -> RawUserTurn:
        """相同幂等键只接受语义完全相同的原始请求。"""

        with self._lock, self._connect() as connection:
            existing = None
            if turn.idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM conversation_raw_turns "
                    "WHERE owner_id=? AND task_id=? AND idempotency_key=?",
                    (turn.owner_id, turn.task_id, turn.idempotency_key),
                ).fetchone()
            if existing is not None:
                saved = self._turn(existing)
                assert saved is not None
                if saved.result_context != turn.result_context:
                    raise ResultContextConflict("相同幂等键不能更换结果引用上下文")
                if (
                    saved.revision != turn.revision
                    or saved.text != turn.text
                ):
                    raise ValueError("相同幂等键不能提交不同的用户原话")
                return saved
            try:
                connection.execute(
                    "INSERT INTO conversation_raw_turns "
                    "(turn_id, owner_id, task_id, revision, text, "
                    "idempotency_key, created_at, result_context_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        turn.turn_id,
                        turn.owner_id,
                        turn.task_id,
                        turn.revision,
                        turn.text,
                        turn.idempotency_key,
                        turn.created_at.isoformat(),
                        turn.result_context.model_dump_json() if turn.result_context else None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("用户回合已存在，禁止覆盖") from exc
        saved = self.get_turn(turn.owner_id, turn.turn_id)
        assert saved is not None
        return saved

    def get_turn(self, owner_id: str, turn_id: str) -> RawUserTurn | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_raw_turns "
                "WHERE owner_id=? AND turn_id=?",
                (owner_id, turn_id),
            ).fetchone()
        return self._turn(row)

    def claim_result_context(self, owner_id: str, turn_id: str) -> None:
        """提交后再发送；取消或进程重建均不回收，避免未知调用重复收费。"""
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE conversation_raw_turns SET result_context_claimed=1 "
                "WHERE owner_id=? AND turn_id=? AND result_context_json IS NOT NULL AND result_context_claimed=0",
                (owner_id, turn_id),
            ).rowcount
        if updated != 1:
            raise ResultContextConflict("这条结果追问已提交或结果未知，禁止自动重复请求模型")

    def list_turns(
        self,
        owner_id: str,
        task_id: str,
        *,
        revision: int | None = None,
    ) -> tuple[RawUserTurn, ...]:
        query = (
            "SELECT * FROM conversation_raw_turns "
            "WHERE owner_id=? AND task_id=?"
        )
        args: list[object] = [owner_id, task_id]
        if revision is not None:
            query += " AND revision=?"
            args.append(revision)
        query += " ORDER BY created_at, turn_id"
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return tuple(
            turn
            for row in rows
            if (turn := self._turn(row)) is not None
        )

    def save_delta(self, turn_id: str, delta: ContextDelta) -> ContextDelta:
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO conversation_context_deltas "
                    "(delta_id, owner_id, task_id, turn_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        delta.delta_id,
                        delta.owner_id,
                        delta.task_id,
                        turn_id,
                        delta.model_dump_json(),
                        delta.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = connection.execute(
                    "SELECT payload_json FROM conversation_context_deltas "
                    "WHERE owner_id=? AND turn_id=?",
                    (delta.owner_id, turn_id),
                ).fetchone()
                if existing is not None:
                    saved = ContextDelta.model_validate_json(
                        existing["payload_json"]
                    )
                    if saved == delta:
                        return saved
                raise ValueError("上下文变更草案已存在，禁止覆盖") from exc
        return delta

    def get_delta_for_turn(
        self,
        owner_id: str,
        turn_id: str,
    ) -> ContextDelta | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_context_deltas "
                "WHERE owner_id=? AND turn_id=?",
                (owner_id, turn_id),
            ).fetchone()
        return (
            ContextDelta.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def get_delta(
        self,
        owner_id: str,
        delta_id: str,
    ) -> ContextDelta | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_context_deltas "
                "WHERE owner_id=? AND delta_id=?",
                (owner_id, delta_id),
            ).fetchone()
        return (
            ContextDelta.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def save_proposal(self, proposal: RevisionProposal) -> RevisionProposal:
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO conversation_revision_proposals "
                    "(proposal_id, owner_id, task_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        proposal.proposal_id,
                        proposal.owner_id,
                        proposal.task_id,
                        proposal.model_dump_json(),
                        proposal.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Revision 草案已存在，禁止覆盖") from exc
        return proposal

    def get_proposal(
        self,
        owner_id: str,
        proposal_id: str,
    ) -> RevisionProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_revision_proposals "
                "WHERE owner_id=? AND proposal_id=?",
                (owner_id, proposal_id),
            ).fetchone()
        return (
            RevisionProposal.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_proposals(
        self,
        owner_id: str,
        task_id: str,
    ) -> tuple[RevisionProposal, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM conversation_revision_proposals "
                "WHERE owner_id=? AND task_id=? ORDER BY created_at, proposal_id",
                (owner_id, task_id),
            ).fetchall()
        return tuple(
            RevisionProposal.model_validate_json(row["payload_json"])
            for row in rows
        )

    def update_proposal(self, proposal: RevisionProposal) -> RevisionProposal:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversation_revision_proposals SET payload_json=? "
                "WHERE owner_id=? AND proposal_id=?",
                (
                    proposal.model_dump_json(),
                    proposal.owner_id,
                    proposal.proposal_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError("Revision 草案不存在或无权访问")
        return proposal

    def save_decision(self, decision: RevisionDecision) -> RevisionDecision:
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO conversation_revision_decisions "
                    "(decision_id, proposal_id, owner_id, task_id, "
                    "base_revision, status, payload_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        decision.decision_id,
                        decision.proposal_id,
                        decision.owner_id,
                        decision.task_id,
                        decision.base_revision,
                        decision.status.value,
                        decision.model_dump_json(),
                        decision.created_at.isoformat(),
                        decision.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = connection.execute(
                    "SELECT payload_json FROM conversation_revision_decisions "
                    "WHERE owner_id=? AND proposal_id=?",
                    (decision.owner_id, decision.proposal_id),
                ).fetchone()
                if existing is not None:
                    saved = RevisionDecision.model_validate_json(existing["payload_json"])
                    if saved.mode != decision.mode or saved.external_api_confirmed != decision.external_api_confirmed:
                        raise ValueError("同一决策不能更换切换方式或外发确认")
                    return saved
                raise ValueError("Revision 决策已存在") from exc
        return decision

    def get_decision_for_proposal(self, owner_id: str, proposal_id: str) -> RevisionDecision | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM conversation_revision_decisions WHERE owner_id=? AND proposal_id=?", (owner_id, proposal_id)).fetchone()
        return RevisionDecision.model_validate_json(row[0]) if row else None

    def get_decision(
        self,
        owner_id: str,
        decision_id: str,
    ) -> RevisionDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_revision_decisions "
                "WHERE owner_id=? AND decision_id=?",
                (owner_id, decision_id),
            ).fetchone()
        return (
            RevisionDecision.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def waiting_decision(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> RevisionDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_revision_decisions "
                "WHERE owner_id=? AND task_id=? AND base_revision=? AND status=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (
                    owner_id,
                    task_id,
                    revision,
                    RevisionDecisionStatus.WAITING_SAFE_POINT.value,
                ),
            ).fetchone()
        return (
            RevisionDecision.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def replace_waiting_decision(self, previous: RevisionDecision, mode: RevisionSwitchMode, *, external_api_confirmed: bool, expected_cancel_generation: int) -> RevisionDecision:
        """只允许静止版本显式改选；原选择审计与新选择在同一事务提交。"""
        if previous.status is not RevisionDecisionStatus.WAITING_SAFE_POINT or mode not in {RevisionSwitchMode.CANCEL_NOW, RevisionSwitchMode.NEW_TASK}:
            raise ValueError("该决策不能更换切换方式")
        decision = previous.model_copy(update={
            "mode": mode, "external_api_confirmed": external_api_confirmed,
            "status": RevisionDecisionStatus.READY_TO_APPLY if mode is RevisionSwitchMode.CANCEL_NOW else RevisionDecisionStatus.NEW_TASK_REQUIRED,
            "updated_at": datetime.now(timezone.utc),
        })
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT active_revision,status,cancel_generation FROM semantic_workspace_tasks WHERE user_id=? AND task_id=?",
                (previous.owner_id, previous.task_id),
            ).fetchone()
            if not task or task["active_revision"] != previous.base_revision or task["cancel_generation"] != expected_cancel_generation or task["status"] not in {"needs_input", "paused", "completed", "candidate_ready", "failed", "cancelled"}:
                raise ValueError("任务状态或停止代际已变化，请重新确认")
            stored = connection.execute("SELECT payload_json FROM conversation_revision_decisions WHERE owner_id=? AND decision_id=?", (previous.owner_id, previous.decision_id)).fetchone()
            if not stored or RevisionDecision.model_validate_json(stored[0]) != previous:
                raise ValueError("决策已被处理，请刷新后确认")
            cursor = connection.execute(
                "UPDATE conversation_revision_decisions SET status=?,payload_json=?,updated_at=? WHERE owner_id=? AND decision_id=? AND status=? AND payload_json=?",
                (decision.status.value, decision.model_dump_json(), decision.updated_at.isoformat(), previous.owner_id, previous.decision_id, previous.status.value, stored[0]),
            )
            if cursor.rowcount != 1:
                raise ValueError("决策已被处理，请刷新后确认")
            from src.api.store import WebUIStore
            WebUIStore._clarification_event(connection, previous.owner_id, previous.task_id, "revision.choice_changed", {
                "summary": "已按明确选择更新等待中的版本切换方式",
                "decision_id": previous.decision_id, "revision": previous.base_revision,
                "previous_mode": previous.mode.value, "mode": mode.value,
                "previous_external_api_confirmed": previous.external_api_confirmed,
                "external_api_confirmed": external_api_confirmed,
            }, event_id=f"revision-choice:{previous.decision_id}")
        return decision

    def update_decision(self, decision: RevisionDecision, *, expected: RevisionDecision | None = None) -> RevisionDecision | None:
        with self._lock, self._connect() as connection:
            original = None
            if expected is not None:
                connection.execute("BEGIN IMMEDIATE")
                stored = connection.execute("SELECT payload_json FROM conversation_revision_decisions WHERE owner_id=? AND decision_id=?", (decision.owner_id, decision.decision_id)).fetchone()
                if not stored or RevisionDecision.model_validate_json(stored[0]) != expected:
                    return None
                # 旧 JSON 可省略新增可空字段；比较完整语义，再使用原文 CAS。
                original = stored[0]
            cursor = connection.execute(
                "UPDATE conversation_revision_decisions "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE owner_id=? AND decision_id=?" + (" AND payload_json=?" if expected else ""),
                (
                    decision.status.value,
                    decision.model_dump_json(),
                    decision.updated_at.isoformat(),
                    decision.owner_id,
                    decision.decision_id,
                ) + ((original,) if expected else ()),
            )
        if cursor.rowcount != 1:
            if expected is not None:
                return None
            raise KeyError("Revision 决策不存在或无权访问")
        return decision

    def save_result(self, result: SteeringResult) -> SteeringResult:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO conversation_steering_results "
                    "(result_id, owner_id, task_id, turn_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        result.result_id,
                        result.owner_id,
                        result.task_id,
                        result.turn_id,
                        result.model_dump_json(),
                        result.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = self._result_for_turn_with_connection(
                    connection,
                    result.owner_id,
                    result.turn_id,
                )
                if existing is not None:
                    return existing
                raise ValueError("对话转向结果已存在，禁止覆盖") from exc
            if result.clarification and connection.execute(
                "SELECT 1 FROM semantic_workspace_tasks WHERE user_id=? AND task_id=?",
                (result.owner_id, result.task_id),
            ).fetchone():
                from src.api.store import WebUIStore
                question = result.clarification
                WebUIStore._clarification_event(connection, result.owner_id, result.task_id, "question_required", {
                    "question": question, "round_id": question["round_id"], "revision": result.revision,
                    "asked_at": result.created_at.isoformat(), "summary": question["prompt"],
                    "action": {"action_id": question["question_id"]}, "recovery_status": "pending",
                }, event_id=f"question:{question['round_id']}")
        return result

    @staticmethod
    def _result_for_turn_with_connection(
        connection: sqlite3.Connection,
        owner_id: str,
        turn_id: str,
    ) -> SteeringResult | None:
        row = connection.execute(
            "SELECT payload_json FROM conversation_steering_results "
            "WHERE owner_id=? AND turn_id=?",
            (owner_id, turn_id),
        ).fetchone()
        return (
            SteeringResult.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def get_result_for_turn(
        self,
        owner_id: str,
        turn_id: str,
    ) -> SteeringResult | None:
        with self._connect() as connection:
            return self._result_for_turn_with_connection(
                connection,
                owner_id,
                turn_id,
            )

    def get_result(
        self,
        owner_id: str,
        result_id: str,
    ) -> SteeringResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_steering_results "
                "WHERE owner_id=? AND result_id=?",
                (owner_id, result_id),
            ).fetchone()
        return (
            SteeringResult.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )
