"""逐张图片预算与失败停止，来源和识别服务均使用合成替身。"""
import asyncio
import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector
from src.conductor import bank_benefits, source_images
from src.conductor import evidence_collection as flow
from src.conductor.task_spec import TaskSpec


def image_flow(monkeypatch, tmp_path, *, fail_at=None, timeout_at=None, cancel_at=None):
    monkeypatch.setattr(source_images.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(source_images.settings, "semantic_execution_root", str(tmp_path / "execution"))
    calls = {"download": [], "ocr": [], "assess": [], "search": []}
    png = io.BytesIO()
    Image.new("RGB", (2, 2)).save(png, format="PNG")

    async def collect(self, spec):
        calls["search"].append(spec.xhs_search_page)
        return CollectResult(True, "synthetic", items=[CollectedItem(
            content="会员权益原文", metadata={"note_id": str(i), "note_type": "normal",
                "publish_time": "2026-09-01T00:00:00+00:00",
                "image_urls": [f"https://example.com/{i}/{j}.png" for j in range(12)]})
            for i in range(2)], coverage={"pages": [{"has_more": False}]})

    async def assess(note, scope, state):
        calls["assess"].append((state["_assessment_stage"], note["metadata"]["note_id"]))
        return {"relevant": True, "tag_stale": False, "tag_audience_mismatch": False,
                "credit_card_only": False, "benefits": [], "rank_reason": "相关"}

    async def respond(request):
        calls["download"].append(str(request.url))
        if len(calls["download"]) == fail_at:
            return httpx.Response(461)
        if len(calls["download"]) == timeout_at:
            await asyncio.Event().wait()
        if len(calls["download"]) == cancel_at:
            raise asyncio.CancelledError
        return httpx.Response(200, content=png.getvalue(), headers={"content-type": "image/png"})

    def parse(self, artifact, raw):
        from src.services.document_parser_resilience import document_parser_single_attempt
        assert document_parser_single_attempt()
        calls["ocr"].append(artifact.uri)
        return [SimpleNamespace(data={"text": "会员权益", "elements": []})], []

    monkeypatch.setattr(SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(source_images.HttpSecurityGuard, "validate", lambda self, url: None)
    monkeypatch.setattr(source_images, "PinnedAsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    monkeypatch.setattr(source_images.ImageParser, "parse", parse)
    spec = TaskSpec(intent="银行权益", bank_benefits={"source_evidence": "小红书", "target_count": 2,
        "publication_from": "2026-01-01", "publication_to": "2026-12-31", "ocr_limit": 20,
        "banks": [{"name": "光大", "keyword": "光大权益"}]})
    return {"task_id": "image-budget", "task_spec": spec}, calls


def test_twenty_image_budget_counts_each_picture_across_notes(monkeypatch, tmp_path):
    state, calls = image_flow(monkeypatch, tmp_path)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls["download"]) == len(calls["ocr"]) == 20
    assert calls["search"] == [1]
    assert [item for item in calls["assess"] if item[0] == "extract"] == [("extract", "0")]
    report = result["bank_collection"]
    assert report["banks"][0]["ocr_attempted"] == 20
    assert report["banks"][0]["stop_reason"] == "ocr_limit"
    assert [len(note["images"]) for note in report["candidates"]] == [12, 8]
    assert [image["image_index"] for image in report["candidates"][1]["images"]] == list(range(1, 9))
    assert report["candidates"][1]["status"] == "review_required"
    assert not result["collection_outcome"]["target_complete"]


def test_image_refusal_keeps_previous_note_and_stops_all_followup(monkeypatch, tmp_path):
    state, calls = image_flow(monkeypatch, tmp_path, fail_at=14)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls["download"]) == 14 and len(calls["ocr"]) == 13
    assert [item for item in calls["assess"] if item[0] == "extract"] == [("extract", "0")]
    report = result["bank_collection"]
    assert report["banks"][0]["ocr_attempted"] == 14
    assert report["banks"][0]["stop_reason"] == "image_read_or_parse_failed"
    assert report["banks"][0]["image_failure_reason"] == "image_download_failed"
    assert [len(note["images"]) for note in report["candidates"]] == [12, 2]
    assert report["candidates"][1]["images"][-1]["status"] == "failed"
    assert report["candidates"][0]["status"] == "selected"
    assert not result["collection_outcome"]["ready_for_review"]


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_image_unknown_keeps_budget_and_refuses_resume(monkeypatch, tmp_path, failure):
    state, calls = image_flow(monkeypatch, tmp_path, **{failure + "_at": 2})
    monkeypatch.setattr(flow, "execution_owner", lambda: "image-budget-owner")
    monkeypatch.setitem(flow.STEP_TIMEOUT_SECONDS, "images", .2)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bank_benefits.collect_bank_benefits(state))
    else:
        first = asyncio.run(bank_benefits.collect_bank_benefits(state))
        assert first["bank_collection"]["banks"][0]["stop_reason"] == "images_outcome_unknown"
    before = {key: list(value) for key, value in calls.items()}
    resumed = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert calls == before
    report = resumed["bank_collection"]
    assert report["banks"][0]["ocr_attempted"] == 2
    assert report["banks"][0]["stop_reason"] == "images_outcome_unknown"
    assert len(report["candidates"][0]["images"]) == 2
    assert report["candidates"][0]["images"][0]["status"] == "recognized"
    assert report["candidates"][0]["status"] == "review_required"


def test_global_deadline_during_image_does_not_release_partial_draft(monkeypatch, tmp_path):
    state, calls = image_flow(monkeypatch, tmp_path, timeout_at=14)
    state["task_spec"] = state["task_spec"].model_copy(update={"bank_benefits":
        state["task_spec"].bank_benefits.model_copy(update={"time_budget_seconds": 1})})
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls["download"]) == 14
    assert result["bank_collection"]["banks"][0]["stop_reason"] == "time_budget"
    assert result["bank_collection"]["candidates"][0]["status"] == "selected"
    assert result["bank_collection"]["candidates"][1]["reason"] == "images_outcome_unknown"
    assert not result["collection_outcome"]["ready_for_review"]
