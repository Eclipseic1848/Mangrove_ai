import pytest
import sys,json,sqlite3,hashlib,time,asyncio,io,zipfile,runpy
from pathlib import Path

@pytest.mark.parametrize('refresh',[False,True])
def test_actual_create_manager_same_run_publisher_and_deletion(tmp_path,monkeypatch,refresh):
 import tests.test_pi_runtime_workspace_api as fixtures
 from tests.test_source_deletion_delivery import DeletionFixtureRuntime,_delete
 from src.config.settings import settings
 from src.api import auth
 from src.account_execution import ExecutionAuthorization,execution_context
 from src.source_acquisition.authenticated_sources import AuthenticatedSources
 from src.model_connections.vault import FernetCredentialVault
 from src.source_acquisition.authenticated_run import RunSourceGate,RunSourceContinuation
 from src.source_acquisition.authenticated_host import DriverHost,combined_router
 from src.source_acquisition.authenticated_http import ChallengeImages
 from src.source_acquisition.service import SourceAcquisitionRequest
 from src.connectors.http_security import HttpSecurityGuard
 import httpx
 from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
 from src.source_acquisition.service import SourceAcquisitionRepository,AnonymousWebFetcher
 from src.agentic_runtime.repository import AgenticRuntimeRepository
 state={}
 def factory(**kwargs):
  path=Path(settings.webui_db_path)
  sources=SourceAcquisitionRepository(path);runtime_repo=AgenticRuntimeRepository(path)
  authentication=AuthenticatedSources(path,FernetCredentialVault.generate(),drivers={'site-a':'v1'})
  gate=RunSourceGate(auth.get_store(),sources,authentication,runtime_repo,settings.semantic_execution_root)
  manager=SemanticWorkspaceManager(authenticated_source_gate=gate,**kwargs)
  state.update(gate=gate,manager=manager,authentication=authentication,sources=sources,runtime_repo=runtime_repo)
  return manager
 monkeypatch.setattr(fixtures,'SemanticWorkspaceManager',factory)
 runtime=DeletionFixtureRuntime();client=fixtures._client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
 sources=state['sources'];authentication=state['authentication'];manager=state['manager']
 calls=[]
 class Driver:
  website='site-a';version='v1';allowed_hosts=('fixture.invalid',)
  async def detect(self,*args):return {'status':'authentication_required','account_id':None,'observed_version':0}
  def start(self,*args):return object()
  def challenge_image(self,*args):return None
  def poll(self,*args):return {'status':'verified','state':{'synthetic':'state'}}
  def verify(self,*args):return {'status':'valid','account_id':'account-a'}
  def close(self,*args):pass
  def source_fetcher(self,*args):
   def send(request):
    calls.append(request)
    return httpx.Response(200,text='<html>synthetic authenticated original</html>',headers={'content-type':'text/html'})
   return AnonymousWebFetcher(security_guard=HttpSecurityGuard(resolver=lambda host:['93.184.216.34']),transport=httpx.MockTransport(send))
 driver=Driver()
 continuation=RunSourceContinuation(sources,authentication,{'site-a':driver},store=auth.get_store(),runtime_repository=state['runtime_repo'],kernel=manager._agent_kernel,manager=manager)
 from src.source_acquisition.authenticated_entry import DetectedSourceService
 entry=DetectedSourceService(sources,authentication,driver)
 host=DriverHost(authentication,ChallengeImages(authentication),{'site-a':driver},authorize=continuation.authorize,continuation=continuation,source_entries={'site-a':entry})
 manager.authenticated_source_host=host
 client.app.include_router(combined_router(host))
 from src.api.routes import source_acquisition
 client.app.include_router(source_acquisition.router)
 with client:
  detected=client.post('/api/semantic-workspace/source-acquisitions',headers={'Idempotency-Key':'original'},json={'authenticated_website':'site-a','url':'https://fixture.invalid/','purpose':'冻结原文'})
  assert detected.status_code==202,detected.text
  attempt=detected.json();assert attempt['authentication_state']=='needs_auth'
  pending=authentication.get_request('user-a',attempt['current_auth_request_id'])
  created=client.post('/api/semantic-workspace/tasks',headers={'Idempotency-Key':'create'},json={'objective_text':'整理资料输出JSON','authenticated_source_attempt_id':attempt['attempt_id'],'output_formats':['json'],'runtime_version':'pi','provider':'local'})
  assert created.status_code==202,created.text
  assert 'authorization_digest' not in created.text and 'scope_sha256' not in created.text
  task=created.json()['task_id']
  for _ in range(200):
   row=state['runtime_repo'].get('user-a',task,1)
   if row and row['status'].value=='needs_input':break
   time.sleep(.02)
  assert row['status'].value=='needs_input',row
  run_id=row['run_id'];assert runtime.start_calls==0
  if refresh:
   from src.source_acquisition.authenticated_entry import DetectedSourceService
   driver.allowed_hosts=('fixture.invalid',)
   with sources._connect() as db:db.execute('UPDATE source_reauthentication_requests SET expires=0 WHERE request_id=?',(pending['request_id'],))
   with execution_context(ExecutionAuthorization('user-a',0)):
    replacement=DetectedSourceService(sources,authentication,driver).refresh_expired('user-a',pending['request_id'],'renew')
   assert replacement['request_id']!=pending['request_id']
   pending=replacement
  url='/api/authenticated-sources/requests/'+pending['request_id']
  started=client.post(url+'/start',json={},headers={'Idempotency-Key':'start'})
  assert started.status_code==200,started.text
  verified=client.post(url+'/verify',json={'generation':started.json()['generation']},headers={'Idempotency-Key':'verify'})
  assert verified.status_code==200,verified.text
  detail=fixtures._wait_for_delivery(client,task)
  assert detail['delivery'] is not None and runtime.start_calls==1
  assert state['runtime_repo'].get('user-a',task,1)['run_id']==run_id
  archive=client.get(f'/api/semantic-workspace/tasks/{task}/source-bundle',params={'revision':1})
  assert archive.status_code==200,archive.text
  with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
   assert any(b'synthetic authenticated original' in bundle.read(n) for n in bundle.namelist())
  frozen_contract=auth.get_store().get_source_contract('user-a',task,1)
  frozen_refs=auth.get_store().get_semantic_workspace_revision('user-a',task,1)['source_refs']
  # 认证精确页刷新不能静默落回匿名读取。
  refresh_response=client.post(f'/api/semantic-workspace/tasks/{task}/source-refresh',json={'expected_active_revision':1},headers={'Idempotency-Key':'authenticated-refresh-refused'})
  assert refresh_response.status_code==409,refresh_response.text
  assert refresh_response.json()['detail']=='authenticated_source_refresh_unsupported'
  assert len(calls)==1
  cleanup_errors=[]
  original_cancel=manager._agent_kernel._adapter.cancel
  async def checked_cancel(*args):
   try:return await original_cancel(*args)
   except Exception as error:
    cleanup_errors.append(repr(error));raise
  monkeypatch.setattr(manager._agent_kernel._adapter,'cancel',checked_cancel)
  try:plan,operation=_delete(client,task,'delete_shared')
  except AssertionError as error:raise AssertionError(str(cleanup_errors)) from error
  assert operation['state']=='completed',operation
  assert len(calls)==1
  with sqlite3.connect(settings.webui_db_path) as db:
   assert db.execute('SELECT COUNT(*) FROM source_artifacts').fetchone()[0]==0
   from src.source_acquisition.authenticated_run import effective_refs
   assert effective_refs(db,'user-a',[],frozen_contract)==frozen_refs
   changed={**frozen_contract,'authenticated_source':{**frozen_contract['authenticated_source'],'request_hash':'0'*64}}
   with pytest.raises(ValueError):effective_refs(db,'user-a',[],changed)
