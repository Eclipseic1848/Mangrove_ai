"""澄清回答的事务、竞争和进程重建边界。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from src.account_execution import ExecutionAuthorization, execution_context
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def waiting(tmp_path):
    database = migrated_webui_database(tmp_path / "answers.db")
    seed_execution_owner(database, "owner-a")
    store = WebUIStore(str(database))
    with execution_context(ExecutionAuthorization("owner-a", 0)):
        store.create_semantic_workspace_task(
            "owner-a", task_id="answer-task", title="合成任务", objective_text="统计订单",
            upload_ids=[], output_formats=["csv"], provider="local", model="fixture",
            external_api_confirmed=False,
        )
        question = store.publish_workspace_question("owner-a", "answer-task", {
            "kind": "plan", "question_id": "date.basis", "prompt": "使用哪一种日期？",
            "options": [], "allow_free_text": True, "purpose": "business", "continuation": "resume",
        })
        yield store, question


def _accept(store, question, **changes):
    payload = dict(answer="使用到账日期", expected_revision=question["revision"],
                   question_round_id=question["round_id"], idempotency_key="answer-key")
    payload.update(changes)
    return store.accept_workspace_answer("owner-a", "answer-task", **payload)


@pytest.mark.parametrize("change", [
    {"answer": "使用交易日期"}, {"expected_revision": 2},
    {"question_round_id": "clarification_" + "0" * 32},
])
def test_same_key_cannot_replace_answer_identity(waiting, change):
    store, question = waiting
    receipt = _accept(store, question)
    with pytest.raises(ValueError):
        _accept(store, question, **change)
    assert _accept(WebUIStore(store.db_path), question) == receipt
    history = store.workspace_clarification_history("owner-a", "answer-task", 1)
    assert len(history) == 1 and history[0]["answer"] == "使用到账日期"


def test_two_connections_compete_for_one_round(waiting):
    store, question = waiting
    barrier = Barrier(2)

    def compete(key):
        independent = WebUIStore(store.db_path)
        with execution_context(ExecutionAuthorization("owner-a", 0)):
            barrier.wait(timeout=10)
            try:
                return _accept(independent, question, idempotency_key=key)
            except ValueError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(compete, ("first", "second")))
    assert sum(receipt is not None for receipt in receipts) == 1
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM conversation_raw_turns").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM semantic_workspace_events WHERE event_type='clarification.accepted'").fetchone()[0] == 1


@pytest.mark.parametrize("stage", ["question_required", "question_answered", "clarification.accepted"])
def test_interrupted_transaction_leaves_no_partial_question_or_answer(waiting, monkeypatch, stage):
    store, question = waiting
    before = store.get_semantic_workspace_task("owner-a", "answer-task")
    with store._conn() as conn:
        before_events = [tuple(row) for row in conn.execute("SELECT * FROM semantic_workspace_events ORDER BY sequence")]
        before_revisions = [tuple(row) for row in conn.execute("SELECT * FROM semantic_workspace_revisions ORDER BY revision")]
    original = store._clarification_event

    def interrupt(conn, user_id, task_id, event_type, details, *, event_id):
        original(conn, user_id, task_id, event_type, details, event_id=event_id)
        if event_type == stage:
            raise RuntimeError("合成事务中断")

    monkeypatch.setattr(store, "_clarification_event", interrupt)
    with pytest.raises(RuntimeError, match="合成事务中断"):
        if stage == "question_required":
            store.publish_workspace_question("owner-a", "answer-task", question)
        else:
            _accept(store, question)
    reopened = WebUIStore(store.db_path)
    after = reopened.get_semantic_workspace_task("owner-a", "answer-task")
    assert after == before
    history = reopened.workspace_clarification_history("owner-a", "answer-task", 1)
    assert len(history) == 1 and history[0]["answer"] is None
    with reopened._conn() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM semantic_workspace_events ORDER BY sequence")] == before_events
        assert [tuple(row) for row in conn.execute("SELECT * FROM semantic_workspace_revisions ORDER BY revision")] == before_revisions
        assert conn.execute("SELECT count(*) FROM conversation_raw_turns").fetchone()[0] == 0
    # 重建实例已无故障注入；相同业务请求仍有且仅有一次接收资格。
    if stage == "question_required":
        question = reopened.publish_workspace_question("owner-a", "answer-task", question)
    assert _accept(reopened, question)["status"] == "accepted"


def test_repeated_question_id_has_distinct_round_and_rejects_old_answer(waiting):
    store, first = waiting
    second = store.publish_workspace_question("owner-a", "answer-task", first)
    assert second["question_id"] == first["question_id"]
    assert second["round_id"] != first["round_id"]
    with pytest.raises(ValueError):
        _accept(store, first)
    assert _accept(store, second)["round_id"] == second["round_id"]


@pytest.mark.parametrize("change", [
    {"active_revision": 2}, {"cancel_requested": True}, {"status": "cancelling"},
])
def test_stale_or_stopped_task_rejects_unaccepted_answer(waiting, change):
    store, question = waiting
    store.update_semantic_workspace_task("owner-a", "answer-task", **change)
    with pytest.raises(ValueError):
        _accept(store, question)
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM conversation_raw_turns").fetchone()[0] == 0


def test_send_claim_survives_rebuild_and_never_reopens_permission(waiting):
    store, question = waiting
    receipt = _accept(store, question)
    reopened = WebUIStore(store.db_path)
    assert _accept(reopened, question) == receipt
    reopened.claim_workspace_answer("owner-a", "answer-task", receipt["turn_id"])
    rebuilt = WebUIStore(store.db_path)
    assert _accept(rebuilt, question)["status"] == "unknown"
    with pytest.raises(ValueError):
        rebuilt.claim_workspace_answer("owner-a", "answer-task", receipt["turn_id"])
    with pytest.raises(ValueError):
        _accept(rebuilt, question, idempotency_key="another-key")
    rebuilt.finish_workspace_answer("owner-a", "answer-task", receipt["turn_id"])
    assert _accept(WebUIStore(store.db_path), question)["status"] == "accepted"
    with pytest.raises(ValueError):
        rebuilt.claim_workspace_answer("owner-a", "answer-task", receipt["turn_id"])


def test_cancellation_generation_invalidates_unsent_accepted_answer(waiting):
    store, question = waiting
    receipt = _accept(store, question)
    store.request_semantic_workspace_cancellation("owner-a", "answer-task")
    with pytest.raises(ValueError):
        WebUIStore(store.db_path).claim_workspace_answer("owner-a", "answer-task", receipt["turn_id"])
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM semantic_workspace_events WHERE event_type='clarification.send_claimed'").fetchone()[0] == 0


@pytest.mark.parametrize("answer", ["confirm", "local"])
def test_external_answer_cannot_override_a_later_cancellation(waiting, monkeypatch, answer):
    import asyncio
    from src.api import semantic_workspace_runtime as runtime

    store, _ = waiting
    question = store.publish_workspace_question("owner-a", "answer-task", {
        "kind": "external", "question_id": "external.permission", "prompt": "是否允许外发？",
        "options": [{"value": value, "label": value} for value in ("confirm", "local", "cancel")],
        "allow_free_text": False, "purpose": "authorization", "continuation": "unavailable",
    })
    receipt = _accept(store, question, answer=answer)
    store.request_semantic_workspace_cancellation("owner-a", "answer-task")
    store.update_semantic_workspace_task("owner-a", "answer-task", status="cancelled", cancel_requested=True)
    stopped = store.get_semantic_workspace_task("owner-a", "answer-task")
    monkeypatch.setattr(runtime, "get_store", lambda: store)
    monkeypatch.setattr(runtime.settings, "webui_db_path", store.db_path)
    manager = runtime.SemanticWorkspaceManager()
    queued = []
    monkeypatch.setattr(manager, "enqueue", lambda *args: queued.append(args))
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(manager.answer("owner-a", "answer-task", answer, accepted_turn_id=receipt["turn_id"]))
    assert store.get_semantic_workspace_task("owner-a", "answer-task") == stopped
    assert queued == []
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM semantic_workspace_events WHERE event_type='clarification.consumed'").fetchone()[0] == 0
