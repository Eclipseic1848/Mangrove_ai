import asyncio,json,sqlite3,httpx,pytest
from src.connectors.database_connector import DatabaseConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceSpec,SourceType
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition.connection_source import freeze,read_frozen,ReadOnlyHttpConnector

def test_sqlite_read_freezes_owner_version_and_snapshot(tmp_path):
    path=tmp_path/'input.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, water INTEGER, name TEXT)')
        db.executemany('INSERT INTO t VALUES(?,?,?)',[(1,7,'甲'),(2,7,'乙'),(3,8,'丙')])
    spec=SourceSpec(source_id='source-a',source_type=SourceType.DATABASE,locator='dbconn://a',options={'mode':'table','table':'t','sqlite_db_path':str(path),'task_id':'attempt-a','incremental':{'cursor_field':'water'}})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    frozen=freeze('a',row)
    store=ArtifactStore(str(tmp_path/'artifacts'))
    connector=DatabaseConnector(artifact_store=store)
    result=asyncio.run(read_frozen('a',frozen,lambda:row,connector,store))
    assert result['status']=='succeeded'
    content=b''.join(store.read_raw_bytes('attempt-a',a['storage_path']) for a in result['artifacts'])
    assert [r['id'] for r in map(json.loads,content.splitlines())]==[1,2,3]
    assert result['connection_version']==frozen.version


def test_http_paged_raw_with_pinned_transport_and_read_only_scope(tmp_path):
    calls=[]
    def respond(request):
        calls.append(request)
        assert request.url.host=='93.184.216.34'
        assert request.headers['host']=='fixture.invalid'
        page=int(request.url.params.get('page','1'))
        return httpx.Response(200,json=[{'id':page}] if page<3 else [])
    store=ArtifactStore(str(tmp_path/'http'))
    guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34'])
    spec=SourceSpec(source_id='http-a',source_type=SourceType.HTTP_API,locator='https://fixture.invalid/data',options={'task_id':'attempt-http','pagination':{'strategy':'page','options':{'max_pages':4}}})
    row={'owner':'a','connection_id':'http','version':'1','spec':spec}
    connector=ReadOnlyHttpConnector(url=spec.locator,artifact_store=store,security_guard=guard,transport=httpx.MockTransport(respond),authorize=lambda:None)
    result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,store))
    assert result['status']=='succeeded' and len(calls)==3


@pytest.mark.parametrize('sql',['DELETE FROM t','DROP TABLE t','CALL p()','SELECT side_effect()'])
def test_client_sql_never_reaches_database(sql):
    spec=SourceSpec(source_id='x',source_type=SourceType.DATABASE,locator='dbconn://a',options={'mode':'sql','sql':sql})
    with pytest.raises(ValueError,match='read_only'):
        freeze('a',{'owner':'a','connection_id':'a','version':'1','spec':spec})


def test_owner_and_changed_version_reject_before_read(tmp_path):
    spec=SourceSpec(source_id='x',source_type=SourceType.DATABASE,locator='dbconn://a',options={'table':'t'})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    frozen=freeze('a',row)
    with pytest.raises(PermissionError): freeze('b',row)
    row['version']='2'
    with pytest.raises(PermissionError): asyncio.run(read_frozen('a',frozen,lambda:row,None,None))


def test_cancel_waits_for_actual_database_thread(tmp_path):
    import threading
    from src.connectors.base import RecordBatch
    entered=threading.Event();release=threading.Event();closed=[]
    spec=SourceSpec(source_id='x',source_type=SourceType.DATABASE,locator='dbconn://a',options={'table':'t'})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    class Slow:
        async def read(self,*args):
            def block(): entered.set();release.wait(3)
            await asyncio.to_thread(block)
            yield RecordBatch()
        async def close(self): closed.append(True)
    async def run():
        task=asyncio.create_task(read_frozen('a',freeze('a',row),lambda:row,Slow(),None))
        try:
            await asyncio.to_thread(entered.wait,1)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done() and closed==[]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert closed==[True]
    asyncio.run(run())


@pytest.mark.parametrize('status', [401,403,503])
def test_http_error_retains_classification(tmp_path,status):
    store=ArtifactStore(str(tmp_path/'http'))
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator='https://fixture.invalid/data',options={'task_id':'a','max_retries':0})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    connector=ReadOnlyHttpConnector(url=spec.locator,artifact_store=store,security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(lambda request:httpx.Response(status)),authorize=lambda:None)
    result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,store))
    assert result['status']=='failed'
    assert result['error_code']==({503:'source_network',401:'authorization_expired',403:'permission_denied'}[status])


@pytest.mark.parametrize('body,status,expected',[(b'[]',200,'no_results'),(b'not json',200,'local_parse'),(b'',401,'authorization_expired'),(b'',403,'permission_denied')])
def test_precise_http_empty_parse_and_permission(tmp_path,body,status,expected):
    store=ArtifactStore(str(tmp_path/'http'))
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator='https://fixture.invalid/data',options={'task_id':'a','max_retries':0})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    connector=ReadOnlyHttpConnector(url=spec.locator,artifact_store=store,security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(lambda request:httpx.Response(status,content=body)),authorize=lambda:None)
    result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,store))
    assert result.get('error_code',result['status'])==expected
    if expected=='local_parse':assert not list((tmp_path/'http').rglob('raw-*'))


def test_database_resume_repeated_watermark_uses_frozen_checkpoint(tmp_path):
    path=tmp_path/'ties.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, water INTEGER)')
        db.executemany('INSERT INTO t VALUES(?,7)',[(n,) for n in range(1,7)])
    spec=SourceSpec(source_id='x',source_type=SourceType.DATABASE,locator='dbconn://a',options={'table':'t','sqlite_db_path':str(path),'task_id':'resume','batch_size':2,'incremental':{'cursor_field':'water'}})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec};frozen=freeze('a',row)
    store=ArtifactStore(str(tmp_path/'raw'))
    async def first():
        connector=DatabaseConnector(store);stream=connector.read(spec)
        try: return await anext(stream)
        finally: await stream.aclose();await connector.close()
    batch=asyncio.run(first())
    saved={'connection_version':frozen.version,'value':batch.checkpoint.to_dict()}
    result=asyncio.run(read_frozen('a',frozen,lambda:row,DatabaseConnector(store),store,checkpoint=saved))
    artifacts=[a.model_dump(mode='json') for a in batch.artifacts]+result['artifacts']
    rows=[json.loads(line) for a in artifacts for line in store.read_raw_bytes('resume',a['storage_path']).splitlines()]
    assert [r['id'] for r in rows]==list(range(1,7))
    saved['connection_version']='wrong'
    with pytest.raises(ValueError,match='checkpoint_binding'):
        asyncio.run(read_frozen('a',frozen,lambda:row,None,store,checkpoint=saved))


def test_isolated_real_http_service_pagination_and_cleanup(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from urllib.parse import urlsplit,parse_qs
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            page=int(parse_qs(urlsplit(self.path).query).get('page',['1'])[0]);calls.append(page)
            content=json.dumps([{'id':page}] if page<3 else []).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/data'
        spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator=url,options={'task_id':'http-real','pagination':{'strategy':'page','options':{'max_pages':4}}})
        row={'owner':'a','connection_id':'synthetic-http','version':'1','spec':spec};store=ArtifactStore(str(tmp_path/'raw'))
        # 仅此隔离fixture显式回环许可；生产默认守卫仍拒绝回环。
        guard=HttpSecurityGuard(allow_private=True,loopback_host_allowlist=('127.0.0.1',))
        connector=ReadOnlyHttpConnector(url=url,artifact_store=store,security_guard=guard,authorize=lambda:None)
        result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,store))
        assert result['status']=='succeeded' and calls==[1,2,3]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
        assert not thread.is_alive()


def test_repeated_http_page_is_gap_not_duplicate_success(tmp_path):
    store=ArtifactStore(str(tmp_path/'http'));calls=[]
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator='https://fixture.invalid/data',options={'task_id':'a','pagination':{'strategy':'page','options':{'max_pages':4}}})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    def send(request):calls.append(request);return httpx.Response(200,json=[{'id':1}])
    connector=ReadOnlyHttpConnector(url=spec.locator,artifact_store=store,security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(send),authorize=lambda:None)
    result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,store))
    assert result['error_code']=='pagination_repeated'
    assert len(result['artifacts'])==1 and len(calls)==2


def test_raw_cleanup_rejects_wrong_hash_and_never_touches_other_namespace(tmp_path):
    from src.source_acquisition.connection_source import remove_connector_raw
    import hashlib
    store=ArtifactStore(str(tmp_path));data=b'{"a":1}'
    raw=store.write_raw('target','source',data,uri='connector:a',media_type='application/json')
    other=store.write_raw('other','source',data,uri='connector:a',media_type='application/json')
    path=store.resolve_path(raw.storage_path);path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='identity_changed'):remove_connector_raw({'artifact_namespace':'target'},hashlib.sha256(data).hexdigest(),artifact_store=store)
    assert path.exists() and store.resolve_path(other.storage_path).exists()
    path.write_bytes(data);remove_connector_raw({'artifact_namespace':'target'},raw.sha256,artifact_store=store)
    assert not path.exists() and store.resolve_path(other.storage_path).exists()


def test_gzip_http_response_is_not_decoded_twice(tmp_path):
    import gzip
    connector=ReadOnlyHttpConnector(url='https://example.test/data',artifact_store=ArtifactStore(str(tmp_path)),authorize=lambda:None,security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(lambda request:httpx.Response(200,content=gzip.compress(b'[{"id":1}]'),headers={'content-encoding':'gzip'})))
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator='https://example.test/data',options={'task_id':'gzip'})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,ArtifactStore(str(tmp_path))))
    assert result['status']=='succeeded'


@pytest.mark.parametrize('status,expected,calls_expected',[(302,'source_redirect_denied',1),(503,'source_network',1),(200,'source_scope_denied',0)])
def test_http_scope_redirect_and_zero_retry(tmp_path,status,expected,calls_expected):
    calls=[]
    def response(request):
        calls.append(request)
        return httpx.Response(status,headers={'location':'https://other.test/data'})
    store=ArtifactStore(str(tmp_path))
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator='https://fixture.invalid/data',options={'task_id':'scope'})
    row={'owner':'a','connection_id':'a','version':'1','spec':spec}
    guard=HttpSecurityGuard(resolver=lambda host:['127.0.0.1' if status==200 else '93.184.216.34'])
    connector=ReadOnlyHttpConnector(url=spec.locator,artifact_store=store,security_guard=guard,transport=httpx.MockTransport(response),authorize=lambda:None)
    result=asyncio.run(read_frozen('a',freeze('a',row),lambda:row,connector,store))
    assert result['error_code']==expected and len(calls)==calls_expected


def test_http_checkpoint_duplicate_is_not_counted_twice(tmp_path,monkeypatch):
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.test_source_acquisition import migrated_webui_database
    from src.account_execution import ExecutionAuthorization,execution_context
    from src.source_acquisition.connection_source import ConnectorRequest,acquire_connector
    from pathlib import Path
    database=migrated_webui_database(tmp_path/'workspace.db')
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator='https://fixture.invalid/data',options={'task_id':'persist','pagination':{'strategy':'page','options':{'max_pages':4}}})
    row={'owner':'owner-a','connection_id':'a','version':'1','spec':spec}
    request=ConnectorRequest(freeze('owner-a',row),'读取');store=ArtifactStore(str(tmp_path/'raw'));calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(200,json=[{'id':1}] if len(calls)<=2 else [])
    def connector():
        return ReadOnlyHttpConnector(url=spec.locator,artifact_store=store,security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(send),authorize=lambda:None)
    from src.api.execution import execution_validation
    from src.account_execution import require_authorized
    def authorize(auth):
        with sqlite3.connect(database) as db:
            db.execute("BEGIN IMMEDIATE");require_authorized(db,auth)
    with execution_validation(authorize),execution_context(ExecutionAuthorization('owner-a',0)):
        from src.source_acquisition.service import SourceAcquisitionRepository
        repo=SourceAcquisitionRepository(database)
        first=asyncio.run(acquire_connector(repo,owner='owner-a',key='pause',request=request,load_current=lambda:row,connector=connector(),artifact_store=store,pause_after_batches=1))
        assert first['error_code']=='connector_checkpoint_ready'
        repo=SourceAcquisitionRepository(database)
        resumed=asyncio.run(acquire_connector(repo,owner='owner-a',key='pause',request=request,load_current=lambda:row,connector=connector(),artifact_store=store,resume_checkpoint=True))
        assert resumed['attempt_id']==first['attempt_id']
        snapshot=repo.get_snapshot('owner-a',resumed['snapshot_id'])
        assert snapshot['failures'][0]['error_code']=='pagination_repeated',snapshot
        assert len(snapshot['artifacts'])==1
        assert len(calls)==2


@pytest.mark.parametrize('url',['https://synthetic-user:synthetic-password@fixture.invalid/data','file:///synthetic-private-path','https://fixture.invalid:bad/data'])
def test_invalid_http_url_api_never_creates_attempt(tmp_path,url):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.auth import get_execution_user
    from src.source_acquisition.service import SourceAcquisitionRepository
    from tests.test_source_acquisition import migrated_webui_database
    from src.api.routes.connector_sources import router_for
    database=migrated_webui_database(tmp_path/'webui.db')
    repository=SourceAcquisitionRepository(database)
    spec=SourceSpec(source_id='x',source_type=SourceType.HTTP_API,locator=url,options={'url':url})
    row={'owner':'owner-a','connection_id':'a','version':'1','spec':spec}
    def no_connector(row):raise AssertionError('非法URL不得构造读取器')
    app=FastAPI();app.include_router(router_for(repository,lambda *args:row,no_connector,None))
    app.dependency_overrides[get_execution_user]=lambda:{'user_id':'owner-a'}
    with TestClient(app) as client:
        source={'source_type':'http_api','url':url}
        assert client.post('/api/connector-sources/resolve',json=source).status_code==422
        assert client.post('/api/connector-sources/acquisitions',json={'source':source,'purpose':'读取'},headers={'Idempotency-Key':'invalid'}).status_code==422
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT COUNT(*) FROM source_acquisition_attempts').fetchone()[0]==0
