"""旧Harness入口类型矩阵不随文档描述自动扩大。"""
import pytest
from fastapi import HTTPException

from scripts.evaluate_task_generalization import base_plan
from src.api.routes.semantic_harness import _capability_id
from src.semantic_harness.capabilities import get_capability_registry
from src.semantic_harness.models import SemanticTaskPlan


@pytest.mark.parametrize("family,expected", [
    ("tabular_transform", "table.duckdb"), ("extract", "document.evidence"),
    ("compare", "document.evidence"), ("audit", "document.evidence"),
    ("compose", "document.evidence"), ("summarize", "document.evidence"),
    ("translate", "document.evidence"), ("convert", None),
    ("transcribe", None), ("discover", None),
])
def test_existing_type_to_registered_executor_boundary(family, expected):
    plan = SemanticTaskPlan.model_validate(base_plan(family))
    if expected is None:
        with pytest.raises(HTTPException) as caught:
            _capability_id(plan)
        assert caught.value.status_code == 409
    else:
        capability = _capability_id(plan)
        assert capability == expected
        registry = get_capability_registry()
        assert registry.manifest(capability).capability_id == expected
        assert callable(registry.executor(capability))
