"""隔离临时库验证确认到物理原件清理的真实API。"""
from pathlib import Path
from tests.test_workspace_canvas import canvas,execution_owner


def test_confirmed_deletion_removes_original_and_replays_after_task_purge(canvas):
    client,_,task_id,_,upload=canvas
    assert client.delete('/api/semantic-workspace/tasks/'+task_id).status_code==200
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan')
    assert plan.status_code==200,plan.text
    payload={key:plan.json()[key] for key in ('plan_token','shared_policy')}
    result=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json=payload,headers={'Idempotency-Key':'cleanup'})
    assert result.status_code==202,result.text
    assert result.json()['state']=='completed',result.text
    assert not Path(upload.storage_path).exists()
    import json,hashlib
    from src.api.auth import get_store
    tombstone=get_store().get_semantic_workspace_audit_tombstone('user-a',task_id)
    assert tombstone['task_id']==task_id and tombstone['user_id']=='user-a'
    assert tombstone['objective_sha256']==hashlib.sha256('提取谢超群工作量并输出Excel'.encode()).hexdigest()
    assert tombstone['source_refs']==[{'upload_id':upload.upload_id,'sha256':upload.sha256}]
    assert tombstone['purge_reason']=='user_permanent_delete'
    serialized=json.dumps(tombstone,ensure_ascii=False)
    assert '提取谢超群工作量并输出Excel' not in serialized
    assert upload.original_name not in serialized
    assert str(Path(upload.storage_path).parent) not in serialized
    assert client.get(f'/api/semantic-workspace/tasks/{task_id}').status_code==404
    replay=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json=payload,headers={'Idempotency-Key':'cleanup'})
    assert replay.status_code==202,replay.text
    assert replay.json()['operation_id']==result.json()['operation_id']


def test_old_upload_and_permanent_entries_cannot_bypass_confirmation(canvas):
    client,_,task_id,_,upload=canvas
    assert client.delete('/api/data-sources/uploads/'+upload.upload_id).status_code==409
    assert client.delete('/api/semantic-workspace/tasks/'+task_id).status_code==200
    denied=client.delete(f'/api/semantic-workspace/tasks/{task_id}/permanent')
    assert denied.status_code==409
    assert denied.headers['X-Mangrove-Deletion-Outcome']=='rejected'
    assert Path(upload.storage_path).is_file()


def test_unknown_key_lookup_is_not_success_and_cross_owner_is_hidden(canvas):
    client,owner,task_id,_,upload=canvas
    assert client.get('/api/semantic-workspace/deletion-operations/by-key',params={'task_id':task_id,'idempotency_key':'unknown'}).status_code==404
    owner['value']='user-b'
    denied=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan')
    assert denied.status_code==404
    assert Path(upload.storage_path).is_file()


def test_failed_cleanup_continues_same_operation_and_preserves_completed_objects(canvas,monkeypatch):
    from src.source_acquisition import deletion
    client,_,task_id,_,upload=canvas
    client.delete('/api/semantic-workspace/tasks/'+task_id)
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan').json()
    original=deletion._remove_object
    calls=[]
    def fail_second(owner,item):
        calls.append(item['source_key'])
        if len(calls)==2: raise OSError('合成磁盘故障')
        original(owner,item)
    monkeypatch.setattr(deletion,'_remove_object',fail_second)
    response=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={k:plan[k] for k in ('plan_token','shared_policy')},headers={'Idempotency-Key':'recover'})
    assert response.status_code==202,response.text
    assert response.json()['state']=='incomplete',response.text
    completed=response.json()['completed_source_keys']
    assert completed
    monkeypatch.setattr(deletion,'_remove_object',original)
    resumed=client.post('/api/semantic-workspace/deletion-operations/'+response.json()['operation_id']+'/resume',json={})
    assert resumed.status_code==202,resumed.text
    assert resumed.json()['state']=='completed',resumed.text
    assert set(completed)<=set(resumed.json()['completed_source_keys'])


def test_unlink_before_database_failure_recovers_only_persisted_removing_phase(canvas,monkeypatch):
    from src.source_acquisition import deletion
    client,_,task_id,_,upload=canvas
    client.delete('/api/semantic-workspace/tasks/'+task_id)
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan').json()
    original=deletion._connection
    failed=[]
    class Connection:
        def __init__(self): self.inner=original()
        def __getattr__(self,name): return getattr(self.inner,name)
        def execute(self,sql,*args):
            if "SET state='deleted',deleted_at=" in sql and not failed:
                failed.append(True)
                raise OSError('合成记账失败')
            return self.inner.execute(sql,*args)
    monkeypatch.setattr(deletion,'_connection',Connection)
    result=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={k:plan[k] for k in ('plan_token','shared_policy')},headers={'Idempotency-Key':'unlink-recovery'})
    assert result.status_code==202,result.text
    assert result.json()['state']=='incomplete'
    assert failed
    monkeypatch.setattr(deletion,'_connection',original)
    resumed=client.post('/api/semantic-workspace/deletion-operations/'+result.json()['operation_id']+'/resume',json={})
    assert resumed.status_code==202,resumed.text
    assert resumed.json()['state']=='completed',resumed.text


def test_changed_output_path_cannot_delete_other_owner_identical_bytes(canvas):
    import sqlite3
    from src.config.settings import settings
    from src.api.auth import get_store
    client,_,task_id,task,upload=canvas
    output=task['delivery']['outputs'][0]
    original=get_store().get_semantic_delivery_output('user-a',output['output_id'])
    victim=Path(settings.semantic_execution_root)/'other-owner'/'retained.csv'
    victim.parent.mkdir();victim.write_bytes(Path(original['file_path']).read_bytes())
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute('UPDATE semantic_delivery_outputs SET file_path=? WHERE user_id=? AND output_id=?',(get_store()._persist_semantic_path(victim),'user-a',output['output_id']))
    client.delete('/api/semantic-workspace/tasks/'+task_id)
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan').json()
    result=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={k:plan[k] for k in ('plan_token','shared_policy')},headers={'Idempotency-Key':'changed-path'})
    assert result.status_code==202,result.text
    assert result.json()['state']=='incomplete'
    assert result.json()['error_code']=='unsafe_output_path'
    assert victim.is_file()
    assert Path(original['file_path']).is_file()


def test_unknown_open_read_blocks_cleanup_without_ttl(canvas):
    from src.source_acquisition.reuse import SourceReadUse
    client,_,task_id,_,upload=canvas
    client.delete('/api/semantic-workspace/tasks/'+task_id)
    use=SourceReadUse('user-a',[{'upload_id':upload.upload_id,'sha256':upload.sha256}],operation='preview',task_id=task_id,revision=1)
    use.start()
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan',params={'shared_policy':'delete_shared'}).json()
    try:
        active=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={k:plan[k] for k in ('plan_token','shared_policy')},headers={'Idempotency-Key':'blocked-read'})
        assert active.status_code==409,active.text
    finally: use.finish(known=False)
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan',params={'shared_policy':'delete_shared'}).json()
    unknown=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={k:plan[k] for k in ('plan_token','shared_policy')},headers={'Idempotency-Key':'unknown-read'})
    assert unknown.status_code==202,unknown.text
    assert unknown.json()['state']=='incomplete'
    assert unknown.json()['error_code']=='source_in_use'
    assert Path(upload.storage_path).is_file()


def test_task_detail_body_is_read_under_persisted_use(canvas,monkeypatch):
    import sqlite3
    from src.config.settings import settings
    from src.api.routes import semantic_workspace as route
    client,_,task_id,_,upload=canvas
    original=route._task_detail
    observed=[]
    def read(*args,**kwargs):
        with sqlite3.connect(settings.webui_db_path) as connection:
            observed.append(connection.execute("SELECT COUNT(*) FROM source_read_uses WHERE owner_id='user-a' AND state='active'").fetchone()[0])
        return original(*args,**kwargs)
    monkeypatch.setattr(route,'_task_detail',read)
    result=client.get('/api/semantic-workspace/tasks/'+task_id)
    assert result.status_code==200,result.text
    assert observed and observed[0]>0


def test_resume_unknown_and_other_owner_are_hidden(canvas):
    client,identity,task_id,_,_=canvas
    assert client.post('/api/semantic-workspace/deletion-operations/unknown/resume',json={}).status_code==404
    client.delete('/api/semantic-workspace/tasks/'+task_id)
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan').json()
    operation=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={key:plan[key] for key in ('plan_token','shared_policy')},headers={'Idempotency-Key':'owner-resume'}).json()
    identity['value']='user-b'
    assert client.post('/api/semantic-workspace/deletion-operations/'+operation['operation_id']+'/resume',json={}).status_code==404


def test_binding_report_copy_is_redacted_and_cannot_be_read_after_deletion(tmp_path,monkeypatch):
    import json,sqlite3,asyncio
    from tests.test_semantic_binding_api import _make_client,_upload,_compile
    from src.api.auth import get_store
    from src.config.settings import settings
    from src.source_acquisition import deletion
    client,_=_make_client(tmp_path,monkeypatch,user_id='user-a')
    upload=_upload(tmp_path,'user-a','姓名,核销工作量天数,工作量费用\n谢超群,271828.125,271828.125\n')
    plan_id=_compile(client,upload.upload_id)
    response=client.post(f'/api/semantic-plans/{plan_id}/inspect-bind',json={'use_local_semantics':False})
    assert response.status_code==200,response.text
    with sqlite3.connect(settings.webui_db_path) as connection:
        original=connection.execute('SELECT reports_json,result_json,bound_plan_json FROM semantic_binding_revisions WHERE plan_id=?',(plan_id,)).fetchone()
        assert all('271828.125' in value for value in original)
    from tests.test_source_deletion import _add_consumer
    task=_add_consumer(get_store(),upload,'binding-cleanup')
    task_id=task['task_id'];get_store().soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id);op=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','binding-copy')
    class Manager:
        async def cancel(self,*args): pass
        async def _confirm_runtime_stopped(self,*args,**kwargs): return True
    assert asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))['state']=='completed'
    with sqlite3.connect(settings.webui_db_path) as connection:
        redacted=connection.execute('SELECT reports_json,result_json,bound_plan_json FROM semantic_binding_revisions WHERE plan_id=?',(plan_id,)).fetchone()
        assert all('271828.125' not in value for value in redacted)
        assert json.loads(redacted[0])[0]['source_deleted'] is True
    assert client.get(f'/api/semantic-plans/{plan_id}/bound-revisions/1').status_code in (404,409)
    import pytest
    from src.semantic_harness.inspection_models import SourceInspectionReport,BindResult
    stale=response.json()
    with pytest.raises(ValueError,match='source_deleted'):
        get_store().save_semantic_binding_revision('user-a',reports=[SourceInspectionReport.model_validate(item) for item in stale['reports']],result=BindResult.model_validate(stale['result']).model_copy(update={'binding_revision':2}))


def test_changed_reference_requires_same_operation_reconfirmation(canvas,monkeypatch):
    from src.source_acquisition import deletion
    from src.api.auth import get_store
    from tests.test_source_deletion import _add_consumer
    client,_,task_id,_,upload=canvas
    client.delete('/api/semantic-workspace/tasks/'+task_id)
    plan=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan').json()
    original=deletion._remove_object;count=[]
    def fail_second(owner,item):
        count.append(item['source_key'])
        if len(count)==2: raise OSError('合成后项故障')
        original(owner,item)
    monkeypatch.setattr(deletion,'_remove_object',fail_second)
    response=client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations',json={k:plan[k] for k in ('plan_token','shared_policy')},headers={'Idempotency-Key':'reconfirm'}).json()
    assert response['state']=='incomplete'
    completed=response['completed_source_keys'];assert completed
    # 模拟持久引用集合外来变化；正常创建入口已由删除意图阻止。
    _add_consumer(get_store(),upload,'late-consumer')
    monkeypatch.setattr(deletion,'_remove_object',original)
    path='/api/semantic-workspace/deletion-operations/'+response['operation_id']+'/resume'
    changed=client.post(path,json={}).json()
    assert changed['state']=='needs_confirmation',changed
    fresh=client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan?shared_policy=delete_shared').json()
    resumed=client.post(path,json={k:fresh[k] for k in ('plan_token','shared_policy')})
    assert resumed.status_code==202,resumed.text
    assert resumed.json()['state']=='completed',resumed.text
    assert resumed.json()['operation_id']==response['operation_id']
    assert set(completed)<=set(resumed.json()['completed_source_keys'])


def test_events_body_uses_frozen_sequence_under_source_use(canvas,monkeypatch):
    import sqlite3
    from src.api.auth import get_store
    from src.config.settings import settings
    from src.api.routes import semantic_workspace as route
    client,_,task_id,_,_=canvas
    store=get_store();original=store.list_semantic_workspace_events;seen=[]
    def read(owner,identity,**kwargs):
        with sqlite3.connect(settings.webui_db_path) as connection:
            seen.append(connection.execute("SELECT COUNT(*) FROM source_read_uses WHERE owner_id=? AND state='active'",(owner,)).fetchone()[0])
        store.append_semantic_workspace_event(owner,identity,stage='reading',event_type='source.observed',summary='晚到观察',details={'revision':1,'source_findings':['late-body-marker']})
        return original(owner,identity,**kwargs)
    monkeypatch.setattr(store,'list_semantic_workspace_events',read)
    result=client.get('/api/semantic-workspace/tasks/'+task_id+'/events')
    assert result.status_code==200,result.text
    assert seen and seen[0]>0
    assert 'late-body-marker' not in result.text


def test_binding_old_revision_and_list_lock_actual_frozen_source(tmp_path,monkeypatch):
    import sqlite3,json
    from tests.test_semantic_binding_api import _make_client,_upload,_compile
    from src.config.settings import settings
    from src.source_acquisition.reuse import source_locks
    client,_=_make_client(tmp_path,monkeypatch,user_id='user-a')
    source_a=_upload(tmp_path,'user-a','姓名,核销工作量天数,工作量费用\n谢超群,0.5,1200\n')
    plan_id=_compile(client,source_a.upload_id)
    assert client.post(f'/api/semantic-plans/{plan_id}/inspect-bind',json={'use_local_semantics':False}).status_code==200
    source_b=_upload(tmp_path,'user-a','姓名,核销工作量天数,工作量费用\n谢超群,1,2400\n')
    # 新逻辑版本改为B；旧binding1仍冻结A，不能借最新计划取得错误读取授权。
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.row_factory=sqlite3.Row
        old=dict(connection.execute('SELECT * FROM semantic_plan_revisions WHERE user_id=? AND plan_id=?',('user-a',plan_id)).fetchone())
        old['revision']=2
        payload=json.loads(old['plan_json']);payload['source_scope']['artifact_ids']=[source_b.upload_id]
        old['plan_json']=json.dumps(payload,ensure_ascii=False)
        connection.execute('INSERT INTO semantic_plan_revisions('+','.join(old)+') VALUES ('+','.join('?' for _ in old)+')',list(old.values()))
    with source_locks('user-a',[{'upload_id':source_a.upload_id}]):
        assert client.get(f'/api/semantic-plans/{plan_id}/bound-revisions/1').status_code==409
        assert client.get(f'/api/semantic-plans/{plan_id}/bound-revisions').status_code==409
    assert client.get(f'/api/semantic-plans/{plan_id}/bound-revisions/1').status_code==200
    from src.api.auth import get_store
    store=get_store();original=store.get_semantic_binding_revision;inserted=[]
    def late_binding(*args,**kwargs):
        if not inserted:
            inserted.append(True)
            with sqlite3.connect(settings.webui_db_path) as connection:
                connection.row_factory=sqlite3.Row
                row=dict(connection.execute('SELECT * FROM semantic_binding_revisions WHERE user_id=? AND plan_id=? AND binding_revision=1',('user-a',plan_id)).fetchone())
                row['binding_revision']=2
                connection.execute('INSERT INTO semantic_binding_revisions('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'get_semantic_binding_revision',late_binding)
    listed=client.get(f'/api/semantic-plans/{plan_id}/bound-revisions')
    assert listed.status_code==200,listed.text
    assert [row['binding_revision'] for row in listed.json()]==[1]


def test_reconfirmation_does_not_mutate_while_operation_lock_is_held(canvas):
    import sqlite3,hashlib
    from filelock import FileLock
    from src.config.settings import settings
    from src.source_acquisition import deletion
    from src.api.auth import get_store
    client,_,task_id,_,_=canvas
    get_store().soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id)
    operation=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','serial-reconfirm')
    operation_id=operation['operation_id']
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("UPDATE source_deletion_operations SET state='needs_confirmation' WHERE operation_id=?",(operation_id,))
        prior=connection.execute('SELECT plan_json FROM source_deletion_operations WHERE operation_id=?',(operation_id,)).fetchone()[0]
    fresh=deletion.deletion_plan('user-a',task_id,'delete_shared')
    path=Path(settings.webui_db_path).resolve().parent/'.source-read-locks'/('operation-'+hashlib.sha256(('user-a\0'+operation_id).encode()).hexdigest()+'.lock')
    with FileLock(str(path),timeout=0,thread_local=False):
        response=client.post('/api/semantic-workspace/deletion-operations/'+operation_id+'/resume',json={k:fresh[k] for k in ('plan_token','shared_policy')})
        assert response.status_code==202,response.text
        assert response.json()['state']=='needs_confirmation'
        with sqlite3.connect(settings.webui_db_path) as connection:
            assert connection.execute('SELECT plan_json FROM source_deletion_operations WHERE operation_id=?',(operation_id,)).fetchone()[0]==prior
