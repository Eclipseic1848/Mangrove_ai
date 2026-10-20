"""小批推进、阶段预算和部分证据均使用隔离合成来源。"""
import asyncio
import json

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.conductor import evidence_collection as flow
from src.conductor.task_spec import TaskSpec


def setup_flow(monkeypatch, tmp_path, *, count=8, target=1, images=False):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    async def collect(self, request):
        return CollectResult(True, "synthetic", items=[CollectedItem(
            content="会员权益原文", metadata={"note_id": str(i), "note_type": "normal",
                "image_urls": ["first", "second"] if images else []}) for i in range(count)],
            coverage={"pages": [{"has_more": False}]})
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    spec = TaskSpec(intent="采集会员权益", evidence_collection={"source_evidence": "小红书",
        "queries": [{"name": "会员", "keyword": "会员权益"}], "fields": ["name"], "target_count": target})
    return spec


def run_flow(spec, assess, images=None, *, task="isolated"):
    return asyncio.run(flow.collect_evidence({"task_id": task, "task_spec": spec},
        scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
        assess=assess, exclude=flow.exclude_topic, read_images=images))


def test_first_valid_note_finishes_before_whole_batch_screening(monkeypatch, tmp_path):
    spec = setup_flow(monkeypatch, tmp_path)
    calls = []
    async def assess(note, scope, state):
        calls.append((state["_assessment_stage"], note["metadata"]["note_id"]))
        return {"relevant": True, "records": []}
    result = run_flow(spec, assess)
    first_extract = next(i for i, item in enumerate(calls) if item[0] == "extract")
    assert first_extract <= 2
    assert result["collection_outcome"]["selected_count"] == 1
    assert len([item for item in calls if item[0] == "screen"]) <= 2


def test_screen_parallelism_is_two_and_context_is_preserved(monkeypatch, tmp_path):
    from contextvars import ContextVar
    marker = ContextVar("isolated_scope", default="missing")
    spec = setup_flow(monkeypatch, tmp_path, target=4)
    active = maximum = 0
    async def assess(note, scope, state):
        nonlocal active, maximum
        assert marker.get() == "owner-model-binding"
        if state["_assessment_stage"] == "screen":
            active += 1; maximum = max(maximum, active)
            try: await asyncio.sleep(0.01)
            finally: active -= 1
        return {"relevant": True, "records": []}
    token = marker.set("owner-model-binding")
    try: result = run_flow(spec, assess)
    finally: marker.reset(token)
    assert maximum == 2
    assert active == 0 and result["collection_outcome"]["selected_count"] == 4


def test_failed_image_keeps_proved_fields_without_counting_complete(monkeypatch, tmp_path):
    spec = setup_flow(monkeypatch, tmp_path, count=1, images=True)
    calls = []
    async def assess(note, scope, state):
        calls.append(state["_assessment_stage"])
        return {"relevant": True, "records": [{"name": "会员权益", "evidence": {
            "name": {"source_id": "note", "quote": "会员权益原文"}}}]}
    async def images(*args):
        return [{"image_index": 1, "status": "recognized", "text": "会员权益原文"},
                {"image_index": 2, "status": "failed"}]
    result = run_flow(spec, assess, images)
    note = result["evidence_collection"]["candidates"][0]
    assert calls == ["screen", "extract"]
    assert note["status"] == "review_required" and note["reason"] == "image_incomplete"
    assert note["assessment"]["records"][0]["name"] == "会员权益"
    assert result["collection_outcome"]["selected_count"] == 0
    assert not result["collection_outcome"]["ready_for_review"]


def test_screen_deadline_marks_unknown_and_does_not_replay(monkeypatch, tmp_path):
    spec = setup_flow(monkeypatch, tmp_path, count=2)
    monkeypatch.setattr(flow, "STEP_TIMEOUT_SECONDS", {"discovery": 180, "screen": .01, "extract": 180,
        "images": 120, "comments": 60}, raising=False)
    calls = []; closed = []
    async def assess(note, scope, state):
        identity = note["metadata"]["note_id"]; calls.append(identity)
        try: await asyncio.Event().wait()
        finally: closed.append(identity)
    result = run_flow(spec, assess)
    notes = result["evidence_collection"]["candidates"]
    assert sorted(calls) == sorted(closed) == ["0", "1"]
    assert all(n["status"] == "review_required" and n["reason"] == "screen_outcome_unknown" for n in notes)
    assert result["evidence_collection"]["groups"][0]["stop_reason"] != "time_budget"


def test_model_failure_cancels_other_screen_without_extract(monkeypatch, tmp_path):
    spec = setup_flow(monkeypatch, tmp_path, count=2)
    failed = False; second_started = asyncio.Event(); closed = []
    def verify():
        if failed: raise ValueError("所选模型已失效")
    monkeypatch.setattr(flow, "verify_bound_model", verify)
    async def assess(note, scope, state):
        nonlocal failed
        assert state["_assessment_stage"] == "screen"
        identity = note["metadata"]["note_id"]
        if identity == "0":
            await second_started.wait(); failed = True; raise ValueError("所选模型已失效")
        second_started.set()
        try: await asyncio.Event().wait()
        finally: closed.append(identity)
    with pytest.raises(ValueError, match="模型已失效"):
        run_flow(spec, assess)
    assert closed == ["1"]


def test_live_snapshot_is_saved_and_announced_before_final_result(monkeypatch, tmp_path):
    spec = setup_flow(monkeypatch, tmp_path, target=3)
    monkeypatch.setattr(flow, "execution_owner", lambda: "isolated-owner")
    events = []
    monkeypatch.setattr(flow, "emit_progress", lambda *args, **kwargs: events.append(kwargs))
    async def assess(note, scope, state): return {"relevant": True, "records": []}
    run_flow(spec, assess)
    published = [e for e in events if e.get("files")]
    assert published and any(e["candidate_counts"]["selected"] == 1 for e in published)
    path = tmp_path / "artifacts" / "isolated" / "data-in-progress.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    assert snapshot["in_progress"] is True
    assert snapshot["snapshot_revision"] > 0
    assert all("_access_handle" not in n and "_stage" not in n for n in snapshot["candidates"])


def test_partial_comment_request_keeps_fields_and_never_counts_complete(monkeypatch, tmp_path):
    from src.collectors import source_access
    spec = setup_flow(monkeypatch, tmp_path, count=1).model_copy(update={"include_comments": True})
    monkeypatch.setattr(source_access, "open_access", lambda *args: "https://example.com/note")
    async def collect(self, request):
        return CollectResult(True, "synthetic", items=[CollectedItem(content="会员权益原文", metadata={
            "note_id": "0", "note_type": "normal", "comment_coverage": {"truncated": True}})],
            coverage={"pages": [{"has_more": False}]})
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    async def assess(note, scope, state):
        return {"relevant": True, "records": [{"name": "会员权益", "evidence": {
            "name": {"source_id": "note", "quote": "会员权益原文"}}}]}
    result = run_flow(spec, assess)
    note = result["evidence_collection"]["candidates"][0]
    assert note["status"] == "review_required" and note["reason"] == "comments_incomplete"
    assert note["assessment"]["records"][0]["name"] == "会员权益"
    assert result["collection_outcome"]["selected_count"] == 0


def test_global_budget_prevents_even_immediate_discovery(monkeypatch, tmp_path):
    from types import SimpleNamespace
    spec = setup_flow(monkeypatch, tmp_path, count=1)
    calls = []
    clock = iter([0, 1801, 1801, 1801, 1801, 1801])
    monkeypatch.setattr(flow, "time", SimpleNamespace(monotonic=lambda: next(clock, 1801)))
    async def collect(self, request): calls.append("discovery"); return CollectResult(True, "synthetic")
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    async def assess(*args): raise AssertionError("预算用尽不能请求模型")
    result = run_flow(spec, assess)
    assert calls == []
    assert result["evidence_collection"]["groups"][0]["stop_reason"] == "time_budget"


def test_cancelled_batch_closes_both_calls_before_return(monkeypatch, tmp_path):
    spec = setup_flow(monkeypatch, tmp_path, count=2)
    started = []; closed = []; both = asyncio.Event()
    async def assess(note, scope, state):
        identity = note["metadata"]["note_id"]; started.append(identity)
        if len(started) == 2: both.set()
        try: await asyncio.Event().wait()
        finally: closed.append(identity)
    async def scenario():
        task = asyncio.create_task(flow.collect_evidence({"task_id": "cancel", "task_spec": spec},
            scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
            assess=assess, exclude=flow.exclude_topic, read_images=None))
        await asyncio.wait_for(both.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert sorted(closed) == ["0", "1"]
    asyncio.run(scenario())
