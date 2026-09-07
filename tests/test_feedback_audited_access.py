import json

import pytest

from src.api.store import WebUIStore
from src.database_migrations import DatabaseTarget, apply_migrations


@pytest.fixture
def feedback(tmp_path):
    database = tmp_path / 'webui.db'
    apply_migrations(DatabaseTarget('webui', database), tmp_path / 'backup')
    store = WebUIStore(str(database))
    owner = store.create_user('owner', 'unused')
    admin = store.create_user('admin', 'unused', role='admin')
    conv = store.create_conversation(owner['user_id'])['conv_id']
    store.add_message(conv, 'user', 'QUESTION_SENTINEL')
    message = store.add_message(conv, 'assistant', 'ANSWER_SENTINEL', meta={'model': 'SECRET_SENTINEL'})
    store.upsert_feedback(message, conv, owner['user_id'], 'down', '["未知私密理由"]', 'COMMENT_SENTINEL')
    return store, owner, admin, conv, message


def test_default_metadata_and_fixed_reasons(feedback):
    store, *_ = feedback
    payload = store.feedback_list()
    assert not any(word in json.dumps(payload) for word in ('SENTINEL', '未知私密理由'))
    assert payload['items'][0]['reasons'] == ['其他']
    assert payload['items'][0]['has_comment'] is True
    assert store.feedback_overview()['reason_counts'] == {'其他': 1}
    assert store.feedback_list(reason='其他')['total'] == 1
    with pytest.raises(ValueError):
        store.feedback_list(reason='私密')


def test_feedback_write_validates_actual_assistant_owner(feedback):
    store, owner, admin, conv, message = feedback
    other = store.create_conversation(admin['user_id'])['conv_id']
    other_message = store.add_message(other, 'assistant', 'OTHER_SECRET')
    with pytest.raises(ValueError):
        store.upsert_feedback(other_message, conv, owner['user_id'], 'down')
    with pytest.raises(ValueError):
        store.upsert_feedback(message - 1, conv, owner['user_id'], 'down')
    store.upsert_feedback(message, conv, owner['user_id'], 'up')
    assert store.list_feedback(conv, owner['user_id'])[message]['rating'] == 'up'


def test_audit_commits_real_content_and_rejects_changed_retry(feedback):
    store, owner, admin, conv, message = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    result = store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='retry-1')
    assert result['content']['answer'] == 'ANSWER_SENTINEL'
    assert result['event_id']
    assert store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='retry-1') == result
    with store._conn() as conn:
        record = dict(conn.execute('SELECT * FROM feedback_content_access').fetchone())
        assert record['owner_id'] == owner['user_id']
        assert 'SENTINEL' not in json.dumps(record)
    store.upsert_feedback(message, conv, owner['user_id'], 'down', comment='changed')
    with pytest.raises(ValueError, match='冲突'):
        store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='retry-1')


@pytest.mark.parametrize('role', ['admin', 'super_admin'])
def test_api_single_read_and_metadata_csv(feedback, role):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.auth import get_current_user, get_store
    from src.api.routes.feedback_routes import router
    store, owner, admin, *_ = feedback
    if role == 'super_admin':
        admin = store.create_user('super-admin', 'unused', role=role)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    # 路由的存储函数调用也指向同一显式迁移临时库。
    import src.api.routes.feedback_routes as routes
    from unittest.mock import patch
    app.dependency_overrides[get_current_user] = lambda: admin
    with patch.object(routes, 'get_store', lambda: store), TestClient(app) as client:
        fb_id = store.feedback_list()['items'][0]['id']
        response = client.post(f'/api/feedback/{fb_id}/audit-content', json={'reason': '排查反馈内容', 'idempotency_key': 'api-1'})
        assert response.status_code == 200
        assert response.json()['content']['answer'] == 'ANSWER_SENTINEL'
        assert response.headers.get('Cache-Control') == 'no-store'
        for path in ('list', 'overview'):
            metadata = client.get('/api/feedback/' + path)
            assert metadata.status_code == 200
            assert 'SENTINEL' not in metadata.text
        assert 'SENTINEL' not in client.get('/api/feedback/export').text
        assert client.get('/api/feedback/list', params={'reason': '%'}).status_code == 400
        assert client.patch(f'/api/feedback/{fb_id}', json={'admin_note': 'new-note'}).status_code == 200
        assert store.feedback_list()['items'][0]['status'] == 'pending'
        app.dependency_overrides[get_current_user] = lambda: owner
        for path in ('list', 'overview', 'export'):
            assert client.get('/api/feedback/' + path).status_code == 403
        assert client.post(f'/api/feedback/{fb_id}/audit-content', json={'reason': '排查反馈内容', 'idempotency_key': 'api-2'}).status_code == 403


def test_note_omission_preserves_and_old_note_requires_real_audit(feedback):
    store, owner, admin, *_ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    with store._conn() as conn:
        conn.execute('UPDATE message_feedback SET admin_note=? WHERE id=?', ('OLD_NOTE', fb_id))
    store.update_feedback_status(fb_id, 'resolved')
    assert store.feedback_list()['items'][0]['has_admin_note'] is True
    with pytest.raises(PermissionError):
        store.update_feedback_status(fb_id, 'resolved', None, actor_id=admin['user_id'])
    store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈备注', idempotency_key='note')
    store.update_feedback_status(fb_id, 'resolved', None, actor_id=admin['user_id'])
    assert store.feedback_list()['items'][0]['has_admin_note'] is False


@pytest.mark.parametrize('role', ['user', 'admin', 'super_admin'])
def test_roles_broken_links_and_reason_conflict(feedback, role):
    store, owner, _, conv, message = feedback
    actor = store.create_user('actor', 'unused', role=role)
    fb_id = store.feedback_list()['items'][0]['id']
    args = dict(actor_id=actor['user_id'], reason='排查反馈内容', idempotency_key='role')
    if role == 'user':
        with pytest.raises(PermissionError):
            store.audit_feedback_content(fb_id, **args)
        return
    store.audit_feedback_content(fb_id, **args)
    with pytest.raises(ValueError, match='冲突'):
        store.audit_feedback_content(fb_id, **{**args, 'reason': '其他明确查看理由'})
    with store._conn() as conn:
        conn.execute('UPDATE message_feedback SET conv_id=? WHERE id=?', ('broken', fb_id))
    assert store.feedback_list()['items'][0]['content_available'] is False
    with pytest.raises(LookupError):
        store.audit_feedback_content(fb_id, **{**args, 'idempotency_key': 'broken'})


def test_audit_is_immutable_and_survives_business_deletion(feedback):
    import sqlite3
    store, _, admin, *_ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='immutable')
    for statement in ('DELETE FROM feedback_content_access', "UPDATE feedback_content_access SET reason='changed'",
                      'INSERT OR REPLACE INTO feedback_content_access SELECT * FROM feedback_content_access'):
        with pytest.raises(sqlite3.IntegrityError), store._conn() as conn:
            conn.execute(statement)
    store.delete_feedback_admin(fb_id)
    with store._conn() as conn:
        assert conn.execute('SELECT count(*) FROM feedback_content_access').fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['insert', 'commit'])
def test_audit_failure_returns_no_body_and_rolls_back(feedback, monkeypatch, failure):
    from fastapi import HTTPException
    import src.api.routes.feedback_routes as routes
    store, _, admin, *_ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    original_conn = store._conn
    if failure == 'insert':
        with original_conn() as conn:
            conn.execute("CREATE TRIGGER fail_access BEFORE INSERT ON feedback_content_access BEGIN SELECT RAISE(ABORT,'SECRET_SENTINEL'); END")
    else:
        # 延迟外键在真实 conn.commit 时失败，而不是模拟成功前的普通异常。
        with original_conn() as conn:
            conn.execute('CREATE TABLE fail_parent(id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE fail_child(parent_id INTEGER REFERENCES fail_parent(id) DEFERRABLE INITIALLY DEFERRED)')
            conn.execute('CREATE TRIGGER fail_access AFTER INSERT ON feedback_content_access BEGIN INSERT INTO fail_child VALUES (999); END')
    monkeypatch.setattr(routes, 'get_store', lambda: store)
    with pytest.raises(HTTPException) as exc:
        routes.audit_feedback(fb_id, routes.FeedbackAuditIn(reason='排查反馈内容', idempotency_key='failure'), admin)
    assert exc.value.status_code == 503
    assert 'SENTINEL' not in str(exc.value.detail)
    with original_conn() as conn:
        assert conn.execute('SELECT count(*) FROM feedback_content_access').fetchone()[0] == 0


def test_bounded_actual_json_and_concurrent_idempotency(feedback):
    from concurrent.futures import ThreadPoolExecutor
    from src.api.feedback_audit import encoded, CONTENT_LIMIT
    store, _, admin, _, message = feedback
    with store._conn() as conn:
        conn.execute('UPDATE messages SET content=? WHERE id=?', ('中文🙂\n"' * 300000, message))
    fb_id = store.feedback_list()['items'][0]['id']
    def read():
        # 不同Store模拟独立请求连接，不能只靠进程内同一把锁。
        other = WebUIStore(store.db_path)
        return other.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='concurrent')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: read(), range(2)))
    assert first == second
    assert first['truncated'] is True
    assert len(encoded(first)) <= CONTENT_LIMIT
    assert first['content_bytes'] == len(encoded(first['content']))
    with store._conn() as conn:
        assert conn.execute('SELECT count(*) FROM feedback_content_access').fetchone()[0] == 1


def test_truncated_note_cannot_overwrite_full_old_note(feedback):
    store, _, admin, *_ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    with store._conn() as conn:
        conn.execute('UPDATE message_feedback SET admin_note=? WHERE id=?', ('大备注' * 800000, fb_id))
    response = store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈备注', idempotency_key='large-note')
    assert response['truncated'] is True
    with pytest.raises(PermissionError):
        store.update_feedback_status(fb_id, admin_note=response['content']['admin_note'], actor_id=admin['user_id'])


def test_failed_object_access_is_committed_and_cannot_retry_as_success(feedback):
    store, _, admin, conv, _ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    with store._conn() as conn:
        conn.execute('UPDATE message_feedback SET conv_id=? WHERE id=?', ('broken', fb_id))
    args = dict(actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='failed-object')
    with pytest.raises(LookupError):
        store.audit_feedback_content(fb_id, **args)
    with store._conn() as conn:
        row = conn.execute('SELECT result,failure_code,owner_id FROM feedback_content_access').fetchone()
        assert tuple(row) == ('failure', 'feedback_unavailable', None)
        conn.execute('UPDATE message_feedback SET conv_id=? WHERE id=?', (conv, fb_id))
    with pytest.raises(ValueError, match='冲突'):
        store.audit_feedback_content(fb_id, **args)


def test_missing_owner_is_not_a_readable_business_object(feedback):
    store, owner, admin, *_ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    with store._conn() as conn:
        conn.execute('DELETE FROM users WHERE user_id=?', (owner['user_id'],))
    assert store.feedback_list()['items'][0]['content_available'] is False
    with pytest.raises(LookupError):
        store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='orphan')


def test_idempotency_never_crosses_object_or_actor(feedback):
    store, owner, admin, conv, _ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    args = dict(actor_id=admin['user_id'], reason='排查反馈内容', idempotency_key='identity')
    first = store.audit_feedback_content(fb_id, **args)
    message = store.add_message(conv, 'assistant', 'different-object')
    store.upsert_feedback(message, conv, owner['user_id'], 'down')
    other_id = store.feedback_list()['items'][0]['id']
    with pytest.raises(ValueError, match='冲突'):
        store.audit_feedback_content(other_id, **args)
    other_admin = store.create_user('other-admin', 'unused', role='admin')
    second = store.audit_feedback_content(fb_id, **{**args, 'actor_id': other_admin['user_id']})
    assert second['event_id'] != first['event_id']
    assert second['content'] == first['content']


def test_reason_and_key_boundaries_fail_before_read(feedback):
    store, _, admin, *_ = feedback
    fb_id = store.feedback_list()['items'][0]['id']
    for reason, key in [('短', 'key'), ('字' * 1001, 'key'), ('排查反馈内容', ' '), ('排查反馈内容', 'k' * 129)]:
        with pytest.raises(ValueError):
            store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason=reason, idempotency_key=key)
    with store._conn() as conn:
        assert conn.execute('SELECT count(*) FROM feedback_content_access').fetchone()[0] == 0
