"""草稿转换必须保留明确的执行范围。"""
from src.conductor.task_spec import TaskSpec


def test_draft_preserves_requested_search_page():
    spec = TaskSpec.from_draft({"xhs_search_page": 2}, "采集第二页")
    assert spec.xhs_search_page == 2

import pytest


@pytest.mark.parametrize("draft", [
    {"max_items": 0}, {"max_items": 2001}, {"max_items": True},
    {"max_items": 1.5}, {"max_items": "abc"},
    {"data_type": "unknown"}, {"analysis_type": "unknown"},
    {"login_strategy": "unknown"}, {"outputs": ["json", "unknown"]},
    {"outputs": []}, {"outputs": [42]}, {"keywords": [{"text": "x"}]},
    {"include_comments": "false"}, {"comment_limit": 0},
    {"xhs_search_page": True}, {"unexpected_scope": "all"},
])
def test_explicit_invalid_constraints_are_not_replaced(draft):
    with pytest.raises(ValueError):
        TaskSpec.from_draft(draft, "采集公开内容")


def test_missing_defaults_and_planner_reasoning_remain_compatible():
    spec = TaskSpec.from_draft({"max_items": None, "reasoning": "采用默认"}, "公开内容")
    assert spec.max_items == 50
    assert spec.outputs == ["report_md"]

@pytest.mark.parametrize("page", [1, 2, 50, 100])
def test_page_boundaries(page):
    assert TaskSpec.from_draft({"xhs_search_page": page}).xhs_search_page == page


@pytest.mark.parametrize("value", [1, 50, 2000, "20"])
def test_valid_item_limits(value):
    assert TaskSpec.from_draft({"max_items": value}).max_items == int(value)


@pytest.mark.parametrize("draft", [
    {"xhs_search_page": 0}, {"xhs_search_page": 101}, {"xhs_search_page": 1.1},
    {"comment_limit": 201}, {"comment_limit": True}, {"comment_limit": 2.5},
    {"keywords": 4}, {"keywords": [""]}, {"outputs": "invalid"},
    {"intent": 123}, [], "text",
])
def test_invalid_shapes_and_boundaries_are_rejected(draft):
    with pytest.raises(ValueError):
        TaskSpec.from_draft(draft)


def test_text_enum_normalization_and_output_order():
    spec = TaskSpec.from_draft({"data_type": " POST ", "outputs": ["json", "report_md", "json"]})
    assert spec.data_type == "post"
    assert spec.outputs == ["json", "report_md"]


def test_disabled_comments_keep_explicit_limit():
    spec = TaskSpec.from_draft({"include_comments": False, "comment_limit": 5})
    assert not spec.include_comments and spec.comment_limit == 5

@pytest.mark.parametrize("field", ["intent", "time_range", "analysis_instruction", "db_target", "email_to", "schedule"])
@pytest.mark.parametrize("value", [False, 0, []])
def test_false_values_do_not_bypass_text_validation(field, value):
    with pytest.raises(ValueError):
        TaskSpec.from_draft({field: value}, "用户要求")


def test_planner_diagnostic_does_not_expose_draft_values():
    from src.conductor.nodes.planner import _validation_hint
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as caught:
        TaskSpec(intent="x", comment_limit=0)
    assert "comment_limit" in _validation_hint(caught.value)
    assert _validation_hint(ValueError("SECRET_TOKEN")) == "参数类型或取值不符合任务契约"
    assert _validation_hint(ValueError("任务草稿包含未支持字段")) == "任务草稿包含未支持字段"
    from src.conductor.task_spec import EvidenceCollectionScope
    with pytest.raises(ValidationError) as caught:
        EvidenceCollectionScope(source_evidence="小红书", queries=[{"name":"x","keyword":"x"}], raw_only=True, SECRET_TOKEN="hidden")
    assert "SECRET_TOKEN" not in _validation_hint(caught.value)
    assert "hidden" not in _validation_hint(caught.value)


@pytest.mark.parametrize("field,value", [("include_comments", False), ("comment_limit", 5),
    ("data_type", "post"), ("analysis_type", "none"), ("outputs", ["json"]),
    ("xhs_sort", "general"), ("xhs_note_type", "image")])
def test_evidence_scope_reuses_known_top_level_normalization(field, value):
    scope = {"source_evidence": "小红书", "queries": [{"name": "主题", "keyword": "主题"}],
             "raw_only": True, field: value}
    spec = TaskSpec.from_draft({"evidence_collection": scope})
    assert getattr(spec, field) == value
    assert scope[field] == value


def test_evidence_scope_conflict_and_unknown_fields_remain_rejected():
    scope = {"source_evidence": "小红书", "queries": [{"name": "主题", "keyword": "主题"}],
             "raw_only": True, "include_comments": False}
    with pytest.raises(ValueError):
        TaskSpec.from_draft({"evidence_collection": scope, "include_comments": True})
    with pytest.raises(ValueError):
        TaskSpec.from_draft({"evidence_collection": {**scope, "unsupported": 1}})
