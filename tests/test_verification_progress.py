"""不同任务共用有效证据进展判断，不以工具调用次数代替进展。"""
import pytest

from src.agentic_runtime.verification_progress import VerificationProgress


@pytest.mark.parametrize("task_kind", ["document", "table", "collection"])
def test_repeating_same_evidence_stops_after_two_repair_rounds(task_kind):
    progress = VerificationProgress()
    assert progress.observe(gaps=["目标字段缺少依据"], evidence={"source": task_kind, "trusted": []})
    assert progress.observe(gaps=["目标字段缺少依据"], evidence={"source": task_kind, "trusted": []})
    assert not progress.observe(gaps=["目标字段缺少依据"], evidence={"source": task_kind, "trusted": []})


def test_new_evidence_resets_budget_but_rewording_does_not():
    progress = VerificationProgress()
    assert progress.observe(gaps=["缺少证据"], evidence={"trusted": []})
    assert progress.observe(gaps=["换一句话描述同一缺口"], evidence={"trusted": []})
    assert progress.observe(gaps=["缺少证据"], evidence={"trusted": ["evidence:1"]})
    assert progress.observe(gaps=["缺少证据"], evidence={"trusted": ["evidence:1"]})
    assert not progress.observe(gaps=["仍有缺口"], evidence={"trusted": ["evidence:1"]})


def test_resolved_gaps_allow_completion():
    progress = VerificationProgress()
    for _ in range(3):
        progress.observe(gaps=["缺少证据"], evidence={})
    assert progress.observe(gaps=[], evidence={})


def test_worsening_quality_reordering_or_changing_parser_is_not_progress():
    progress = VerificationProgress()
    assert progress.observe(gaps=["缺口"], evidence={"trusted": ["a", "b"], "unknown_units": ["c"]})
    assert progress.observe(gaps=["缺口", "更多缺口"], evidence={"trusted": ["b", "a"], "unknown_units": ["c", "d"]})
    assert not progress.observe(gaps=["未解决"], evidence={"trusted": ["a", "b"], "parser_versions": ["new"]})
