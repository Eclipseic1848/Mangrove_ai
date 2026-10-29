"""旧 Chat 的恢复与冻结 Grant 均不能通过新执行身份绕过补充隔离。"""
from contextlib import closing
import asyncio
import sqlite3

from fastapi import HTTPException
import httpx
import pytest

from src.api.routes import chat
from src.deployment_quarantine import CHAT_ACCEPTED_RISK, QuarantinedExecutionError, register_chat_quarantine, snapshot_record
from tests.test_account_execution_grants import grants, broker_and_grant, relay


def isolate(store):
    with closing(sqlite3.connect(store.db_path)) as connection, connection:
        connection.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) VALUES ('operator','synthetic-operator','synthetic','super_admin','before')")
        connection.executemany("INSERT INTO account_execution_bindings VALUES ('synthetic-owner','chat',?,0,'cleanup_failed',1)",
                               [('synthetic-run',), ('synthetic-other-run',)])
        targets = [{'owner_id': 'synthetic-owner', 'kind': 'chat', 'resource_id': run, 'generation': 0}
                   for run in ('synthetic-run', 'synthetic-other-run')]
        register_chat_quarantine(connection, {
            'version': 1, 'approval_reference': 'synthetic-chat-isolation', 'actor_id': 'operator',
            'accepted_risk': CHAT_ACCEPTED_RISK, 'targets': targets,
            'records': [snapshot_record(connection, 'webui', 'account_execution_bindings',
                                        {'owner_user_id': 'synthetic-owner', 'resource_kind': 'chat', 'resource_id': target['resource_id']})
                        for target in targets],
        })


def test_recovery_checks_original_chat_before_a_new_chat_binding_is_created(grants):
    store, _, _, _ = grants
    conv = store.create_conversation('synthetic-owner', '合成会话')['conv_id']
    recovery = {'task_id': 'synthetic-collection',
                'state': {'session_id': conv, 'model': 'synthetic-model'},
                'saved': {'binding_fields': {'owner': 'synthetic-owner', 'task': 'synthetic-collection',
                    'connection': ('synthetic-owner', 'connection', 'v1', 'synthetic-model', conv, 'synthetic-run', 1)}}}
    isolate(store)
    with pytest.raises(HTTPException) as error:
        chat._require_recovery_unquarantined(store, 'synthetic-owner', recovery)
    assert error.value.status_code == 409
    recovery['saved']['binding_fields']['connection'] = ('synthetic-owner', 'connection', 'v1', 'synthetic-model', conv, 'new-independent-run', 1)
    chat._require_recovery_unquarantined(store, 'synthetic-owner', recovery)
    assert store.list_messages(conv) == []


def test_old_chat_cannot_issue_a_new_grant_with_its_original_run(grants):
    store, _, _, _ = grants
    isolate(store)
    with pytest.raises(QuarantinedExecutionError):
        broker_and_grant(grants, lambda _: httpx.Response(200))


def test_existing_chat_grant_cannot_send_after_isolation(grants):
    store, _, _, _ = grants
    sent = []
    broker, grant = broker_and_grant(grants, lambda _: (sent.append(True), httpx.Response(200, content=b'synthetic'))[1])
    isolate(store)
    with pytest.raises(QuarantinedExecutionError):
        asyncio.run(relay(broker, grant))
    assert sent == []
