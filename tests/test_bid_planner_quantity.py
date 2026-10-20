"""最新公告的明确目标条数不能被规划模型的候选数量覆盖。"""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.collectors.base import CollectResult
from src.conductor.nodes import planner


REAL_PROMPT = ("每周一三五 9:30 搜集最新发布的3条医疗设备正式招标公告，按本次已检索候选中核实的发布日期倒序选取。"
    "逐条列明发布日期、投标截止时间、是否已截止及原文链接，并整理成标讯报告、保留原始公告JSON。"
    "不另设近7天或近30天的硬时间窗；已截止公告可以保留，必须如实标明截止状态，不得当作当前可投标机会。"
    "汇总页、新闻和非正式公告不占条数，不足3条如实说明，不得猜测缺失字段。")


@pytest.fixture
def plan(monkeypatch):
    draft = {"intent": "最新医疗设备公告", "keywords": ["医疗设备 招标公告"], "data_type": "bid",
             "analysis_type": "summary", "max_items": 12, "outputs": ["report_md", "json"]}
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    monkeypatch.setattr(planner, "frozen_task_context", lambda _state: None)
    monkeypatch.setattr("src.api.auth.get_store", lambda: None)
    monkeypatch.setattr(planner.settings, "collector_max_items", 30)
    async def invoke(text, **changes):
        value = {**copy.deepcopy(draft), **changes}
        monkeypatch.setattr(planner, "_plan", AsyncMock(return_value=(value, None)))
        return await planner.planner_node({"user_input": text, "provider": "bound", "model": "deepseek-flash"})
    return invoke


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [REAL_PROMPT,
    "采集3条最新发布的医疗设备招标公告",
    "获取最新的3条医疗设备公告，候选最多查看15条",
    "查看15条候选公告，再选出最新3条医疗设备正式公告",
    "搜集最新3条正式公告，候选数量最多20条公告",
    "搜集最新3条正式公告并获取3条公告，不增加总数量"])
async def test_explicit_notice_target_overrides_model_candidate_count(plan, text):
    result = await plan(text)
    assert result["needs_clarification"] is False
    assert result["task_spec"].max_items == 3


@pytest.mark.asyncio
async def test_real_schedule_three_target_keeps_configured_candidate_budget(plan, monkeypatch, tmp_path):
    from src.conductor import bid_collection
    result = await plan(REAL_PROMPT)
    search = AsyncMock(return_value=CollectResult(False, "search"))
    monkeypatch.setattr(bid_collection, "get_registry", lambda: {"search": SimpleNamespace(collect_bid_candidates=search)})
    monkeypatch.setattr(bid_collection.settings, "data_prep_artifact_root", str(tmp_path))
    await bid_collection.collect_latest_bid_notices({"task_id": "synthetic-three-notices", "user_input": REAL_PROMPT,
        "task_spec": result["task_spec"], "provider": "bound", "model": "deepseek-flash"})
    assert result["task_spec"].max_items == 3
    assert search.call_args.args[0].max_items == 30
    assert search.call_args.args[0].project_collection is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "采集最新3条医疗设备公告，另获取4条医疗设备公告",
    "搜集最新0条医疗设备公告",
    "搜集最新31条医疗设备公告",
])
async def test_conflicting_or_unrepresentable_notice_target_requires_clarification(plan, text):
    result = await plan(text)
    assert result["needs_clarification"] and "task_spec" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("text,changes", [
    ("查找最新医疗设备公告，最多查看15条候选公告", {}),
    ("采集3条医疗设备公告", {"intent": "医疗设备公告"}),
    ("采集最新3条医疗设备公告", {"data_type": "article"}),
    ("不要求最新，采集3条医疗设备公告", {"intent": "医疗设备公告"}),
    ("读取 https://hospital.example/notice 最新3条医疗设备公告", {"urls": ["https://hospital.example/notice"]}),
])
async def test_other_profiles_urls_and_missing_explicit_target_keep_old_behavior(plan, text, changes):
    result = await plan(text, **changes)
    assert result["needs_clarification"] is False and result["task_spec"].max_items == 12
