"""分用户用量公开接口，全部数据来自隔离测试库。"""
from tests.test_operations_api import platform, period
from uuid import uuid4
import json
from pathlib import Path


def test_china_reference_price_uses_official_yuan_not_converted_dollar(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'])
    result = client.post('/api/operations/tokens/query', json=period(currency='CNY')).json()
    flash = next(item for item in result['pricing']['models'] if item['model'] == 'deepseek-flash')
    assert flash['currency'] == 'CNY'
    assert flash['input'] == '2.00' and flash['output'] == '8.00'
    assert result['items'][0]['cost'] == '10.000000'
    assert all(item['currency'] == 'CNY' for item in result['pricing']['models'])


def test_china_qwen_region_and_glm_prices_do_not_leak_to_other_endpoints(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    cases = [
        ('qwen3.8-flash', 'dashscope.aliyuncs.com/compatible-mode', '3.500000'),
        ('qwen3.8-max', 'testspace.cn-beijing.maas.aliyuncs.com/compatible-mode', '48.000000'),
        ('qwen3.8-max', 'testspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode', None),
        ('qwen3.8-max', 'testspace.cn-beijing.maas.aliyuncs.com.evil.invalid/compatible-mode', None),
        ('glm-5.2', 'open.bigmodel.cn/api/paas', '36.000000'),
        ('glm-5.3', 'open.bigmodel.cn/api/paas', '36.000000'),
        ('glm-5.3-flash', 'open.bigmodel.cn/api/paas', '3.600000'),
    ]
    for index, (model, host, expected) in enumerate(cases):
        owner = store.create_user(f'cn-case-{index}', 'unused', role='user')
        seed_usage(store, owner['user_id'], model=model, host=host)
        # 测试夹具默认v1；智谱按公开协议的v4基础路径冻结。
        if model.startswith('glm-'):
            with store._conn() as conn:
                conn.execute('UPDATE model_connection_grants SET base_url=? WHERE owner_user_id=?',
                             ('https://open.bigmodel.cn/api/paas/v4', owner['user_id']))
        result = client.post('/api/operations/tokens/query', json=period(search=owner['user_id'])).json()
        assert result['items'][0]['cost'] == expected, (model, host)


def test_default_prices_only_cover_platform_catalog_and_keep_legacy_usage(platform):
    from src.model_connections.catalog import PRESETS_BY_ID
    from urllib.parse import urlsplit
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'], model='gpt-4o-mini', host='api.openai.com')
    result = client.post('/api/operations/tokens/query', json=period()).json()
    supported = {(model, urlsplit(preset.base_url).hostname)
                 for preset in PRESETS_BY_ID.values()
                 for model in preset.models}
    assert all((item['model'], item['host']) in supported for item in result['pricing']['models'])
    assert result['items'][0]['requests'] == 1
    assert result['items'][0]['total_tokens'] == 2000000
    assert result['items'][0]['cost'] is None


def seed_usage(store, owner, *, model='deepseek-flash', host='api.deepseek.com', locality='external', tokens=(1000000, 1000000, 2000000), created=None):
    key = uuid4().hex
    created = created or period()['start'] + 'T12:00:00+08:00'
    with store._conn() as conn:
        conn.execute('''INSERT INTO model_connection_grants
            (grant_id,token_hash,owner_user_id,task_id,revision,run_id,connection_id,purpose,base_url,model,api_format,locality,expires_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (key,key,owner,key,1,key,key,'task','https://'+host+'/v1',model,'openai_chat',locality,created,created))
        conn.execute('''INSERT INTO model_provider_usage
            (usage_id,grant_id,owner_user_id,task_id,revision,run_id,connection_id,purpose,status,input_tokens,output_tokens,total_tokens,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', (key,key,owner,key,1,key,key,'task','success',*tokens,created))


def test_empty_usage_requires_admin_and_has_zero_requests(platform):
    client, actor, users, _ = platform
    assert client.post('/api/operations/tokens/query', json=period()).status_code == 403
    actor['user'] = users['manager']
    response = client.post('/api/operations/tokens/query', json=period())
    assert response.status_code == 200
    assert response.json()['total'] == 0
    assert response.json()['summary']['requests'] == 0
    assert response.json()['page_size'] == 10


def test_cost_uses_frozen_official_model_and_does_not_price_unknown_usage(platform):
    client, actor, users, store = platform
    actor['user'] = users['manager']
    seed_usage(store, users['member']['user_id'])
    seed_usage(store, users['member']['user_id'], tokens=(None,None,None))
    seed_usage(store, users['peer']['user_id'])
    response = client.post('/api/operations/tokens/query', json=period(currency='USD'))
    assert response.status_code == 200
    data = response.json()
    assert data['total'] == 1
    item = data['items'][0]
    assert item['requests'] == 2
    assert item['total_tokens'] == 2000000
    assert item['cost'] == '1.428571'
    assert item['priced_requests'] == 1
    assert item['unknown_requests'] == 1
    assert 'base_url' not in response.text and 'api.openai.com/v1' not in response.text


def test_filter_sort_and_export_keep_the_same_scope(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'])
    seed_usage(store, users['manager']['user_id'], tokens=(2000000, 1000000, 3000000))
    seed_usage(store, users['peer']['user_id'], model='unknown-model')
    filters = period(model='deepseek-flash', sort='cost', direction='asc', currency='USD')
    response = client.post('/api/operations/tokens/query', json=filters)
    assert response.status_code == 200
    assert [row['username'] for row in response.json()['items']] == ['member', 'manager']
    exported = client.post('/api/operations/tokens/export', json={'filters': filters, 'format': 'csv'})
    assert exported.status_code == 200
    assert 'member' in exported.text and 'manager' in exported.text and 'peer' not in exported.text
    assert '1.428571' in exported.text
    exported = client.post('/api/operations/tokens/export', json={'filters': filters, 'format': 'xlsx'})
    assert exported.status_code == 200 and exported.content.startswith(b'PK')


def test_subscription_path_is_not_official_metered_pricing(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'], host='api.deepseek.com/subscription')
    result = client.post('/api/operations/tokens/query', json=period()).json()
    assert result['items'][0]['cost'] is None
    assert result['items'][0]['priced_requests'] == 0


def test_beijing_boundaries_and_naive_history_are_not_shifted(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    for created in ['2026-09-18T16:00:00Z', '2026-09-19T15:59:59Z', '2026-09-19T16:00:00Z', '2026-09-19T12:00:00']:
        seed_usage(store, users['member']['user_id'], created=created)
    result = client.post('/api/operations/tokens/query', json={'start': '2026-09-19', 'end': '2026-09-19'}).json()
    assert result['items'][0]['requests'] == 2
    assert result['items'][0]['last_used'] == '2026-09-19T23:59:59+08:00'


def test_out_of_catalog_qwen_usage_is_preserved_without_pricing(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'], model='qwen3.5-plus', host='dashscope.aliyuncs.com/compatible-mode', tokens=(1000, 1000, 2000))
    seed_usage(store, users['member']['user_id'], model='qwen3.5-plus', host='dashscope.aliyuncs.com/compatible-mode', tokens=(300000, 1000, 301000))
    result = client.post('/api/operations/tokens/query', json=period()).json()
    assert result['items'][0]['cost'] is None
    assert result['items'][0]['priced_requests'] == 0
    assert result['items'][0]['requests'] == 2
    assert len(result['items'][0]['models']) == 1


def test_partial_tokens_remain_visible_but_are_marked_incomplete(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'], tokens=(None, None, 500))
    row = client.post('/api/operations/tokens/query', json=period()).json()['items'][0]
    assert row['total_tokens'] == 500
    assert row['unknown_requests'] == 1
    assert row['cost'] is None


def test_pagination_clamps_and_export_does_not_stop_at_current_page(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    for index in range(11):
        user = store.create_user(f'token-user-{index}', 'unused', role='user')
        seed_usage(store, user['user_id'])
    query = period(page=99)
    result = client.post('/api/operations/tokens/query', json=query).json()
    assert result['total'] == 11 and result['page'] == 2 and len(result['items']) == 1
    exported = client.post('/api/operations/tokens/export', json={'filters': query, 'format': 'csv'})
    assert 'token-user-0' in exported.text and 'token-user-10' in exported.text
    assert client.post('/api/operations/tokens/query', json=period(page_size=15)).status_code == 422


def test_invalid_price_config_fails_closed(platform, tmp_path, monkeypatch):
    client, actor, users, _ = platform
    actor['user'] = users['root']
    config = json.loads((Path(__file__).parents[1] / 'src/operations_prices.json').read_text(encoding='utf-8'))
    del config['notice']
    path = tmp_path / 'prices.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('MANGROVE_TOKEN_PRICES_FILE', str(path))
    assert client.post('/api/operations/tokens/query', json=period()).status_code == 503


def test_deleted_owner_usage_is_visible_only_to_super_admin(platform):
    client, actor, users, store = platform
    seed_usage(store, 'deleted-synthetic-owner')
    actor['user'] = users['root']
    result = client.post('/api/operations/tokens/query', json=period()).json()
    assert result['total'] == 1
    assert result['items'][0]['user_id'] == 'deleted-synthetic-owner'
    actor['user'] = users['manager']
    assert client.post('/api/operations/tokens/query', json=period()).json()['total'] == 0


def test_sonnet_long_context_does_not_use_short_context_price(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'], model='claude-sonnet-4-20250514', host='api.anthropic.com', tokens=(250000, 1000, 251000))
    row = client.post('/api/operations/tokens/query', json=period()).json()['items'][0]
    assert row['cost'] is None and row['priced_requests'] == 0


def test_local_usage_is_counted_but_never_priced_even_with_official_model(platform):
    client, actor, users, store = platform
    actor['user'] = users['root']
    seed_usage(store, users['member']['user_id'], locality='managed_private')
    local = client.post('/api/operations/tokens/query', json=period(currency='USD')).json()
    assert local['items'][0]['total_tokens'] == 2000000
    assert local['items'][0]['local_requests'] == 1
    assert local['summary']['cost'] is None
    exported = client.post('/api/operations/tokens/export', json={'filters': period(), 'format': 'csv'})
    assert '无需计价' in exported.text
    seed_usage(store, users['member']['user_id'])
    mixed = client.post('/api/operations/tokens/query', json=period(currency='USD')).json()
    assert mixed['summary']['requests'] == 2
    assert mixed['summary']['local_requests'] == 1
    assert mixed['summary']['cost'] == '1.428571'
    assert len(mixed['items'][0]['models']) == 2
