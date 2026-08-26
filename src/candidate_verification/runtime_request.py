# -*- coding: utf-8 -*-
"""解析 CandidateVerification 使用的冻结 Runtime 请求。"""
from __future__ import annotations

import json
import hashlib

from src.agentic_runtime.models import PiRuntimeRequest


def frozen_request_contract_hashes(
    request: PiRuntimeRequest,
) -> tuple[str, str]:
    """从冻结请求重建目标与交付契约摘要。"""

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
            hashlib.sha256(request.base_url.encode("utf-8")).hexdigest()
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
            item.model_dump(mode="json")
            for item in request.table_output_contracts
        ],
    }

    def digest(payload: object) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    return digest(goal_payload), digest(delivery_payload)


def parse_frozen_runtime_request(
    *,
    request_json: str,
    external_api_confirmed: object,
) -> tuple[PiRuntimeRequest, bool]:
    """只兼容旧请求缺失的外发字段，不修改冻结 JSON 或复用旧授权。"""

    request_values = json.loads(request_json)
    if not isinstance(request_values, dict):
        raise ValueError("冻结 Runtime 请求必须是对象")
    used_legacy_confirmation = "external_api_confirmed" not in request_values
    if isinstance(external_api_confirmed, bool):
        runtime_confirmation = external_api_confirmed
    elif type(external_api_confirmed) is int and external_api_confirmed in (0, 1):
        runtime_confirmation = bool(external_api_confirmed)
    else:
        # SQLite 非 STRICT 列可能容纳字符串或越界整数，权威恢复必须失败关闭。
        raise ValueError("Runtime 外发确认列必须是布尔值或 0/1")
    if used_legacy_confirmation:
        request_values["external_api_confirmed"] = runtime_confirmation
    else:
        frozen_confirmation = request_values["external_api_confirmed"]
        if not isinstance(frozen_confirmation, bool):
            raise ValueError("冻结请求外发确认必须是布尔值")
        if frozen_confirmation != runtime_confirmation:
            raise ValueError("冻结请求与 Runtime 外发确认不一致")
    if not request_values.get("model_connection_id"):
        request_values["api_key"] = "local-runtime"
    return PiRuntimeRequest.model_validate(request_values), used_legacy_confirmation
