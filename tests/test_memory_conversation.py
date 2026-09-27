"""对话记忆经过公开接口和模型HTTP边界验证，禁止真实外发。"""
import json
from types import SimpleNamespace

import httpx
import pytest
from src.api import auth
from src.api.routes import memory_routes, semantic_workspace as routes
from src.conversation_steering import rewriter
from src.memory import loader
from tests.test_workspace_draft_chat import draft_api, payload


@pytest.fixture
def memory_chat(draft_api, tmp_path, monkeypatch):
    client, user, _ = draft_api
    monkeypatch.setattr(loader, 'MEMORY_DIR', tmp_path/'preferences')
    client.app.include_router(memory_routes.router)
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id':'memory-test', 'object':'chat.completion', 'created':0, 'model':'synthetic',
            'usage':{'prompt_tokens':8,'completion_tokens':2,'total_tokens':10},
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({
                'intent':'normalization','confidence':'high','normalized_text':'讨论报告',
                'direct_answer':'已按当前要求回答。','open_questions':[],
            },ensure_ascii=False)}}],
        })
    original = httpx.AsyncClient
    class Client(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))
    connection = SimpleNamespace(provider='local', model='synthetic', api_key='synthetic', base_url='http://127.0.0.1:9/v1', timeout=2, trust_env=False, extra_body=None)
    monkeypatch.setattr(rewriter, 'get_provider', lambda: SimpleNamespace(resolve_model=lambda *a, **kw: connection))
    monkeypatch.setattr(rewriter.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(routes, 'build_context_rewriter', rewriter.build_context_rewriter)
    yield client, user, seen


def test_new_conversation_recalls_only_owner_preferences_and_preserves_current_request(memory_chat):
    client, user, seen = memory_chat
    assert client.post('/api/memory/self',json={'text':'报告先写结论，再列明细'}).status_code == 200
    assert client.post('/api/memory',json={'text':'默认使用简体中文'}).status_code == 200
    user['user_id'] = 'owner-b'
    client.post('/api/memory/self',json={'text':'报告标题必须包含另一个账号的私有标记'})
    user['user_id'] = 'owner-a'
    response = client.post('/api/semantic-workspace/draft/turns',json=payload(text='请写一份周报，这次先列明细再写结论'))
    assert response.status_code == 200, response.text
    sent = json.dumps(seen[-1],ensure_ascii=False)
    assert '报告先写结论，再列明细' in sent
    assert '默认使用简体中文' in sent
    assert '另一个账号的私有标记' not in sent
    assert '这次先列明细再写结论' in sent
    assert '当前用户要求优先' in sent


def test_explicit_remember_and_exact_forget_persist_without_model(memory_chat):
    client, user, seen = memory_chat
    remember = payload(text='请记住：报告先写结论，再列明细')
    response = client.post('/api/semantic-workspace/draft/turns', json=remember)
    assert response.status_code == 200, response.text
    assert '已记住' in response.json()['reply']
    assert response.json()['token_usage'] == {'prompt_tokens':0, 'completion_tokens':0, 'total_tokens':0, 'calls':0, 'no_model_call':True}
    items = client.get('/api/memory').json()['personal']
    assert [item['text'] for item in items] == ['报告先写结论，再列明细']
    assert not seen
    assert client.post('/api/semantic-workspace/draft/turns', json=remember).status_code == 409
    user['user_id'] = 'owner-b'
    assert client.get('/api/memory').json()['personal'] == []
    user['user_id'] = 'owner-a'
    response = client.post('/api/semantic-workspace/draft/turns', json=payload(request_id='memory-forget-0002', text='请忘记：报告先写结论，再列明细'))
    assert response.status_code == 200, response.text
    assert '已忘记' in response.json()['reply']
    assert client.get('/api/memory').json()['personal'] == []
    assert not seen


@pytest.mark.parametrize('text', [
    '不要记住：报告先写结论', '如果我说“记住：报告先写结论”，会发生什么？',
    '解释这段文字：记住：报告先写结论', '记住是什么意思？',
    '请记住：报告先写结论，可以吗？',
])
def test_questions_negations_and_quoted_commands_never_write(memory_chat, text):
    client, _, seen = memory_chat
    response = client.post('/api/semantic-workspace/draft/turns', json=payload(text=text))
    assert response.status_code == 200, response.text
    assert client.get('/api/memory').json()['personal'] == []


def test_forget_does_not_match_fragments_or_other_accounts(memory_chat):
    client, user, _ = memory_chat
    client.post('/api/memory/self', json={'text':'报告先写结论，再列明细'})
    client.post('/api/semantic-workspace/draft/turns', json=payload(text='忘记：报告'))
    assert len(client.get('/api/memory').json()['personal']) == 1
    user['user_id'] = 'owner-b'
    client.post('/api/semantic-workspace/draft/turns', json=payload(text='忘记：报告先写结论，再列明细'))
    user['user_id'] = 'owner-a'
    assert len(client.get('/api/memory').json()['personal']) == 1


def test_sensitive_command_is_not_saved_or_sent_to_model(memory_chat):
    client, _, seen = memory_chat
    response = client.post('/api/semantic-workspace/draft/turns', json=payload(text='记住：api_key=synthetic-test-secret'))
    assert response.status_code == 200, response.text
    assert '未保存' in response.json()['reply']
    assert client.get('/api/memory').json()['personal'] == []
    assert not seen


def test_short_followup_keeps_preferences_relevant_to_conversation(memory_chat):
    client, _, seen = memory_chat
    client.post('/api/memory/self', json={'text':'费用报告按部门分组'})
    first = client.post('/api/semantic-workspace/draft/turns', json=payload(text='请解释费用报告')).json()
    response = client.post('/api/semantic-workspace/draft/turns', json=payload(request_id='memory-followup-0002', conv_id=first['conv_id'], text='继续'))
    assert response.status_code == 200, response.text
    assert '费用报告按部门分组' in json.dumps(seen[-1], ensure_ascii=False)


def test_completed_file_task_can_remember_without_changing_frozen_revision(tmp_path, monkeypatch):
    from tests.test_web_source_delivery_api import _client, CoverageAwareWebPiRuntime
    from tests.test_pi_runtime_workspace_api import _uploads, _wait_for_delivery
    monkeypatch.setattr(loader, 'MEMORY_DIR', tmp_path/'preferences')
    client = _client(tmp_path, monkeypatch, role='admin', pi_runtime=CoverageAwareWebPiRuntime())
    client.app.include_router(memory_routes.router)
    document, _ = _uploads(tmp_path)
    with client:
        created = client.post('/api/semantic-workspace/tasks', json={'objective_text':'汇总费用', 'upload_ids':[document], 'output_formats':['json'], 'runtime_version':'pi', 'provider':'local'})
        assert created.status_code == 202, created.text
        task_id = created.json()['task_id']
        before = _wait_for_delivery(client, task_id)
        body = {'text':'记住：费用报告先列合计，邮件用正式语气'}
        url = f'/api/semantic-workspace/tasks/{task_id}/turns'
        response = client.post(url, json=body, headers={'Idempotency-Key':'memory-command-file'})
        assert response.status_code == 200, response.text
        assert response.json()['action'] == 'answer_only'
        assert '已记住' in response.json()['answer']
        assert client.post(url, json=body, headers={'Idempotency-Key':'memory-command-file'}).json() == response.json()
        assert [item['text'] for item in client.get('/api/memory').json()['personal']] == ['费用报告先列合计，邮件用正式语气']
        after = client.get(f'/api/semantic-workspace/tasks/{task_id}').json()
        assert after['active_revision'] == before['active_revision']
        assert after['task_context'] == before['task_context']
