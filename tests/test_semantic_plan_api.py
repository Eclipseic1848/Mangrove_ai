# -*- coding: utf-8 -*-
"""Phase 4B 批次 1：不可变计划 revision 与测试接口门禁。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
from src.api.auth import get_current_user
from src.api.routes import semantic_plans
from src.config.settings import settings
from src.semantic_harness.compiler_models import CompileRequest, PlanSemanticsDraft
from src.semantic_harness.models import (
    CombineMode,
    CombineSpec,
    ContentPolicy,
    DeliveryFormat,
    DeliverySpec,
    PostconditionSpec,
    PredicateOperator,
    PredicatePostcondition,
    ProjectionField,
    TaskFamily,
)


class ApiFakeGenerator:
    provider = "local"
    model = "api-fixture-model"
    prompt_version = "stp-v1-test"
    prompt_sha256 = "2" * 64

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        request: CompileRequest,
        *,
        diagnostics,
        attempt: int,
    ) -> PlanSemanticsDraft:
        del request, diagnostics, attempt
        self.calls += 1
        return PlanSemanticsDraft(
            task_family=TaskFamily.TABULAR_TRANSFORM,
            normalized_objective="筛选姓名为谢超群的明细并输出两列",
            selection=(
                {
                    "field": "姓名",
                    "operator": PredicateOperator.EQ,
                    "value": "谢超群",
                },
            ),
            projection=(
                ProjectionField(name="核销工作量天数"),
                ProjectionField(name="工作量费用"),
            ),
            record_grain="source_detail_row",
            combine=CombineSpec(mode=CombineMode.ONE_TABLE),
            content_policy=ContentPolicy.VERBATIM,
            delivery=DeliverySpec(formats=(DeliveryFormat.XLSX,)),
            postconditions=PostconditionSpec(
                table_count=1,
                exact_visible_columns=("核销工作量天数", "工作量费用"),
                predicates=(
                    PredicatePostcondition(
                        field="姓名",
                        operator=PredicateOperator.EQ,
                        value="谢超群",
                    ),
                ),
            ),
        )


def _make_client(
    tmp_path,
    monkeypatch,
    generator: ApiFakeGenerator,
    *,
    user_id: str = "user-a",
) -> TestClient:
    monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "semantic.db"))
    auth_mod._store = None
    monkeypatch.setattr(
        semantic_plans,
        "_build_generator",
        lambda **_: generator,
    )
    app = FastAPI()
    app.include_router(semantic_plans.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    return TestClient(app)


def _payload(**overrides):
    values = {
        "task_id": "task-api-1",
        "objective_text": "只提取谢超群的数据，只保留核销工作量天数和工作量费用",
        "artifact_ids": ["artifact-workload"],
        "accepted_formats": ["pdf"],
        "provider": "local",
        "model": "api-fixture-model",
    }
    values.update(overrides)
    return values


def test_compile_persists_immutable_revision_and_owner_can_read(
    tmp_path,
    monkeypatch,
):
    generator = ApiFakeGenerator()
    client = _make_client(tmp_path, monkeypatch, generator)

    response = client.post("/api/semantic-plans/compile", json=_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["revision"] == 1
    plan_id = body["plan_id"]
    stored = client.get(f"/api/semantic-plans/{plan_id}/revisions/1")
    assert stored.status_code == 200, stored.text
    stored_body = stored.json()
    assert stored_body["plan"]["plan_id"] == plan_id
    assert stored_body["plan_hash"] == body["plan_hash"]
    assert stored_body["request"]["artifact_ids"] == ["artifact-workload"]
    assert generator.calls == 1


def test_plan_revision_is_append_only_and_other_user_gets_404(
    tmp_path,
    monkeypatch,
):
    generator = ApiFakeGenerator()
    client_a = _make_client(tmp_path, monkeypatch, generator, user_id="user-a")
    created = client_a.post("/api/semantic-plans/compile", json=_payload()).json()
    plan_id = created["plan_id"]

    revised = client_a.post(
        f"/api/semantic-plans/{plan_id}/revisions",
        json={"answer": "保留源明细，不做汇总"},
    )

    assert revised.status_code == 200, revised.text
    assert revised.json()["revision"] == 2
    revisions = client_a.get(
        f"/api/semantic-plans/{plan_id}/revisions"
    ).json()
    assert [item["revision"] for item in revisions] == [2, 1]

    client_b = _make_client(tmp_path, monkeypatch, generator, user_id="user-b")
    assert client_b.get(
        f"/api/semantic-plans/{plan_id}/revisions/1"
    ).status_code == 404
    assert client_b.post(
        f"/api/semantic-plans/{plan_id}/revisions",
        json={"answer": "试图改写他人计划"},
    ).status_code == 404


def test_unconfirmed_external_request_is_saved_without_model_call(
    tmp_path,
    monkeypatch,
):
    generator = ApiFakeGenerator()
    client = _make_client(tmp_path, monkeypatch, generator)

    response = client.post(
        "/api/semantic-plans/compile",
        json=_payload(provider="deepseek", external_api_confirmed=False),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_user"
    assert body["clarification"]["ambiguity_id"] == "risk.external_api"
    assert body["plan"] is None
    assert generator.calls == 0


def test_unconfirmed_external_request_does_not_resolve_provider(
    tmp_path,
    monkeypatch,
):
    generator = ApiFakeGenerator()
    client = _make_client(tmp_path, monkeypatch, generator)
    monkeypatch.setattr(
        semantic_plans,
        "_build_generator",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("确认前不应解析外部 Provider")
        ),
    )

    response = client.post(
        "/api/semantic-plans/compile",
        json=_payload(provider="DEEPSEEK", external_api_confirmed=False),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_user"
    assert body["provenance"]["provider"] == "deepseek"
