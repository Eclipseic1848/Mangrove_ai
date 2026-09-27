"""用合成来源验证逐银行预算、OCR 递补及停止，不调用外部服务。"""
import asyncio
import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector
from src.conductor.task_spec import TaskSpec
from src.conductor import bank_benefits, source_images


def test_each_bank_replaces_rejected_ocr_and_stops_at_target(monkeypatch):
    searches, images = [], []

    async def collect(self, spec):
        name = spec.keywords[0]
        searches.append((name, spec.xhs_search_page))
        return CollectResult(True, "mediacrawler", items=[CollectedItem(
            url=f"https://example.com/{name}/{i}", title=name, content="相关权益",
            metadata={"note_id": f"{name}-{i}", "note_type": "normal", "image_urls": ["image1", "image2"],
                      "publish_time": "2026-09-01T00:00:00+00:00"},
        ) for i in range(20)])

    async def assess(note, scope, state):
        stale = bool(note.get("images")) and note["metadata"]["note_id"].endswith("-0")
        return {"relevant": True, "tag_stale": stale, "tag_audience_mismatch": False,
                "credit_card_only": False, "benefits": [], "rank_reason": "相关"}

    async def read_images(note, task_id, raw_only):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": index, "status": "recognized", "text": "规则"} for index in (1, 2)]

    monkeypatch.setattr(SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    scope = {"source_evidence": "小红书", "publication_from": "2026-01-01", "publication_to": "2026-12-31",
             "banks": [{"name": "中信", "keyword": "中信权益"}, {"name": "招行", "keyword": "招行权益"}]}
    spec = TaskSpec(intent="小红书银行权益", bank_benefits=scope)
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "test"}))
    assert searches == [("中信权益", 1), ("招行权益", 1)]
    assert len(images) == 12
    assert len(result["raw_dataset"]) == 10
    for report in result["bank_collection"]["banks"]:
        assert report["selected_count"] == 5 and report["ocr_attempted"] == 6
        assert report["stop_reason"] == "target_reached"
    assert len(result["bank_collection"]["candidates"]) == 40


@pytest.mark.parametrize("image_status", ["failed", "downloaded"])
def test_ocr_failures_count_toward_limit(monkeypatch, image_status):
    async def collect(self, spec):
        return CollectResult(True, "mediacrawler", items=[CollectedItem(
            url=f"https://example.com/{i}", content="权益", metadata={"note_id": str(i),
            "publish_time": "2026-09-01T00:00:00+00:00", "note_type": "normal", "image_urls": ["bad"]},
        ) for i in range(20)])

    async def assess(*args):
        return {"relevant": True, "tag_stale": None, "tag_audience_mismatch": False, "credit_card_only": False}

    async def fail(*args):
        return [{"image_index": 1, "status": image_status, "reason": "image_ocr_required"}]

    monkeypatch.setattr(SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", fail)
    spec = TaskSpec(intent="权益", bank_benefits={"source_evidence": "小红书",
        "publication_from": "2026-01-01", "publication_to": "2026-12-31",
        "banks": [{"name": "中信", "keyword": "中信权益"}]})
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "test"}))
    report = result["bank_collection"]["banks"][0]
    assert report["ocr_attempted"] == 20 and report["selected_count"] == 0
    assert report["stop_reason"] == "ocr_limit"


def test_image_candidate_and_beijing_year_boundary():
    spec = TaskSpec(intent="权益", bank_benefits={"source_evidence": "小红书",
        "publication_from": "2026-01-01", "publication_to": "2026-12-31",
        "banks": [{"name": "中信", "keyword": "中信权益"}]})
    note = {"metadata": {"publish_time": "2025-12-31T16:30:00+00:00", "note_type": "normal", "image_urls": ["image"]},
            "assessment": {"relevant": False, "tag_resale": True}}
    assert bank_benefits._exclusion(note, spec.bank_benefits, before_ocr=True) is None
    assert bank_benefits._exclusion(note, spec.bank_benefits) == "unrelated"
    note["assessment"]["relevant"] = True
    note["metadata"]["note_type"] = "video"
    assert bank_benefits._exclusion(note, spec.bank_benefits) == "content_type_mismatch"


def test_expired_query_preserves_stale_evidence_and_rule_images_rank_first():
    spec = TaskSpec(intent="已取消权益", bank_benefits={"source_evidence": "小红书",
        "publication_from": "2026-01-01", "publication_to": "2026-12-31",
        "require_current_rules": False, "banks": [{"name": "中信", "keyword": "中信已取消权益"}]})
    note = {"metadata": {"publish_time": "2026-08-01T00:00:00+00:00", "note_type": "normal"},
            "assessment": {"relevant": True, "tag_stale": True, "evidence_type": ["benefit_table"]}}
    assert bank_benefits._exclusion(note, spec.bank_benefits) is None
    assert bank_benefits._exclusion(note, spec.bank_benefits.model_copy(update={"require_current_rules": True})) == "expired_rule"
    ordinary = {**note, "assessment": {**note["assessment"], "evidence_type": ["promotion"]}}
    assert bank_benefits._priority(note) < bank_benefits._priority(ordinary)


def test_parser_exception_retains_both_images_but_never_marks_recognized(tmp_path, monkeypatch):
    import io
    import httpx
    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (2, 2)).save(data, format="PNG")
    monkeypatch.setattr(source_images.settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(source_images.HttpSecurityGuard, "validate", lambda self, url: None)
    monkeypatch.setattr(source_images, "PinnedAsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(
        lambda request: httpx.Response(200, content=data.getvalue(), headers={"content-type": "image/png"})))
    calls = []

    def fail(self, artifact, raw):
        calls.append(artifact.sha256)
        raise RuntimeError("private-parser-path")

    monkeypatch.setattr(source_images.ImageParser, "parse", fail)
    note = {"metadata": {"note_id": "note", "image_urls": ["https://example.com/1.png", "https://example.com/2.png"]}}
    rows = asyncio.run(bank_benefits.read_images(note, "ocr-error", False))
    assert len(calls) == len(rows) == 2
    assert all(row["status"] == "failed" and row["artifact_path"] for row in rows)
    assert "private-parser-path" not in str(rows)


def test_hundred_candidate_cap_keeps_exclusions_and_exports_empty_snapshot(tmp_path, monkeypatch):
    from src.conductor.nodes.collect import collect_node
    from src.conductor.nodes.output import output_node
    from src.conductor.graph import _route_after_collect

    calls = []
    async def collect(self, spec):
        calls.append(spec.xhs_search_page)
        return CollectResult(True, "mediacrawler", items=[CollectedItem(
            url=f"https://example.com/{spec.xhs_search_page}-{i}", content="无关内容", metadata={
                "note_id": f"{spec.xhs_search_page}-{i}", "note_type": "normal",
                "publish_time": "2026-08-01T00:00:00+00:00", "image_urls": []}) for i in range(20)])

    async def assess(*args):
        return {"relevant": False}

    monkeypatch.setattr(source_images.settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    spec = TaskSpec(intent="权益", bank_benefits={"source_evidence": "小红书",
        "publication_from": "2026-01-01", "publication_to": "2026-12-31",
        "banks": [{"name": "中信", "keyword": "中信权益"}]})
    state = {"task_id": "cap", "task_spec": spec, "collector_candidates": ["must-not-fallback"]}
    state.update(asyncio.run(collect_node(state)))
    assert calls == [1, 2, 3, 4, 5]
    assert not state["raw_dataset"]
    assert len(state["bank_collection"]["candidates"]) == 100
    assert state["bank_collection"]["banks"][0]["stop_reason"] == "candidate_limit"
    assert _route_after_collect(state) == "output"
    output = asyncio.run(output_node(state))
    assert set(output["outputs"]) == {"json", "xlsx", "evidence_zip"}


def test_partial_collection_keeps_coverage_in_snapshot_and_reply(monkeypatch):
    async def collect(self, spec):
        return CollectResult(True, "mediacrawler", items=[CollectedItem(content="相关权益", metadata={
            "note_id": "one", "note_type": "normal", "publish_time": "2026-09-01T00:00:00+00:00",
            "comment_coverage": {"truncated": True, "reasons": ["collector_timeout"]}})],
            message="采集超时，已保留有效数据", coverage={"partial": True, "collector_timeout": True,
                "pages": [{"item_count": 1, "has_more": False}]})
    async def assess(*args):
        return {"relevant": True}
    monkeypatch.setattr(SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    spec = TaskSpec(intent="权益", bank_benefits={"source_evidence": "小红书",
        "publication_from": "2026-01-01", "publication_to": "2026-12-31",
        "banks": [{"name": "中信", "keyword": "中信权益"}]})
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "partial"}))
    report = result["bank_collection"]["banks"][0]
    assert report["selected_count"] == 1
    assert report["batches"][0]["coverage"]["collector_timeout"]
    assert "评论未采全" in result["collector_notes"][0]
    assert result["raw_dataset"][0]["metadata"]["comment_coverage"]["truncated"]


@pytest.mark.parametrize("url,allowed", [
    ("http://sns-webpic-qc.xhscdn.com/a", True),
    ("https://sns-webpic-qc.xhscdn.com/a", True),
    ("https://sns-webpic-qc.xhscdn.com:8443/a", False),
    ("https://sns-webpic-qc.xhscdn.com.evil.test/a", False),
    ("http://user@sns-webpic-qc.xhscdn.com/a", False),
    ("https://example.com/a", False),
    ("https://127.0.0.1/a", False),
])
def test_image_proxy_compatibility_is_exact_https_cdn_only(tmp_path, monkeypatch, url, allowed):
    import io
    import httpx
    from PIL import Image
    from src.connectors import http_security

    data = io.BytesIO()
    Image.new("RGB", (2, 2)).save(data, format="PNG")
    monkeypatch.setattr(source_images.settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(http_security, "default_resolver", lambda host: ["198.18.0.190"])
    requested = []
    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(200, content=data.getvalue(), headers={"content-type": "image/png"})
    monkeypatch.setattr(source_images, "PinnedAsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    note = {"metadata": {"note_id": "note", "image_urls": [url]}}
    rows = asyncio.run(bank_benefits.read_images(note, "proxy-image", True))
    assert (rows[0]["status"] == "downloaded") is allowed
    assert rows[0]["source_url"] == url
    assert bool(requested) is allowed
    if allowed:
        assert requested[0].startswith("https://sns-webpic-qc.xhscdn.com/")
