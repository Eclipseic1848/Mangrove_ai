"""银行权益任务的逐图预算分配。"""
import asyncio

from src.collectors.base import CollectedItem, CollectResult
from src.conductor import bank_benefits, evidence_collection
from src.conductor.task_spec import TaskSpec


def test_small_unknown_notes_are_processed_before_multi_image_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence_collection.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(evidence_collection.settings, "semantic_execution_root", str(tmp_path / "execution"))
    reads = []
    image_counts = [4, 1, 8, 11, 1, 1, 1]

    async def collect(self, request):
        assert request.xhs_search_page == 1
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行权益", metadata={
            "note_id": str(index), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": [f"synthetic:{index}:{j}" for j in range(count)]})
            for index, count in enumerate(image_counts)], coverage={"pages": [{"has_more": True}]})

    async def assess(note, scope, state):
        index = int(note["metadata"]["note_id"])
        relevant = (index < 2 or None) if state["_assessment_stage"] == "screen" else index != 2
        return {"relevant": relevant, "tag_stale": False, "tag_audience_mismatch": False,
                "credit_card_only": False, "benefits": []}

    async def read_images(note, task_id, raw_only):
        reads.append(int(note["metadata"]["note_id"]))
        return [{"image_index": 1, "status": "recognized", "text": "合成权益原文"}]

    monkeypatch.setattr(evidence_collection.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    monkeypatch.setattr(bank_benefits, "_priority", lambda note: 0)
    spec = TaskSpec(intent="合成私行权益", bank_benefits={"source_evidence": "小红书", "target_count": 5,
        "candidate_limit": 100, "ocr_limit": 20, "banks": [{"name": "光大", "keyword": "权益"}]})
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "allocation"}))

    assert reads == [1, 0, 0, 0, 0, 4, 5, 6]
    assert result["collection_outcome"]["selected_count"] == 5
    assert result["bank_collection"]["banks"][0]["ocr_attempted"] == 8


def test_multi_image_unknown_is_deferred_to_preserve_later_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence_collection.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(evidence_collection.settings, "semantic_execution_root", str(tmp_path / "execution"))
    reads, pages = [], []
    counts = [4, 1, 8, 11, 1, 1, 1]

    async def collect(self, request):
        pages.append(request.xhs_search_page)
        indices = range(4) if request.xhs_search_page == 1 else range(4, 7)
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行权益", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": [f"synthetic:{i}:{j}" for j in range(counts[i])]}) for i in indices],
            coverage={"pages": [{"has_more": request.xhs_search_page == 1}]})

    async def assess(note, scope, state):
        index = int(note["metadata"]["note_id"])
        relevant = (index < 2 or None) if state["_assessment_stage"] == "screen" else index != 2
        return {"relevant": relevant, "tag_stale": False, "tag_audience_mismatch": False,
                "credit_card_only": False, "benefits": []}

    async def read_images(note, task_id, raw_only):
        reads.append(int(note["metadata"]["note_id"]))
        return [{"image_index": 1, "status": "recognized", "text": "合成权益原文"}]

    monkeypatch.setattr(evidence_collection.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    monkeypatch.setattr(bank_benefits, "_priority", lambda note: 0)
    spec = TaskSpec(intent="合成私行权益", bank_benefits={"source_evidence": "小红书", "target_count": 5,
        "candidate_limit": 100, "ocr_limit": 20, "banks": [{"name": "光大", "keyword": "权益"}]})
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "reserve"}))

    assert pages == [1, 2]
    assert reads.count(2) == 8 and 3 not in reads
    assert reads[-3:] == [4, 5, 6]
    assert len(reads) == 16 and result["collection_outcome"]["selected_count"] == 5
