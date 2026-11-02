"""整批详情中断不能丢掉已返回资料；只运行合成子进程。"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector
from src.config.user_ctx import user_overrides_context
from src.conductor.task_spec import TaskSpec
from tests.test_social_collection_isolation import crawler


@pytest.fixture
def partial_crawler(crawler, monkeypatch):
    (crawler / "media_platform/xhs/client.py").write_text('''
class PlatformAccessError(Exception):
    def __init__(self, http_status):
        self.http_status, self.platform_code = http_status, None
''', encoding="utf-8")
    (crawler / "media_platform/xhs/core.py").write_text('''
import asyncio, json, os, sys
from pathlib import Path
from .client import PlatformAccessError
class NoteNotFoundError(Exception):
    pass

class XiaoHongShuClient:
    async def _request_once(self, method, url, **kwargs):
        mode = os.environ.get("SYNTHETIC_BATCH", "challenge")
        identity = kwargs.get("identity", "search")
        path = Path("attempts.json")
        calls = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        path.write_text(json.dumps([*calls, identity]), encoding="utf-8")
        if identity == "note-0" and mode == "missing_note":
            raise NoteNotFoundError()
        if identity == "note-1" and mode not in {"success", "missing_note"}:
            if mode == "challenge":
                raise PlatformAccessError(461)
            if mode == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("合成连接结果未知")
        return {}
    async def get_note_by_keyword(self, **kwargs):
        await self._request_once("POST", "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes")
        return {"has_more": False, "items": [
            {"id": "query", "model_type": "rec_query"},
            *[{"id": "note-" + str(i), "model_type": "note", "xsec_token": "private-synthetic-token",
               "note_card": {"type": "normal", "display_title": "候选" + str(i)}} for i in (0, 0, 1, 2)]]}
    async def get_note_by_id(self, identity):
        await self._request_once("POST", "https://edith.xiaohongshu.com/api/sns/web/v1/feed", identity=identity)
        return {"note_id": identity, "title": "完整详情", "desc": "会员可享合成服务", "type": "normal",
                "source_keyword": "服务规则", "note_url": "https://www.xiaohongshu.com/explore/" + identity}

class Store:
    async def update_xhs_note(self, note):
        root = Path(sys.argv[sys.argv.index("--save_data_path") + 1]) / "xhs/json"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "notes.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        path.write_text(json.dumps([*rows, note]), encoding="utf-8")
xhs_store = Store()

class XiaoHongShuCrawler:
    def __init__(self):
        self.xhs_client = XiaoHongShuClient()
    async def get_note_detail_async_task(self, note_id, semaphore, **kwargs):
        async with semaphore:
            try:
                return await self.xhs_client.get_note_by_id(note_id)
            except NoteNotFoundError:
                return None
    async def search(self):
        result = await self.xhs_client.get_note_by_keyword(keyword="服务规则", page=1)
        semaphore = asyncio.Semaphore(1)
        # 复现现役上游先 gather 全批、随后统一写入的调用顺序。
        notes = await asyncio.gather(*[self.get_note_detail_async_task(
            item["id"], semaphore) for item in result["items"]])
        for note in notes:
            if note:
                await xhs_store.update_xhs_note(note)
''', encoding="utf-8")
    (crawler / "main.py").write_text('''
import asyncio
from media_platform.xhs.core import XiaoHongShuCrawler
asyncio.run(XiaoHongShuCrawler().search())
''', encoding="utf-8")
    return crawler


def collect_batch():
    with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie"}):
        return asyncio.run(SocialMediaCollector().collect(TaskSpec(
            intent="合成采集", platforms=["小红书"], keywords=["服务规则"], max_items=3)))


def test_challenge_keeps_search_candidates_and_completed_detail(partial_crawler):
    result = collect_batch()
    assert not result.success
    assert result.coverage["failure"]["rejection_confirmed"] is True
    assert result.coverage["failure"]["reason"] == "challenge_required"
    assert json.loads((partial_crawler / "attempts.json").read_text(encoding="utf-8")) == ["search", "note-0", "note-1"]
    assert len(result.items) == 3
    assert result.coverage["pages"][0]["accepted_count"] == 3
    notes = {note.metadata["note_id"]: note for note in result.items}
    assert notes["note-0"].content == "会员可享合成服务"
    assert notes["note-0"].metadata["detail_status"] == "complete"
    assert notes["note-1"].content == notes["note-2"].content == ""
    assert notes["note-1"].metadata["detail_status"] == "unavailable"
    assert result.coverage["operations"]["search"]["status"] == "succeeded"
    assert result.coverage["operations"]["detail"]["status"] == "failed"
    assert "private-synthetic-token" not in json.dumps([note.to_dict() for note in result.items])
    assert result.coverage["partial"] is True


def test_success_does_not_duplicate_early_saved_details(partial_crawler, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_BATCH", "success")
    result = collect_batch()
    assert result.success
    assert [note.metadata["note_id"] for note in result.items] == ["note-0", "note-1", "note-2"]
    assert all(note.content == "会员可享合成服务" for note in result.items)


def test_missing_note_does_not_block_other_healthy_notes(partial_crawler, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_BATCH", "missing_note")
    result = collect_batch()
    assert result.success
    assert [note.metadata["note_id"] for note in result.items] == ["note-1", "note-2"]


def test_unknown_error_keeps_observed_data_without_claiming_auth_rejection(partial_crawler, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_BATCH", "unknown")
    result = collect_batch()
    assert not result.success
    assert result.coverage["failure"].get("rejection_confirmed") is not True
    assert json.loads((partial_crawler / "attempts.json").read_text(encoding="utf-8")) == ["search", "note-0", "note-1"]
    assert len(result.items) == 3
    assert result.items[0].content == "会员可享合成服务"


def test_cancelled_request_stops_remaining_queued_requests(partial_crawler, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_BATCH", "cancel")
    result = collect_batch()
    assert not result.success
    assert result.coverage["failure"].get("rejection_confirmed") is not True
    assert json.loads((partial_crawler / "attempts.json").read_text(encoding="utf-8")) == ["search", "note-0", "note-1"]
    assert result.items[0].content == "会员可享合成服务"


def test_evidence_failure_preserves_items_but_never_processes_or_delivers_them(tmp_path, monkeypatch):
    from src.conductor import evidence_collection as flow
    from src.conductor.collection_recovery import authentication_refused
    from src.conductor.bank_benefits_export import export_snapshot
    from openpyxl import load_workbook
    from tests.test_evidence_collection_flow import spec_for

    spec = spec_for("服务规则", target_count=3, initial_candidates=3, candidate_limit=3, include_images=False)
    partial = CollectResult(False, "mediacrawler", message="详情读取受限，已保留资料", items=[
        CollectedItem(title="候选", content="已有正文" if i == 0 else "", metadata={
            "note_id": str(i), "note_type": "normal", "detail_status": "complete" if i == 0 else "unavailable"})
        for i in range(3)], coverage={"partial": True, "failure": {
            "reason": "challenge_required", "rejection_confirmed": True, "diagnostic_hint": {"http_status": 461}},
            "operations": {"search": {"status": "succeeded", "next_action": "none"},
                           "detail": {"status": "failed", "next_action": "complete_platform_challenge"}}})
    collector = AsyncMock(return_value=partial)
    forbidden = AsyncMock(side_effect=AssertionError("来源拒绝后不能继续调用模型或图片"))
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collector)
    monkeypatch.setattr(flow, "execution_owner", lambda: None)
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path))
    result = asyncio.run(flow.collect_evidence({"task_spec": spec, "task_id": "partial-batch"},
        scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
        assess=forbidden, exclude=lambda *a, **k: None, read_images=forbidden))
    snapshot = result["evidence_collection"]
    assert len(snapshot["candidates"]) == 3
    assert result["collection_outcome"]["candidate_counts"]["found"] == 3
    assert result["collection_outcome"]["selected_count"] == 0
    assert not result["collection_outcome"]["ready_for_review"]
    assert not authentication_refused(snapshot["groups"][0])
    assert collector.await_count == 1 and forbidden.await_count == 0
    outputs = export_snapshot({**result, "task_id": "partial-batch", "task_spec": spec})["outputs"]
    from pathlib import Path
    exported = json.loads(Path(outputs["json"]).read_text(encoding="utf-8"))
    assert exported["outcome"]["candidate_counts"]["found"] == 3
    assert not exported["outcome"]["ready_for_review"]
    assert len(exported["candidates"]) == 3
    workbook = load_workbook(outputs["xlsx"], read_only=True)
    try:
        assert any(sheet.max_row >= 4 for sheet in workbook.worksheets)
    finally:
        workbook.close()


def test_historical_lost_results_never_refund_search_budget():
    from src.conductor.collection_recovery import authentication_refused
    group = {"stop_reason": "collection_failed", "done": True, "batches": [{
        "phase": "discovery", "attempted": 20, "returned_count": 0, "coverage": {
            "failure": {"reason": "challenge_required", "rejection_confirmed": True},
            "runtime": {"search_list_received": True, "search_item_count": 22, "note_detail_count": 1}}}]}
    assert not authentication_refused(group)


def test_unknown_source_failure_stops_subsequent_queries(tmp_path, monkeypatch):
    from src.conductor import evidence_collection as flow
    from tests.test_evidence_collection_flow import spec_for
    spec = spec_for("服务规则", queries=[{"name": "甲", "keyword": "甲"}, {"name": "乙", "keyword": "乙"}])
    collector = AsyncMock(return_value=CollectResult(False, "mediacrawler", coverage={
        "failure": {"reason": "collection_failed"}}))
    forbidden = AsyncMock(side_effect=AssertionError("未知来源失败后不能继续模型或图片"))
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collector)
    monkeypatch.setattr(flow, "execution_owner", lambda: None)
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path))
    asyncio.run(flow.collect_evidence({"task_spec": spec, "task_id": "unknown-batch"},
        scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
        assess=forbidden, exclude=lambda *a, **k: None, read_images=forbidden))
    assert collector.await_count == 1 and forbidden.await_count == 0
