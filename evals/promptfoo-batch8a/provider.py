# -*- coding: utf-8 -*-
"""Promptfoo 批次 8A 小型 PoC：验证场景契约与本地运行链。"""
from __future__ import annotations

import json
from typing import Any


_SCENARIOS: dict[str, dict[str, Any]] = {
    "docx_to_txt": {
        "outcome": "accepted",
        "source_kind": "document",
        "formats": ["txt"],
    },
    "pdf_audit": {
        "outcome": "accepted",
        "source_kind": "document",
        "formats": ["docx", "pdf"],
    },
    "xlsx_transform": {
        "outcome": "accepted",
        "source_kind": "table",
        "formats": ["xlsx"],
    },
    "csv_summary": {
        "outcome": "accepted",
        "source_kind": "table",
        "formats": ["csv", "json"],
    },
    "local_model_truncation": {
        "outcome": "failed",
        "error_code": "STP_COMPILE_FAILED",
        "external_fallback": False,
    },
    "mixed_inputs": {
        "outcome": "rejected",
        "error_code": "MIXED_INPUT_KINDS",
    },
}


def call_api(
    prompt: str,
    options: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """返回稳定 JSON，供 Promptfoo 验证六类 Batch 8A 契约。"""
    scenario_id = str(
        (context.get("vars") or {}).get("scenario_id") or prompt
    ).strip()
    result = _SCENARIOS.get(scenario_id)
    if result is None:
        return {"error": f"未知场景：{scenario_id}"}
    return {
        "output": json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    }
