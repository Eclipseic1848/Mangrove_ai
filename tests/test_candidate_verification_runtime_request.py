# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

from src.candidate_verification.runtime_request import (
    parse_frozen_runtime_request,
)


def _request_json(*, include_confirmation: bool, external: bool = True) -> str:
    values = {
        "user_id": "owner-a",
        "task_id": "task-a",
        "revision": 1,
        "objective_text": "读取来源并输出 CSV",
        "requested_output_formats": ["csv"],
        "sources": [
            {
                "upload_id": "upload-a",
                "original_name": "source.txt",
                "host_path": "C:/frozen/source.txt",
                "sha256": "a" * 64,
                "media_type": "text/plain",
            }
        ],
        "permission_profile": "standard",
    }
    if external:
        values.update(
            {
                "model_connection_id": "connection-a",
                "model_connection_version": "version-a",
                "model_connection_model": "deepseek-v4-flash",
            }
        )
    else:
        values.update(
            {
                "model": "local-model",
                "base_url": "http://127.0.0.1:11434/v1",
            }
        )
    if include_confirmation:
        values["external_api_confirmed"] = True
    return json.dumps(values, ensure_ascii=False)


@pytest.mark.parametrize("confirmation", [True, 1])
def test_parser_accepts_strict_true_runtime_confirmation(confirmation) -> None:
    request, used_legacy_confirmation = parse_frozen_runtime_request(
        request_json=_request_json(include_confirmation=False),
        external_api_confirmed=confirmation,
    )

    assert request.external_api_confirmed is True
    assert used_legacy_confirmation is True


@pytest.mark.parametrize("confirmation", [False, 0])
def test_parser_accepts_strict_false_runtime_confirmation(confirmation) -> None:
    request, used_legacy_confirmation = parse_frozen_runtime_request(
        request_json=_request_json(
            include_confirmation=False,
            external=False,
        ),
        external_api_confirmed=confirmation,
    )

    assert request.external_api_confirmed is False
    assert used_legacy_confirmation is True


@pytest.mark.parametrize("confirmation", ["false", "0", 2, -1, None, 1.0])
def test_parser_rejects_corrupt_runtime_confirmation(confirmation) -> None:
    with pytest.raises(ValueError, match="Runtime 外发确认列必须是布尔值或 0/1"):
        parse_frozen_runtime_request(
            request_json=_request_json(include_confirmation=False),
            external_api_confirmed=confirmation,
        )
