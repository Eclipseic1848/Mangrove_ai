"""仅从明确业务核验失败沉淀本人教训，供应商全部模拟。"""
import asyncio
import json
import sqlite3
import time
from threading import Event

import httpx
import pytest

from src.agentic_runtime.models import VerificationReport, VerificationStatus, VerificationCheck
from src.config.settings import settings
from src.memory import lessons
from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.test_web_source_delivery_api import _client, CoverageAwareWebPiRuntime
from tests.test_pi_runtime_workspace_api import _uploads, _wait_for_status


@pytest.mark.parametrize("outcome", ["failed", "inconclusive", "infrastructure", "queue_unavailable", "report_changed", "terminal_write_failed"])
def test_failed_verification_learns_once_and_repeated_failure_becomes_trial(tmp_path, monkeypatch, outcome):
    import src.model_connections.broker as broker_module

    class FailedRuntime(CoverageAwareWebPiRuntime):
        def _verification_report(self):
            return VerificationReport(status=VerificationStatus.INCONCLUSIVE if outcome == "inconclusive" else VerificationStatus.FAILED,
                summary="候选未满足冻结目标", evidence_count=1,
                checks=(VerificationCheck(code="source_grounding" if outcome == "infrastructure" else "semantic_goal",
                    passed=False, summary="费用汇总遗漏指定字段"),))

    runtime = FailedRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        data = {"title": "费用汇总遗漏字段", "keywords": ["费用汇总"],
                "body": "费用汇总前逐项检查要求的字段，不要忽略用户明确指定的项目。"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    broker = ConnectionBroker(repository=ModelConnectionRepository(settings.webui_db_path),
        vault=FernetCredentialVault.generate(), transport=httpx.MockTransport(provider), resolver=lambda _: ["8.8.8.8"])
    connection = asyncio.run(broker.configure_personal(owner_user_id="user-a", preset_id="deepseek", api_key="synthetic-only"))
    # 连接验证不属于任务完成后的教训生成调用。
    calls.clear()
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    document, _ = _uploads(tmp_path)
    payload = {"objective_text": "费用汇总", "upload_ids": [document], "output_formats": ["json"], "runtime_version": "pi",
        "model_connection_id": connection["connection_id"], "model_connection_model": connection["default_model"], "external_api_confirmed": True}
    if outcome in {"queue_unavailable", "terminal_write_failed"}:
        queue_blocked = Event()
        queue_blocked.set()
        connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if outcome == "queue_unavailable" and queue_blocked.is_set() and "INSERT" in sql.upper() and "library_learning_receipts" in sql:
                    raise sqlite3.OperationalError("模拟学习队列不可写")
                if (outcome == "terminal_write_failed" and "UPDATE" in sql.upper()
                        and "semantic_workspace_tasks" in sql and args and "candidate_ready" in args[0]):
                    raise sqlite3.OperationalError("模拟候选终态写入前中断")
                return super().execute(sql, *args, **kwargs)

        def fault_connection(*args, **kwargs):
            if str(args[0]) == str(settings.webui_db_path):
                kwargs.setdefault("factory", FailingConnection)
            return connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", fault_connection)
    if outcome == "report_changed":
        from src.memory import learning_receipts
        from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
        from tests.test_pi_runtime_workspace_api import _StaticReportVerifier, FakePiRuntime
        write_blocked, write_attempted = Event(), Event()
        write = learning_receipts.atomic_write

        def fail_lesson_write(path, content):
            if write_blocked.is_set() and path.parent == lessons.LESSONS_DIR and path.suffix == ".md":
                write_attempted.set()
                raise OSError("模拟教训落盘失败")
            return write(path, content)

        monkeypatch.setattr(learning_receipts, "atomic_write", fail_lesson_write)
        monkeypatch.setattr(SemanticWorkspaceManager, "_build_full_candidate_verifier",
            lambda *_args, **_kwargs: _StaticReportVerifier(FakePiRuntime()._verification_report()))
    with client:
        for index in range(3 if outcome == "failed" else 2 if outcome == "report_changed" else 1):
            if outcome == "report_changed" and index == 1:
                write_blocked.set()
            response = client.post("/api/semantic-workspace/tasks", json=payload)
            assert response.status_code == 202, response.text
            task_id = response.json()["task_id"]
            if outcome == "terminal_write_failed":
                failed = _wait_for_status(client, task_id, "failed")
                assert any(event["event_type"] == "candidate_verification_failed"
                    and event["details"].get("failure_learning_requested") is True for event in failed["events"])
                assert lessons.load_lessons(owner_id="user-a") == []
                return
            detail = _wait_for_status(client, task_id, "candidate_ready")
            if outcome == "report_changed" and index == 1:
                assert write_attempted.wait(12), "后台未尝试写入教训"
                from tests.test_pi_runtime_workspace_api import _FixedCandidateRulesetResolver
                previous_rules = _FixedCandidateRulesetResolver().resolve(None)
                # 已明确失败的候选只有规则更新后才能重验，不绕过真实资格门。
                monkeypatch.setattr(_FixedCandidateRulesetResolver, "resolve", lambda *_:
                    previous_rules.model_copy(update={"verifier_ruleset_hash": "9" * 64,
                        "verifier_ruleset_manifest_json": json.dumps({"schema_version": 1,
                            "verifier_ruleset_hash": "9" * 64}, sort_keys=True, separators=(",", ":"))}))
                response = client.post(f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
                    headers={"Idempotency-Key": "verify-before-learning-replay"}, json={"expected_revision": 1,
                        "expected_previous_attempt_id": detail["agentic_runtime"]["latest_verification_attempt"]["attempt_id"],
                        "external_api_confirmed": True})
                assert response.status_code == 202, (response.text, detail["agentic_runtime"].get("reverification_offer"))
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    latest = client.get(f"/api/semantic-workspace/tasks/{task_id}").json()["agentic_runtime"]["latest_verification_attempt"]
                    if latest["attempt_id"] == response.json()["attempt_id"] and latest["status"] == "passed":
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("重新核验未通过")
                write_blocked.clear()
                from src.memory.learning_receipts import LearningReceipts
                receipts = LearningReceipts(settings.webui_db_path, lessons.LESSONS_DIR)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    receipt = receipts.get(owner_id="user-a", task_id=task_id, revision=1,
                        run_id=detail["run_id"], kind="lesson_new")
                    if receipt and receipt["state"] == "conflict":
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("报告变化后的旧学习未被拒绝")
                assert lessons.load_lessons(owner_id="user-a")[0]["occurrences"] == 1
                assert len(calls) == 2
                return
            if outcome == "queue_unavailable":
                assert detail["agentic_runtime"]["candidates"]
                time.sleep(5.2)
                assert client.get(f"/api/semantic-workspace/tasks/{task_id}").json()["status"] == "candidate_ready"
                assert lessons.load_lessons(owner_id="user-a") == []
                queue_blocked.clear()
            if outcome in {"inconclusive", "infrastructure"}:
                time.sleep(5.2)
                assert lessons.load_lessons(owner_id="user-a") == []
                assert calls == []
                return
            deadline = time.monotonic() + 12
            learned = []
            while time.monotonic() < deadline:
                learned = lessons.load_lessons(owner_id="user-a")
                if learned and learned[0]["occurrences"] == index + 1:
                    break
                time.sleep(0.05)
            assert len(learned) == 1
            assert learned[0]["occurrences"] == index + 1
            assert learned[0]["status"] == "draft" and learned[0]["helped_avoid"] == 0
            assert lessons.load_lessons(owner_id="user-b") == []
            for _ in range(2):
                assert client.get(f"/api/semantic-workspace/tasks/{task_id}").status_code == 200
            assert lessons.load_lessons(owner_id="user-a")[0]["occurrences"] == index + 1
            if index == 2:
                assert detail["task_context"]["lessons"][0]["slug"] == learned[0]["slug"]
        assert len(calls) == (3 if outcome == "failed" else 1)
