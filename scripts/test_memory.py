#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""记忆与技能加载单元测试。

运行：python scripts/test_memory.py
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory import loader
from src.conductor.task_spec import AnalysisType, DataType, TaskSpec


def _tmp_skills_dir() -> Path:
    """把 loader.SKILLS_DIR 重定向到临时目录，隔离测试、不依赖真实 skills/ 的文件内容。"""
    d = Path(tempfile.mkdtemp(prefix="mg_skills_"))
    loader.SKILLS_DIR = d
    return d


def _write_skill(d: Path, filename: str, content: str) -> None:
    (d / filename).write_text(content, encoding="utf-8")


def test_load_skills_parses_frontmatter():
    d = _tmp_skills_dir()
    _write_skill(d, "demo.md", "---\ntitle: 示例技能\ninject: analyze\ntrigger:\n  always: true\n---\n正文内容")
    skills = loader.load_skills()
    assert "demo" in skills
    assert skills["demo"]["title"] == "示例技能"
    assert skills["demo"]["inject"] == "analyze"
    assert skills["demo"]["body"] == "正文内容"


def test_load_skills_skips_readme_silently():
    d = _tmp_skills_dir()
    _write_skill(d, "README.md", "# 说明文档，没有 frontmatter")
    assert loader.load_skills() == {}


def test_load_skills_skips_no_frontmatter_with_info_log():
    d = _tmp_skills_dir()
    _write_skill(d, "draft.md", "# 忘了加 frontmatter 的技能草稿")
    with patch.object(loader.logger, "info") as mock_info:
        skills = loader.load_skills()
    assert "draft" not in skills
    assert mock_info.called


def test_load_skills_skips_malformed_yaml_with_info_log():
    d = _tmp_skills_dir()
    _write_skill(d, "broken.md", "---\ntitle: {unbalanced\n---\n正文")
    with patch.object(loader.logger, "info") as mock_info:
        skills = loader.load_skills()
    assert "broken" not in skills
    assert mock_info.called


def test_load_skills_skips_empty_body():
    d = _tmp_skills_dir()
    _write_skill(d, "empty.md", "---\ntitle: 空技能\ninject: analyze\n---\n   \n")
    with patch.object(loader.logger, "info") as mock_info:
        skills = loader.load_skills()
    assert "empty" not in skills
    assert mock_info.called


def test_skill_for_analysis_matches_analysis_type():
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文标记ABC")
    spec = TaskSpec(intent="口碑", analysis_type=AnalysisType.VOC)
    injected = loader.skill_for_analysis(spec)
    assert "VOC正文标记ABC" in injected
    assert "VOC做法" in injected  # 标题也应出现在注入的小标题里


def test_skill_for_analysis_no_match_returns_empty():
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文")
    spec = TaskSpec(intent="摘要", analysis_type=AnalysisType.SUMMARY)
    assert loader.skill_for_analysis(spec) == ""


def test_skill_for_analysis_intent_keywords_or_semantics():
    d = _tmp_skills_dir()
    _write_skill(d, "cmp.md",
        "---\ntitle: 对比做法\ninject: analyze\ntrigger:\n  intent_keywords: [对比, 比较]\n---\n对比正文")
    spec = TaskSpec(intent="比较一下两款产品", analysis_type=AnalysisType.SUMMARY)
    assert "对比正文" in loader.skill_for_analysis(spec)


def test_skill_for_analysis_data_type_list_or_semantics():
    d = _tmp_skills_dir()
    _write_skill(d, "cd.md",
        "---\ntitle: 评论帖子做法\ninject: analyze\ntrigger:\n  data_type: [comment, post]\n---\n评论帖子正文")
    spec_comment = TaskSpec(intent="x", data_type=DataType.COMMENT)
    spec_bid = TaskSpec(intent="x", data_type=DataType.BID)
    assert "评论帖子正文" in loader.skill_for_analysis(spec_comment)
    assert loader.skill_for_analysis(spec_bid) == ""


def test_skill_for_analysis_and_across_trigger_keys():
    d = _tmp_skills_dir()
    _write_skill(d, "strict.md",
        "---\ntitle: 严格触发\ninject: analyze\ntrigger:\n  analysis_type: voc\n  data_type: [comment]\n---\n严格正文")
    matches = TaskSpec(intent="x", analysis_type=AnalysisType.VOC, data_type=DataType.COMMENT)
    only_voc = TaskSpec(intent="x", analysis_type=AnalysisType.VOC, data_type=DataType.ARTICLE)
    assert "严格正文" in loader.skill_for_analysis(matches)
    assert loader.skill_for_analysis(only_voc) == ""  # data_type 不满足，AND 语义应拒绝


def test_skill_for_analysis_time_range_required():
    d = _tmp_skills_dir()
    _write_skill(d, "trend.md",
        "---\ntitle: 趋势做法\ninject: analyze\ntrigger:\n  time_range_required: true\n  intent_keywords: [趋势]\n---\n趋势正文")
    spec_no_range = TaskSpec(intent="趋势变化", time_range=None)
    spec_with_range = TaskSpec(intent="趋势变化", time_range="最近30天")
    assert loader.skill_for_analysis(spec_no_range) == ""
    assert "趋势正文" in loader.skill_for_analysis(spec_with_range)


def test_skill_for_analysis_ignores_planner_inject():
    d = _tmp_skills_dir()
    _write_skill(d, "plan-demo.md",
        "---\ntitle: 平台做法\ninject: planner\ntrigger:\n  always: true\n---\n平台正文")
    spec = TaskSpec(intent="x")
    assert loader.skill_for_analysis(spec) == ""  # inject:planner 的技能不进 analyze


def test_skill_for_analysis_multiple_skills_concatenate():
    """真实场景：一个任务可能同时命中多个 analyze 侧技能（如"对比A和B的口碑"
    同时是 analysis_type=voc 又命中"对比"关键词），两条技能正文都应出现在注入结果里。"""
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文标记ABC")
    _write_skill(d, "cmp-demo.md",
        "---\ntitle: 对比做法\ninject: analyze\ntrigger:\n  intent_keywords: [对比, 比较]\n---\n对比正文标记XYZ")
    spec = TaskSpec(intent="对比A和B的口碑", analysis_type=AnalysisType.VOC)
    injected = loader.skill_for_analysis(spec)
    assert "VOC正文标记ABC" in injected
    assert "对比正文标记XYZ" in injected


def test_real_skills_dir_is_self_consistent():
    """守护真实 skills/*.md：防止 inject 拼写错误或 trigger 结构损坏导致功能在生产环境
    静默失效，而临时目录里的 38 个测试仍然全绿。不重定向 SKILLS_DIR —— 显式重置为真实路径，
    不依赖测试执行顺序（防止前面某个测试遗留重定向状态）。"""
    from src.config.settings import PROJECT_ROOT as _ROOT
    loader.SKILLS_DIR = _ROOT / "skills"

    skills = loader.load_skills()
    assert set(skills.keys()) == {
        "voc-analysis", "comparison-analysis", "trend-analysis", "platform-selection",
    }
    assert skills["voc-analysis"]["inject"] == "analyze"
    assert skills["comparison-analysis"]["inject"] == "analyze"
    assert skills["trend-analysis"]["inject"] == "analyze"
    assert skills["platform-selection"]["inject"] == "planner"

    assert "平台选型经验" in loader.skills_for_planner()


def test_skills_for_planner_always_trigger():
    d = _tmp_skills_dir()
    _write_skill(d, "plan-demo.md",
        "---\ntitle: 平台做法\ninject: planner\ntrigger:\n  always: true\n---\n平台正文")
    assert "平台正文" in loader.skills_for_planner()


def test_skills_for_planner_ignores_analyze_inject():
    d = _tmp_skills_dir()
    _write_skill(d, "voc-demo.md",
        "---\ntitle: VOC做法\ninject: analyze\ntrigger:\n  analysis_type: voc\n---\nVOC正文")
    assert loader.skills_for_planner() == ""


def test_select_skills_removed():
    assert not hasattr(loader, "select_skills")


def test_preferences_context_and_add():
    # 用临时目录，避免污染真实 memory/
    with tempfile.TemporaryDirectory() as d:
        old_dir = loader.MEMORY_DIR
        try:
            loader.MEMORY_DIR = Path(d)
            # 初始为空
            assert loader.load_preferences() == ""
            assert loader.preferences_context() == ""
            # 追加偏好 → 落盘 + 注入上下文包含该偏好
            assert loader.add_preference("默认产出 JSON")
            pref = loader.load_preferences()
            assert "默认产出 JSON" in pref
            ctx = loader.preferences_context()
            assert "用户偏好" in ctx and "默认产出 JSON" in ctx
            # 再加一条，两条都在
            loader.add_preference("- 语言：英文")  # 前缀 - 应被规整
            pref2 = loader.load_preferences()
            assert "默认产出 JSON" in pref2 and "语言：英文" in pref2
            # 空输入不写入
            assert loader.add_preference("   ") is False
        finally:
            loader.MEMORY_DIR = old_dir


def main():
    tests = [
        test_load_skills_parses_frontmatter,
        test_load_skills_skips_readme_silently,
        test_load_skills_skips_no_frontmatter_with_info_log,
        test_load_skills_skips_malformed_yaml_with_info_log,
        test_load_skills_skips_empty_body,
        test_skill_for_analysis_matches_analysis_type,
        test_skill_for_analysis_no_match_returns_empty,
        test_skill_for_analysis_intent_keywords_or_semantics,
        test_skill_for_analysis_data_type_list_or_semantics,
        test_skill_for_analysis_and_across_trigger_keys,
        test_skill_for_analysis_time_range_required,
        test_skill_for_analysis_ignores_planner_inject,
        test_skill_for_analysis_multiple_skills_concatenate,
        test_real_skills_dir_is_self_consistent,
        test_skills_for_planner_always_trigger,
        test_skills_for_planner_ignores_analyze_inject,
        test_select_skills_removed,
        test_preferences_context_and_add,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
