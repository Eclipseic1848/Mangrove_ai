"""反馈管理公开接口的隔离回归。"""
import csv
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.auth import get_current_user, get_execution_user
from src.api.routes import chat, feedback_routes, semantic_workspace
from tests.test_feedback_audited_access import feedback
from tests.test_workspace_draft_acceptance import draft_task


@pytest.mark.parametrize('rating', ['up', 'down'])
def test_delivery_feedback_contains_verified_result(draft_task, monkeypatch, rating):
    import asyncio
    from src.api.workspace_draft_acceptance import accept_draft
    from src.api.workspace_feedback import WorkspaceFeedbackIn, write_feedback
    store, root, draft = draft_task
    result = asyncio.run(accept_draft(store=store, manager=None, output_root=root.parent,
        owner_id='a', task_id='t', source_revision=1, draft_id=draft['draft_id']))
    monkeypatch.setattr(semantic_workspace, 'get_store', lambda: store)
    admin = store.create_user('reviewer', 'unused', role='admin')
    write_feedback(store, 'a', 't', WorkspaceFeedbackIn(revision=result['revision'], rating=rating, comment='请核对结果'))
    fb_id = store.feedback_list()['items'][0]['id']
    data = store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='检查正式结果反馈', idempotency_key='delivery')
    assert '合成初稿' in data['content']['result_preview']
    assert '请核对结果' == data['content']['comment']
    assert str(root) not in str(data)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'latest_semantic_delivery', lambda *args: {'outputs': [{'filename': 'large.json', 'size_bytes': 9 * 1024 * 1024}]})
        patch.setattr(semantic_workspace, '_verified_canvas_output', lambda *args: pytest.fail('超限文件不能进入哈希或解析'))
        limited = store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='检查正式结果反馈', idempotency_key='large')
        assert '超过8MB' in limited['content']['result_preview']
        patch.setattr(store, 'latest_semantic_delivery', lambda *args: {'outputs': [{'filename': 'archive.xlsx', 'size_bytes': 100}]})
        office = store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='检查正式结果反馈', idempotency_key='office')
        assert '办公文档暂不支持' in office['content']['result_preview']
    output = store.latest_semantic_delivery('a', store.get_semantic_workspace_revision('a', 't', result['revision'])['run_id'])['outputs'][0]
    from pathlib import Path
    Path(store.get_semantic_delivery_output('a', output['output_id'])['file_path']).write_text('篡改结果', encoding='utf-8')
    changed = store.audit_feedback_content(fb_id, actor_id=admin['user_id'], reason='检查正式结果反馈', idempotency_key='changed')
    assert '篡改结果' not in str(changed)
    assert '暂不可预览' in changed['content']['result_preview']


@pytest.fixture
def api(feedback, monkeypatch):
    store, owner, admin, conv, message = feedback
    app = FastAPI()
    for module in (chat, feedback_routes, semantic_workspace):
        monkeypatch.setattr(module, 'get_store', lambda: store)
        app.include_router(module.router)
    identity = [admin]
    app.dependency_overrides[get_current_user] = lambda: identity[0]
    app.dependency_overrides[get_execution_user] = lambda: identity[0]
    with TestClient(app) as client:
        yield client, identity, store, owner, admin, conv, message


def test_search_and_beijing_date_range(api):
    client, _, store, owner, *_ = api
    assert client.get('/api/feedback/list', params={'q': 'owner'}).json()['total'] == 1
    assert client.get('/api/feedback/list', params={'q': '不存在的用户名'}).json()['total'] == 0
    assert client.get('/api/feedback/list', params={'q': '%'}).json()['total'] == 0
    assert client.get('/api/feedback/list', params={'user_id': owner['user_id']}).json()['total'] == 1
    with store._conn() as conn:
        conn.execute("UPDATE message_feedback SET created_at='2026-09-18T17:00:00+00:00'")
    params = {'date_from': '2026-09-19', 'date_to': '2026-09-19'}
    assert client.get('/api/feedback/list', params=params).json()['total'] == 1
    assert client.get('/api/feedback/list', params={'date_to': '2026-09-18'}).json()['total'] == 0
    assert client.get('/api/feedback/list', params={'date_from': '错误日期'}).status_code == 400
    assert client.get('/api/feedback/list', params={'date_from': '2026-09-20', 'date_to': '2026-09-19'}).status_code == 400


@pytest.mark.parametrize('status', ['fixed', 'no_change', 'deferred', 'resolved', 'ignored'])
def test_processing_requires_current_review_and_conclusion(api, status):
    client, _, store, *_ = api
    fb_id = store.feedback_list()['items'][0]['id']
    url = f'/api/feedback/{fb_id}'
    assert client.patch(url, json={'status': status, 'admin_note': '核对结果说明'}).status_code == 403
    assert client.post(url + '/audit-content', json={'reason': '核对原始任务与反馈', 'idempotency_key': 'review'}).status_code == 200
    assert client.patch(url, json={'status': status, 'admin_note': '  '}).status_code == 400
    assert client.patch(url, json={'status': status, 'admin_note': '核对结果说明'}).status_code == 200
    assert store.feedback_list(status=status)['total'] == 1
    if status in ('fixed', 'no_change', 'deferred'):
        assert client.post(url + '/audit-content', json={'reason': '复核已保存的处理结果', 'idempotency_key': 'review-saved'}).status_code == 200
        assert client.patch(url, json={'admin_note': None}).status_code == 400
        assert client.patch(url, json={'admin_note': ' '}).status_code == 400


def test_daily_statistics_use_beijing_calendar_and_thirty_days(api, monkeypatch):
    from datetime import datetime
    import src.api.store as store_module
    client, _, store, owner, _, conv, message = api
    monkeypatch.setattr(store_module, 'beijing_now', lambda: datetime.fromisoformat('2026-09-20T12:00:00+08:00'))
    with store._conn() as conn:
        conn.execute("UPDATE message_feedback SET created_at='2026-09-18T17:00:00+00:00'")
        conn.execute("INSERT INTO message_feedback(message_id,conv_id,user_id,rating,created_at) VALUES (?,?,?,'up','2020-01-01T00:00:00+08:00')", (message + 1, conv, owner['user_id']))
    data = client.get('/api/feedback/overview').json()
    assert data['daily'] == [{'date': '2026-09-19', 'up': 0, 'down': 1}]
    assert data['total_tasks'] == 0
    assert data['down_rate'] is None
    assert data['total_pending'] == 1
    assert client.get('/api/feedback/list', params={'status': 'pending'}).json()['total'] == 1
    assert client.get('/api/feedback/list', params={'rating': 'up', 'status': 'pending'}).json()['total'] == 0
    up = store.feedback_list(rating='up')['items'][0]
    assert client.patch(f"/api/feedback/{up['id']}", json={'status': 'fixed', 'admin_note': '无需处理点赞'}).status_code == 400
    assert store.feedback_list(rating='up')['total'] == 1


def test_down_rate_uses_all_owners_unique_tasks_not_feedback_count(api):
    client, _, store, owner, admin, conv, message = api
    with store._conn() as conn:
        for task_id, user_id, status in [('one', owner['user_id'], 'completed'), ('two', admin['user_id'], 'failed')]:
            conn.execute("INSERT INTO semantic_workspace_tasks(task_id,user_id,title,objective_text,status,created_at,updated_at) VALUES (?,?, '任务','需求',?,'now','now')", (task_id, user_id, status))
        conn.execute("INSERT INTO data_prep_tasks(task_id,user_id,spec_json,status,created_at,updated_at) VALUES ('one',?,'{}','SUCCEEDED','now','now')", (owner['user_id'],))
        conn.execute("UPDATE messages SET task_id='legacy' WHERE conv_id=?", (conv,))
    data = client.get('/api/feedback/overview').json()
    assert data['total_tasks'] == 3
    assert data['total_down'] == 1
    assert data['down_rate'] == pytest.approx(1 / 3)


@pytest.mark.parametrize('rating', ['up', 'down'])
def test_detail_includes_original_task_and_current_answer(api, rating):
    client, identity, store, owner, admin, *_ = api
    with store._conn() as conn:
        conn.execute("INSERT INTO semantic_workspace_tasks(task_id,user_id,title,objective_text,status,created_at,updated_at) VALUES ('task',?,'原始任务标题','原始任务要求','completed','now','now')", (owner['user_id'],))
        conn.execute("INSERT INTO semantic_workspace_revisions(task_id,revision,user_id,objective_text,status,summary,created_at,updated_at) VALUES ('task',1,?,'原始任务要求','completed','结果摘要','now','now')", (owner['user_id'],))
        conn.execute("INSERT INTO conversation_raw_turns(turn_id,owner_id,task_id,revision,text,created_at) VALUES ('turn',?,'task',1,'用户追问','now')", (owner['user_id'],))
        conn.execute("INSERT INTO conversation_steering_results(result_id,owner_id,task_id,turn_id,payload_json,created_at) VALUES ('reply',?,'task','turn','{\"revision\":1,\"answer\":\"当前回答\"}','now')", (owner['user_id'],))
    identity[0] = owner
    assert client.post('/api/semantic-workspace/tasks/task/feedback', json={'revision': 1, 'result_id': 'reply', 'rating': rating, 'comment': '用户补充说明'}).status_code == 200
    identity[0] = admin
    listing = client.get('/api/feedback/list').json()
    assert '原始任务要求' not in str(listing) and '用户补充说明' not in str(listing)
    detail = client.post(f"/api/feedback/{listing['items'][0]['id']}/audit-content", json={'reason': '分析反馈并改善回答质量', 'idempotency_key': 'detail'}).json()
    assert detail['content']['original_task'] == '原始任务要求'
    assert detail['content']['task_title'] == '原始任务标题'
    assert detail['content']['question'] == '用户追问'
    assert detail['content']['answer'] == '当前回答'
    assert detail['content']['comment'] == '用户补充说明'
    assert detail['context'] == {'task_id': 'task', 'revision': 1, 'result_id': 'reply', 'message_id': None, 'conv_id': None}


@pytest.mark.parametrize('workspace', [False, True])
def test_changed_feedback_reopens_but_identical_retry_preserves_processing(api, workspace):
    client, identity, store, owner, admin, conv, message = api
    if workspace:
        with store._conn() as conn:
            conn.execute("INSERT INTO semantic_workspace_tasks(task_id,user_id,title,objective_text,status,created_at,updated_at) VALUES ('state',?,'title','question','completed','now','now')", (owner['user_id'],))
            conn.execute("INSERT INTO semantic_workspace_revisions(task_id,revision,user_id,objective_text,status,summary,created_at,updated_at) VALUES ('state',1,?,'question','completed','answer','now','now')", (owner['user_id'],))
    path = '/api/semantic-workspace/tasks/state/feedback' if workspace else '/api/chat/feedback'
    body = {'revision': 1} if workspace else {'message_id': message, 'conv_id': conv}
    body.update(rating='down', reasons=['格式错误'], comment='缺少表头')
    identity[0] = owner
    assert client.post(path, json=body).status_code == 200
    identity[0] = admin
    before = client.get('/api/feedback/list').json()['items'][0]
    assert client.post(f"/api/feedback/{before['id']}/audit-content", json={'reason': '核对用户原始反馈', 'idempotency_key': 'state'}).status_code == 200
    assert client.patch(f"/api/feedback/{before['id']}", json={'status': 'resolved', 'admin_note': '已核对并修正格式'}).status_code == 200
    identity[0] = owner
    assert client.post(path, json=body).status_code == 200
    identity[0] = admin
    same = client.get('/api/feedback/list').json()['items'][0]
    assert same['status'] == 'resolved' and same['created_at'] == before['created_at']
    identity[0] = owner
    assert client.post(path, json={**body, 'comment': '表头和金额都错误'}).status_code == 200
    identity[0] = admin
    assert client.get('/api/feedback/list').json()['items'][0]['status'] == 'pending'


def test_limits_missing_objects_and_export_never_silently_truncates(api):
    client, identity, store, owner, admin, conv, message = api
    identity[0] = owner
    body = {'message_id': message, 'conv_id': conv, 'rating': 'down'}
    assert client.post('/api/chat/feedback', json={**body, 'comment': '字' * 5001}).status_code == 422
    assert client.post('/api/chat/feedback', json={**body, 'reasons': ['非法原因']}).status_code == 422
    identity[0] = admin
    fb_id = client.get('/api/feedback/list').json()['items'][0]['id']
    assert client.patch(f'/api/feedback/{fb_id}', json={'admin_note': '字' * 5001}).status_code == 422
    assert client.patch('/api/feedback/999999', json={'status': 'resolved'}).status_code == 404
    with store._conn() as conn:
        conn.executemany('INSERT INTO message_feedback(message_id,conv_id,user_id,rating,created_at) VALUES (?,?,?,?,?)',
                         [(message + n, conv, owner['user_id'], 'up', '2026-09-19T02:00:00+00:00') for n in range(1, 10001)])
    response = client.get('/api/feedback/export')
    assert response.status_code == 409
    assert '10000' in response.json()['detail']
    response = client.get('/api/feedback/export', params={'rating': 'down', 'q': 'owner'})
    assert len(list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))) == 1


def test_processing_audit_identifies_feedback_and_status_change(api, monkeypatch):
    from datetime import datetime
    from src.timezone import BEIJING
    from src.api import auth
    from src.api.operations_middleware import OperationsMiddleware
    from src.api.routes import operations
    _, _, store, _, admin, *_ = api
    monkeypatch.setattr(auth, 'access_identity', lambda request: (admin, {}, None))
    monkeypatch.setattr(operations, 'get_store', lambda: store)
    app = FastAPI()
    app.include_router(feedback_routes.router)
    app.include_router(operations.router)
    app.add_middleware(OperationsMiddleware, store_provider=lambda: store)
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as client:
        fb_id = client.get('/api/feedback/list').json()['items'][0]['id']
        assert client.post(f'/api/feedback/{fb_id}/audit-content', json={'reason': '核对用户原始反馈', 'idempotency_key': 'state'}).status_code == 200
        assert client.patch(f'/api/feedback/{fb_id}', json={'status': 'resolved', 'admin_note': '已核对并修正格式'}).status_code == 200
        assert client.delete(f'/api/feedback/{fb_id}').status_code == 200
        day = datetime.now(BEIJING).date().isoformat()
        events = client.post('/api/operations/events/query', json={'start': day, 'end': day, 'module': '反馈管理'}).json()['items']
        modified = next(event for event in events if event['action'] == '修改')
        detail = client.get(f"/api/operations/events/{modified['event_id']}").json()
        assert str(fb_id) in str(detail)
        assert 'pending' in str(detail) and 'resolved' in str(detail)
        assert all(event['object_ref'] == f'反馈:{fb_id}' for event in events if event['action'] in ('修改', '删除'))
