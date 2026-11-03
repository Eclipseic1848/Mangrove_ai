"""银行权益初筛只在新处理版本使用单次非思考选项。"""
import asyncio
import json
import socket

import pytest

from src.conductor import bank_benefits
from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector
from src.conductor import evidence_collection as flow
from src.conductor.task_spec import TaskSpec
from src.llm.provider import _bound_chat


@pytest.mark.parametrize(
    "stage,new_version,expected_options",
    [
        ("screen", True, {"max_tokens": 4096, "thinking": False}),
        ("screen", False, {"max_tokens": 4096}),
        ("extract", True, {"max_tokens": 65536}),
    ],
)
def test_bound_bank_assessment_only_disables_thinking_for_new_screen(stage, new_version, expected_options):
    calls = []

    async def bound(messages, **options):
        calls.append(options)
        return json.dumps({"relevant": None, "rank_reason": "证据不足", "benefits": []}, ensure_ascii=False)

    spec = TaskSpec(intent="合成银行权益", bank_benefits={"source_evidence": "小红书",
        "banks": [{"name": "光大", "keyword": "合成权益"}]})
    state = {"task_spec": spec, "_assessment_stage": stage,
             "_bank_screen_thinking_disabled": new_version}
    token = _bound_chat.set(bound)
    try:
        result = asyncio.run(bank_benefits.assess_note({"content": "合成权益", "metadata": {}}, spec.bank_benefits, state))
    finally:
        _bound_chat.reset(token)
    assert calls == [expected_options]
    assert result["relevant"] is None


def test_v9_collection_passes_screen_option_through_bound_chat(monkeypatch, tmp_path):
    calls = []
    images = []

    def no_network(*args, **kwargs):
        raise AssertionError("合成测试不得访问网络")

    async def collect(self, request):
        return CollectResult(True, "synthetic", items=[CollectedItem(
            content="光大银行私行客户权益", metadata={"note_id": "synthetic", "note_type": "normal",
                "publish_time": "2026-09-01T00:00:00+00:00", "image_urls": ["synthetic-image"]})],
            coverage={"pages": [{"has_more": False}]})

    async def read_images(note, task_id, raw_only):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "合成权益"}]

    async def bound(messages, **options):
        calls.append(options)
        return json.dumps({"relevant": True, "benefits": [], "rank_reason": "相关",
            "evidence": {"relevant": {"source_id": "note", "quote": "光大银行私行客户权益"}}}, ensure_ascii=False)

    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow, "execution_owner", lambda: None)
    monkeypatch.setattr(SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    spec = TaskSpec(intent="合成银行权益", bank_benefits={"source_evidence": "小红书", "target_count": 1,
        "publication_from": "2026-01-01", "publication_to": "2026-12-31",
        "banks": [{"name": "光大", "keyword": "合成权益"}]})

    async def run():
        monkeypatch.setattr(socket.socket, "connect", no_network)
        monkeypatch.setattr(socket.socket, "connect_ex", no_network)
        return await bank_benefits.collect_bank_benefits({"task_id": "synthetic-v9", "task_spec": spec})

    token = _bound_chat.set(bound)
    try:
        result = asyncio.run(run())
    finally:
        _bound_chat.reset(token)
    assert calls == [{"max_tokens": 4096, "thinking": False}, {"max_tokens": 65536}]
    assert images == ["synthetic"]
    assert result["collection_outcome"]["selected_count"] == 1
