# -*- coding: utf-8 -*-
"""能力获取记录的前向 SQLite Adapter。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .models import (
    AcquisitionEvent,
    AcquisitionRecord,
    AcquisitionRequest,
    AcquisitionResult,
)
from src.conversation_steering import AcquisitionStatus


_DDL = (
    Path(__file__).parent / "migrations" / "0001_acquisition_runs.sql"
).read_text(encoding="utf-8")


class SqliteAcquisitionRepository:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, acquisition_id: str) -> AcquisitionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (acquisition_id,),
            ).fetchone()
        return (
            AcquisitionRecord.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def create(self, request: AcquisitionRequest) -> AcquisitionRecord:
        from src.conversation_steering import AcquisitionStatus

        proposed = AcquisitionRecord(
            request=request,
            status=AcquisitionStatus.DISCOVERING,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_acquisition_runs "
                "(acquisition_id, owner_id, status, payload_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    request.acquisition_id,
                    request.owner_id,
                    proposed.status.value,
                    proposed.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (request.acquisition_id,),
            ).fetchone()
            assert row is not None
            saved = AcquisitionRecord.model_validate_json(row["payload_json"])
            if saved.request != request:
                raise ValueError("同一 acquisition_id 不得改写请求")
        return saved

    def save(self, record: AcquisitionRecord) -> AcquisitionRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (record.request.acquisition_id,),
            ).fetchone()
            if row is None:
                raise ValueError("能力获取记录不存在")
            existing = AcquisitionRecord.model_validate_json(row["payload_json"])
            if existing.request != record.request:
                raise ValueError("能力获取请求身份不一致")
            if existing.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            } and existing != record:
                return existing
            if existing.cancel_requested and not record.cancel_requested:
                record = record.model_copy(update={"cancel_requested": True})
            connection.execute(
                "UPDATE capability_acquisition_runs "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE acquisition_id=?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    record.request.acquisition_id,
                ),
            )
        return record

    def finalize_ready(
        self,
        record: AcquisitionRecord,
    ) -> AcquisitionRecord | None:
        """在同一 SQLite 写事务中检查取消并发布 READY。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (record.request.acquisition_id,),
            ).fetchone()
            if row is None:
                return None
            existing = AcquisitionRecord.model_validate_json(row["payload_json"])
            if (
                existing.request != record.request
                or existing.cancel_requested
            ):
                return None
            if existing.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                return existing if existing.status is AcquisitionStatus.READY else None
            connection.execute(
                "UPDATE capability_acquisition_runs "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE acquisition_id=?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    record.request.acquisition_id,
                ),
            )
        return record

    def request_cancel(
        self,
        acquisition_id: str,
        owner_id: str,
    ) -> AcquisitionRecord:
        """取消检查与写入同事务完成，终态绝不被旧快照倒写。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (acquisition_id,),
            ).fetchone()
            if row is None:
                raise KeyError("能力获取记录不存在")
            record = AcquisitionRecord.model_validate_json(row["payload_json"])
            if record.request.owner_id != owner_id:
                raise PermissionError("不能取消其他用户的能力获取")
            if record.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                return record
            if record.status is AcquisitionStatus.AWAITING_PERMISSION:
                event = AcquisitionEvent(
                    acquisition_id=acquisition_id,
                    owner_id=owner_id,
                    sequence=len(record.events) + 1,
                    status=AcquisitionStatus.CANCELLED,
                    summary="等待权限的能力获取已取消",
                )
                result = AcquisitionResult(
                    acquisition_id=acquisition_id,
                    owner_id=owner_id,
                    status=AcquisitionStatus.CANCELLED,
                    failure_code="CANCELLED",
                )
                record = record.model_copy(
                    update={
                        "status": AcquisitionStatus.CANCELLED,
                        "events": (*record.events, event),
                        "result": result,
                        "cancel_requested": True,
                    }
                )
            else:
                record = record.model_copy(update={"cancel_requested": True})
            connection.execute(
                "UPDATE capability_acquisition_runs "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE acquisition_id=?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    acquisition_id,
                ),
            )
        return record
