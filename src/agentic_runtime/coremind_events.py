# -*- coding: utf-8 -*-
"""把 CoreMind v2 事件投影为 Mangrove 的安全工作记录。"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import re
from typing import Any

from .kernel import AgentKernelCapabilityError
from .models import RuntimeEvent


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[^\s\x00-\x1f\x7f]{1,256}", value) is not None


def _public_id(kind: str, run_id: str, value: str) -> str:
    # 协议 ID 允许路径等任意文本；公开记录只用 Run 绑定的指纹关联，不泄漏原值。
    digest = hashlib.sha256(f"{run_id}\0{value}".encode("utf-8")).hexdigest()
    return f"cm_{kind}_{digest}"


def project_coremind_event(
    event: Mapping[str, Any],
    *,
    run_id: str,
    model: str,
    tool_names: Mapping[str, str] | None = None,
) -> RuntimeEvent | None:
    """只复制白名单字段；上游成功事件本身不产生 Candidate 或 Delivery。"""

    if not isinstance(event, Mapping):
        raise AgentKernelCapabilityError("CoreMind 事件不是有效对象")
    timestamp = event.get("timestamp")
    if (
        not isinstance(timestamp, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", timestamp) is None
    ):
        raise AgentKernelCapabilityError("CoreMind 事件时间格式不兼容")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError:
        raise AgentKernelCapabilityError("CoreMind 事件时间无效") from None
    payload = event.get("payload")
    if (
        event.get("protocolVersion") != "2.0"
        or type(event.get("eventSchemaVersion")) is not int
        or event["eventSchemaVersion"] != 1
        or event.get("runId") != run_id
        or type(event.get("sequence")) is not int
        or event["sequence"] < 1
        or not _valid_id(event.get("eventId"))
        or not _valid_id(event.get("runId"))
        or any(
            key in event and not _valid_id(event[key])
            for key in ("turnId", "stepId", "callId", "approvalId", "receiptId", "parentRunId", "childRunId", "delegationId")
        )
        or type(event.get("ignorable")) is not bool
        or event.get("sensitivity") != "local"
        or not isinstance(event.get("eventType"), str)
        or not event["eventType"]
    ):
        raise AgentKernelCapabilityError("CoreMind 事件身份或协议不兼容")
    event_type = event.get("eventType")
    if event_type not in {"turn_end", "tool_call", "tool_result"}:
        # 未知必需事件可能改变任务状态，不能跳过后继续声称执行成功。
        if event["ignorable"] and re.fullmatch(r"fact\.[a-z][a-z0-9_]*", event_type):
            return None
        raise AgentKernelCapabilityError("CoreMind 必需事件尚无安全映射")
    if not isinstance(payload, Mapping) or payload.get("type") != event_type:
        raise AgentKernelCapabilityError("CoreMind 事件类型与载荷不一致")
    identity = {
        "runtime_event_id": _public_id("event", run_id, event["eventId"]),
        "runtime_sequence": event["sequence"],
        "runtime_timestamp": timestamp,
        **({"turn_id": _public_id("turn", run_id, event["turnId"])} if "turnId" in event else {}),
    }
    if event_type == "turn_end":
        return RuntimeEvent(
            event_type="provider.usage",
            summary="模型调用已完成，用量以模型请求账本为准",
            details={
                **identity,
                "trace_normalized": True,
                "purpose": "执行任务",
                "model_name": model,
                # 0.7.1 会把原生 usage 缺失归一为零，事件无法证明计数来源。
                # 只让现有 Broker 的原生 ProviderUsage 参与账本；缺少账本时保持未知。
                "input_tokens": None,
                "output_tokens": None,
                "cache_tokens": None,
                "total_tokens": None,
            },
        )
    if event_type in {"tool_call", "tool_result"}:
        tool = payload.get("tool")
        names = tool_names or {}
        call_id = event.get("callId", payload.get("callId"))
        # 展示名称只能来自冻结目录，不能让模型参数或工具原始输出伪装成安全记录。
        if (
            not isinstance(tool, str)
            or tool not in names
            or not _valid_id(call_id)
            or payload.get("callId", call_id) != call_id
        ):
            raise AgentKernelCapabilityError("CoreMind 工具身份不属于冻结目录")
        public_call_id = _public_id("call", run_id, call_id)
        details = {
            **identity,
            "trace_normalized": True,
            "purpose": "执行任务",
            "tool": tool,
            "tool_call_id": public_call_id,
            "action": {"tool": names[tool], "action_id": public_call_id},
        }
        if event_type == "tool_call":
            return RuntimeEvent(
                event_type="tool.started", summary=f"正在使用{names[tool]}", details=details,
            )
        failed = payload.get("isError")
        if type(failed) is not bool:
            raise AgentKernelCapabilityError("CoreMind 工具结果状态无效")
        return RuntimeEvent(
            event_type="tool.failed" if failed else "tool.completed",
            summary=f"{names[tool]}执行失败" if failed else f"{names[tool]}已完成",
            details={**details, "failed": failed},
        )
