"""实际Pi复制路径和持久Run身份下的合成缓存清理。"""
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from tests.test_workspace_canvas import canvas,execution_owner


def test_stopped_run_copies_are_removed_without_touching_independent_output(canvas):
    from src.config.settings import settings
    from src.agentic_runtime.pi_runtime import PiRuntime
    from src.source_acquisition.deletion import _cleanup_runtime_copies
    _,_,task_id,_,upload=canvas
    root=Path(settings.semantic_execution_root)
    run_root=root/'agentic-vnext'/hashlib.sha256(b'user-a').hexdigest()[:16]/task_id/'r1'/'synthetic-run'
    inputs=run_root/'input';inputs.mkdir(parents=True)
    source=SimpleNamespace(upload_id=upload.upload_id,host_path=Path(upload.storage_path),original_name='source.csv',sha256=upload.sha256)
    request=SimpleNamespace(sources=[source])
    names=PiRuntime._copy_sources(request,inputs)
    assert (inputs/names[0]).read_bytes()==Path(upload.storage_path).read_bytes()
    independent=root/'independent-result.csv';independent.write_bytes(b'official independent')
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("UPDATE agentic_runtime_runs SET status='cancelled',run_id=?,workspace_root=? WHERE user_id=? AND task_id=? AND revision=1",('synthetic-run',str(run_root),'user-a',task_id))
    _cleanup_runtime_copies('user-a',{task_id})
    assert not run_root.exists()
    assert independent.read_bytes()==b'official independent'
    assert Path(upload.storage_path).is_file()


def test_historical_dependency_does_not_stop_or_clean_new_unrelated_revision(canvas):
    import asyncio,json
    from src.config.settings import settings
    from src.api.auth import get_store
    from src.source_acquisition import deletion,reuse
    _,_,target,_,upload=canvas
    store=get_store()
    old={'upload_id':upload.upload_id,'sha256':upload.sha256}
    fresh=reuse.uploads().save_bytes('user-a','fresh.csv',b'new\n1\n',media_type='text/csv')
    store.create_semantic_workspace_task('user-a',task_id='consumer',title='保留需求',objective_text='用户自己的新需求',upload_ids=[upload.upload_id],source_refs=[old],output_formats=['csv'],provider='local',model='fixture',external_api_confirmed=False)
    root=Path(settings.semantic_execution_root)/'agentic-vnext'/hashlib.sha256(b'user-a').hexdigest()[:16]/'consumer'/'r2'/'live-new'
    root.mkdir(parents=True);(root/'input.txt').write_text('新来源正文',encoding='utf-8')
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.row_factory=sqlite3.Row
        row=dict(connection.execute("SELECT * FROM semantic_workspace_revisions WHERE task_id='consumer'").fetchone())
        row.update(revision=2,source_refs_json=json.dumps([{'upload_id':fresh.upload_id,'sha256':fresh.sha256}]))
        connection.execute('INSERT INTO semantic_workspace_revisions ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        connection.execute("UPDATE semantic_workspace_tasks SET active_revision=2,status='running' WHERE task_id='consumer'")
        connection.execute("INSERT INTO agentic_runtime_runs(user_id,task_id,revision,runtime_version,permission_profile,status,run_id,workspace_root,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",('user-a','consumer',2,'pi','readonly','running','live-new',str(root),'now','now'))
    store.soft_delete_semantic_workspace_task('user-a',target)
    plan=deletion.deletion_plan('user-a',target,'delete_shared')
    op=deletion.begin_operation('user-a',target,plan['plan_token'],'delete_shared','exact-revision')
    calls=[]
    class Manager:
        async def cancel(self,owner,task): calls.append(('task',task))
        async def _confirm_runtime_stopped(self,owner,task,revision,**kwargs): calls.append(('run',task,revision));return True
    result=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
    assert result['state']=='completed',result
    assert ('task','consumer') not in calls
    assert ('run','consumer',2) not in calls
    assert (root/'input.txt').read_text(encoding='utf-8')=='新来源正文'
    assert store.get_semantic_workspace_task('user-a','consumer')['objective_text']=='用户自己的新需求'


def test_closed_worker_receipt_survives_cache_cleanup_and_new_manager_retry(canvas,monkeypatch):
    import asyncio
    from src.config.settings import settings
    from src.api.auth import get_store
    from src.source_acquisition import deletion
    _,_,task_id,_,upload=canvas
    root=Path(settings.semantic_execution_root)/'coremind-runs'/'coremind'/hashlib.sha256(b'user-a').hexdigest()[:16]/task_id/'r1'/'closed-run'
    root.mkdir(parents=True);(root/'worker-closed').write_text('closed',encoding='utf-8')
    (root/'input.txt').write_text('合成旧正文',encoding='utf-8')
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("UPDATE agentic_runtime_runs SET run_id='closed-run',workspace_root=? WHERE user_id='user-a' AND task_id=? AND revision=1",(str(root),task_id))
    get_store().soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id)
    op=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','worker-restart')
    calls=[]
    class Manager:
        async def cancel(self,*args): calls.append('cancel')
        async def _confirm_runtime_stopped(self,*args,**kwargs):
            calls.append('confirm')
            return (root/'worker-closed').is_file()
    original=deletion._remove_object
    monkeypatch.setattr(deletion,'_remove_object',lambda *args:(_ for _ in ()).throw(OSError('合成后项失败')))
    first=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
    assert first['state']=='incomplete'
    assert not root.exists()
    assert calls==['cancel','confirm']
    monkeypatch.setattr(deletion,'_remove_object',original)
    second=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
    assert second['state']=='completed',second
    assert calls==['cancel','confirm']


def test_legacy_attempt_and_checkpoint_bodies_removed_while_formal_result_survives(canvas):
    import json
    from src.config.settings import settings
    from src.api.auth import get_store
    from src.source_acquisition.deletion import _cleanup_runtime_copies
    from langgraph.checkpoint.sqlite import SqliteSaver
    _,_,task_id,task,upload=canvas
    store=get_store();run_id=task['run_id']
    # 此fixture只发布合成结果；另按真实Harness布局登记待清理的合成attempt副本。
    with sqlite3.connect(settings.webui_db_path) as connection:
        logical=connection.execute('SELECT logical_plan_id FROM semantic_harness_runs WHERE user_id=? AND run_id=?',('user-a',run_id)).fetchone()[0]
        copied=Path(settings.semantic_execution_root)/'user-a'/logical/run_id/'attempt-1'/'result.json'
        copied.parent.mkdir(parents=True,exist_ok=True);copied.write_text('合成中间正文',encoding='utf-8')
        connection.execute("INSERT INTO semantic_harness_attempts(attempt_id,run_id,user_id,node,attempt_number,idempotency_key,input_hash,status,artifact_paths_json,created_at) VALUES ('cleanup-attempt',?,'user-a','execute',1,'cleanup-copy','hash','succeeded',?,'now')",(run_id,json.dumps({'result':str(copied)})))
    # 公共attempt投影不暴露宿主路径；按刚登记的真实隔离路径核物理清理。
    paths=[copied]
    assert paths and any(path.is_file() for path in paths)
    output=store.get_semantic_delivery_output('user-a',task['delivery']['outputs'][0]['output_id'])
    formal=Path(output['file_path']);original=formal.read_bytes()
    checkpoint=Path(settings.semantic_execution_root)/'_checkpoints'/'semantic-harness.sqlite'
    checkpoint.parent.mkdir(exist_ok=True)
    with sqlite3.connect(checkpoint) as connection:
        saver=SqliteSaver(connection);saver.setup()
        connection.execute("INSERT OR REPLACE INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint) VALUES (?,'','delete-test',?)",(run_id,b'original copied body'))
        connection.execute("INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint) VALUES ('unrelated-thread','','kept',?)",(b'other task body',))
    _cleanup_runtime_copies('user-a',{task_id:{1}})
    assert all(not path.exists() for path in paths)
    assert formal.read_bytes()==original
    with sqlite3.connect(checkpoint) as connection:
        assert connection.execute('SELECT 1 FROM checkpoints WHERE thread_id=?',(run_id,)).fetchone() is None
        assert connection.execute("SELECT checkpoint FROM checkpoints WHERE thread_id='unrelated-thread'").fetchone()[0]==b'other task body'


async def _legacy_blocking_reader_case(monkeypatch,tmp_path):
    import asyncio,threading
    from src.semantic_harness import harness_adapters as adapters
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    def read(*args,**kwargs):
        entered.set();release.wait(5);finished.set();return object()
    monkeypatch.setattr(adapters.PhysicalPlan,'model_validate',lambda value:value)
    monkeypatch.setattr(adapters,'execute_physical_plan',read)
    monkeypatch.setattr('src.api.execution.execution_checkpoint',lambda:None)
    running=asyncio.create_task(adapters.TableHarnessAdapter().execute(None,None,[],profile=None,artifact_paths={},output_dir=tmp_path,physical_plan=object()))
    assert await asyncio.to_thread(entered.wait,3)
    try:
        running.cancel();await asyncio.sleep(0);await asyncio.sleep(0)
        assert not running.done(), '线程未退出时不得宣称Legacy已停止'
    finally:
        release.set()
        try: await running
        except asyncio.CancelledError: pass
        assert await asyncio.to_thread(finished.wait,3)


def test_legacy_cancellation_awaits_actual_reader_thread(monkeypatch,tmp_path):
    import asyncio
    asyncio.run(_legacy_blocking_reader_case(monkeypatch,tmp_path))


def test_legacy_unclosed_source_blocks_cleanup_then_same_operation_resumes(canvas,monkeypatch):
    import asyncio
    from src.config.settings import settings
    from src.api.auth import get_store
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
    from src.source_acquisition import SourceAcquisitionRepository
    from src.source_acquisition import deletion
    _,_,task_id,_,upload=canvas
    # Legacy修订没有Agentic登记；使用真实Manager的来源关闭判定，屏障保持读取未闭合。
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute('DELETE FROM agentic_runtime_runs WHERE user_id=? AND task_id=?',('user-a',task_id))
    get_store().soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id)
    operation=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','legacy-close')
    closed=False
    monkeypatch.setattr(SourceAcquisitionRepository,'task_attempts',lambda *args,**kwargs:['synthetic-open-attempt'])
    monkeypatch.setattr(SourceAcquisitionRepository,'cancel_for_task',lambda *args,**kwargs:closed)
    manager=SemanticWorkspaceManager()
    first=asyncio.run(deletion.continue_operation('user-a',operation['operation_id'],manager))
    assert first['state']=='incomplete' and first['error_code']=='runtime_stop_unconfirmed',first
    assert Path(upload.storage_path).is_file()
    closed=True
    second=asyncio.run(deletion.continue_operation('user-a',operation['operation_id'],manager))
    assert second['state']=='completed',second
    assert not Path(upload.storage_path).exists()


def test_another_manager_execution_lease_blocks_cleanup(canvas):
    import asyncio
    from src.api.auth import get_store
    from src.api.execution import execution_lock
    from src.source_acquisition import deletion
    _,_,task_id,_,upload=canvas
    store=get_store();store.soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id);op=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','worker-lease')
    class Manager:
        async def cancel(self,*args): return {'status':'cancelled'}
        async def _confirm_runtime_stopped(self,*args,**kwargs): return True
    lease=execution_lock(store,'user-a','workspace',task_id);lease.acquire(timeout=0)
    try:
        result=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
        assert result['state']=='incomplete',result
        assert Path(upload.storage_path).is_file()
    finally:lease.release()
    assert asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))['state']=='completed'


def test_unpublished_staging_requires_publication_lock_and_is_removed(canvas):
    import asyncio
    from src.api.auth import get_store
    from src.config.settings import settings
    from src.delivery_publishing.service import _publication_lock
    from src.source_acquisition import deletion
    _,_,task_id,task,upload=canvas
    root=Path(settings.semantic_execution_root);safe=lambda value:hashlib.sha256(value.encode()).hexdigest()[:16]
    base=root/safe('user-a')/safe(task_id)/'revision-1'/safe(task['run_id'])/'publications'/'pending-cleanup'
    staging=base/'.staging';staging.mkdir(parents=True);(staging/'copy.csv').write_text('合成未正式发布正文',encoding='utf-8')
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("INSERT INTO delivery_publish_intents(publication_key,command_hash,owner_id,task_id,task_revision,run_id,status,created_at,updated_at) VALUES ('pending-cleanup','hash','user-a',?,1,?,'committing','now','now')",(task_id,task['run_id']))
    store=get_store();store.soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id);op=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','pending-publication')
    class Manager:
        async def cancel(self,*args): pass
        async def _confirm_runtime_stopped(self,*args,**kwargs): return True
    with _publication_lock(root,'pending-cleanup',timeout=0):
        result=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
        assert result['state']=='incomplete',result
        assert staging.is_dir() and Path(upload.storage_path).is_file()
    result=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
    assert result['state']=='completed',result
    assert not base.exists()


def test_historical_stop_failure_does_not_rewrite_unrelated_active_revision(canvas,monkeypatch):
    import asyncio
    from src.config.settings import settings
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
    from src.source_acquisition import SourceAcquisitionRepository
    from src.api.auth import get_store
    _,_,task_id,_,_=canvas
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("UPDATE semantic_workspace_tasks SET active_revision=2,status='running' WHERE user_id='user-a' AND task_id=?",(task_id,))
        connection.execute("DELETE FROM agentic_runtime_runs WHERE user_id='user-a' AND task_id=?",(task_id,))
    monkeypatch.setattr(SourceAcquisitionRepository,'cancel_for_task',lambda *args,**kwargs:False)
    assert asyncio.run(SemanticWorkspaceManager()._confirm_runtime_stopped('user-a',task_id,1,deletion_revision_only=True)) is False
    saved=get_store().get_semantic_workspace_task('user-a',task_id)
    assert saved['active_revision']==2 and saved['status']=='running'


import pytest


@pytest.mark.parametrize('receipt_owner,receipt_state,wrong_run,accepted', [('user-a','completed',False,True),('user-b','completed',False,False),('user-a','incomplete',False,False),('user-a','completed',True,False)])
def test_completed_other_operation_stop_receipt_is_reused_per_exact_run(canvas,receipt_owner,receipt_state,wrong_run,accepted):
    import asyncio,json
    from src.config.settings import settings
    from src.api.auth import get_store
    from src.source_acquisition import deletion
    from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
    _,_,task_id,_,_=canvas
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.row_factory=sqlite3.Row
        row=dict(connection.execute('SELECT * FROM semantic_workspace_revisions WHERE user_id=? AND task_id=? AND revision=1',('user-a',task_id)).fetchone())
        old_run=connection.execute('SELECT COALESCE(a.run_id,r.run_id) FROM semantic_workspace_revisions r LEFT JOIN agentic_runtime_runs a ON a.user_id=r.user_id AND a.task_id=r.task_id AND a.revision=r.revision WHERE r.user_id=? AND r.task_id=? AND r.revision=1',('user-a',task_id)).fetchone()[0]
        row['revision']=2;row['run_id']='new-run'
        connection.execute('INSERT INTO semantic_workspace_revisions('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        connection.execute('UPDATE semantic_workspace_tasks SET active_revision=2 WHERE user_id=? AND task_id=?',('user-a',task_id))
        receipt={'stopped_tasks':[{'task_id':task_id,'runs':[{'revision':1,'run_id':'wrong-run' if wrong_run else old_run}]}]}
        connection.execute("INSERT INTO source_deletion_operations VALUES ('prior-cleanup',?,'old-producer','old-key','hash',?,?,NULL,'now','now')",(receipt_owner,json.dumps(receipt),receipt_state))
    get_store().soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id);op=deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','later-cleanup')
    seen=[]
    class Manager:
        async def cancel(self,*args): pass
        async def _confirm_runtime_stopped(self,owner,task,revision,**kwargs):
            seen.append(revision)
            if revision==1:
                # 原root已由前次清理删除；真实Adapter现在无法再次从Worker文件证明静止。
                try: CoreMindAgentKernelAdapter._assert_persisted_worker_closed(SimpleNamespace(execution_root=Path(settings.semantic_execution_root)/'coremind-runs'),owner,task,revision)
                except Exception:return False
            return True
    result=asyncio.run(deletion.continue_operation('user-a',op['operation_id'],Manager()))
    assert result['state']==('completed' if accepted else 'incomplete'),result
    assert seen==([2] if accepted else [1])
