"""组合后的真实临时库门与HTTP刷新；无真实网络。"""
import asyncio,sqlite3
import pytest,httpx
from src.account_execution import execution_context
from src.api import auth
from src.api.store import WebUIStore
from src.source_acquisition.service import SourceAcquisitionRepository,SourceAcquisitionRequest,SourceAcquisitionService,AnonymousWebFetcher
from src.connectors.http_security import HttpSecurityGuard
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database

@pytest.mark.parametrize('code',['auth_cleanup_unknown','connector_cleanup_unknown','connector_resume_unknown'])
def test_combined_unknown_cancel_guards(tmp_path,monkeypatch,code):
 path=migrated_webui_database(tmp_path/'webui.db');authorization=seed_execution_owner(path,'owner-a')
 monkeypatch.setattr(auth,'_store',WebUIStore(str(path)));repo=SourceAcquisitionRepository(path)
 with execution_context(authorization):
  attempt,_=repo.claim_attempt(owner_id='owner-a',idempotency_key='guard',request=SourceAcquisitionRequest(url='https://fixture.invalid/',purpose='合成'))
  with repo._connect() as db:db.execute('UPDATE source_acquisition_attempts SET error_code=?,cancel_requested_at=? WHERE attempt_id=?',(code,'2026-01-01',attempt['attempt_id']))
  assert not repo.confirm_account_stop('owner-a',attempt['attempt_id'])
  repo._confirm_cancel('owner-a',attempt['attempt_id'])
  assert repo.get_attempt('owner-a',attempt['attempt_id'])['status']=='cancelling'
  assert not repo.fail_if_stale('owner-a',attempt['attempt_id'],stale_after_seconds=-1)

def test_browser_provider_refresh_refused_before_network(tmp_path,monkeypatch):
 from tests.test_source_deletion_delivery import DeletionFixtureRuntime,_task
 from tests.test_web_source_delivery_api import _client
 from src.api.routes import semantic_workspace as route
 from src.config.settings import settings
 client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=DeletionFixtureRuntime())
 with client:
  repo=SourceAcquisitionRepository(settings.webui_db_path)
  authorization=seed_execution_owner(settings.webui_db_path,'user-a')
  class Search:
   provider='browser:bilibili:v1'
   async def search(self,*args,**kwargs):return [{'url':'https://fixture.invalid/','title':'合成'}]
  fetcher=AnonymousWebFetcher(security_guard=HttpSecurityGuard(resolver=lambda _:['93.184.216.34']),transport=httpx.MockTransport(lambda _:httpx.Response(200,text='<html>synthetic search content</html>',headers={'content-type':'text/html'})))
  async def acquire():
   with execution_context(authorization):return await SourceAcquisitionService(repo,fetcher,search_client=Search()).acquire(owner_id='user-a',idempotency_key='browser',request=SourceAcquisitionRequest(url='',query='合成查询',purpose='合成',scope_kind='public_search',page_limit=1,search_provider=Search.provider))
  result=asyncio.run(acquire());assert result['status']=='succeeded'
  task,_=_task(client,'browser-task',[],[result['snapshot_id']])
  monkeypatch.setattr(route,'_source_acquisition_service',lambda:(_ for _ in ()).throw(AssertionError('不得退回匿名网络')))
  with repo._connect() as db:before=db.execute('SELECT COUNT(*) FROM source_acquisition_attempts').fetchone()[0]
  response=client.post(f'/api/semantic-workspace/tasks/{task}/source-refresh',json={'expected_active_revision':1},headers={'Idempotency-Key':'refresh'})
  assert response.status_code==409 and response.json()['detail']=='authenticated_source_refresh_unsupported'
  with repo._connect() as db:assert db.execute('SELECT COUNT(*) FROM source_acquisition_attempts').fetchone()[0]==before
