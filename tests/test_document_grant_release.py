"""文档授权释放的合成生命周期回归，不调用模型、Docker 或外部服务。"""
import asyncio
import hashlib
import threading

import pytest

from src.agentic_runtime.document_tools import DocumentToolBroker, DocumentToolError
from src.agentic_runtime.models import SourceInput
from src.agentic_runtime.repository import AgenticRuntimeRepository
from tests.database_migration_helpers import migrated_webui_database
from tests.test_document_tool_relay import InspectAdapter


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(b"synthetic-document-release-fixture")
    return SourceInput(upload_id="upload-a", original_name=path.name, host_path=path,
                       sha256=hashlib.sha256(path.read_bytes()).hexdigest(), media_type="application/pdf")


def issue(broker, source, owner="owner-a", task="task-a", revision=1, run="run-a"):
    return broker.issue_grant(owner_user_id=owner, task_id=task, revision=revision, run_id=run, sources=(source,))


async def inspect(broker, grant):
    return await broker.call(grant_token=grant.token, operation="inspect_source", payload={"source_id": "upload-a"})


def assert_released(broker):
    assert not broker._grants
    assert not broker._grant_keys
    assert not broker._active_tasks
    assert not broker._cancel_events


@pytest.mark.asyncio
async def test_release_scope_preserves_other_owner_task_revision_and_run(source):
    broker = DocumentToolBroker(retriever=InspectAdapter())
    targets = [issue(broker, source), issue(broker, source)]
    other_run = issue(broker, source, run="run-b")
    independent = [issue(broker, source, owner="owner-b"), issue(broker, source, task="task-b"),
                   issue(broker, source, revision=2)]

    await broker.release_grants("owner-a", "task-a", 1, run_id="run-a", reason="合成原 Run 已关闭")
    for grant in targets:
        with pytest.raises(DocumentToolError):
            await inspect(broker, grant)
    for grant in [other_run, *independent]:
        assert (await inspect(broker, grant))["source_id"] == "upload-a"

    await broker.release_grants("owner-a", "task-a", 1, reason="合成原修订已取消")
    with pytest.raises(DocumentToolError):
        await inspect(broker, other_run)
    for grant in independent:
        assert (await inspect(broker, grant))["source_id"] == "upload-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("failure_on_stop", [False, True])
async def test_release_waits_for_real_thread_despite_repeated_cancellation(source, batch, failure_on_stop):
    from src.agentic_runtime.document_retrieval import DocumentRetrievalModule, _CANCEL_EVENT

    entered, cancel_seen, allow_finish, stopped = (threading.Event() for _ in range(4))

    class BlockingRetriever(DocumentRetrievalModule):
        def _inspect(self, source, owner_key):
            entered.set()
            try:
                cancellation = _CANCEL_EVENT.get()
                assert cancellation is not None and cancellation.wait(5)
                cancel_seen.set()
                assert allow_finish.wait(5)
                if failure_on_stop:
                    raise RuntimeError("合成线程已收敛，但检索返回异常")
                return {"source_id": source.upload_id, "units": []}
            finally:
                stopped.set()

    broker = DocumentToolBroker(retriever=BlockingRetriever(document_clients=()))
    grants = [issue(broker, source)]
    if batch:
        grants.append(issue(broker, source))
    pending = asyncio.create_task(inspect(broker, grants[0]))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(
            broker.release_grants("owner-a", "task-a", 1, run_id="run-a", reason="合成范围停止") if batch else
            broker.release_grant(grants[0].grant_id, reason="合成单次停止")
        )
        assert await asyncio.to_thread(cancel_seen.wait, 2)
        for grant in grants:
            # 不启动第二个读取；用同步能力入口验证整批在等待前已全部撤权。
            with pytest.raises(DocumentToolError):
                await broker.call(grant_token=grant.token, operation="request_clarification",
                                  payload={"question": "合成问题", "reason": "合成范围"})
        for _ in range(2):
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
            assert not stopped.is_set()
            assert len(broker._grants) == len(grants)
            assert len(broker._grant_keys) == len(grants)
            assert grants[0].grant_id in broker._active_tasks
            assert grants[0].grant_id in broker._cancel_events
    finally:
        allow_finish.set()
        pending.cancel()
        waiting = [pending, closing] if closing is not None else [pending]
        await asyncio.wait_for(asyncio.gather(*waiting, return_exceptions=True), timeout=5)
    assert stopped.is_set()
    assert_released(broker)


@pytest.mark.asyncio
async def test_revoked_completion_stays_readable_until_release_and_coverage_restores(source, tmp_path):
    repository = AgenticRuntimeRepository(migrated_webui_database(tmp_path / "runtime.db"))
    broker = DocumentToolBroker(retriever=InspectAdapter(), state_store=repository)
    grant = issue(broker, source)
    await inspect(broker, grant)
    await broker.call(grant_token=grant.token, operation="freeze_coverage", payload={
        "authorized_scope": {"source_ids": ["upload-a"], "unit_ids": ["upload-a:page:1"]},
        "result_cardinality": "first", "completeness": "strict", "ordering": "页码升序",
        "required_fields": ["名称"], "object_boundary": "一条完整记录", "stop_semantics": "首条完整记录已读",
        "interpretation": "读取第一页首条完整记录", "confidence": "high",
    })
    await broker.call(grant_token=grant.token, operation="read_evidence",
                      payload={"source_id": "upload-a", "unit_ids": ["upload-a:page:1"], "needs": ["text"]})
    completed = broker.completion_state(grant.grant_id)
    assert completed is not None
    assert completed[1].authoritatively_read_unit_ids == ("upload-a:page:1",)
    broker.revoke_grant(grant.grant_id, "合成执行结束")
    assert broker.completion_state(grant.grant_id) == completed
    await broker.release_grant(grant.grant_id, reason="合成结果已消费")
    assert broker.completion_state(grant.grant_id) is None
    assert_released(broker)
    with pytest.raises(DocumentToolError):
        await inspect(broker, grant)
    assert repository.get_coverage(user_id="owner-a", task_id="task-a", revision=1, run_id="run-a") == completed
    resumed = DocumentToolBroker(retriever=InspectAdapter(), state_store=repository)
    replacement = issue(resumed, source)
    assert replacement.token != grant.token
    assert resumed.completion_state(replacement.grant_id) == completed
    await resumed.release_grant(replacement.grant_id, reason="合成恢复验收结束")


@pytest.mark.asyncio
async def test_clarification_is_consumed_before_release_without_retaining_old_token(source):
    broker = DocumentToolBroker(retriever=InspectAdapter())
    grant = issue(broker, source)
    await broker.call(grant_token=grant.token, operation="request_clarification",
                      payload={"question": "请选择需要的章节", "reason": "合成材料存在两种范围"})
    broker.revoke_grant(grant.grant_id, "合成等待用户输入")
    assert broker.clarification_state(grant.grant_id) == {
        "question": "请选择需要的章节", "reason": "合成材料存在两种范围",
    }
    await broker.release_grant(grant.grant_id, reason="合成澄清结果已消费")
    assert broker.clarification_state(grant.grant_id) is None
    assert_released(broker)
    with pytest.raises(DocumentToolError):
        await inspect(broker, grant)


@pytest.mark.asyncio
async def test_repeated_document_runs_do_not_retain_process_state(source):
    broker = DocumentToolBroker(retriever=InspectAdapter())
    for index in range(64):
        grant = issue(broker, source, run=f"synthetic-run-{index}")
        assert (await inspect(broker, grant))["source_id"] == "upload-a"
        await broker.release_grant(grant.grant_id, reason="合成单次运行结束")
        await broker.release_grant(grant.grant_id, reason="合成重复释放")
        assert_released(broker)
    await broker.release_grant("missing-grant", reason="合成不存在身份")
    assert_released(broker)
    with pytest.raises(DocumentToolError):
        await inspect(broker, grant)
