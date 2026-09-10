"""真实临时Repository与浏览器传输替身；不表示真实站点资格。"""
import asyncio,base64,hashlib,threading
from types import SimpleNamespace
import pytest
from src.account_execution import execution_context
from src.api import auth
from src.api.store import WebUIStore
from src.source_acquisition.service import SourceAcquisitionRepository,SourceAcquisitionRequest
from src.source_acquisition.authenticated_entry import DetectedSourceService
from src.source_acquisition.authenticated_browser_source import read_source
from tests.database_migration_helpers import migrated_webui_database
from tests.account_execution_helpers import seed_execution_owner

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','close_unknown','cancel'])
async def test_browser_source_closes_before_snapshot(tmp_path,monkeypatch,mode):
    path=migrated_webui_database(tmp_path/'webui.db')
    authorization=seed_execution_owner(path,'browser-owner')
    monkeypatch.setattr(auth,'_store',WebUIStore(str(path)))
    repo=SourceAcquisitionRepository(path)
    url='https://www.bilibili.com/video/BV1ZutN6UEGX/'
    raw=b'<html><p>synthetic public content</p></html>'
    entered=threading.Event();release=threading.Event()
    class Session:
        closed=False
        def call(self,payload):
            if payload['action']=='observe':return {'status':'not_required','candidates':[{'url':url,'title':'合成'}]}
            entered.set()
            if mode=='cancel':assert release.wait(5)
            return {'status':'read','identity':url,'media_type':'text/html','title':'合成','content':base64.b64encode(raw).decode()}
        def close(self):
            assert repo.count_snapshots('browser-owner')==0
            if mode=='close_unknown':raise RuntimeError('synthetic close failure')
            self.closed=True;release.set()
    session=Session()
    class Driver:
        website='bilibili';version='v1';search_provider='browser:bilibili:v1'
        factory=staticmethod(lambda:session)
        def validate_source(self,request):return request.normalized()
        def content_identity(self,value):
            if value!=url:raise ValueError('scope_changed')
            return value
        def _payload(self,action,**kwargs):return {'action':action,**kwargs}
        async def detect(self,*args):return {'status':'not_required'}
        async def read_source(self,*args):return await read_source(self,*args)
    driver=Driver();service=DetectedSourceService(repo,SimpleNamespace(drivers={'bilibili':'v1'}),driver)
    request=SourceAcquisitionRequest(url='',purpose='合成阅读',scope_kind='public_search',query='合成查询',domains=('bilibili.com',),page_limit=3,search_provider=driver.search_provider)
    with execution_context(authorization):
        operation=asyncio.create_task(service.acquire(owner_id='browser-owner',idempotency_key='browser-source',request=request))
        if mode=='cancel':
            assert await asyncio.to_thread(entered.wait,5)
            with repo._connect() as db:identity=db.execute('SELECT attempt_id FROM source_acquisition_attempts').fetchone()[0]
            repo.cancel_attempt('browser-owner',identity);operation.cancel()
            result=await operation
            assert result['status']=='canceled' and session.closed
        elif mode=='close_unknown':
            with pytest.raises(RuntimeError):await operation
            with repo._connect() as db:identity=db.execute('SELECT attempt_id FROM source_acquisition_attempts').fetchone()[0]
            assert not repo.fail_if_stale('browser-owner',identity,stale_after_seconds=-1)
            assert repo.cancel_attempt('browser-owner',identity)['status']=='cancelling'
            assert SourceAcquisitionRepository(path).get_attempt('browser-owner',identity)['authentication_state']=='auth_cleanup_unknown'
        else:
            result=await operation;assert result['status']=='succeeded' and session.closed
            assert repo.get_snapshot('browser-owner',result['snapshot_id'])['artifacts'][0]['content_sha256']==hashlib.sha256(raw).hexdigest()
        if mode!='success':assert repo.count_snapshots('browser-owner')==0


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['unknown','navigation_failed','scope_denied','future_protocol_state','site_refused','timeout','http_403'])
async def test_mixed_unknown_page_never_finalizes_or_retries(tmp_path,monkeypatch,status):
    path=migrated_webui_database(tmp_path/'webui.db')
    authorization=seed_execution_owner(path,'browser-owner')
    monkeypatch.setattr(auth,'_store',WebUIStore(str(path)))
    repo=SourceAcquisitionRepository(path)
    urls=['https://www.bilibili.com/video/BV1ZutN6UEGX/','https://www.bilibili.com/video/BV1tBCYBmEMw/']
    calls=[]
    class Session:
        closed=False
        def call(self,payload):
            calls.append(payload['action'])
            if payload['action']=='observe':return {'status':'not_required','candidates':[{'url':url} for url in urls]}
            if payload['identity']==urls[1]:return {'status':'site_refused','http_status':403} if status=='http_403' else {'status':status}
            return {'status':'read','identity':urls[0],'media_type':'text/html','content':base64.b64encode(b'<html>synthetic first page</html>').decode()}
        def close(self):self.closed=True
    session=Session()
    class Driver:
        website='bilibili';version='v1';search_provider='browser:bilibili:v1'
        factory=staticmethod(lambda:session)
        def validate_source(self,request):return request.normalized()
        def content_identity(self,value):return value
        def _payload(self,action,**kwargs):return {'action':action,**kwargs}
        async def detect(self,*args):return {'status':'not_required'}
        async def read_source(self,*args):return await read_source(self,*args)
    driver=Driver();service=DetectedSourceService(repo,SimpleNamespace(drivers={'bilibili':'v1'}),driver)
    request=SourceAcquisitionRequest(url='',purpose='合成阅读',scope_kind='public_search',query='合成查询',domains=('bilibili.com',),page_limit=3,search_provider=driver.search_provider)
    with execution_context(authorization):
        if status in ('timeout','http_403'):
            result=await service.acquire(owner_id='browser-owner',idempotency_key='mixed-unknown',request=request)
            assert result['status']=='succeeded' and repo.count_snapshots('browser-owner')==1
            with repo._connect() as db:
                codes=[row[0] for row in db.execute('SELECT error_code FROM source_page_failures')]
            assert codes==[('timeout' if status=='timeout' else 'site_refused')]
            assert session.closed
            return
        with pytest.raises(ValueError):await service.acquire(owner_id='browser-owner',idempotency_key='mixed-unknown',request=request)
        assert session.closed and calls==['observe','read','read']
        with repo._connect() as db:
            identity=db.execute('SELECT attempt_id FROM source_acquisition_attempts').fetchone()[0]
            assert db.execute('SELECT COUNT(*) FROM source_page_failures').fetchone()[0]==0
        assert repo.count_snapshots('browser-owner')==0
        restarted=SourceAcquisitionRepository(path)
        assert restarted.get_attempt('browser-owner',identity)['authentication_state']=='auth_cleanup_unknown'
        assert not restarted.fail_if_stale('browser-owner',identity,stale_after_seconds=-1)
        before=list(calls)
        await service.acquire(owner_id='browser-owner',idempotency_key='mixed-unknown',request=request)
        assert calls==before
