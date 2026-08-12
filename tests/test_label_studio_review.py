# -*- coding: utf-8 -*-
"""Label Studio 文档复核适配器测试。"""
from src.services.label_studio_review import (
    build_label_studio_tasks,
    parse_label_studio_decisions,
)


def test_build_label_studio_tasks_keeps_mangrove_identity_and_prediction():
    tasks = build_label_studio_tasks(
        "document-task-1",
        [{
            "task_id": "review-1",
            "artifact_id": "artifact-1",
            "page": 2,
            "field_name": "amount",
            "reasons": ["置信度不足"],
            "candidates": [{
                "value": "CNY 100.00",
                "confidence": 0.72,
            }],
        }],
        [{
            "name": "amount",
            "value": "CNY 100.00",
            "evidence_refs": [{
                "page": 2,
                "quote": "amount: CNY 100.00",
                "confidence": 0.72,
            }],
        }],
        document_urls={"artifact-1": "http://mangrove/doc.pdf#page=2"},
    )

    assert len(tasks) == 1
    assert tasks[0]["meta"]["mangrove_review_task_id"] == "review-1"
    assert tasks[0]["data"]["document_url"].endswith("#page=2")
    assert "amount: CNY 100.00" in tasks[0]["data"]["evidence_html"]
    assert tasks[0]["predictions"][0]["result"][0]["value"]["text"] == [
        "CNY 100.00"
    ]


def test_parse_label_studio_decisions_maps_latest_annotation():
    exported = [{
        "meta": {"mangrove_review_task_id": "review-1"},
        "annotations": [
            {
                "id": 10,
                "updated_at": "2026-07-23T01:00:00Z",
                "result": [{
                    "from_name": "decision",
                    "value": {"choices": ["标记未找到"]},
                }],
            },
            {
                "id": 11,
                "updated_at": "2026-07-23T02:00:00Z",
                "result": [
                    {
                        "from_name": "decision",
                        "value": {"choices": ["使用修订值"]},
                    },
                    {
                        "from_name": "replacement",
                        "value": {"text": ["CNY 101.00"]},
                    },
                    {
                        "from_name": "note",
                        "value": {"text": ["人工核对原页"]},
                    },
                ],
            },
        ],
    }]

    decisions = parse_label_studio_decisions(exported)

    assert len(decisions) == 1
    assert decisions[0].review_task_id == "review-1"
    assert decisions[0].decision == "replace"
    assert decisions[0].value == "CNY 101.00"
    assert decisions[0].annotation_id == 11
