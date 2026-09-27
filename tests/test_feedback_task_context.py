"""管理员从反馈追溯原任务，必须绑定审计身份及原版本。"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.auth import get_current_user
from src.api.routes import feedback_routes, semantic_workspace
from src.api.workspace_draft_acceptance import accept_draft
from src.api.workspace_feedback import WorkspaceFeedbackIn, write_feedback
from tests.test_workspace_draft_acceptance import draft_task
from tests.test_feedback_management import api
from tests.test_feedback_audited_access import feedback


@pytest.mark.parametrize('rating', ['up', 'down'])
def test_context_and_complete_result_are_bound_to_feedback(draft_task, monkeypatch, rating):
    store, root, draft = draft_task
    result = asyncio.run(accept_draft(store=store, manager=None, output_root=root.parent,
        owner_id='a', task_id='t', source_revision=1, draft_id=draft['draft_id']))
    for module in (feedback_routes, semantic_workspace):
        monkeypatch.setattr(module, 'get_store', lambda: store)
    admin = store.create_user('reviewer', 'unused', role='admin')
    identity = [admin]
    write_feedback(store, 'a', 't', WorkspaceFeedbackIn(revision=result['revision'], rating=rating))
    fb_id = store.feedback_list()['items'][0]['id']
    app = FastAPI()
    app.include_router(feedback_routes.router)
    app.dependency_overrides[get_current_user] = lambda: identity[0]
    with TestClient(app) as client:
        audited = client.post(f'/api/feedback/{fb_id}/audit-content', json={
            'reason': '核对用户原任务与实际结果', 'idempotency_key': rating}).json()
        path = f'/api/feedback/{fb_id}/task-context'
        body = {'audit_event_id': audited['event_id']}
        response = client.post(path, json=body)
        assert response.status_code == 200
        context = response.json()
        assert context['revision'] == result['revision']
        outputs = [item for item in context['files'] if item['kind'] == 'output']
        assert outputs
        selection = {**body, 'file_id': outputs[0]['id']}
        preview = client.post(path, json={**selection, 'action': 'preview'})
        assert preview.status_code == 200
        assert '合成初稿' in preview.text
        download = client.post(path, json={**selection, 'action': 'download'})
        assert download.status_code == 200 and '合成初稿' in download.text
        assert download.headers['cache-control'] == 'no-store'
        inputs = [item for item in context['files'] if item['kind'] == 'input']
        assert inputs
        source = client.post(path, json={**body, 'file_id': inputs[0]['id'], 'action': 'download'})
        assert source.status_code == 200 and '.csv' in source.headers['content-disposition']
        with monkeypatch.context() as patch:
            from fastapi import HTTPException
            def unavailable(*args):
                raise HTTPException(409, '冻结来源不可用')
            patch.setattr(semantic_workspace, '_frozen_canvas_sources', unavailable)
            assert client.post(path, json=body).status_code == 200
            assert client.post(path, json={**selection, 'action': 'preview'}).status_code == 200
        assert str(root) not in response.text
        assert client.post(path, json={**selection, 'file_id': 'output:not-this-task', 'action': 'download'}).status_code == 404
        assert client.post(path, json={'audit_event_id': 'unknown'}).status_code == 403
        another = store.create_user('another-admin', 'unused', role='admin')
        identity[0] = another
        assert client.post(path, json=body).status_code == 403
        identity[0] = admin
        with store._conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM operations_events WHERE action='下载反馈关联文件'").fetchone()[0] == 3
            conn.execute("UPDATE users SET role='user' WHERE user_id=?", (admin['user_id'],))
        assert client.post(path, json=body).status_code == 403


def test_legacy_real_messages_files_and_paging(api, tmp_path, monkeypatch):
    import json
    from src.api.routes import downloads
    from src.config.settings import settings
    client, _, store, owner, admin, conv, message = api
    monkeypatch.setattr(downloads, 'get_store', lambda: store)
    monkeypatch.setattr(settings, 'data_prep_artifact_root', str(tmp_path))
    folder = tmp_path / 'legacy-task'
    folder.mkdir()
    (folder / 'report.md').write_text('真实报告，不是摘要', encoding='utf-8')
    with store._conn() as conn:
        conn.execute('UPDATE messages SET task_id=?,meta_json=?,content=? WHERE id=?', ('legacy-task',
            json.dumps({'files': [{'url': '/api/downloads/legacy-task/report.md'}]}), '实际回答' * 5000, message))
    fb_id = store.feedback_list()['items'][0]['id']
    event = client.post(f'/api/feedback/{fb_id}/audit-content', json={'reason': '核对旧会话真实结果', 'idempotency_key': 'legacy'}).json()['event_id']
    path = f'/api/feedback/{fb_id}/task-context'
    body = {'audit_event_id': event}
    context = client.post(path, json=body).json()
    assert context['total'] == 2
    assert context['files'][0]['name'] == 'report.md'
    question = client.post(path, json={**body, 'action': 'message', 'message_id': str(message - 1)}).json()
    assert question['text'] == 'QUESTION_SENTINEL'
    answer = client.post(path, json={**body, 'action': 'message', 'message_id': str(message), 'offset': 16000}).json()
    assert answer['total'] == 20000 and len(answer['text']) == 4000
    response = client.post(path, json={**body, 'action': 'download', 'file_id': context['files'][0]['id']})
    assert response.status_code == 200 and response.content.decode('utf-8') == '真实报告，不是摘要'
    with store._conn() as conn:
        conn.execute("UPDATE messages SET content=? WHERE id=?", ('正常内容' * 5000 + '<analysis>不可公开内容</analysis>', message))
    hidden = client.post(path, json={**body, 'action': 'message', 'message_id': str(message)}).json()
    assert '已隐藏' in hidden['text'] and '正常内容' not in hidden['text']
    assert client.post(path, json={**body, 'action': 'message', 'message_id': 'another-owner-message'}).status_code == 404


def test_audit_failure_never_returns_body(api, monkeypatch):
    import sqlite3
    from src import operations
    client, _, store, *_ = api
    fb_id = store.feedback_list()['items'][0]['id']
    event = client.post(f'/api/feedback/{fb_id}/audit-content', json={'reason': '验证审计失败关闭', 'idempotency_key': 'failure'}).json()['event_id']
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('模拟审计存储失败')
    monkeypatch.setattr(operations, 'finish', fail)
    response = client.post(f'/api/feedback/{fb_id}/task-context', json={'audit_event_id': event})
    assert response.status_code == 503 and 'SENTINEL' not in response.text
