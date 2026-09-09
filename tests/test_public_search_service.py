"""公开查询来源service合成HTTP回归，不访问真实站点。"""
import httpx
import pytest
from src.account_execution import ExecutionAuthorization, execution_context
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import SourceAcquisitionRequest, SourceAcquisitionService, SourceAcquisitionRepository, AnonymousWebFetcher
from src.source_acquisition.public_search import PublicSearchClient
from tests.test_source_acquisition import migrated_webui_database


@pytest.mark.asyncio
async def test_query_reads_two_cross_site_candidates_into_snapshot(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        if request.headers['host'] == 'html.duckduckgo.com':
            return httpx.Response(200, text='<div class="result"><a class="result__a" href="https://one.example/a">甲</a></div><div class="result"><a class="result__a" href="https://two.example/b">乙</a></div>')
        return httpx.Response(200, text='<html><body>真实合成正文</body></html>', headers={'content-type':'text/html'})
    guard = HttpSecurityGuard(resolver=lambda _: ['93.184.216.34'])
    transport = httpx.MockTransport(handler)
    repository = SourceAcquisitionRepository(migrated_webui_database(tmp_path/'search.db'))
    service = SourceAcquisitionService(repository, AnonymousWebFetcher(security_guard=guard, transport=transport), search_client=PublicSearchClient(security_guard=guard, transport=transport))
    with execution_context(ExecutionAuthorization('owner-a',0)):
        result = await service.acquire(owner_id='owner-a', idempotency_key='first', request=SourceAcquisitionRequest(url='', purpose='查公开资料', scope_kind='public_search', query='示例查询', page_limit=2))
    assert result['status'] == 'succeeded'
    assert result['search_report']['read_count'] == 2
    snapshot = repository.get_snapshot('owner-a',result['snapshot_id'])
    assert len(snapshot['artifacts']) == 2
    assert snapshot['coverage']['search_report']['status'] == 'complete'
    assert len(requests) == 3


def test_query_default_ten_and_url_hash_compatibility():
    assert SourceAcquisitionRequest(url='', purpose='查询', scope_kind='public_search', query='中文').normalized().page_limit == 10
    old = SourceAcquisitionRequest(url='https://example.com', purpose='读取')
    assert old.normalized().page_limit == 1
    assert old.request_hash() == SourceAcquisitionRequest(url='https://example.com', purpose='读取', page_limit=1).request_hash()

@pytest.mark.asyncio
async def test_search_dns_error_and_close_failure_are_controlled():
    from src.source_acquisition.public_search import PublicSearchError
    from src.connectors.http_security import SsrfError
    class BadGuard:
        def validate(self, url):
            raise SsrfError('DNS failed')
    with pytest.raises(PublicSearchError) as error:
        await PublicSearchClient(security_guard=BadGuard()).search('query')
    assert error.value.code == 'search_dns_error'
    class ClosingTransport(httpx.AsyncBaseTransport):
        closed = 0
        async def handle_async_request(self, request):
            return httpx.Response(200,text='<div class="no-results">No results</div>')
        async def aclose(self):
            self.closed += 1
            if self.closed == 1:
                raise OSError('temporary close error')
    transport = ClosingTransport()
    client = PublicSearchClient(security_guard=HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']),transport=transport)
    assert await client.search('query') == []
    assert transport.closed == 2


def test_public_search_does_not_claim_global_scope_complete():
    with pytest.raises(ValueError):
        SourceAcquisitionRequest(url='',purpose='query',query='query',scope_kind='public_search',completeness_mode='hard_scope_complete').normalized()

@pytest.mark.asyncio
async def test_search_private_resolution_is_distinct_from_provider_block():
    from src.source_acquisition.public_search import PublicSearchError
    client = PublicSearchClient(security_guard=HttpSecurityGuard(resolver=lambda _: ['198.18.0.156']))
    with pytest.raises(PublicSearchError) as error:
        await client.search('query')
    assert error.value.code == 'search_network_denied'
    assert '网络解析' in str(error.value)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['no_results','challenge','unrecognized','partial','dedup','scope','ssrf','redirect'])
async def test_search_reports_preserve_body_and_boundaries(tmp_path, mode):
    requests = []
    def handler(request):
        host = request.headers['host']
        requests.append(host + request.url.path)
        if host == 'html.duckduckgo.com':
            if mode == 'no_results':
                html = '<div class="no-results">No results</div>'
            elif mode == 'challenge':
                html = '<form action="/anomaly"></form>'
            elif mode == 'unrecognized':
                html = '<html>unknown</html>'
            else:
                urls = [f'https://site{i}.example/page' for i in range(10)] if mode == 'partial' else ['https://one.example/page','https://two.example/page']
                if mode == 'dedup': urls[1] = urls[0]
                if mode == 'ssrf': urls[1] = 'http://127.0.0.1/private'
                html = ''.join(f'<a class="result__a" href="{url}">标题</a>' for url in urls)
            return httpx.Response(200,text=html)
        if mode == 'partial' and host == 'site9.example':
            return httpx.Response(503,text='unavailable')
        if mode == 'redirect' and host == 'two.example':
            return httpx.Response(302,headers={'location':'https://outside.example/private'})
        return httpx.Response(200,text='<html><body>合成完整正文<a href="https://unrequested.example/">不跟随</a></body></html>',headers={'content-type':'text/html'})
    guard = HttpSecurityGuard(resolver=lambda host: ['127.0.0.1'] if host == '127.0.0.1' else ['93.184.216.34'])
    transport = httpx.MockTransport(handler)
    repository = SourceAcquisitionRepository(migrated_webui_database(tmp_path/'matrix.db'))
    service = SourceAcquisitionService(repository, AnonymousWebFetcher(security_guard=guard,transport=transport),search_client=PublicSearchClient(security_guard=guard,transport=transport))
    request = SourceAcquisitionRequest(url='',purpose='读取公开内容',query='合成查询',scope_kind='public_search',page_limit=10 if mode == 'partial' else 2,domains=('one.example',) if mode == 'scope' else ())
    with execution_context(ExecutionAuthorization('owner-a',0)):
        result = await service.acquire(owner_id='owner-a',idempotency_key='same',request=request)
        count = len(requests)
        again = await service.acquire(owner_id='owner-a',idempotency_key='same',request=request)
    assert len(requests) == count
    assert again['attempt_id'] == result['attempt_id']
    assert repository.get_attempt('owner-b',result['attempt_id']) is None
    assert not any('unrequested' in item or 'outside' in item or '127.0.0.1' in item for item in requests)
    report = result['search_report']
    if mode in {'no_results','challenge','unrecognized'}:
        assert result['status'] == 'failed'
        assert not result['snapshot_id']
        assert result['error_code'] == {'no_results':'search_no_results','challenge':'search_blocked','unrecognized':'search_unrecognized'}[mode]
        assert report['read_count'] == 0
    else:
        assert result['status'] == 'succeeded'
        assert report['status'] == 'partial'
        assert report['read_count'] == (9 if mode == 'partial' else 1)
        snapshot = repository.get_snapshot('owner-a',result['snapshot_id'])
        assert len(snapshot['artifacts']) == report['read_count']
        assert snapshot['coverage']['status'] == 'coverage_unknown'
        assert repository.get_snapshot('owner-b',result['snapshot_id']) is None
        if mode != 'dedup': assert report['failed_count'] == 1


@pytest.mark.asyncio
async def test_cancel_during_search_discards_late_candidates(tmp_path):
    repository = SourceAcquisitionRepository(migrated_webui_database(tmp_path/'cancel.db'))
    class LateSearch:
        async def search(self,*args,**kwargs):
            attempt = repository.get_by_idempotency_key('owner-a','cancel')
            repository.cancel_attempt('owner-a',attempt['attempt_id'])
            return [{'url':'https://one.example/page','title':'迟到'}]
    class NoFetch:
        async def fetch(self,url):
            pytest.fail('取消后不得读取候选')
    service = SourceAcquisitionService(repository,NoFetch(),search_client=LateSearch())
    with execution_context(ExecutionAuthorization('owner-a',0)):
        result = await service.acquire(owner_id='owner-a',idempotency_key='cancel',request=SourceAcquisitionRequest(url='',purpose='读取',query='查询',scope_kind='public_search'))
    assert result['status'] == 'canceled'
    assert repository.count_snapshots('owner-a') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['redirect','oversize'])
async def test_search_transport_refuses_redirect_and_oversize(kind):
    from src.source_acquisition.public_search import PublicSearchError
    sent = []
    def handler(request):
        sent.append(request)
        assert not request.headers.get('authorization')
        assert not request.headers.get('cookie')
        if kind == 'redirect': return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
        return httpx.Response(200,content=b'x'*(1024*1024+1))
    client = PublicSearchClient(security_guard=HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']),transport=httpx.MockTransport(handler))
    with pytest.raises(PublicSearchError) as error:
        await client.search('查询')
    assert error.value.code == ('search_blocked' if kind == 'redirect' else 'search_response_limit')
    assert len(sent) == 1


@pytest.mark.parametrize('domains',[('https://example.com',),('example.com:443',),('user@example.com',),('example.com/path',),tuple(f'd{i}.example' for i in range(11))])
def test_query_rejects_unbounded_or_non_domain_scope(domains):
    with pytest.raises(ValueError):
        SourceAcquisitionRequest(url='',purpose='读取',query='查询',scope_kind='public_search',domains=domains).normalized()


def test_query_hash_binds_scope_and_provider_inputs():
    from dataclasses import replace
    request = SourceAcquisitionRequest(url='',purpose='读取',query='查询',scope_kind='public_search')
    assert len({request.request_hash(),replace(request,query='另一查询').request_hash(),replace(request,time_range='day').request_hash(),replace(request,domains=('example.com',)).request_hash()}) == 4

@pytest.mark.asyncio
async def test_search_checks_authority_after_dns_before_send():
    import asyncio
    from src.source_acquisition.service import _execution_check
    revoked = False
    sent = []
    class RevokingGuard:
        def validate(self,url):
            nonlocal revoked
            target = HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']).validate(url)
            revoked = True
            return target
    def check():
        if revoked: raise asyncio.CancelledError()
    def handler(request):
        sent.append(request)
        return httpx.Response(200,text='<div class="no-results">none</div>')
    token = _execution_check.set(check)
    try:
        with pytest.raises(asyncio.CancelledError):
            await PublicSearchClient(security_guard=RevokingGuard(),transport=httpx.MockTransport(handler)).search('query')
    finally:
        _execution_check.reset(token)
    assert sent == []

@pytest.mark.asyncio
async def test_repository_cancel_in_dns_window_sends_nothing(tmp_path):
    repository = SourceAcquisitionRepository(migrated_webui_database(tmp_path/'dns-cancel.db'))
    sent = []
    class CancelingGuard:
        def validate(self,url):
            target = HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']).validate(url)
            attempt = repository.get_by_idempotency_key('owner-a','dns-cancel')
            repository.cancel_attempt('owner-a',attempt['attempt_id'])
            return target
    def handler(request):
        sent.append(request)
        return httpx.Response(200,text='<div class="no-results">none</div>')
    service = SourceAcquisitionService(repository,AnonymousWebFetcher(),search_client=PublicSearchClient(security_guard=CancelingGuard(),transport=httpx.MockTransport(handler)))
    with execution_context(ExecutionAuthorization('owner-a',0)):
        result = await service.acquire(owner_id='owner-a',idempotency_key='dns-cancel',request=SourceAcquisitionRequest(url='',purpose='读取',query='查询',scope_kind='public_search'))
    assert sent == []
    assert result['status'] == 'canceled'
    assert repository.count_snapshots('owner-a') == 0


def test_public_search_migration_replay_checks_existing_column():
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('search_migration','src/database_migrations/alembic/versions/webui_0015_public_search.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for declaration, valid in [('TEXT',True),('INTEGER',False),('TEXT NOT NULL',False)]:
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE TABLE source_acquisition_attempts (id TEXT, search_report_json {declaration})')
            with Operations.context(MigrationContext.configure(connection)):
                if valid:
                    module.upgrade()
                else:
                    with pytest.raises(RuntimeError,match='search_report_json'):
                        module.upgrade()
        engine.dispose()

@pytest.mark.asyncio
async def test_persisted_owner_disable_in_dns_window_sends_nothing(tmp_path):
    from contextlib import closing
    import sqlite3
    from src import account_execution as execution
    from tests.account_execution_helpers import seed_execution_owner

    database = migrated_webui_database(tmp_path/'dns-owner-disabled.db')
    authorization = seed_execution_owner(database)
    repository = SourceAcquisitionRepository(database)
    sent = []

    class DisablingGuard:
        def validate(self, url):
            target = HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']).validate(url)
            # 在 DNS 返回之前提交真实停用，验证发送门读取持久 Owner 状态。
            with closing(sqlite3.connect(database)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                execution.update_account_status(connection, authorization.owner_user_id, disabled=True, actor_user_id='synthetic-admin', now=1)
                connection.commit()
            return target

    def handler(request):
        sent.append(request)
        return httpx.Response(200,text='<div class="no-results">none</div>')

    service = SourceAcquisitionService(repository, AnonymousWebFetcher(), search_client=PublicSearchClient(security_guard=DisablingGuard(), transport=httpx.MockTransport(handler)))
    with execution.execution_context(authorization):
        with pytest.raises(execution.ExecutionDenied):
            await service.acquire(owner_id=authorization.owner_user_id, idempotency_key='dns-disabled', request=SourceAcquisitionRequest(url='',purpose='读取',query='查询',scope_kind='public_search'))
    assert sent == []
    assert repository.count_snapshots(authorization.owner_user_id) == 0
