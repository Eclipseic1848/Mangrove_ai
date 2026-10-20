"""新任务不附加年份或评论，初筛保持简短且只依据原文。"""
import asyncio
from datetime import date, datetime
import hashlib
import json

import pytest

from src.conductor import bank_benefits, prompts
from src.conductor import evidence_collection as flow
from src.collectors.base import CollectedItem, CollectResult
from src.conductor.task_spec import TaskSpec
from src.conductor.collection_recovery import progress_location


def bank_scope(**extra):
    return {"source_evidence": "小红书", "banks": [{"name": "光大银行", "keyword": "光大银行私行权益"}],
            "audience": "私行", **extra}


@pytest.fixture
def seed_v5_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "execution_owner", lambda: "isolated-bank-owner")
    monkeypatch.setattr(flow.settings, "webui_db_path", str(tmp_path / "webui.db"))
    def seed(state, version="v5"):
        original_collect = flow.SocialMediaCollector.collect
        async def empty(self, request):
            return CollectResult(False, "synthetic")
        monkeypatch.setattr(flow.SocialMediaCollector, "collect", empty)
        try:
            asyncio.run(bank_benefits.collect_bank_benefits(state))
        finally:
            monkeypatch.setattr(flow.SocialMediaCollector, "collect", original_collect)
        store, key = progress_location("isolated-bank-owner", state["task_id"])
        saved = store.read_json_if_exists(key, "progress.json")
        # 合成旧版开工快照，既有数据与真实失败预算均不参与本测试。
        saved["binding_fields"]["processing_version"] = f"bank-benefits-collection-{version}"
        saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        saved["groups"][0].update(candidate_attempted=0, done=False, batches=[], discovery_inflight=False, stop_reason="candidate_limit")
        saved["elapsed_seconds"] = 0
        store.write_json(key, "progress.json", saved)
        return saved["binding"]
    return seed


def test_new_scope_has_no_publication_lower_bound_or_default_comments():
    spec = TaskSpec.from_draft({"intent": "采集光大银行私行权益", "bank_benefits": bank_scope()})
    scope = spec.bank_benefits
    assert scope.publication_from is None
    assert scope.publication_to == scope.frozen_at.date()
    assert not spec.include_comments
    assert scope.require_current_rules and not scope.include_credit_card and scope.audience == "私行"


def test_default_publication_end_uses_frozen_day():
    frozen = datetime.fromisoformat("2024-04-06T23:59:59+08:00")
    spec = TaskSpec(intent="权益", bank_benefits=bank_scope(frozen_at=frozen))
    assert spec.bank_benefits.publication_to == date(2024, 4, 6)


@pytest.mark.parametrize("comments", [True, False])
def test_frozen_scope_keeps_dates_comments_and_user_sort(comments):
    spec = TaskSpec(intent="历史私行权益", include_comments=comments, comment_limit=50,
        xhs_sort="time_descending", bank_benefits=bank_scope(publication_from="2024-01-01",
        publication_to="2024-12-31", frozen_at="2025-01-01T01:00:00+08:00", require_current_rules=False))
    frozen = spec.model_dump(mode="json")
    restored = TaskSpec.model_validate(frozen)
    assert restored.model_dump(mode="json") == frozen
    assert restored.include_comments is comments
    assert restored.bank_benefits.publication_from == date(2024, 1, 1)
    assert restored.bank_benefits.publication_to == date(2024, 12, 31)
    assert restored.comment_limit == 50 and restored.xhs_sort == "time_descending"


def test_explicit_nested_comments_are_kept():
    spec = TaskSpec.from_draft({"bank_benefits": bank_scope(include_comments=True, comment_limit=30,
        xhs_sort="time_descending")})
    assert spec.include_comments and spec.comment_limit == 30
    assert spec.xhs_sort == "time_descending"


def test_old_but_current_rule_is_not_removed_by_new_scope():
    scope = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope()).bank_benefits
    note = {"metadata": {"note_type": "normal", "publish_time": "2024-03-01T09:00:00+08:00"},
            "assessment": {"relevant": True, "tag_stale": False, "tag_audience_mismatch": False,
                           "credit_card_only": False}}
    assert bank_benefits._exclusion(note, scope) is None
    note["assessment"]["tag_stale"] = True
    assert bank_benefits._exclusion(note, scope) == "expired_rule"
    assert bank_benefits._exclusion(note, scope.model_copy(update={"publication_from": date(2026, 1, 1)})) == "publication_outside_scope"


def test_unknown_picture_note_still_reaches_ocr():
    scope = TaskSpec(intent="权益", bank_benefits=bank_scope()).bank_benefits
    note = {"content": "权益详见图片", "metadata": {"note_type": "normal", "image_urls": ["image"],
            "publish_time": "2024-03-01T09:00:00+08:00"}, "assessment": {"relevant": None}}
    assert bank_benefits._exclusion(note, scope, before_ocr=True) is None
    assert bank_benefits._exclusion(note, scope) == "relevance_unknown"


@pytest.mark.parametrize("reference", [None, {"source_id": "note", "quote": "不存在的排除依据"},
                                    {"source_id": "not-a-source", "quote": "仅限普通客户"}])
def test_screen_cannot_reject_without_a_matching_source_quote(monkeypatch, reference):
    async def model(messages, **kwargs):
        assert len(messages[0]["content"]) < 1600
        assert "benefit_name" not in messages[0]["content"]
        assert "原文不足且有图片" in messages[0]["content"]
        result = {"relevant": False, "tag_audience_mismatch": True, "rank_reason": "客群不符"}
        if reference:
            result["evidence"] = {"relevant": reference, "tag_audience_mismatch": reference}
        return json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope())
    result = asyncio.run(bank_benefits.assess_note({"content": "仅限普通客户", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is None and result["tag_audience_mismatch"] is None
    assert not result["benefits"]


def test_screen_keeps_short_verified_judgment_evidence(monkeypatch):
    async def model(messages, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False,
            "rank_reason": "光大私行权益", "evidence": {
                "relevant": {"source_id": "note", "quote": "光大银行私行客户权益"},
                "tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope())
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行私行客户权益", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is True and result["tag_audience_mismatch"] is False
    assert result["judgment_evidence"]["relevant"] == {"source_id": "note", "quote": "光大银行私行客户权益"}


@pytest.mark.parametrize("field", ["tag_stale", "tag_audience_mismatch", "credit_card_only"])
def test_screen_exclusion_flags_and_positive_relevance_require_evidence(monkeypatch, field):
    async def model(messages, **kwargs):
        return json.dumps({"relevant": True, field: True, "rank_reason": "待核对" * 100})
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope())
    result = asyncio.run(bank_benefits.assess_note({"content": "权益见图", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is None and result[field] is None
    assert len(result["rank_reason"]) == 120


def test_screen_preserves_quoted_exclusion(monkeypatch):
    async def model(messages, **kwargs):
        return json.dumps({"relevant": True, "tag_stale": True, "rank_reason": "规则明确已失效",
            "evidence": {"relevant": {"source_id": "note", "quote": "光大银行私行权益"},
                         "tag_stale": {"source_id": "note", "quote": "活动已结束"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope())
    note = {"content": "光大银行私行权益，本活动已结束", "metadata": {"note_type": "normal", "publish_time": "2024-01-01T00:00:00+08:00"}}
    note["assessment"] = asyncio.run(bank_benefits.assess_note(note,
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "screen"}))
    assert note["assessment"]["tag_stale"] is True
    assert bank_benefits._exclusion(note, spec.bank_benefits, before_ocr=True) == "expired_rule"


def test_planner_instructions_do_not_add_year_or_comments():
    assert "系统冻结当年1月1日" not in prompts.PLANNER_SYSTEM
    assert "include_comments 默认 false" in prompts.PLANNER_SYSTEM
    assert "仅用户明确要求评论" in prompts.PLANNER_SYSTEM


def test_v5_unknown_picture_notes_keep_later_proved_topic_priority(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, assessments, images = [], [], []
    async def collect(self, request):
        searches.append(request.xhs_search_page)
        assert request.keywords == ["光大银行私行权益"] and request.xhs_sort == "time_descending"
        ids = range(20) if request.xhs_search_page == 1 else [20]
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"]}) for i in ids], coverage={"pages": [{"has_more": request.xhs_search_page == 1}]})
    async def assess(note, scope, state):
        identity = note["metadata"]["note_id"]
        assessments.append((identity, state["_assessment_stage"]))
        return {"relevant": True if identity == "20" else None, "benefits": [], "tag_audience_mismatch": False,
                "judgment_evidence": {"tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}
    async def read_images(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    spec = TaskSpec(intent="光大银行私行权益", xhs_sort="time_descending", bank_benefits=bank_scope(target_count=1))
    state = {"task_spec": spec, "task_id": "defer"}
    original_binding = seed_v5_progress(state)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert result["collection_outcome"]["selected_count"] == 1
    assert searches == [1, 2] and images == ["20"]
    assert len([item for item in assessments if item[1] == "screen"]) == 21
    assert all(n["status"] == "unprocessed" for n in result["bank_collection"]["candidates"][:20])
    store, key = progress_location("isolated-bank-owner", "defer")
    assert store.read_json_if_exists(key, "progress.json")["binding"] == original_binding


def test_exhausted_ocr_does_not_block_later_text_only_topic(monkeypatch, tmp_path):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, images = [], []
    async def collect(self, request):
        searches.append(request.xhs_search_page)
        ids = range(20) if request.xhs_search_page == 1 else [20]
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"] if i < 20 else []}) for i in ids],
            coverage={"pages": [{"has_more": request.xhs_search_page == 1}]})
    async def assess(note, scope, state):
        return {"relevant": True if state["_assessment_stage"] == "screen" or note["metadata"]["note_id"] == "20" else None,
                "benefits": [], "tag_audience_mismatch": False,
                "judgment_evidence": {"tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}
    async def read_images(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope(target_count=1))
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "text-after-ocr"}))
    assert searches == [1, 2] and len(images) == 20
    assert result["collection_outcome"]["selected_count"] == 1
    assert result["bank_collection"]["banks"][0]["ocr_attempted"] == 20


@pytest.mark.parametrize("no_new", [False, True])
@pytest.mark.parametrize("version", ["v5", "v6"])
def test_deferred_unknown_keeps_existing_rank(monkeypatch, tmp_path, no_new, version, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, assessments, images = [], [], []
    async def collect(self, request):
        searches.append(request.xhs_search_page)
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"]}) for i in (0, 1)], coverage={"pages": [{"has_more": no_new}]})
    async def assess(note, scope, state):
        identity, stage = note["metadata"]["note_id"], state["_assessment_stage"]
        assessments.append((identity, stage))
        return {"relevant": None if stage == "screen" else True, "tag_stale": False if identity == "1" else None,
                "benefits": [], "tag_audience_mismatch": False,
                "judgment_evidence": {"tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}
    async def read_images(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope(target_count=1))
    state = {"task_spec": spec, "task_id": "defer-ended"}
    seed_v5_progress(state, version)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert searches == ([1, 2] if no_new and version == "v5" else [1]) and images == ["1"]
    assert len([item for item in assessments if item[1] == "screen"]) == 2
    assert result["collection_outcome"]["selected_count"] == 1


def test_v5_deferred_discovery_keeps_frozen_candidate_cap_order(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, images = [], []
    async def collect(self, request):
        searches.append(request.max_items)
        ids = range((request.xhs_search_page - 1) * 20, request.xhs_search_page * 20)
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"]}) for i in ids], coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        return {"relevant": None if state["_assessment_stage"] == "screen" else True, "benefits": [], "tag_audience_mismatch": False,
                "judgment_evidence": {"tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}
    async def read_images(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope(target_count=1))
    state = {"task_spec": spec, "task_id": "defer-cap"}
    seed_v5_progress(state)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert searches == [20] * 5 and images == ["0"]
    assert result["bank_collection"]["banks"][0]["candidate_attempted"] == 100
    assert result["collection_outcome"]["selected_count"] == 1


@pytest.mark.parametrize("version", ["v5", "v6"])
def test_cancelled_deferred_fallback_retains_pending_and_does_not_replay(monkeypatch, tmp_path, version, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow, "execution_owner", lambda: "isolated-bank-owner")
    searches, assessments, images = [], [], []
    image_started = asyncio.Event()
    async def collect(self, request):
        searches.append(request.xhs_search_page)
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"]}) for i in (0, 1)], coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        assessments.append((note["metadata"]["note_id"], state["_assessment_stage"]))
        return {"relevant": None if state["_assessment_stage"] == "screen" else True, "benefits": [], "tag_audience_mismatch": False,
                "judgment_evidence": {"tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}
    async def read_images(note, *args):
        identity = note["metadata"]["note_id"]
        images.append(identity)
        if identity == "0":
            image_started.set()
            await asyncio.Event().wait()
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read_images)
    spec = TaskSpec(intent="光大银行私行权益", bank_benefits=bank_scope(target_count=1))
    state = {"task_spec": spec, "task_id": "defer-cancel"}
    seed_v5_progress(state, version)
    async def scenario():
        task = asyncio.create_task(bank_benefits.collect_bank_benefits(state))
        await asyncio.wait_for(image_started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await bank_benefits.collect_bank_benefits(state)
    result = asyncio.run(scenario())
    assert searches == ([1, 2] if version == "v5" else [1]) and images == ["0", "1"]
    assert assessments.count(("0", "screen")) == assessments.count(("1", "screen")) == 1
    assert [n["status"] for n in result["bank_collection"]["candidates"]] == ["review_required", "selected"]
    if version == "v6":
        assert result["bank_collection"]["banks"][0]["batches"][0]["unknown_ocr_attempted"] == 2


@pytest.mark.parametrize("failed_image", [False, True])
def test_v6_reads_two_unknown_before_next_batch_and_keeps_proved_first(monkeypatch, tmp_path, failed_image, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    events = []
    async def collect(self, request):
        events.append(("search", request.xhs_search_page))
        ids = range(20) if request.xhs_search_page == 1 else [20, 21, 22]
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大私行权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]}) for i in ids],
            coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        i = int(note["metadata"]["note_id"])
        return {"relevant": True if i in (20, 22) else None, "benefits": [], "tag_audience_mismatch": False}
    async def images(note, *args):
        events.append(("image", int(note["metadata"]["note_id"])))
        return [{"image_index": 1, "status": "failed" if failed_image and int(note["metadata"]["note_id"]) < 20 else "recognized", "text": "光大私行权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    spec = TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=2))
    state = {"task_spec": spec, "task_id": "v6-batches"}
    seed_v5_progress(state, "v6")
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert events == [("search", 1), ("image", 0), ("image", 1), ("search", 2), ("image", 20), ("image", 22)]
    group = result["bank_collection"]["banks"][0]
    assert group["ocr_attempted"] == 4 and group["candidate_attempted"] == 40
    assert group["batches"][0]["unknown_ocr_attempted"] == 2
    assert group["batches"][1]["unknown_ocr_attempted"] == 0
    assert result["collection_outcome"]["selected_count"] == 2
    assert [n["metadata"]["note_id"] for n in result["raw_dataset"]] == ["20", "22"]


@pytest.mark.parametrize("failure", ["collection_failed", "challenge_required", "rate_limited"])
def test_v6_reserves_remaining_target_ocr_and_does_not_continue_failed_search(monkeypatch, tmp_path, failure, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, images = [], []
    async def collect(self, request):
        searches.append(request.xhs_search_page)
        if request.xhs_search_page == 2:
            return CollectResult(False, "synthetic", coverage={"failure": {"reason": failure}, "operations": {"search": {"status": "unknown"}}})
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大私行权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]}) for i in range(20)], coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        return {"relevant": True if note["metadata"]["note_id"] == "0" else None, "benefits": [], "tag_audience_mismatch": False}
    async def read(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大私行权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    spec = TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=19))
    state = {"task_spec": spec, "task_id": "reserve"}
    seed_v5_progress(state, "v6")
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert searches == [1, 2] and images == ["0", "1"]
    group = result["bank_collection"]["banks"][0]
    assert group["ocr_attempted"] == 2 and group["batches"][0]["unknown_ocr_attempted"] == 1
    assert group["stop_reason"] == "collection_failed" and result["collection_outcome"]["selected_count"] == 1
    assert not result["collection_outcome"]["target_complete"]


@pytest.mark.parametrize("counter", [None, True, -1, 3, "2"])
def test_v6_invalid_saved_batch_counter_fails_before_any_request(monkeypatch, tmp_path, seed_v5_progress, counter):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    state = {"task_spec": TaskSpec(intent="光大私行权益", bank_benefits=bank_scope()), "task_id": "invalid-counter"}
    seed_v5_progress(state)
    store, key = progress_location("isolated-bank-owner", "invalid-counter")
    saved = store.read_json_if_exists(key, "progress.json")
    saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v6"
    saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    saved["groups"][0]["batches"] = [{"phase": "discovery", "unknown_ocr_attempted": counter}]
    store.write_json(key, "progress.json", saved)
    path = store.task_dir(key) / "progress.json"
    original = path.read_bytes()
    async def forbidden(*args):
        raise AssertionError("无效持久计数不能重置后继续请求")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", forbidden)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    with pytest.raises(ValueError, match="批次识图计数"):
        asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert path.read_bytes() == original


def test_v6_candidate_budget_remains_cumulative_when_no_note_qualifies(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    events = []
    async def collect(self, request):
        events.append(("search", request.max_items))
        ids = range((request.xhs_search_page - 1) * 20, request.xhs_search_page * 20)
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大私行权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]}) for i in ids], coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        return {"relevant": None, "benefits": [], "tag_audience_mismatch": False}
    async def images(note, *args):
        events.append(("image", note["metadata"]["note_id"]))
        return [{"image_index": 1, "status": "recognized", "text": "光大私行权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    spec = TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=10))
    state = {"task_spec": spec, "task_id": "v6-cap"}
    seed_v5_progress(state, "v6")
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert events[:4] == [("search", 20), ("image", "0"), ("image", "1"), ("search", 20)]
    assert [value for kind, value in events if kind == "search"] == [20] * 5
    assert len([value for kind, value in events if kind == "image"]) == 20
    group = result["bank_collection"]["banks"][0]
    assert group["candidate_attempted"] == 100 and group["ocr_attempted"] == 20
    assert all(batch["unknown_ocr_attempted"] == 2 for batch in group["batches"])
    assert result["collection_outcome"]["selected_count"] == 0


def test_finished_failed_126_candidate_snapshot_does_not_restart_sources(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    spec = TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=10))
    # 仅合成已冻结的私有200额度反例，不改变公共任务参数校验或真实任务。
    spec = spec.model_copy(update={"bank_benefits": spec.bank_benefits.model_copy(update={"candidate_limit": 200})})
    state = {"task_spec": spec, "task_id": "failed-126"}
    original_binding = seed_v5_progress(state)
    store, key = progress_location("isolated-bank-owner", "failed-126")
    saved = store.read_json_if_exists(key, "progress.json")
    artifact_store = flow.ArtifactStore()
    saved["candidates"] = []
    for i in range(126):
        artifact = artifact_store.write_raw("failed-126", str(i), b"synthetic-note", uri="synthetic:note", media_type="application/json")
        saved["candidates"].append({"group": "光大银行", "metadata": {"note_id": str(i), "note_type": "normal", "image_urls": ["not-downloaded"] if i >= 58 else []},
            "status": "selected" if i == 0 else "excluded" if i < 58 else "unprocessed", "images": [], "_stage": "screen_done",
            "assessment": {"relevant": True if i == 0 else None}, "source_artifact": artifact.model_dump(mode="json")})
    saved["groups"][0].update(done=True, stop_reason="collection_failed", candidate_attempted=200,
        searched_count=178, unique_count=126, selected_count=1, ocr_attempted=1, pending=list(range(58, 126)),
        batches=[{"phase": "discovery", "attempted": 20, "returned_count": 0, "coverage": {"failure": {"reason": "collection_failed"}, "operations": {"search": {"status": "unknown"}}}}])
    store.write_json(key, "progress.json", saved)
    async def forbidden(*args):
        raise AssertionError("结束的失败快照不能重新请求来源、OCR或模型")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", forbidden)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert result["collection_outcome"]["candidate_counts"]["unfinished"] == 68
    assert result["bank_collection"]["banks"][0]["stop_reason"] == "collection_failed"
    assert result["collection_outcome"]["selected_count"] == 1 and not result["collection_outcome"]["target_complete"]
    assert store.read_json_if_exists(key, "progress.json")["binding"] == original_binding


@pytest.mark.parametrize("proved", [True, False])
def test_v6_cold_screen_done_batch_finishes_before_any_new_search(monkeypatch, tmp_path, seed_v5_progress, proved):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    state = {"task_spec": TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=1)), "task_id": "cold-batch"}
    seed_v5_progress(state)
    store, key = progress_location("isolated-bank-owner", "cold-batch")
    saved = store.read_json_if_exists(key, "progress.json")
    saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v6"
    saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    for i in range(2):
        raw = flow.ArtifactStore().write_raw("cold-batch", str(i), b"synthetic-note", uri="synthetic:note", media_type="application/json")
        saved["candidates"].append({"group": "光大银行", "metadata": {"note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]},
            "status": "unprocessed", "images": [], "_stage": "screen_done", "assessment": {"relevant": True if proved and i == 1 else None, "tag_audience_mismatch": False},
            "source_artifact": raw.model_dump(mode="json")})
    saved["groups"][0].update(candidate_attempted=20, page=2, pending=[0, 1], batches=[{
        "phase": "discovery", "attempted": 20, "returned_count": 2, "unknown_ocr_attempted": 0}])
    store.write_json(key, "progress.json", saved)
    images = []
    async def forbidden(*args):
        raise AssertionError("已筛当前批必须先处理，不能恢复后发下一页")
    async def assess(note, scope, current):
        assert current["_assessment_stage"] == "extract"
        return {"relevant": True, "benefits": [], "tag_audience_mismatch": False}
    async def read(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大私行权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert images == (["1"] if proved else ["0"])
    assert result["collection_outcome"]["selected_count"] == 1
    assert result["bank_collection"]["banks"][0]["batches"][0]["unknown_ocr_attempted"] == (0 if proved else 1)


def test_v6_ocr_exhausted_proved_pending_does_not_loop_or_block_later_text(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, images = [], []
    checkpoints = 0
    def checkpoint():
        nonlocal checkpoints
        checkpoints += 1
        assert checkpoints < 1000, "无可推进识图名额时不能忙循环"
    async def collect(self, request):
        searches.append(request.xhs_search_page)
        ids = range(20) if request.xhs_search_page == 1 else range(20, 40) if request.xhs_search_page == 2 else [40]
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大私行权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"] if i < 40 else []}) for i in ids], coverage={"pages": [{"has_more": request.xhs_search_page < 3}]})
    async def assess(note, scope, state):
        i = int(note["metadata"]["note_id"])
        return {"relevant": (True if i >= 20 else None) if state["_assessment_stage"] == "screen" else True if i == 40 else None,
            "benefits": [], "tag_audience_mismatch": False}
    async def read(note, *args):
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "光大私行权益原文"}]
    monkeypatch.setattr(flow, "execution_checkpoint", checkpoint)
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    spec = TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=1))
    state = {"task_spec": spec, "task_id": "exhausted-proved"}
    seed_v5_progress(state, "v6")
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert searches == [1, 2, 3] and len(images) == 20
    assert result["bank_collection"]["banks"][0]["ocr_attempted"] == 20
    assert result["collection_outcome"]["selected_count"] == 1
    assert result["raw_dataset"][0]["metadata"]["note_id"] == "40"


@pytest.mark.parametrize("proved", [True, False])
def test_v6_inflight_discovery_stops_before_pending_image_or_model(monkeypatch, tmp_path, seed_v5_progress, proved):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    state = {"task_spec": TaskSpec(intent="光大私行权益", bank_benefits=bank_scope(target_count=1)),
        "task_id": "inflight-pending"}
    seed_v5_progress(state)
    store, key = progress_location("isolated-bank-owner", "inflight-pending")
    saved = store.read_json_if_exists(key, "progress.json")
    saved["binding_fields"]["processing_version"] = "bank-benefits-collection-v6"
    saved["binding"] = hashlib.sha256(json.dumps(saved["binding_fields"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    for i in range(2):
        raw = flow.ArtifactStore().write_raw("inflight-pending", str(i), b"synthetic-note",
            uri="synthetic:note", media_type="application/json")
        saved["candidates"].append({"group": "光大银行", "metadata": {"note_id": str(i), "note_type": "normal",
            "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]},
            "status": "unprocessed", "images": [], "_stage": "screen_done",
            "assessment": {"relevant": True if proved else None, "tag_audience_mismatch": False},
            "source_artifact": raw.model_dump(mode="json")})
    # 新搜索已预留额度但结果未知，旧候选仍有可用早读额度；冷恢复必须先停。
    saved["groups"][0].update(candidate_attempted=40, page=3, pending=[0, 1], discovery_inflight=True,
        batches=[{"phase": "discovery", "attempted": 20, "returned_count": 2, "unknown_ocr_attempted": 0}])
    store.write_json(key, "progress.json", saved)
    calls = []
    async def forbidden(*args):
        calls.append("external")
        raise AssertionError("搜索结果未知时，不得继续请求来源、图片或模型")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", forbidden)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    group = result["bank_collection"]["banks"][0]
    assert calls == []
    assert group["stop_reason"] == "interrupted_discovery_outcome_unknown"
    assert group["candidate_attempted"] == 40 and group["ocr_attempted"] == 0
    assert group["batches"][0]["unknown_ocr_attempted"] == 0
    assert all(note["status"] == "unprocessed" for note in result["bank_collection"]["candidates"])


def test_v7_reads_batch_unknowns_beyond_two_before_next_search(monkeypatch, tmp_path):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    events = []
    async def collect(self, request):
        events.append(("search", request.xhs_search_page))
        assert request.xhs_search_page == 1, "本批能达到合成目标时不得抢先发下一批"
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户合成权益原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"]}) for i in [5, 0, 4, 1, 3, 2, 6]], coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        return {"relevant": True if state["_assessment_stage"] == "extract" or note["metadata"]["note_id"] == "6" else None,
            "benefits": [], "tag_audience_mismatch": False,
            "judgment_evidence": {"tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}
    async def read(note, *args):
        events.append(("image", int(note["metadata"]["note_id"])))
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户合成权益原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    monkeypatch.setattr(bank_benefits, "_priority", lambda note: int(note["metadata"]["note_id"]))
    spec = TaskSpec(intent="合成调度目标", bank_benefits=bank_scope(target_count=5))
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "v7-batch-target"}))
    assert events == [("search", 1), ("image", 6), ("image", 0), ("image", 1), ("image", 2), ("image", 3)]
    group = result["bank_collection"]["banks"][0]
    assert group["batches"][0]["unknown_ocr_attempted"] == 4
    assert group["candidate_attempted"] == 20 and group["ocr_attempted"] == 5
    assert result["collection_outcome"]["selected_count"] == 5


def test_v7_reserves_remaining_target_and_stops_after_failed_search(monkeypatch, tmp_path):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    searches, images, extracts = [], [], []
    failed = False
    after_failure_calls = []
    async def collect(self, request):
        nonlocal failed
        searches.append(request.xhs_search_page)
        if request.xhs_search_page == 2:
            failed = True
            return CollectResult(False, "synthetic", coverage={"failure": {"reason": "collection_failed"},
                "operations": {"search": {"status": "unknown"}}})
        return CollectResult(True, "synthetic", items=[CollectedItem(content="光大银行私行客户合成原文", metadata={
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"]}) for i in range(20)], coverage={"pages": [{"has_more": True}]})
    async def assess(note, scope, state):
        if failed:
            after_failure_calls.append("model")
        assert not failed, "来源失败后不得继续模型处理"
        if state["_assessment_stage"] == "extract":
            extracts.append(note["metadata"]["note_id"])
        return {"relevant": True if note["metadata"]["note_id"] == "0" else None,
            "benefits": [], "tag_audience_mismatch": False}
    async def read(note, *args):
        if failed:
            after_failure_calls.append("image")
        assert not failed, "来源失败后不得继续识图"
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "合成私行原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    spec = TaskSpec(intent="合成预留边界", bank_benefits=bank_scope(target_count=19))
    result = asyncio.run(bank_benefits.collect_bank_benefits({"task_spec": spec, "task_id": "v7-reserve"}))
    assert searches == [1, 2] and images == extracts == ["0", "1"]
    assert after_failure_calls == []
    group = result["bank_collection"]["banks"][0]
    assert group["batches"][0]["unknown_ocr_attempted"] == 1 and group["ocr_attempted"] == 2
    assert group["stop_reason"] == "collection_failed" and group["selected_count"] == 1


def test_v7_cold_batch_counter_above_two_is_not_reset(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    state = {"task_spec": TaskSpec(intent="合成冷恢复", bank_benefits=bank_scope(target_count=1)), "task_id": "v7-cold"}
    frozen_binding = seed_v5_progress(state, "v7")
    store, key = progress_location("isolated-bank-owner", "v7-cold")
    saved = store.read_json_if_exists(key, "progress.json")
    for i in range(4):
        raw = flow.ArtifactStore().write_raw("v7-cold", str(i), b"synthetic-note", uri="synthetic:note", media_type="application/json")
        saved["candidates"].append({"group": "光大银行", "content": "光大银行私行客户合成原文", "metadata": {
            "note_id": str(i), "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]},
            "status": "review_required" if i < 3 else "unprocessed", "images": [], "_stage": "screen_done",
            "assessment": {"relevant": None, "tag_audience_mismatch": False}, "source_artifact": raw.model_dump(mode="json")})
    saved["groups"][0].update(candidate_attempted=20, page=2, ocr_attempted=3, pending=[3], batches=[{
        "phase": "discovery", "attempted": 20, "returned_count": 4, "unknown_ocr_attempted": 3}])
    store.write_json(key, "progress.json", saved)
    images = []
    async def forbidden(*args):
        raise AssertionError("当前批剩余早读名额必须先用，不能冷恢复后抢发新搜索")
    async def assess(note, scope, current):
        assert current["_assessment_stage"] == "extract"
        return {"relevant": True, "benefits": [], "tag_audience_mismatch": False}
    async def read(note, *args):
        persisted = store.read_json_if_exists(key, "progress.json")
        assert persisted["groups"][0]["ocr_attempted"] == 4
        assert persisted["groups"][0]["batches"][0]["unknown_ocr_attempted"] == 4
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "合成私行原文"}]
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert images == ["3"] and result["collection_outcome"]["selected_count"] == 1
    persisted = store.read_json_if_exists(key, "progress.json")
    assert persisted["binding"] == frozen_binding
    assert persisted["binding_fields"]["processing_version"] == "bank-benefits-collection-v7"
    assert persisted["groups"][0]["batches"][0]["unknown_ocr_attempted"] == 4


@pytest.mark.parametrize("counter", [None, True, -1, 21, "3", "missing"])
def test_v7_invalid_saved_counter_rejects_before_requests(monkeypatch, tmp_path, seed_v5_progress, counter):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    state = {"task_spec": TaskSpec(intent="合成无效计数", bank_benefits=bank_scope()), "task_id": "v7-invalid-counter"}
    seed_v5_progress(state, "v7")
    store, key = progress_location("isolated-bank-owner", state["task_id"])
    saved = store.read_json_if_exists(key, "progress.json")
    batch = {"phase": "discovery", "unknown_ocr_attempted": counter}
    if counter == "missing":
        del batch["unknown_ocr_attempted"]
    saved["groups"][0]["batches"] = [batch]
    store.write_json(key, "progress.json", saved)
    path = store.task_dir(key) / "progress.json"
    before = path.read_bytes()
    async def forbidden(*args):
        raise AssertionError("无效冻结计数不得进入外调")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", forbidden)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    with pytest.raises(ValueError, match="批次识图计数"):
        asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert path.read_bytes() == before


def test_v7_frozen_sixty_ocr_cap_still_closes_without_loop(monkeypatch, tmp_path, seed_v5_progress):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    spec = TaskSpec(intent="合成冻结60调度", bank_benefits=bank_scope(target_count=2))
    # 仅构造调度器已冻结的60额度，不改变公共Pydantic的Literal20门。
    spec = spec.model_copy(update={"bank_benefits": spec.bank_benefits.model_copy(update={"ocr_limit": 60})})
    state = {"task_spec": spec, "task_id": "v7-sixty"}
    seed_v5_progress(state, "v7")
    store, key = progress_location("isolated-bank-owner", state["task_id"])
    saved = store.read_json_if_exists(key, "progress.json")
    for i in range(3):
        raw = flow.ArtifactStore().write_raw(state["task_id"], str(i), b"synthetic-note", uri="synthetic:note", media_type="application/json")
        saved["candidates"].append({"group": "光大银行", "metadata": {"note_id": str(i), "note_type": "normal",
            "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]},
            "status": "unprocessed", "images": [], "_stage": "screen_done", "assessment": {"relevant": None, "tag_audience_mismatch": False},
            "source_artifact": raw.model_dump(mode="json")})
    saved["groups"][0].update(candidate_attempted=100, page=6, ocr_attempted=59, pending=[0, 1, 2], batches=[{
        "phase": "discovery", "attempted": 20, "returned_count": 3, "unknown_ocr_attempted": 59}])
    store.write_json(key, "progress.json", saved)
    images, checkpoints = [], []
    def checkpoint():
        checkpoints.append(1)
        assert len(checkpoints) < 100, "总额度耗尽后不能忙循环"
    async def forbidden(*args):
        raise AssertionError("已达候选上限后不能发新搜索")
    async def assess(*args):
        return {"relevant": None, "benefits": [], "tag_audience_mismatch": False}
    async def read(note, *args):
        assert store.read_json_if_exists(key, "progress.json")["groups"][0]["ocr_attempted"] == 60
        images.append(note["metadata"]["note_id"])
        return [{"image_index": 1, "status": "recognized", "text": "合成私行原文"}]
    monkeypatch.setattr(flow, "execution_checkpoint", checkpoint)
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    group = result["bank_collection"]["banks"][0]
    assert images == ["0"] and group["ocr_attempted"] == 60
    assert group["batches"][0]["unknown_ocr_attempted"] == 59
    assert group["stop_reason"] == "ocr_limit" and group["done"] is True


@pytest.mark.parametrize("include_images,raw_only", [(True, False), (False, False), (True, True)])
def test_v7_inflight_pending_stops_in_every_new_mode(monkeypatch, tmp_path, seed_v5_progress, include_images, raw_only):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    state = {"task_spec": TaskSpec(intent="合成未知检索", bank_benefits=bank_scope(target_count=1,
        include_images=include_images, raw_only=raw_only)), "task_id": "v7-inflight"}
    seed_v5_progress(state, "v7")
    store, key = progress_location("isolated-bank-owner", state["task_id"])
    saved = store.read_json_if_exists(key, "progress.json")
    raw = flow.ArtifactStore().write_raw(state["task_id"], "0", b"synthetic-note", uri="synthetic:note", media_type="application/json")
    saved["candidates"].append({"group": "光大银行", "metadata": {"note_id": "0", "note_type": "normal",
        "publish_time": "2026-09-01T00:00:00+08:00", "image_urls": ["synthetic-image"]},
        "status": "unprocessed", "images": [], "_stage": "screen_done", "assessment": {"relevant": True, "tag_audience_mismatch": False},
        "source_artifact": raw.model_dump(mode="json")})
    saved["groups"][0].update(candidate_attempted=40, page=3, pending=[0], discovery_inflight=True, batches=[{
        "phase": "discovery", "attempted": 20, "unknown_ocr_attempted": 0}])
    store.write_json(key, "progress.json", saved)
    calls = []
    async def forbidden(*args):
        calls.append("external")
        raise AssertionError("未知检索不能借旧pending进入图、模型或来源")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(bank_benefits, "assess_note", forbidden)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    group = result["bank_collection"]["banks"][0]
    assert calls == [] and group["stop_reason"] == "interrupted_discovery_outcome_unknown"
    assert group["ocr_attempted"] == 0 and group["batches"][0]["unknown_ocr_attempted"] == 0
    assert result["bank_collection"]["candidates"][0]["status"] == "unprocessed"
