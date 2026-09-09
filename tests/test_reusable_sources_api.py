"""历史资料复用HTTP合同；仅合成资料与隔离数据库。"""
import pytest
from tests.test_workspace_canvas import execution_owner


@pytest.fixture
def canvas(tmp_path,monkeypatch):
    from tests.test_web_source_delivery_api import _client
    from tests.test_pi_runtime_workspace_api import _wait_for_delivery
    from src.services.upload_store import UploadStore
    from src.config.settings import settings
    client=_client(tmp_path,monkeypatch,role="admin")
    from src.api.routes import data_sources
    client.app.include_router(data_sources.router)
    upload=UploadStore(settings.data_prep_upload_root,max_bytes=1000).save_bytes("user-a","source.csv",b"name,value\nA,2\n",media_type="text/csv")
    with client:
        created=client.post("/api/semantic-workspace/tasks",json={"objective_text":"整理资料","upload_ids":[upload.upload_id],"output_formats":["json"],"runtime_version":"pi", "provider":"local"})
        assert created.status_code==202,created.text
        task_id=created.json()["task_id"]
        task=_wait_for_delivery(client,task_id)
        yield client,None,task_id,task,upload



def test_resolve_original_and_formal_output_without_new_task(canvas):
    client, _, _, task, upload = canvas
    output = task["delivery"]["outputs"][0]
    result = client.post("/api/semantic-workspace/reusable-sources/resolve", json={"upload_ids":[upload.upload_id], "delivery_output_ids":[output["output_id"]]})
    assert result.status_code == 200, result.text
    original, derived = result.json()["items"]
    assert original["identity"] == "original" and original["sha256"] == upload.sha256
    assert derived["identity"] == "derived" and derived["sha256"] == output["sha256"]
    assert derived["output_id"] == output["output_id"] and derived["availability"] == "available"
    assert "file_path" not in str(result.json()) and "storage_path" not in str(result.json())


def test_formal_output_is_frozen_directly_in_new_task_and_revision(canvas, monkeypatch):
    from src.api.routes import semantic_workspace as route
    from src.api.auth import get_store
    client, _, _, task, upload = canvas
    output = task["delivery"]["outputs"][0]
    monkeypatch.setattr(route.get_semantic_workspace_manager(), "enqueue", lambda *args:None)
    created = client.post("/api/semantic-workspace/tasks", json={"objective_text":"整理已生成资料", "upload_ids":[upload.upload_id], "delivery_output_ids":[output["output_id"]], "output_formats":["json"], "runtime_version":"pi", "provider":"local"})
    assert created.status_code == 202, created.text
    task_id = created.json()["task_id"]
    old = get_store().get_semantic_workspace_revision("user-a",task_id,1)
    assert len(old["source_refs"]) == 2
    assert old["source_refs"][1]["kind"] == "delivery_output"
    assert old["source_refs"][1]["output_id"] == output["output_id"]
    get_store().update_semantic_workspace_task("user-a",task_id,status="completed")
    revised = client.post("/api/semantic-workspace/tasks/"+task_id+"/revisions", json={"instruction":"只保留正式结果", "expected_active_revision":1, "upload_ids":[]})
    assert revised.status_code == 202, revised.text
    new = get_store().get_semantic_workspace_revision("user-a",task_id,2)
    assert new["source_refs"] == [old["source_refs"][1]]
    assert get_store().get_semantic_workspace_revision("user-a",task_id,1)["source_refs"] == old["source_refs"]


def test_output_preview_and_history_keep_formal_bytes_identity(canvas):
    client, _, _, task, _ = canvas
    output = task["delivery"]["outputs"][0]
    preview = client.get("/api/semantic-workspace/reusable-sources/outputs/"+output["output_id"]+"/preview")
    assert preview.status_code == 200, preview.text
    value = preview.json()
    assert value["representation"]["kind"] == "output"
    assert value["representation"]["sha256"] == output["sha256"]
    assert "item_refs" not in value
    history=client.get("/api/semantic-workspace/reusable-sources")
    assert history.status_code==200,history.text
    assert any(item.get("output_id")==output["output_id"] for item in history.json()["items"])


def test_history_does_not_advertise_known_invalid_formal_qa(canvas,monkeypatch):
    from src.source_acquisition import reuse
    client,_,_,task,_=canvas
    output=task["delivery"]["outputs"][0]
    store=reuse._store()
    original=store.get_semantic_delivery
    def invalid(owner,identity):
        import copy
        manifest=copy.deepcopy(original(owner,identity))
        if manifest:
            for item in manifest["outputs"]: item["qa"]["openable"]=False
        return manifest
    monkeypatch.setattr(store,"get_semantic_delivery",invalid)
    item=next(item for item in reuse.history("user-a") if item.get("output_id")==output["output_id"])
    assert item["availability"]=="unavailable"
    assert item["reason_code"]=="formal_evidence_invalid"


def test_independent_result_bundle_survives_missing_original(canvas):
    from pathlib import Path
    client,_,task_id,_,upload=canvas
    assert client.delete('/api/data-sources/uploads/'+upload.upload_id).status_code==409
    # 仅隔离夹具模拟历史原件丢失；正式关联清理由删除纵切面验证。
    Path(upload.storage_path).unlink()
    result=client.get('/api/semantic-workspace/tasks/'+task_id+'/bundle')
    assert result.status_code==200,result.text
    assert client.get('/api/semantic-workspace/tasks/'+task_id+'/bundle?include_sources=true').status_code==409


def test_derived_source_is_observed_without_upload_copy(canvas,monkeypatch):
    from src.api.routes import semantic_workspace as route
    from src.api.auth import get_store
    client,_,_,task,_=canvas
    output=task['delivery']['outputs'][0]
    monkeypatch.setattr(route.get_semantic_workspace_manager(),'enqueue',lambda *args:None)
    created=client.post('/api/semantic-workspace/tasks',json={'objective_text':'检查资料','delivery_output_ids':[output['output_id']],'output_formats':['json'],'runtime_version':'pi','provider':'local'})
    assert created.status_code==202,created.text
    selected=get_store().get_semantic_workspace_task('user-a',created.json()['task_id'])
    findings=route._workspace_source_findings('user-a',selected)
    assert findings and output['output_id'] in str(findings)


def test_legacy_rejects_formal_output_without_creating_task(canvas):
    from src.api.auth import get_store
    client,_,_,task,_=canvas
    before=get_store().list_semantic_workspace_tasks('user-a')
    result=client.post('/api/semantic-workspace/tasks',json={'objective_text':'整理','delivery_output_ids':[task['delivery']['outputs'][0]['output_id']],'output_formats':['json'],'runtime_version':'legacy','provider':'local'})
    assert result.status_code==422,result.text
    assert get_store().list_semantic_workspace_tasks('user-a')==before


def test_busy_source_freeze_rejects_create_and_revision_without_new_receipt(canvas,monkeypatch):
    from src.source_acquisition.reuse import source_locks
    from src.api.routes import semantic_workspace as route
    from src.api.auth import get_store
    client,_,task_id,task,upload=canvas
    monkeypatch.setattr(route.get_semantic_workspace_manager(),'enqueue',lambda *args:None)
    with source_locks('user-a',[{'upload_id':upload.upload_id}]):
        created=client.post('/api/semantic-workspace/tasks',headers={'Idempotency-Key':'busy-create'},json={'objective_text':'整理','upload_ids':[upload.upload_id],'runtime_version':'pi','output_formats':['json'],'provider':'local'})
        assert created.status_code==409 and created.json()['detail']=='source_in_use',created.text
        revised=client.post('/api/semantic-workspace/tasks/'+task_id+'/revisions',headers={'Idempotency-Key':'busy-revision'},json={'instruction':'更新要求','expected_active_revision':1})
        assert revised.status_code==409 and revised.json()['detail']=='source_in_use',revised.text
        assert 'X-Mangrove-Revision-Outcome' not in revised.headers
    assert get_store().get_semantic_workspace_task('user-a',task_id)['active_revision']==1


def test_saved_web_readers_share_lock_and_reject_changed_body(tmp_path,monkeypatch):
    import httpx,sqlite3
    from contextlib import closing
    from tests.test_source_acquisition_api import _client
    from src.config.settings import settings
    from src.api import auth
    from src.source_acquisition.reuse import source_locks
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,text='<html><body>原始事实</body></html>',headers={'content-type':'text/html'})
    client,database,owner=_client(tmp_path,monkeypatch,handler)
    monkeypatch.setattr(settings,'webui_db_path',str(database))
    monkeypatch.setattr(auth,'_store',None)
    with client:
        result=client.post('/api/semantic-workspace/source-acquisitions',headers={'Idempotency-Key':'saved'},json={'url':'https://example.com/data','purpose':'读取'})
        assert result.status_code==202,result.text
        data=result.json();snapshot=data['snapshot'];artifact=snapshot['artifacts'][0]
        ref={'kind':'web_artifact','artifact_id':artifact['artifact_id'],'snapshot_id':snapshot['snapshot_id'],'sha256':artifact['content_sha256']}
        paths=['source-acquisitions/'+data['attempt_id'],'source-snapshots/'+snapshot['snapshot_id'],'source-artifacts/'+artifact['artifact_id']]
        for path in paths:
            assert client.get('/api/semantic-workspace/'+path).status_code==200
        with source_locks('owner-a',[ref]):
            for path in paths: assert client.get('/api/semantic-workspace/'+path).status_code==409
        with closing(sqlite3.connect(database)) as connection:
            # 仅临时库模拟宿主存储损坏，立即恢复原触发器合同。
            trigger=connection.execute("SELECT sql FROM sqlite_master WHERE name='source_artifacts_no_update'").fetchone()[0]
            connection.execute('DROP TRIGGER source_artifacts_no_update')
            connection.execute('UPDATE source_artifacts SET content_blob=? WHERE artifact_id=?',(b'changed',artifact['artifact_id']))
            connection.execute(trigger);connection.commit()
        for path in paths: assert client.get('/api/semantic-workspace/'+path).status_code==409
        owner['value']='owner-b'
        for path in paths: assert client.get('/api/semantic-workspace/'+path).status_code==404
    assert len(calls)==1


def test_attempt_status_does_not_read_new_snapshot_after_empty_identity_capture(monkeypatch):
    from types import SimpleNamespace
    from src.api.routes import source_acquisition as route
    calls=[]
    def captured(owner,identity,*,include_snapshot=True):
        calls.append(include_snapshot)
        assert include_snapshot is False
        # 元数据返回后采集可已完成；本次只返回已捕获的无快照状态。
        return {'attempt_id':identity,'snapshot_id':None,'snapshot':None,'status':'acquiring'}
    def forbidden(*args,**kwargs): raise AssertionError('空集合不能读取稍后出现的正文')
    monkeypatch.setattr(route,'get_source_acquisition_service',lambda:SimpleNamespace(repository=SimpleNamespace(get_attempt=captured,get_snapshot=forbidden)))
    result=route.get_source_acquisition('pending',user={'user_id':'owner'})
    assert result['snapshot'] is None and calls==[False]


@pytest.mark.parametrize('with_upload',[False,True])
def test_reuse_need_checks_derived_actual_format_before_prepare(canvas,monkeypatch,with_upload):
    from src.api.routes import semantic_workspace as route
    from src.capability_catalog import SqliteCapabilityCatalogRepository
    from src.conversation_steering import CapabilityPack,ProcedureScope,CapabilityMaturity
    from src.semantic_harness.capabilities import TABLE_DUCKDB_MANIFEST
    from src.config.settings import settings
    from tests.test_capability_reuse import need
    client,_,_,task,upload=canvas
    monkeypatch.setattr(settings,'pi_capability_host_enabled',True)
    SqliteCapabilityCatalogRepository(settings.webui_db_path).save_pack(CapabilityPack(pack_id='table.duckdb',version='1.0.0',digest='sha256:'+'a'*64,scope=ProcedureScope.PERSONAL,owner_id='user-a',maturity=CapabilityMaturity.VERIFIED,source_provenance=('https://github.com/duckdb/duckdb',),manifest=(('reuse_contract',TABLE_DUCKDB_MANIFEST.model_dump_json()),('license','MIT'))))
    monkeypatch.setattr(route,'_check_freeze_gate',lambda *args,**kwargs:None)
    async def forbidden(*args,**kwargs): raise AssertionError('格式不匹配不能准备Runtime')
    monkeypatch.setattr(route.get_semantic_workspace_manager(),'prepare_runtime_binding',forbidden)
    result=client.post('/api/semantic-workspace/tasks',json={'objective_text':'筛选','upload_ids':[upload.upload_id] if with_upload else [],'delivery_output_ids':[task['delivery']['outputs'][0]['output_id']],'runtime_version':'pi','provider':'local','output_formats':['parquet'],'capability_need':need().model_dump(mode='json')})
    assert result.status_code==409,result.text
    assert '格式' in result.json()['detail']


@pytest.mark.parametrize('mode',['cancel_now','new_task'])
def test_confirmed_sibling_cannot_freeze_busy_derived_source(canvas,monkeypatch,mode):
    from src.api.routes import semantic_workspace as route
    from src.api.auth import get_store
    from src.source_acquisition.reuse import source_locks
    from src.conversation_steering import ContextDelta,DeltaConfidence,TurnIntent
    client,_,_,producer,_=canvas
    manager=route.get_semantic_workspace_manager()
    monkeypatch.setattr(manager,'enqueue',lambda *args:None)
    output_id=producer['delivery']['outputs'][0]['output_id']
    created=client.post('/api/semantic-workspace/tasks',json={'objective_text':'整理来源','delivery_output_ids':[output_id],'output_formats':['json'],'runtime_version':'pi','provider':'local'})
    assert created.status_code==202,created.text
    task_id=created.json()['task_id'];store=get_store()
    store.update_semantic_workspace_task('user-a',task_id,status='completed')
    class Rewrite:
        async def rewrite(self,turn,request):
            return ContextDelta(delta_id='rule-'+turn.turn_id,owner_id=turn.owner_id,task_id=turn.task_id,inherited_revision=turn.revision,source_turn_ids=(turn.turn_id,),intent=TurnIntent.TASK_REFINEMENT,confidence=DeltaConfidence.HIGH,normalized_text=request.text,field_semantics_delta={'值':'保留原值'})
    monkeypatch.setattr(route,'build_context_rewriter',lambda *args,**kwargs:Rewrite())
    proposed=client.post('/api/semantic-workspace/tasks/'+task_id+'/turns',json={'text':'值字段保留原值'})
    assert proposed.status_code==200,proposed.text
    before=len(store.list_semantic_workspace_tasks('user-a'))
    with source_locks('user-a',[{'kind':'delivery_output','output_id':output_id}]):
        response=client.post('/api/semantic-workspace/tasks/'+task_id+'/revision-proposals/'+proposed.json()['proposal_id']+'/decision',json={'mode':mode})
        assert response.status_code==409 and response.json()['detail']=='source_in_use',response.text
    assert store.get_semantic_workspace_task('user-a',task_id)['active_revision']==1
    assert len(store.list_semantic_workspace_tasks('user-a'))==before


def test_canvas_verified_output_preserves_identity_404_and_digest_409(canvas):
    from src.api.routes import semantic_workspace as route
    from fastapi import HTTPException
    client,_,_,task,_=canvas
    manifest=task['delivery'];output=manifest['outputs'][0]
    with pytest.raises(HTTPException) as identity:
        route._verified_canvas_output('user-a','other-run',manifest,output)
    assert identity.value.status_code==404
    with pytest.raises(HTTPException) as digest:
        route._verified_canvas_output('user-a',manifest['run_id'],manifest,{**output,'sha256':'0'*64})
    assert digest.value.status_code==409
