"""每个泛化变体独立报告；失败不能用生成器异常或总数掩盖。"""
import pytest
from pydantic import ValidationError

from scripts.evaluate_task_generalization import evaluate_case, generate_cases
from src.semantic_harness.models import SourceScope


CASES = generate_cases()


def test_case_inventory_has_500_unique_ids():
    assert len(CASES) == len({case["id"] for case in CASES}) == 500


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_generalized_contract(case):
    result = evaluate_case(case)
    assert result["status"] == "passed", {**result, "oracle": case["oracle"]}


@pytest.mark.parametrize("payload", [
    {"source_ids": [" "]}, {"artifact_ids": ["\t"]},
    {"pages": {" ": [1]}}, {"pages": {"fixture": [True]}},
    {"pages": {"fixture": [1.0]}}, {"pages": {"fixture": ["1"]}},
])
def test_source_identity_and_page_types_fail_closed(payload):
    with pytest.raises(ValidationError):
        SourceScope.model_validate(payload)


def test_valid_source_identity_and_integer_pages_are_preserved():
    source = SourceScope(artifact_ids=("fixture",), source_ids=("source",), pages={"fixture": (1, 2)})
    assert source.pages == {"fixture": (1, 2)}
    assert source.source_ids == ("source",)
