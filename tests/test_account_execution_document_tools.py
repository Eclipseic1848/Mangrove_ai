"""真实账号代数保护文档 Grant 与迟到检索结果。"""
import asyncio
from contextvars import Context

import pytest

from src.account_execution import execution_context
from src.agentic_runtime.document_tools import DocumentToolBroker, DocumentToolError
from src.agentic_runtime.models import SourceInput
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_document_tool_relay import InspectAdapter


@pytest.fixture
def document(tmp_path):
    database = migrated_webui_database(tmp_path / "document.db")
    auth = seed_execution_owner(database, "document-owner")
    other = seed_execution_owner(database, "other-owner")
    source = SourceInput(upload_id="upload-a", original_name="fake.pdf", host_path=tmp_path / "fake.pdf",
                         sha256="a" * 64, media_type="application/pdf")
    return WebUIStore(str(database)), AgenticRuntimeRepository(database), auth, other, source


def issue(broker, auth, source):
    with execution_context(auth):
        return broker.issue_grant(owner_user_id=auth.owner_user_id, task_id="fake-task", revision=1,
                                  run_id="fake-run", sources=(source,))


def inspect(broker, grant):
    return broker.call(grant_token=grant.token, operation="inspect_source", payload={"source_id": "upload-a"})


def test_stale_authorization_cannot_issue_document_grant(document):
    store, repository, auth, _, source = document
    broker = DocumentToolBroker(retriever=InspectAdapter(), state_store=repository)
    store.update_user(auth.owner_user_id, disabled=True)
    store.update_user(auth.owner_user_id, disabled=False)
    with pytest.raises(DocumentToolError):
        issue(broker, auth, source)


@pytest.mark.asyncio
async def test_old_document_grant_rejected_without_request_context_after_reenable(document):
    store, repository, auth, other, source = document
    broker = DocumentToolBroker(retriever=InspectAdapter(), state_store=repository)
    grant = issue(broker, auth, source)
    store.update_user(auth.owner_user_id, disabled=True)
    store.update_user(auth.owner_user_id, disabled=False)
    with pytest.raises(DocumentToolError):
        await Context().run(asyncio.create_task, inspect(broker, grant))
    fresh = issue(broker, other, source)
    result = await Context().run(asyncio.create_task, inspect(broker, fresh))
    assert result["source_id"] == "upload-a"


@pytest.mark.asyncio
async def test_document_late_result_is_not_returned_or_retained(document):
    store, repository, auth, _, source = document
    entered, release = asyncio.Event(), asyncio.Event()

    class Barrier(InspectAdapter):
        async def inspect(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await super().inspect(*args, **kwargs)

    broker = DocumentToolBroker(retriever=Barrier(), state_store=repository)
    grant = issue(broker, auth, source)
    task = Context().run(asyncio.create_task, inspect(broker, grant))
    await entered.wait()
    store.update_user(auth.owner_user_id, pending=True)
    release.set()
    with pytest.raises(DocumentToolError):
        await task
    assert not next(iter(broker._grants.values())).inspected_units


def test_document_http_relay_uses_frozen_owner_without_cookie(document):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes import document_tools as routes
    from tests.test_document_tool_relay import _relay_headers

    store, repository, auth, other, source = document
    broker = DocumentToolBroker(retriever=InspectAdapter(), state_store=repository)
    grant = issue(broker, auth, source)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_document_tool_broker] = lambda: broker
    with TestClient(app) as client:
        assert client.post("/internal/document-tools/inspect_source", headers=_relay_headers(grant),
                           json={"source_id": "upload-a"}).status_code == 200
        store.update_user(auth.owner_user_id, disabled=True)
        store.update_user(auth.owner_user_id, disabled=False)
        assert client.post("/internal/document-tools/inspect_source", headers=_relay_headers(grant),
                           json={"source_id": "upload-a"}).status_code == 403
        fresh = issue(broker, other, source)
        assert client.post("/internal/document-tools/inspect_source", headers=_relay_headers(fresh),
                           json={"source_id": "upload-a"}).status_code == 200


@pytest.mark.asyncio
async def test_document_ledger_rechecks_account_in_actual_commit(document, monkeypatch):
    store, repository, auth, _, source = document
    broker = DocumentToolBroker(retriever=InspectAdapter(), state_store=repository)
    grant = issue(broker, auth, source)
    await inspect(broker, grant)
    original = repository.save_coverage

    def before_commit(**kwargs):
        store.update_user(auth.owner_user_id, disabled=True)
        store.update_user(auth.owner_user_id, disabled=False)
        return original(**kwargs)

    monkeypatch.setattr(repository, "save_coverage", before_commit)
    with pytest.raises(DocumentToolError):
        await broker.call(grant_token=grant.token, operation="freeze_coverage", payload={
            "authorized_scope": {"source_ids": ["upload-a"]}, "result_cardinality": "first",
            "completeness": "strict", "ordering": "页码升序", "required_fields": ["姓名"],
            "object_boundary": "完整单据", "stop_semantics": "首个完整单据字段齐全", "interpretation": "读取首个单据",
            "confidence": "high"})
    assert repository.get_coverage(user_id=auth.owner_user_id, task_id="fake-task", revision=1, run_id="fake-run") is None


@pytest.mark.asyncio
async def test_document_account_stop_waits_for_actual_retriever_thread(document):
    import threading
    from src.agentic_runtime.document_retrieval import DocumentRetrievalModule, _CANCEL_EVENT

    store, repository, auth, _, source = document
    entered, release, stopped, cancel_seen = (threading.Event() for _ in range(4))

    class AtomicRetriever(DocumentRetrievalModule):
        def _inspect(self, source, owner_key):
            entered.set()
            try:
                assert _CANCEL_EVENT.get().wait(3)
                cancel_seen.set()
                assert release.wait(3)
                return {"source_id": source.upload_id, "units": []}
            finally:
                stopped.set()

    broker = DocumentToolBroker(retriever=AtomicRetriever(document_clients=()), state_store=repository)
    grant = issue(broker, auth, source)
    task = Context().run(asyncio.create_task, inspect(broker, grant))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        store.update_user(auth.owner_user_id, disabled=True)
        assert await asyncio.to_thread(cancel_seen.wait, 2)
        assert not task.done()
        assert not stopped.is_set()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises((DocumentToolError, asyncio.CancelledError)):
            await task
    assert stopped.is_set()
    assert not broker._active_tasks[grant.grant_id]
    assert not next(iter(broker._grants.values())).inspected_units
