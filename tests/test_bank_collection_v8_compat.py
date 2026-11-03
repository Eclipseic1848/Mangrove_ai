"""冻结 v8 的多图调度和初筛行为不能被新版本改变。"""
import asyncio
import hashlib
import json

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.conductor import bank_benefits
from src.conductor import evidence_collection as flow
from src.conductor.collection_recovery import progress_location
from src.conductor.task_spec import TaskSpec


def test_frozen_v8_keeps_multi_image_order_and_screen_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow.settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(flow, "execution_owner", lambda: "isolated-bank-v8-owner")
    spec = TaskSpec(intent="合成冻结银行权益", bank_benefits={"source_evidence": "小红书",
        "banks": [{"name": "光大银行", "keyword": "合成私行权益"}], "target_count": 5,
        "candidate_limit": 100, "ocr_limit": 20, "time_budget_seconds": 1800})
    state = {"task_id": "frozen-v8-multi-image", "task_spec": spec}
    reads, screens = [], []

    async def empty(self, request):
        return CollectResult(False, "synthetic")

    async def assess(note, scope, current):
        number = int(note["metadata"]["note_id"])
        if current["_assessment_stage"] == "screen":
            screens.append(current.get("_bank_screen_thinking_disabled", False))
            relevant = True if number < 2 else None
        else:
            relevant = number != 2
        return {"relevant": relevant, "tag_stale": False, "tag_audience_mismatch": False,
                "credit_card_only": False, "benefits": []}

    async def read(note, task_id, raw_only):
        reads.append(int(note["metadata"]["note_id"]))
        return [{"image_index": 1, "status": "recognized", "text": "合成私行权益原文"}]

    async def run():
        return await flow._collect_evidence(state, scope=spec.bank_benefits,
            queries=spec.bank_benefits.banks, assess=assess, exclude=bank_benefits._exclusion,
            read_images=read, rank=lambda note: 0, legacy=True, credential_binding="synthetic")

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", empty)
    asyncio.run(run())
    store, key = progress_location("isolated-bank-v8-owner", state["task_id"])
    saved = store.read_json_if_exists(key, "progress.json")
    saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v8"
    saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"], ensure_ascii=False,
        sort_keys=True).encode("utf-8")).hexdigest()
    original_binding = saved["binding"]
    for number, count in enumerate([4, 1, 8, 11, 1, 1, 1]):
        item = CollectedItem(content="光大银行私行客户合成权益", metadata={
            "note_id": str(number), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": [f"synthetic:{number}:{i}" for i in range(count)]})
        raw = item.to_dict()
        artifact = flow.ArtifactStore().write_raw(state["task_id"], str(number),
            json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            uri=item.url, media_type="application/json")
        saved["candidates"].append({**raw, "group": "光大银行", "bank": "光大银行",
            "status": "unprocessed", "images": [], "_stage": "discovered",
            "_access_handle": None, "source_artifact": artifact.model_dump(mode="json")})
    saved["elapsed_seconds"] = 0
    saved["groups"][0].update(candidate_attempted=20, page=2, selected_count=0, ocr_attempted=0,
        done=False, exhausted=False, discovery_inflight=False, stop_reason="candidate_limit",
        pending=list(range(7)), batches=[{"phase": "discovery", "attempted": 20,
        "returned_count": 7, "unknown_ocr_attempted": 0}])
    store.write_json(key, "progress.json", saved)

    async def forbidden(self, request):
        raise AssertionError("冻结候选未处理完毕前不能检索")

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    original_path = flow.ArtifactStore().resolve_path(saved["candidates"][0]["source_artifact"]["storage_path"])
    original_bytes = original_path.read_bytes()
    original_path.write_bytes(b"synthetic-tampered")
    with pytest.raises(ValueError, match="已冻结来源发生变化"):
        asyncio.run(run())
    assert not screens and not reads
    original_path.write_bytes(original_bytes)
    result = asyncio.run(run())
    current = store.read_json_if_exists(key, "progress.json")
    assert current["binding"] == original_binding
    assert current["binding_fields"]["processing_version"] == "bank-benefits-collection-v8"
    assert screens == [False] * 7
    assert [reads.count(i) for i in range(7)] == [4, 1, 8, 7, 0, 0, 0]
    group = result["bank_collection"]["banks"][0]
    assert group["selected_count"] == 2 and group["ocr_attempted"] == 20
    assert group["stop_reason"] == "ocr_limit"


def test_frozen_v8_screen_does_not_disable_thinking(monkeypatch):
    calls = []

    async def model(messages, **kwargs):
        calls.append(kwargs)
        return '{"relevant": null}'

    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = TaskSpec(intent="合成银行权益", bank_benefits={"source_evidence": "小红书",
        "banks": [{"name": "光大银行", "keyword": "合成私行权益"}]})
    asyncio.run(bank_benefits.assess_note({"content": "合成原文", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "screen",
                             "_bank_screen_thinking_disabled": False}))
    assert len(calls) == 1 and calls[0].get("thinking") is None
    assert calls[0]["max_tokens"] == flow.ASSESSMENT_TOKEN_LIMITS["screen"]
