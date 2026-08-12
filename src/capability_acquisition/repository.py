# -*- coding: utf-8 -*-
"""CapabilityAcquisition 的持久化 Seam 与内存 Adapter。"""
from __future__ import annotations

import threading
from typing import Protocol

from src.conversation_steering import AcquisitionStatus

from .models import AcquisitionRecord, AcquisitionRequest


class AcquisitionRepository(Protocol):
    def get(self, acquisition_id: str) -> AcquisitionRecord | None: ...

    def create(self, request: AcquisitionRequest) -> AcquisitionRecord: ...

    def save(self, record: AcquisitionRecord) -> AcquisitionRecord: ...

    def finalize_ready(
        self,
        record: AcquisitionRecord,
    ) -> AcquisitionRecord | None: ...

    def request_cancel(
        self,
        acquisition_id: str,
        owner_id: str,
    ) -> AcquisitionRecord: ...


class InMemoryAcquisitionRepository:
    def __init__(self) -> None:
        self._records: dict[str, AcquisitionRecord] = {}
        self._lock = threading.Lock()

    def get(self, acquisition_id: str) -> AcquisitionRecord | None:
        return self._records.get(acquisition_id)

    def create(self, request: AcquisitionRequest) -> AcquisitionRecord:
        existing = self._records.get(request.acquisition_id)
        if existing is not None:
            if existing.request != request:
                raise ValueError("同一 acquisition_id 不得改写请求")
            return existing
        record = AcquisitionRecord(
            request=request,
            status=AcquisitionStatus.DISCOVERING,
        )
        self._records[request.acquisition_id] = record
        return record

    def save(self, record: AcquisitionRecord) -> AcquisitionRecord:
        with self._lock:
            existing = self._records.get(record.request.acquisition_id)
            if existing is None or existing.request != record.request:
                raise ValueError("获取记录不存在或请求身份不一致")
            if existing.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            } and existing != record:
                return existing
            if existing.cancel_requested and not record.cancel_requested:
                record = record.model_copy(update={"cancel_requested": True})
            self._records[record.request.acquisition_id] = record
            return record

    def finalize_ready(
        self,
        record: AcquisitionRecord,
    ) -> AcquisitionRecord | None:
        with self._lock:
            existing = self._records.get(record.request.acquisition_id)
            if (
                existing is None
                or existing.request != record.request
                or existing.cancel_requested
            ):
                return None
            if existing.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                return existing if existing.status is AcquisitionStatus.READY else None
            self._records[record.request.acquisition_id] = record
            return record

    def request_cancel(
        self,
        acquisition_id: str,
        owner_id: str,
    ) -> AcquisitionRecord:
        with self._lock:
            record = self._records.get(acquisition_id)
            if record is None:
                raise KeyError("能力获取记录不存在")
            if record.request.owner_id != owner_id:
                raise PermissionError("不能取消其他用户的能力获取")
            if record.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                return record
            if record.status is AcquisitionStatus.AWAITING_PERMISSION:
                from .models import AcquisitionEvent, AcquisitionResult

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
            self._records[acquisition_id] = record
            return record
