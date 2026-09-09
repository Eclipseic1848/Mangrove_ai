# -*- coding: utf-8 -*-
"""有依据的逐步澄清：实际观察、不可变轮次和确认边界。"""
import pytest
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database
from tests.account_execution_helpers import seed_execution_owner
from src.account_execution import ExecutionAuthorization, execution_context

from src.services.upload_store import UploadStore
from src.semantic_harness.inspectors import uploads
from src.conversation_steering import RawUserTurn, SteeringRequest
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _execution_authority():
    with execution_context(ExecutionAuthorization("owner-a", 0)):
        yield


def test_source_findings_are_bounded_real_observations(tmp_path):
    store = UploadStore(tmp_path / "uploads", max_bytes=1024 * 1024)
    item = store.save_bytes(
        "owner-a", "订单.csv", "客户,实收,状态\n甲,20,完成\n乙,30,作废\n".encode("utf-8"),
    )
    inspector = uploads.UploadSourceInspector(user_id="owner-a", upload_store=store)
    reports = inspector.inspect_artifacts((item.upload_id,))
    findings = uploads.public_source_findings(reports)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["source_sha256"] == item.sha256
    assert finding["inspection_sha256"] == reports[0].canonical_hash()
    assert finding["status"] == "ready"
    assert "实收" in finding["summary"] and "20" in finding["summary"]
    assert "抽样" in finding["summary"]
    assert item.upload_id not in finding["summary"]
    assert len(finding["summary"]) <= 500
    assert "physical_ref" not in finding
    with pytest.raises(PermissionError):
        uploads.UploadSourceInspector(user_id="owner-b", upload_store=store).inspect_artifacts((item.upload_id,))



def test_column_samples_do_not_claim_row_alignment(tmp_path):
    store = UploadStore(tmp_path / "uploads", max_bytes=1024 * 1024)
    item = store.save_bytes(
        "owner-a", "样例.csv", "对象,数值\n甲,10\n甲,\n乙,20\n丙,10\n".encode("utf-8"),
    )
    reports = uploads.UploadSourceInspector(user_id="owner-a", upload_store=store).inspect_artifacts((item.upload_id,))
    columns = reports[0].tables[0].columns
    assert columns[0].sample_values == ("甲", "乙", "丙")
    assert columns[1].sample_values == ("10", "20")
    summary = uploads.public_source_findings(reports)[0]["summary"]
    assert summary.startswith("各列独立样例，无行对应关系；")
    assert len(summary) <= 500


def test_question_round_and_answer_are_persisted_once(tmp_path):
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "owner-a")
    store = WebUIStore(str(database))
    store.create_semantic_workspace_task(
        "owner-a", task_id="task-clarify", title="合成任务", objective_text="统计订单",
        upload_ids=[], output_formats=["csv"], provider="local", model="fixture",
        external_api_confirmed=False,
    )
    question = store.publish_workspace_question("owner-a", "task-clarify", {
        "kind": "plan", "question_id": "date.basis", "prompt": "使用哪一种日期？",
        "reason": "两种日期会得到不同期间", "options": [], "allow_free_text": True,
        "purpose": "business", "continuation": "resume",
    })
    assert question["round_id"].startswith("clarification_")
    receipt = store.accept_workspace_answer(
        "owner-a", "task-clarify", answer="使用到账日期", expected_revision=1,
        question_round_id=question["round_id"], idempotency_key="answer-1",
    )
    reopened = WebUIStore(str(database))
    repeated = reopened.accept_workspace_answer(
        "owner-a", "task-clarify", answer="使用到账日期", expected_revision=1,
        question_round_id=question["round_id"], idempotency_key="answer-1",
    )
    assert repeated == receipt
    with pytest.raises(ValueError):
        reopened.accept_workspace_answer(
            "owner-a", "task-clarify", answer="使用交易日期", expected_revision=1,
            question_round_id=question["round_id"], idempotency_key="answer-2",
        )
    history = reopened.workspace_clarification_history("owner-a", "task-clarify", 1)
    assert len(history) == 1
    assert history[0]["answer"] == "使用到账日期"
    assert history[0]["turn_id"] == receipt["turn_id"]


def test_understanding_rejects_foreign_history_before_any_model_call():
    foreign = RawUserTurn(turn_id="turn-foreign", owner_id="other-owner", task_id="task-a", revision=1, text="其他用户原话")
    with pytest.raises(ValidationError):
        SteeringRequest(owner_id="owner-a", task_id="task-a", revision=1, text="继续", current_status="running", relevant_turns=(foreign,))


@pytest.mark.parametrize("send_outcome", ["success", "unknown"])
def test_http_answer_consumes_original_turn_and_persists_next_question(tmp_path, monkeypatch, send_outcome):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import auth
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    from src.conversation_steering import ContextDelta, DeltaConfidence, TurnIntent
    database = migrated_webui_database(tmp_path / "http.db")
    seed_execution_owner(database, "owner-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(auth, "_store", None)
    store = auth.get_store()
    store.create_semantic_workspace_task("owner-a", task_id="task-http", title="订单", objective_text="统计订单，不含作废", upload_ids=[], output_formats=["json"], provider="local", model="fixture", external_api_confirmed=False)
    store.update_semantic_workspace_task("owner-a", "task-http", status="running")
    requests = []
    class Rewriter:
        async def rewrite(self, turn, request):
            requests.append(request)
            if request.clarification_round_id and send_outcome == "unknown":
                raise RuntimeError("合成响应未知")
            if request.text == "现在进度":
                return ContextDelta(delta_id=f"delta-status-{len(requests)}", owner_id=turn.owner_id, task_id=turn.task_id, inherited_revision=turn.revision,
                    source_turn_ids=tuple(item.turn_id for item in request.relevant_turns)+(turn.turn_id,), intent=TurnIntent.STATUS_QUESTION, confidence=DeltaConfidence.HIGH,
                    normalized_text="询问状态", direct_answer="任务仍在运行")
            return ContextDelta(delta_id=f"delta-{len(requests)}", owner_id=turn.owner_id, task_id=turn.task_id,
                inherited_revision=turn.revision, source_turn_ids=tuple(item.turn_id for item in request.relevant_turns)+(turn.turn_id,),
                intent=TurnIntent.TASK_REFINEMENT, confidence=DeltaConfidence.HIGH,
                normalized_text="输出 CSV，仍排除作废", output_delta=("csv",),
                open_questions=("按到账还是交易日期？",) if len(requests)==1 else ())
    monkeypatch.setattr(route, "build_context_rewriter", lambda request, **kwargs: Rewriter())
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {"user_id":"owner-a", "role":"user", "execution_generation":0}
    with TestClient(app) as client:
        first = client.post("/api/semantic-workspace/tasks/task-http/turns", json={"text":"输出 CSV"}, headers={"Idempotency-Key":"origin"})
        assert first.status_code == 200, first.text
        question = first.json()["clarification"]
        assert question["affected_scope"] == "输出要求"
        assert first.json()["proposal_id"] is None
        detail = client.get("/api/semantic-workspace/tasks/task-http").json()
        assert detail["understanding"]["question"]["round_id"] == question["round_id"]
        status_response = client.post("/api/semantic-workspace/tasks/task-http/turns", json={"text":"现在进度"})
        assert status_response.status_code == 200, status_response.text
        current = client.get("/api/semantic-workspace/tasks/task-http").json()
        assert current["understanding"]["question"]["round_id"] == question["round_id"]
        assert current["understanding"]["status"] == "needs_clarification"
        assert current["understanding"]["summary"] != "询问状态"
        body = {"answer":"  按到账日期，仍不要作废  ", "expected_revision":1, "question_round_id":question["round_id"]}
        answer_text = body["answer"].strip()
        response = client.post("/api/semantic-workspace/tasks/task-http/answer", json=body, headers={"Idempotency-Key":"answer"})
        if send_outcome == "unknown":
            assert response.status_code == 200 and response.json()["answer_receipt"]["status"] == "unknown"
            monkeypatch.setattr(auth, "_store", None)
            repeat = client.post("/api/semantic-workspace/tasks/task-http/answer", json=body, headers={"Idempotency-Key":"answer"})
            assert repeat.status_code == 200 and repeat.json()["answer_receipt"] == response.json()["answer_receipt"]
            assert len(requests) == 3
            detail = client.get("/api/semantic-workspace/tasks/task-http").json()
            assert detail["clarification_history"][0]["answer"] == answer_text
            assert detail["understanding"]["status"] == "unavailable"
            return
        assert response.status_code == 200, response.text
        receipt = response.json()["answer_receipt"]
        assert receipt["turn_id"] != first.json()["turn_id"]
        assert requests[-1].relevant_turns[0].text == "输出 CSV"
        assert requests[-1].prior_delta.open_questions == ("按到账还是交易日期？",)
        assert requests[-1].text == answer_text
        assert response.json()["active_revision"] == 1
        assert response.json()["understanding"]["status"] == "ready"
        repeat = client.post("/api/semantic-workspace/tasks/task-http/answer", json=body, headers={"Idempotency-Key":"answer"})
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["answer_receipt"] == receipt
        assert len(requests) == 3
        changed = client.post("/api/semantic-workspace/tasks/task-http/answer", json={**body,"answer":"改用交易日"}, headers={"Idempotency-Key":"answer"})
        assert changed.status_code == 409
        turns = client.get("/api/semantic-workspace/tasks/task-http/turns").json()
        assert turns["results"][-1]["turn_id"] == receipt["turn_id"]
        assert response.json()["clarification_history"][0]["answer"] == answer_text
        correction = client.post("/api/semantic-workspace/tasks/task-http/turns", json={"text":"刚说错了，改应收，作废还是不要"})
        assert correction.status_code == 200, correction.text
        assert tuple(item.text for item in requests[-1].relevant_turns) == ("输出 CSV", answer_text)
        assert requests[-1].prior_delta.normalized_text == "输出 CSV，仍排除作废"


def test_confirmed_web_clarification_updates_typed_goal_and_preserves_snapshot(tmp_path, monkeypatch):
    from pathlib import Path
    from src.api.routes import semantic_workspace as route
    from src.api.auth import get_store
    from src.config.settings import settings
    from src.conversation_steering import ContextDelta, DeltaConfidence, TurnIntent
    from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime, _client, _seed_snapshot, _wait_for_delivery
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    snapshot, _ = _seed_snapshot(Path(settings.webui_db_path))
    class Rewriter:
        async def rewrite(self, turn, request):
            return ContextDelta(delta_id="delta-web-"+turn.turn_id, owner_id=turn.owner_id, task_id=turn.task_id,
                inherited_revision=turn.revision, source_turn_ids=(turn.turn_id,), intent=TurnIntent.TASK_REFINEMENT,
                confidence=DeltaConfidence.HIGH, normalized_text="至少一项，不含代理机构",
                selection_delta={"explicit_exclusions":["代理机构"]} if request.text != "未知字段" else {"unsupported_key":"不允许"},
                coverage_delta={"quantity_requirement":"至少 1 项", "completeness_requirement":"允许部分但必须有真实证据"})
    monkeypatch.setattr(route, "build_context_rewriter", lambda request, **kwargs: Rewriter())
    # 此 HTTP 夹具的真实账号是 user-a，不能继承本模块其余测试的 owner-a。
    with execution_context(ExecutionAuthorization("user-a", 0)), client:
        created = client.post("/api/semantic-workspace/tasks", json={"objective_text":"整理公开网页产品", "source_snapshot_id":snapshot,
            "quantity_requirement":"尽可能多", "completeness_requirement":"允许部分但必须有证据", "output_formats":["json"], "runtime_version":"pi", "provider":"local"})
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        _wait_for_delivery(client, task_id)
        path = f"/api/semantic-workspace/tasks/{task_id}"
        proposal = client.post(path+"/turns", json={"text":"至少一项，不含代理机构"}).json()["proposal_id"]
        confirmed = client.post(path+f"/revision-proposals/{proposal}/decision", json={"mode":"cancel_now"})
        assert confirmed.status_code == 202, confirmed.text
        _wait_for_delivery(client, task_id)
        saved = get_store().get_web_task_contract("user-a", task_id, 2)
        assert saved["source_snapshot_id"] == snapshot
        goal = saved["goal_contract"]
        assert goal["explicit_exclusions"] == ["代理机构"]
        assert goal["coverage"]["target_result_count"] == 1
        assert goal["coverage"]["require_all"] is False
        assert runtime.requests[-1].goal_contract == goal
        old = get_store().get_web_task_contract("user-a", task_id, 1)
        assert old["goal_contract"]["explicit_exclusions"] == []
        proposal = client.post(path+"/turns", json={"text":"未知字段"}).json()["proposal_id"]
        refused = client.post(path+f"/revision-proposals/{proposal}/decision", json={"mode":"cancel_now"})
        assert refused.status_code == 422
        assert get_store().get_semantic_workspace_task("user-a", task_id)["active_revision"] == 2
        assert runtime.start_calls == 2


def test_public_question_round_upgrade_and_event_whitelist(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import auth
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    database = migrated_webui_database(tmp_path / "public.db")
    seed_execution_owner(database, "owner-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database)); monkeypatch.setattr(auth, "_store", None)
    store = auth.get_store()
    store.create_semantic_workspace_task("owner-a", task_id="task-public", title="合成", objective_text="核对资料", upload_ids=[], output_formats=["json"], provider="local", model="fixture", external_api_confirmed=False)
    store.update_semantic_workspace_task("owner-a", "task-public", status="needs_input", question={"kind":"external", "question_id":"old-external", "prompt":"允许本次外发？", "purpose":"原来说明的用途", "options":[{"value":"cancel","label":"取消"}], "allow_free_text":False, "resume_token":"synthetic-private-token", "metadata":{"internal":"合成内部值"}})
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {"user_id":"owner-a", "role":"user", "execution_generation":0}
    with TestClient(app) as client:
        path = "/api/semantic-workspace/tasks/task-public"
        first = client.get(path)
        assert first.status_code == 200, first.text
        second = client.get(path).json()
        question = first.json()["question"]
        assert question["round_id"] == second["question"]["round_id"]
        assert question["outbound_purpose"] == "原来说明的用途"
        assert question["purpose"] == "authorization" and question["continuation"] == "unavailable"
        assert first.json()["clarification_history"][0]["asked_at"] is None
        events = client.get(path+"/events").json()
        assert len([event for event in events if event["event_type"] == "question_required"]) == 1
        for snapshot in [question, first.json()["clarification_history"][0]["question"], events[-1]["details"]["question"]]:
            assert "resume_token" not in snapshot and "metadata" not in snapshot
        assert store.get_semantic_workspace_task("owner-a", "task-public")["question"]["resume_token"] == "synthetic-private-token"


@pytest.mark.parametrize("waiting_status", ["needs_input", "paused", "completed", "candidate_ready", "failed", "cancelled"])
def test_after_safe_point_without_running_step_does_not_lock_proposal(tmp_path, monkeypatch, waiting_status):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import auth
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    from tests.test_conversation_steering_api import _ApiManager, _ApiMaterialRewriter
    database = migrated_webui_database(tmp_path / "decision.db")
    seed_execution_owner(database, "owner-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database)); monkeypatch.setattr(auth, "_store", None)
    store = auth.get_store()
    store.create_semantic_workspace_task("owner-a", task_id="task-decision", title="合成", objective_text="核对资料", upload_ids=[], output_formats=["json"], provider="local", model="fixture", external_api_confirmed=False)
    store.update_semantic_workspace_task("owner-a", "task-decision", status=waiting_status)
    manager = _ApiManager()
    monkeypatch.setattr(route, "get_semantic_workspace_manager", lambda: manager)
    monkeypatch.setattr(route, "build_context_rewriter", lambda request: _ApiMaterialRewriter())
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {"user_id":"owner-a", "role":"user", "execution_generation":0}
    with TestClient(app) as client:
        path = "/api/semantic-workspace/tasks/task-decision"
        proposal = client.post(path+"/turns", json={"text":"改为 CSV"}).json()["proposal_id"]
        rejected = client.post(path+f"/revision-proposals/{proposal}/decision", json={"mode":"after_safe_point"})
        assert rejected.status_code == 409
        assert route._steering_repository().get_decision_for_proposal("owner-a", proposal) is None
        accepted = client.post(path+f"/revision-proposals/{proposal}/decision", json={"mode":"cancel_now"})
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["revision"]["revision"] == 2


@pytest.mark.parametrize("answer", ["confirm", "local"])
def test_external_confirmation_consumes_current_round_normally(tmp_path, monkeypatch, answer):
    import asyncio
    from src.api import auth
    from src.api import semantic_workspace_runtime as runtime
    from src.config.settings import settings
    database = migrated_webui_database(tmp_path / "external.db")
    seed_execution_owner(database, "owner-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database)); monkeypatch.setattr(auth, "_store", None)
    store = auth.get_store()
    store.create_semantic_workspace_task("owner-a", task_id="task-external", title="合成", objective_text="核对资料", upload_ids=[], output_formats=["json"], provider="synthetic-provider", model="fixture", external_api_confirmed=False)
    question = store.publish_workspace_question("owner-a", "task-external", {"kind":"external", "question_id":"external", "prompt":"是否允许本次外发？", "options":[], "allow_free_text":False})
    receipt = store.accept_workspace_answer("owner-a", "task-external", answer=answer, expected_revision=1, question_round_id=question["round_id"], idempotency_key="external-answer")
    manager = runtime.SemanticWorkspaceManager()
    queued = []
    monkeypatch.setattr(manager, "enqueue", lambda *args: queued.append(args))
    result = asyncio.run(manager.answer("owner-a", "task-external", answer, accepted_turn_id=receipt["turn_id"]))
    assert result["status"] == "queued" and result["question"] is None
    assert result["external_api_confirmed"] is (answer == "confirm")
    assert result["provider"] == ("local" if answer == "local" else "synthetic-provider")
    assert queued == [("owner-a", "task-external")]
