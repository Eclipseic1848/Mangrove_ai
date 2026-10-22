"""文档授权的 Run 收尾只释放内存，保留当前结果和持久证据。"""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agentic_runtime.document_tools import DocumentToolBroker, DocumentToolError
from src.agentic_runtime.models import PiRuntimeCheckpoint, PiRuntimeRequest, SourceInput
from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError
from src.agentic_runtime.repository import AgenticRuntimeRepository
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.asyncio
async def test_runtime_close_releases_document_grant_after_result_is_read(tmp_path: Path):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"synthetic-document")
    broker = DocumentToolBroker(retriever=SimpleNamespace())
    runtime = PiRuntime(
        state_store=AgenticRuntimeRepository(migrated_webui_database(tmp_path / "runtime.db")),
        execution_root=tmp_path / "executions",
        document_tool_broker=broker,
        configure_as_default_document_broker=False,
    )
    key = ("owner-a", "task-a", 1)
    grant = broker.issue_grant(
        owner_user_id=key[0], task_id=key[1], revision=key[2], run_id="run-a",
        sources=(SourceInput(upload_id="source-a", original_name="source.pdf", host_path=source_path,
                             sha256="a" * 64, media_type="application/pdf"),),
    )
    runtime._document_grants[key] = grant
    await broker.call(grant_token=grant.token, operation="request_clarification",
                      payload={"question": "请选择页面", "reason": "缺少页码"})
    assert broker.clarification_state(grant.grant_id)["question"] == "请选择页面"

    await runtime._release_supporting_resources(key, reason="run_closed", cancel_host=False)

    assert not runtime._document_grants
    assert not broker._grants and not broker._grant_keys
    assert not broker._active_tasks and not broker._cancel_events
    assert broker.clarification_state(grant.grant_id) is None
    with pytest.raises(DocumentToolError):
        await broker.call(grant_token=grant.token, operation="request_clarification",
                          payload={"question": "不能重放", "reason": "旧Token"})


@pytest.mark.asyncio
async def test_document_release_failure_keeps_runtime_identity_for_retry(tmp_path, monkeypatch):
    broker = DocumentToolBroker(retriever=SimpleNamespace())
    runtime = PiRuntime(
        state_store=AgenticRuntimeRepository(migrated_webui_database(tmp_path / "runtime.db")),
        execution_root=tmp_path / "executions", document_tool_broker=broker,
        configure_as_default_document_broker=False,
    )
    key = ("owner-a", "task-a", 1)
    source = SourceInput(upload_id="source-a", original_name="source.pdf", host_path=tmp_path / "source.pdf",
                         sha256="a" * 64, media_type="application/pdf")
    grant = broker.issue_grant(owner_user_id=key[0], task_id=key[1], revision=key[2],
                               run_id="run-a", sources=(source,))
    runtime._document_grants[key] = grant
    release = broker.release_grant

    async def failed_release(*args, **kwargs):
        raise RuntimeError("合成收尾失败")

    monkeypatch.setattr(broker, "release_grant", failed_release)
    with pytest.raises(PiRuntimeError, match="合成收尾失败"):
        await runtime._release_supporting_resources(key, reason="run_closed", cancel_host=False)
    assert runtime._document_grants[key] is grant
    monkeypatch.setattr(broker, "release_grant", release)
    await runtime._release_supporting_resources(key, reason="run_closed", cancel_host=False)
    assert not runtime._document_grants and not broker._grants and not broker._grant_keys


@pytest.mark.asyncio
@pytest.mark.parametrize("model_revoke_fails", [False, True])
async def test_resume_releases_old_document_grants_even_if_model_revoke_fails(tmp_path, monkeypatch, model_revoke_fails):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"synthetic-document")
    source = SourceInput(upload_id="source-a", original_name="source.pdf", host_path=source_path,
                         sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(), media_type="application/pdf")
    broker = DocumentToolBroker(retriever=SimpleNamespace())

    def revoke_models(*args, **kwargs):
        if model_revoke_fails:
            raise RuntimeError("合成模型撤权失败")

    runtime = PiRuntime(
        state_store=AgenticRuntimeRepository(migrated_webui_database(tmp_path / "runtime.db")),
        execution_root=tmp_path / "executions", document_tool_broker=broker,
        connection_broker=SimpleNamespace(revoke_run_grants=revoke_models),
        configure_as_default_document_broker=False,
    )
    request = PiRuntimeRequest(user_id="owner-a", task_id="task-a", revision=1,
        objective_text="合成恢复", requested_output_formats=("json",), sources=(source,),
        model_connection_id="connection-a", model_connection_version="synthetic-version-1",
        model_connection_model="synthetic", external_api_confirmed=True)
    run_id = "pi_run_1234567890abcdef"
    root = runtime.execution_root / "agentic-vnext" / hashlib.sha256(b"owner-a").hexdigest()[:16] / "task-a" / "r1" / run_id
    for directory in ("input", "work", "output", "session", "config", "trace"):
        (root / directory).mkdir(parents=True)
    (root / "input" / "source.pdf").write_bytes(source_path.read_bytes())
    old = broker.issue_grant(owner_user_id="owner-a", task_id="task-a", revision=1, run_id=run_id, sources=(source,))
    broker.issue_grant(owner_user_id="owner-a", task_id="task-a", revision=1, run_id=run_id, sources=(source,))
    runtime._document_grants[("owner-a", "task-a", 1)] = old
    other = broker.issue_grant(owner_user_id="owner-a", task_id="task-a", revision=1, run_id="other-run", sources=(source,))
    monkeypatch.setattr(runtime, "_assert_image", AsyncMock())
    restarted = object()
    monkeypatch.setattr(runtime, "start", AsyncMock(return_value=restarted))
    operation = runtime.resume(request, checkpoint=PiRuntimeCheckpoint(run_id=run_id, workspace_root=root), on_event=AsyncMock())
    if model_revoke_fails:
        with pytest.raises(RuntimeError, match="合成模型撤权失败"):
            await operation
        runtime.start.assert_not_awaited()
    else:
        assert await operation is restarted
    assert not runtime._document_grants
    assert set(broker._grant_keys) == {other.grant_id}
    with pytest.raises(DocumentToolError):
        await broker.call(grant_token=old.token, operation="request_clarification", payload={})
