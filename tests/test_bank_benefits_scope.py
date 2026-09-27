"""场景冻结只补默认值，不覆盖用户范围。"""
import asyncio

import pytest

from src.conductor.nodes import planner
from src.conductor.task_spec import AnalysisType


def plan(monkeypatch, text, scope):
    async def fake_plan(*args):
        return {"intent": text, "platforms": ["小红书"], "max_items": 30,
                "bank_benefits": scope}, None

    monkeypatch.setattr(planner, "_plan", fake_plan)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    return asyncio.run(planner.planner_node({"user_input": text}))


def test_bank_defaults_are_per_bank_and_skip_generic_analysis(monkeypatch):
    out = plan(monkeypatch, "在小红书找中信和招行的私银权益", {
        "source_evidence": "小红书",
        "banks": [{"name": "中信", "keyword": "中信私银权益"},
                  {"name": "招行", "keyword": "招行私银权益"}],
        "audience": "私银",
    })
    spec = out["task_spec"]
    assert spec.bank_benefits.target_count == 5
    assert spec.bank_benefits.candidate_limit == 100
    assert spec.bank_benefits.ocr_limit == 20
    assert spec.max_items == 10
    assert spec.analysis_type == AnalysisType.NONE
    assert spec.include_comments and spec.xhs_note_type == "image"
    assert spec.xhs_sort == "general"
    assert not out["needs_clarification"]


@pytest.mark.parametrize("text,scope", [
    ("采集私银相关资料", None),
    ("采集银行权益", {"source_evidence": "小红书", "banks": [{"name": "中信", "keyword": "中信权益"}]}),
])
def test_source_not_requested_does_not_enable_scene(monkeypatch, text, scope):
    out = plan(monkeypatch, text, scope)
    if scope:
        assert out["needs_clarification"] and "task_spec" not in out
    else:
        assert out["task_spec"].bank_benefits is None


def test_explicit_history_raw_and_credit_card_override_defaults(monkeypatch):
    out = plan(monkeypatch, "小红书中信2024年信用卡权益，只取原始内容", {
        "source_evidence": "小红书", "banks": [{"name": "中信", "keyword": "中信信用卡权益"}],
        "publication_from": "2024-01-01", "publication_to": "2024-12-31",
        "include_credit_card": True, "raw_only": True,
    })
    scope = out["task_spec"].bank_benefits
    assert str(scope.publication_from) == "2024-01-01"
    assert str(scope.publication_to) == "2024-12-31"
    assert scope.raw_only and scope.include_credit_card
    assert scope.audience is None


@pytest.mark.parametrize("include_comments", [True, False])
def test_model_nested_collection_options_preserve_bank_query(monkeypatch, include_comments):
    out = plan(monkeypatch, "采集小红书上的中信银行权益", {
        "source_evidence": "小红书", "banks": [{"name": "中信银行", "keyword": "中信银行权益"}],
        "xhs_sort": "general", "xhs_note_type": "image", "include_comments": include_comments,
        "comment_limit": 20, "data_type": "post", "analysis_type": "none", "outputs": ["json"],
    })
    assert not out["needs_clarification"]
    spec = out["task_spec"]
    assert spec.include_comments is include_comments and spec.comment_limit == 20
    assert spec.xhs_sort == "general" and spec.xhs_note_type == "image"
    assert spec.bank_benefits.target_count == 5 and spec.bank_benefits.candidate_limit == 100


def test_conflicting_nested_options_and_unknown_bank_fields_are_rejected():
    from src.conductor.task_spec import TaskSpec
    scene = {"source_evidence": "小红书", "banks": [{"name": "中信", "keyword": "中信权益"}], "xhs_sort": "general"}
    with pytest.raises(ValueError):
        TaskSpec.from_draft({"bank_benefits": scene, "xhs_sort": "time_descending"})
    with pytest.raises(ValueError):
        TaskSpec.from_draft({"bank_benefits": {**scene, "unapproved_budget": 500}})
