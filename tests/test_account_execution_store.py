"""账号停用与业务提交共用事务；所有账号和数据库均为临时虚构数据。"""
import pytest

from src import account_execution as execution
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def accounts(tmp_path):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "account-store.db")))
    owners = [store.create_user(name, "synthetic-hash", pending=False)["user_id"] for name in ("synthetic-a", "synthetic-b")]
    with store._conn() as conn:
        frozen = [execution.capture_authorization(conn, owner) for owner in owners]
    return store, frozen


def create_task(store, auth, task_id="synthetic-task"):
    with execution.execution_context(auth):
        return store.create_semantic_workspace_task(auth.owner_user_id, task_id=task_id, title="虚构", objective_text="虚构", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)


@pytest.mark.parametrize("flag", ["disabled", "pending"])
def test_old_authenticated_first_insert_cannot_survive_disable_and_reenable(accounts, flag):
    store, (old, other) = accounts
    store.update_user(old.owner_user_id, **{flag: True}, actor_user_id=other.owner_user_id)
    store.update_user(old.owner_user_id, **{flag: False}, actor_user_id=other.owner_user_id)
    with pytest.raises(execution.ExecutionDenied):
        create_task(store, old)
    assert store.get_semantic_workspace_task(old.owner_user_id, "synthetic-task") is None
    assert create_task(store, other)["status"] == "queued"


def test_late_result_cannot_overwrite_paused_workspace(accounts):
    store, (old, other) = accounts
    create_task(store, old)
    store.update_user(old.owner_user_id, disabled=True, actor_user_id=other.owner_user_id)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        store.update_semantic_workspace_task(old.owner_user_id, "synthetic-task", status="completed", summary="迟到结果")
    assert store.get_semantic_workspace_task(old.owner_user_id, "synthetic-task")["summary"] == ""


def test_harness_attempt_commit_rejects_old_generation_and_preserves_history(accounts):
    from src.semantic_harness.harness_models import HarnessRun

    store, (old, _) = accounts
    run = HarnessRun(run_id="synthetic-run", user_id=old.owner_user_id,
                     thread_id="synthetic-run", logical_plan_id="synthetic-plan",
                     logical_plan_revision=1, logical_plan_hash="0" * 64,
                     binding_revision=1, binding_hash="1" * 64,
                     capability_id="table.duckdb", capability_version="1.0.0",
                     runtime_profile="windows_local")
    with execution.execution_context(old):
        store.create_semantic_harness_run(run)
        store.save_semantic_harness_attempt(old.owner_user_id, run.run_id,
            attempt_id="original", node="execute", attempt_number=1,
            idempotency_key="original", input_hash="2" * 64, status="succeeded")
    store.update_user(old.owner_user_id, disabled=True)
    store.update_user(old.owner_user_id, disabled=False)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        store.save_semantic_harness_attempt(old.owner_user_id, run.run_id,
            attempt_id="late", node="execute", attempt_number=2,
            idempotency_key="late", input_hash="3" * 64, status="succeeded")
    attempts = store.list_semantic_harness_attempts(old.owner_user_id, run.run_id)
    assert [attempt["attempt_id"] for attempt in attempts] == ["original"]


def test_password_change_revokes_sessions_without_revoking_execution(accounts):
    store, (old, other) = accounts
    create_task(store, old)
    store.update_user(old.owner_user_id, password_hash="another-synthetic-hash", actor_user_id=other.owner_user_id)
    with execution.execution_context(old):
        assert store.update_semantic_workspace_task(old.owner_user_id, "synthetic-task", status="completed")["status"] == "completed"
    assert store.get_user(old.owner_user_id)["execution_generation"] == old.generation


def test_workspace_insert_and_binding_rollback_together(accounts):
    store, (auth, _) = accounts
    def fail(conn):
        raise RuntimeError("虚构下游提交失败")
    with execution.execution_context(auth), pytest.raises(RuntimeError):
        store.create_semantic_workspace_task(auth.owner_user_id, task_id="rolled-back", title="虚构", objective_text="虚构", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False, transaction_hook=fail)
    assert store.get_semantic_workspace_task(auth.owner_user_id, "rolled-back") is None
    with store._conn() as conn:
        assert execution.list_execution_bindings(conn, auth.owner_user_id) == []


def test_deleted_owner_cannot_continue_existing_execution(accounts):
    store, (old, _) = accounts
    create_task(store, old)
    store.delete_user(old.owner_user_id)
    assert store.get_user(old.owner_user_id) is None
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        store.update_semantic_workspace_task(old.owner_user_id, "synthetic-task", status="completed")
    assert store.account_execution_binding(old.owner_user_id, "workspace", "synthetic-task") is not None


@pytest.mark.parametrize("changes", [{"status": "cancelled", "summary": "迟到业务正文"}, {"cancel_requested": False}, {"deleted_at": None, "purge_after": None}])
def test_old_request_cannot_hide_business_write_in_cleanup_fields(accounts, changes):
    store, (old, _) = accounts
    create_task(store, old)
    store.update_user(old.owner_user_id, disabled=True)
    store.update_user(old.owner_user_id, disabled=False)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        store.update_semantic_workspace_task(old.owner_user_id, "synthetic-task", **changes)


def test_old_revision_result_cannot_hide_under_cancelled_status(accounts):
    store, (old, _) = accounts
    create_task(store, old)
    store.update_user(old.owner_user_id, disabled=True)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        store.update_semantic_workspace_revision(old.owner_user_id, "synthetic-task", 1, status="cancelled", run_id="late-run")


def test_frozen_old_cleanup_is_allowed_until_binding_explicitly_resumes(accounts):
    store, (old, _) = accounts
    create_task(store, old)
    store.update_user(old.owner_user_id, disabled=True)
    with execution.execution_context(old):
        store.update_semantic_workspace_task(old.owner_user_id, "synthetic-task", status="cancelling", cancel_requested=True)
        store.update_semantic_workspace_revision(old.owner_user_id, "synthetic-task", 1, status="cancelling")
    store.confirm_account_execution_stopped(old.owner_user_id, "workspace", "synthetic-task", old.generation)
    store.update_account_workspace_state(old.owner_user_id, "synthetic-task", old.generation, "paused")
    store.update_user(old.owner_user_id, disabled=False)
    current = store.capture_account_execution(old.owner_user_id)
    with execution.execution_context(current):
        store.resume_account_workspace_execution(current.owner_user_id, "synthetic-task", old.generation, "queued", expected_active_revision=1)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        store.update_semantic_workspace_task(old.owner_user_id, "synthetic-task", status="cancelled", question=None, failure=None)
    assert store.get_semantic_workspace_task(old.owner_user_id, "synthetic-task")["status"] == "queued"


def test_failed_revision_hook_rolls_back_explicit_account_resume(accounts):
    store, (old, _) = accounts
    create_task(store, old)
    store.update_user(old.owner_user_id, disabled=True)
    store.confirm_account_execution_stopped(old.owner_user_id, "workspace", "synthetic-task", old.generation)
    store.update_account_workspace_state(old.owner_user_id, "synthetic-task", old.generation, "paused")
    store.update_user(old.owner_user_id, disabled=False)
    current = store.capture_account_execution(old.owner_user_id)
    def fail(conn):
        raise RuntimeError("虚构冻结合同失败")
    with execution.execution_context(current), pytest.raises(RuntimeError):
        store.create_semantic_workspace_revision(current.owner_user_id, "synthetic-task", objective_text="虚构新版本", output_formats=[], change_summary="虚构恢复", expected_revision=2, account_resume_generation=old.generation, transaction_hook=fail)
    binding = store.account_execution_binding(old.owner_user_id, "workspace", "synthetic-task")
    assert binding["generation"] == old.generation and binding["state"] == "paused"
    task = store.get_semantic_workspace_task(old.owner_user_id, "synthetic-task")
    assert task["active_revision"] == 1 and task["status"] == "paused"
