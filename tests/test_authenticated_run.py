import sys,json,sqlite3,hashlib,time
from pathlib import Path
import pytest
import httpx
from src.source_acquisition.authenticated_sources import AuthenticatedSources
from src.source_acquisition.service import SourceAcquisitionRepository as Repository
from src.model_connections.vault import FernetCredentialVault
from src.account_execution import execution_context
from src.api.store import WebUIStore
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.agentic_runtime.kernel import AgentKernel
from src.agentic_runtime.models import RuntimeTaskConfig,RuntimeVersion,PiRuntimeResult,RuntimeStatus,PiRuntimeCheckpoint
from src.source_acquisition.service import SourceAcquisitionRequest
from tests.test_agent_kernel import _request
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from src.source_acquisition.authenticated_run import RunSourceGate,AuthenticatedPreflightAdapter,freeze_handle

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['complete','scope_changed','checkpoint_changed','cancelled','reader_unknown','worker_clarification','worker_unknown','early_ready'])
async def test_real_kernel_authentication_wait_has_zero_worker_and_same_binding(tmp_path,monkeypatch,mode):
    from src.api import auth
    from src.config.settings import settings
    path=migrated_webui_database(tmp_path/'webui.db')
    authorization=seed_execution_owner(path,'user-a')
    store=WebUIStore(str(path));monkeypatch.setattr(auth,'_store',store);monkeypatch.setattr(settings,'webui_db_path',str(path))
    sources=Repository(path);runtime_repo=AgenticRuntimeRepository(path)
    authentication=AuthenticatedSources(path,FernetCredentialVault.generate(),drivers={'site-a':'v1'})
    class Runtime:
        image='fixture@sha256:'+'a'*64
        runtime_artifact_digest='sha256:'+'a'*64
        def __init__(self):self.starts=[];self.resumes=[]
        async def start(self,request,*,run_id,on_event):
            self.starts.append(run_id)
            if mode=='worker_unknown':raise RuntimeError('synthetic startup unknown')
            assert any('认证原文' in item.host_path.read_text(encoding='utf-8') for item in request.sources)
            return PiRuntimeResult(status=RuntimeStatus.NEEDS_INPUT if mode in ('worker_clarification','early_ready') else RuntimeStatus.CANDIDATE_READY,run_id=run_id,workspace_root=gate.workspace(request,run_id))
        async def resume(self,request,*,checkpoint,on_event):
            self.resumes.append(checkpoint.run_id)
            return PiRuntimeResult(status=RuntimeStatus.CANDIDATE_READY,run_id=checkpoint.run_id,workspace_root=checkpoint.workspace_root)
        async def cancel(self,*args):raise AssertionError('前置等待没有Worker')
    runtime=Runtime()
    with execution_context(authorization):
        attempt,_=sources.claim_attempt(owner_id='user-a',idempotency_key='original',request=SourceAcquisitionRequest(url='https://fixture.invalid/',purpose='冻结用途'))
        with sources._connect() as db:
            db.execute('BEGIN IMMEDIATE');digest=sources.auth_digest(db,'user-a',attempt['attempt_id'])
        pending=authentication.request('user-a','site-a',None,'login_required',{'attempt_id':attempt['attempt_id']},now=time.time(),ttl=60,authorization_digest=digest,idempotency_key='auth')
        sources.bind_auth_wait('user-a',attempt['attempt_id'],pending['request_id'])
        handle=freeze_handle(sources,authentication,'user-a',attempt['attempt_id'])
        if mode=='scope_changed':handle['scope_sha256']='0'*64
        store.create_semantic_workspace_task('user-a',task_id='task-a',title='合成任务',objective_text='读取来源',upload_ids=[],output_formats=['txt'],provider='local',model=None,external_api_confirmed=False,source_contract={'authenticated_source':handle})
        runtime_repo.register(RuntimeTaskConfig(user_id='user-a',task_id='task-a',revision=1,runtime_version=RuntimeVersion.PI))
        gate=RunSourceGate(store,sources,authentication,runtime_repo,tmp_path)
        kernel=AgentKernel(adapter=AuthenticatedPreflightAdapter(runtime,gate),repository=runtime_repo)
        request=_request(tmp_path)
        async def sink(event):pass
        if mode=='scope_changed':
            with pytest.raises(ValueError,match='handle_changed'):await kernel.start(request,on_event=sink)
            assert runtime.starts==[]
            return
        if mode=='early_ready':
            from src.source_acquisition.authenticated_continuation import SourceContinuation
            from src.source_acquisition.service import AnonymousWebFetcher
            from src.connectors.http_security import HttpSecurityGuard
            class ReadyDriver:
                website='site-a';version='v1'
                def verify(self,*args):return {'status':'valid','account_id':'account-a'}
                def source_fetcher(self,*args):return AnonymousWebFetcher(security_guard=HttpSecurityGuard(resolver=lambda _:['93.184.216.34']),transport=httpx.MockTransport(lambda _:httpx.Response(200,text='<html>认证原文</html>',headers={'content-type':'text/html'})))
            ready=ReadyDriver()
            assert authentication.confirm('user-a',pending['request_id'],{'synthetic':'state'},ready,now=time.time())=='verified'
            completed=await SourceContinuation(sources,authentication,{'site-a':ready}).resume('user-a',pending['request_id'])
            assert completed['continuation_state']=='completed'
            result=await kernel.start(request,on_event=sink)
            assert result.status==RuntimeStatus.NEEDS_INPUT and runtime.starts==[result.run_id]
            assert not any(e['event_type']=='source.authentication_required' for e in runtime_repo.list_events('user-a','task-a',1))
            runtime_repo.update('user-a','task-a',1,status=RuntimeStatus.RUNNING)
            resumed=await kernel.resume(request,checkpoint=PiRuntimeCheckpoint(run_id=result.run_id,workspace_root=result.workspace_root),on_event=sink)
            assert resumed.status==RuntimeStatus.CANDIDATE_READY
            assert runtime.starts==[result.run_id] and runtime.resumes==[result.run_id]
            return
        result=await kernel.start(request,on_event=sink)
        assert result.status==RuntimeStatus.NEEDS_INPUT and runtime.starts==[]
        events=runtime_repo.list_events('user-a','task-a',1)
        assert sum(e['event_type']=='kernel.binding.frozen' for e in events)==1
        assert sum(e['event_type']=='source.authentication_required' for e in events)==1
        if mode=='checkpoint_changed':
            with pytest.raises(ValueError,match='checkpoint'):
                await kernel.resume(request,checkpoint=PiRuntimeCheckpoint(run_id=result.run_id,workspace_root=tmp_path/'wrong'),on_event=sink)
            assert runtime.starts==[]
            return
        if mode=='cancelled':
            await kernel.cancel('user-a','task-a',1)
            assert runtime_repo.get('user-a','task-a',1)['status']==RuntimeStatus.CANCELLED
            assert runtime.starts==[]
            return
        runtime_repo.update('user-a','task-a',1,status=RuntimeStatus.NEEDS_INPUT,run_id=result.run_id,workspace_root=result.workspace_root,request=request.model_dump(mode='json',exclude={'api_key'}))
        from src.source_acquisition.authenticated_run import RunSourceContinuation
        from src.source_acquisition.authenticated_host import DriverHost,combined_router
        from src.source_acquisition.authenticated_http import ChallengeImages
        from src.source_acquisition.service import AnonymousWebFetcher
        from src.connectors.http_security import HttpSecurityGuard
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.auth import get_execution_user
        from src.api.execution import execution_validation
        from src.account_execution import require_authorized
        calls=[]
        class Driver:
            website='site-a';version='v1'
            def start(self,*args):return object()
            def challenge_image(self,*args):return None
            def poll(self,*args):return {'status':'verified','state':{'synthetic':'state'}}
            def verify(self,*args):return {'status':'valid','account_id':'account-a'}
            def close(self,*args):pass
            def source_fetcher(self,state,frozen):
                def send(req):
                    calls.append(str(req.url))
                    if mode=='reader_unknown':raise RuntimeError('合成读取结果未知')
                    return httpx.Response(200,text='<html>认证原文</html>',headers={'content-type':'text/html'})
                return AnonymousWebFetcher(security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(send))
        driver=Driver()
        errors=[]
        class DiagnosedContinuation(RunSourceContinuation):
            async def resume(self,*args):
                try:return await super().resume(*args)
                except Exception:
                    import traceback
                    errors.append(traceback.format_exc());raise
        continuation=DiagnosedContinuation(sources,authentication,{'site-a':driver},store=store,runtime_repository=runtime_repo,kernel=kernel)
        host=DriverHost(authentication,ChallengeImages(authentication),{'site-a':driver},authorize=continuation.authorize,continuation=continuation)
        app=FastAPI();app.include_router(combined_router(host))
        def validate(auth):
            with sqlite3.connect(path) as db:
                db.execute('BEGIN IMMEDIATE');require_authorized(db,auth)
        async def user():
            with execution_context(authorization),execution_validation(validate):yield {'user_id':'user-a'}
        app.dependency_overrides[get_execution_user]=user
        with TestClient(app) as client:
            url='/api/authenticated-sources/requests/'+pending['request_id']
            started=client.post(url+'/start',json={},headers={'Idempotency-Key':'start'})
            response=client.post(url+'/verify',json={'generation':started.json()['generation']},headers={'Idempotency-Key':'verify'})
            assert response.status_code==200,response.text
            continuation.authorize('user-a',authentication.get_request('user-a',pending['request_id']))
            for _ in range(150):
                receipt=client.get(url+'/commands/resume/auto:'+pending['request_id'])
                if receipt.status_code==200 and receipt.json().get('run_receipts'):break
                if errors:break
                time.sleep(.01)
            if mode in ('reader_unknown','worker_unknown'):
                assert errors and runtime.starts==([result.run_id] if mode=='worker_unknown' else [])
                assert receipt.json()['state']=='unknown'
                assert len(calls)==1
                if mode=='worker_unknown':
                    from src.agentic_runtime.kernel import AgentKernelResultUnknownError
                    with pytest.raises(AgentKernelResultUnknownError):
                        await kernel.resume(request,checkpoint=PiRuntimeCheckpoint(run_id=result.run_id,workspace_root=result.workspace_root),on_event=sink)
                    assert runtime.starts==[result.run_id]
                return
            assert not errors,errors
            assert receipt.status_code==200 and receipt.json()['run_receipts'][0]['run_id']==result.run_id,receipt.text
        assert runtime.starts==[result.run_id] and len(calls)==1
        assert sum(e['event_type']=='kernel.binding.frozen' for e in runtime_repo.list_events('user-a','task-a',1))==1

        if mode=='worker_clarification':
            runtime_repo.update('user-a','task-a',1,status=RuntimeStatus.RUNNING)
            resumed=await kernel.resume(request,checkpoint=PiRuntimeCheckpoint(run_id=result.run_id,workspace_root=result.workspace_root),on_event=sink)
            assert resumed.status==RuntimeStatus.CANDIDATE_READY
            assert runtime.starts==[result.run_id] and runtime.resumes==[result.run_id]
