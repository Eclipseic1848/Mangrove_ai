"""真实Manager/Publisher/临时原件；仅Runtime生成与验证为既有替身。"""
import asyncio,sqlite3,io,json,zipfile
from src.source_acquisition.connection_source import freeze,ConnectorRequest,acquire_connector
from src.data_prep.models import SourceSpec,SourceType
from src.data_prep.artifact_store import ArtifactStore
from src.connectors.database_connector import DatabaseConnector


def test_connector_enters_real_manager_publisher_and_source_export(tmp_path,monkeypatch):
    import tests.test_pi_runtime_workspace_api as fixtures
    from tests.test_source_deletion_delivery import DeletionFixtureRuntime as CoverageAwareWebPiRuntime
    from src.account_execution import ExecutionAuthorization,execution_context
    from src.config.settings import settings
    import src.source_acquisition.connection_source as connection_source
    from pathlib import Path
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
    from src.source_acquisition.service import SourceAcquisitionRepository
    monkeypatch.setattr(fixtures,'SemanticWorkspaceManager',SemanticWorkspaceManager)
    runtime=CoverageAwareWebPiRuntime();client=fixtures._client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
    path=tmp_path/'original.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY,name TEXT)');db.execute("INSERT INTO t VALUES(1,'甲')")
    spec=SourceSpec(source_id='x',source_type=SourceType.DATABASE,locator='dbconn://synthetic',options={'table':'t','sqlite_db_path':str(path),'task_id':'acquire'})
    monkeypatch.setattr(settings,'data_prep_artifact_root',str(tmp_path/'raw'))
    row={'owner':'user-a','connection_id':'synthetic','version':'1','spec':spec};store=ArtifactStore(str(tmp_path/'raw'))
    with client:
        with execution_context(ExecutionAuthorization('user-a',0)):
            source=asyncio.run(acquire_connector(SourceAcquisitionRepository(settings.webui_db_path),owner='user-a',key='source',request=ConnectorRequest(freeze('user-a',row),'读取数据库'),load_current=lambda:row,connector=DatabaseConnector(store),artifact_store=store))
        response=client.post('/api/semantic-workspace/tasks',headers={'Idempotency-Key':'task'},json={'objective_text':'整理表格并输出JSON','source_snapshot_ids':[source['snapshot_id']],'quantity_requirement':'尽可能多','completeness_requirement':'允许披露缺口后交付','output_formats':['json'],'runtime_version':'pi','provider':'local'})
        assert response.status_code==202,response.text
        task=response.json()['task_id'];detail=fixtures._wait_for_delivery(client,task)
        assert detail['delivery'] is not None
        assert runtime.requests[0].sources[0].media_type=='application/x-ndjson'
        assert runtime.requests[0].sources[0].host_path.suffix=='.jsonl'
        archive=client.get(f'/api/semantic-workspace/tasks/{task}/source-bundle',params={'revision':1})
        assert archive.status_code==200,archive.text
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            manifest=json.loads(bundle.read('manifest.json'))
            assert manifest['sources'][0]['provenance']['source_kind']=='connector'
            assert manifest['sources'][0]['provenance']['connection_version']==freeze('user-a',row).version
            original=next(file for file in manifest['sources'][0]['files'] if file['role']=='original')
            assert json.loads(bundle.read(original['path']))=={'id':1,'name':'甲'}


        original_refresh=connection_source.refresh_connector
        async def refreshed(repository,**kwargs):
            return await original_refresh(repository,**kwargs,resolver=lambda owner,source,key:row,factory=lambda binding:DatabaseConnector(store),artifact_store=store)
        monkeypatch.setattr(connection_source,'refresh_connector',refreshed)
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO t VALUES(2,'乙')")
        updated=client.post(f'/api/semantic-workspace/tasks/{task}/source-refresh',headers={'Idempotency-Key':'refresh'},json={'expected_active_revision':1,'target_source_snapshot_id':source['snapshot_id']})
        assert updated.status_code==202,updated.text
        assert updated.json()['revision']['revision']==2
        assert fixtures._wait_for_delivery(client,task)['delivery'] is not None
        assert runtime.start_calls==2
        assert len(runtime.requests[1].sources[0].host_path.read_bytes().splitlines())==2
        old=client.get(f'/api/semantic-workspace/tasks/{task}/source-bundle',params={'revision':1})
        assert old.status_code==200
        with zipfile.ZipFile(io.BytesIO(old.content)) as bundle:
            assert json.loads(bundle.read(original['path']))=={'id':1,'name':'甲'}

        assert client.delete(f'/api/semantic-workspace/tasks/{task}').status_code==200
        assert client.post(f'/api/semantic-workspace/tasks/{task}/restore').status_code==200
        restored=client.get(f'/api/semantic-workspace/tasks/{task}/source-bundle',params={'revision':1})
        assert restored.status_code==200
        assert runtime.start_calls==2
        from tests.test_source_deletion_delivery import _delete
        plan,operation=_delete(client,task,'delete_shared')
        assert any(item['kind']=='connector_artifact' for item in plan['objects'])
        with sqlite3.connect(settings.webui_db_path) as db:
            assert db.execute('SELECT COUNT(*) FROM source_artifacts').fetchone()[0]==0
        assert operation['state']=='completed'
        assert not list((tmp_path/'raw').rglob('raw-*'))
        assert not list((Path(settings.semantic_execution_root)/'frozen-web-sources').rglob('*.jsonl'))
