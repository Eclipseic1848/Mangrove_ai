"""关联清理只沿本人冻结身份；预检不读取正文、不改变任务。"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3

from src.config.settings import settings
from src.source_acquisition import reuse


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _connection():
    connection=sqlite3.connect(settings.webui_db_path)
    connection.row_factory=sqlite3.Row
    return connection


def deletion_plan(owner_id,task_id,shared_policy='keep_shared'):
    if shared_policy not in {'keep_shared','delete_shared'}:
        raise ValueError('请选择共享资料处理方式')
    refs={}
    with closing(_connection()) as connection:
        connection.execute('BEGIN')
        task=connection.execute('SELECT * FROM semantic_workspace_tasks WHERE user_id=? AND task_id=?',(owner_id,task_id)).fetchone()
        if task is None:
            raise PermissionError('任务不存在或无权访问')
        if not task['deleted_at']:
            raise ValueError('请先移入回收站')
        revisions=connection.execute('SELECT source_refs_json,run_id FROM semantic_workspace_revisions WHERE user_id=? AND task_id=? ORDER BY revision',(owner_id,task_id)).fetchall()
        for row in revisions:
            for ref in json.loads(row['source_refs_json'] or '[]'):
                refs[reuse.source_key(ref)]=ref
        own_deliveries=set()
        for row in connection.execute('SELECT delivery_id FROM formal_delivery_runs WHERE owner_id=? AND task_id=?',(owner_id,task_id)):
            own_deliveries.add(row[0])
        for table,owner_column in (('formal_delivery_outputs','owner_id'),('semantic_delivery_outputs','user_id')):
            for row in connection.execute(f'SELECT output_id,delivery_id,run_id,sha256 FROM {table} WHERE {owner_column}=?',(owner_id,)):
                if row['delivery_id'] in own_deliveries or row['run_id'] in {r['run_id'] for r in revisions if r['run_id']}:
                    refs['delivery_output:'+row['output_id']]=dict(kind='delivery_output',output_id=row['output_id'],sha256=row['sha256'])
                    own_deliveries.add(row['delivery_id'])
    objects=[];blockers=[];affected={}
    for key,ref in sorted(refs.items()):
        # 连续清理只跳过精确身份已由已完成操作清除的对象；旧墓碑不改写。
        with closing(_connection()) as connection:
            completed=connection.execute("SELECT 1 FROM source_deletions d JOIN source_deletion_operations o ON o.operation_id=d.operation_id AND o.owner_id=d.owner_id WHERE d.owner_id=? AND d.source_key=? AND d.sha256=? AND d.state='deleted' AND o.state='completed'",(owner_id,key,ref.get('sha256'))).fetchone()
        if completed:
            continue
        kind=ref.get('kind','upload');identity=key.split(':',1)[1]
        references=reuse.references(owner_id,kind,identity)
        if kind=='delivery_output':
            with closing(_connection()) as connection:
                producer=connection.execute('SELECT r.task_id,r.task_revision,r.delivery_id,r.run_id,t.deleted_at,t.task_id AS retained_task_id FROM formal_delivery_runs r JOIN formal_delivery_outputs o ON o.delivery_id=r.delivery_id AND o.owner_id=r.owner_id LEFT JOIN semantic_workspace_tasks t ON t.task_id=r.task_id AND t.user_id=r.owner_id WHERE r.owner_id=? AND o.output_id=?',(owner_id,identity)).fetchone()
            if producer and producer['task_id']!=task_id:
                references.append(dict(reference_kind='producer',task_id=producer['task_id'],revision=producer['task_revision'],delivery_id=producer['delivery_id'],run_id=producer['run_id'],state='retained',use_id=None,task_exists=producer['retained_task_id'] is not None))
        external=[r for r in references if r.get('use_id') or (r.get('task_id')!=task_id and r.get('delivery_id') not in own_deliveries)]
        for item in external:
            if item.get('task_id'):
                affected[item['task_id']]=dict(task_id=item['task_id'],task_exists=item.get('task_exists',True),in_recycle_bin=item.get('in_recycle_bin'))
        try:
            public=_metadata(owner_id,ref)
        except (ValueError,PermissionError,OSError):
            blockers.append(dict(code='source_unavailable',message='来源不可核验：'+key))
            public=dict(source_key=key,kind=kind,identity='derived' if kind=='delivery_output' else 'original',sha256=ref.get('sha256'),label='不可用资料',acquired_at=None,time_kind='unknown')
        public.update(disposition='keep_shared' if external and shared_policy=='keep_shared' else 'delete',references=references)
        objects.append(public)
    payload=dict(task_id=task_id,shared_policy=shared_policy,objects=objects,affected_tasks=list(affected.values()),can_execute=not blockers,blockers=blockers)
    payload['plan_token']=hashlib.sha256(_json(dict(owner_id=owner_id,**payload)).encode('utf-8')).hexdigest()
    return payload


def _metadata(owner_id,ref):
    kind=ref.get('kind','upload');key=reuse.source_key(ref)
    with closing(_connection()) as connection:
        deleted=connection.execute("SELECT sha256,snapshot_id FROM source_deletions WHERE owner_id=? AND source_key=? AND state IN ('deleted','removing')",(owner_id,key)).fetchone()
    if deleted:
        return dict(source_key=key,kind=kind,identity='derived' if kind=='delivery_output' else 'original',sha256=deleted['sha256'],label='本操作已清理资料',acquired_at=None,time_kind='unknown',**{k:v for k,v in ref.items() if k in {'upload_id','artifact_id','output_id','snapshot_id'}})
    if kind=='upload':
        store=reuse.uploads();identity=ref['upload_id']
        item=store._load_sidecar(store._user_dir(owner_id,'objects')/(identity+'.meta'),user_id=owner_id,upload_id=identity)
        result=dict(source_key=key,kind=kind,identity='original',upload_id=identity,label=item.original_name,sha256=item.sha256,media_type=item.media_type,size_bytes=item.size_bytes,acquired_at=item.created_at,time_kind='acquired' if item.created_at else 'unknown')
    elif kind=='web_artifact':
        with closing(_connection()) as connection:
            row=connection.execute('SELECT artifact_id,snapshot_id,content_sha256,media_type,size_bytes,title,read_at FROM source_artifacts WHERE owner_id=? AND artifact_id=?',(owner_id,ref['artifact_id'])).fetchone()
        if row is None: raise PermissionError('网页原件不存在')
        result=dict(source_key=key,kind=kind,identity='original',artifact_id=row['artifact_id'],snapshot_id=row['snapshot_id'],label=row['title'] or '网页资料',sha256=row['content_sha256'],media_type=row['media_type'],size_bytes=row['size_bytes'],acquired_at=row['read_at'],time_kind='acquired')
    else:
        item=reuse.verified_output_metadata(owner_id,ref['output_id'])
        result=dict(source_key=key,kind=kind,identity='derived',output_id=ref['output_id'],delivery_id=item['delivery_id'],run_id=item['run_id'],label=item['filename'],sha256=item['sha256'],media_type=item['media_type'],size_bytes=item['size_bytes'],acquired_at=item['created_at'],time_kind='generated')
    if ref.get('sha256') and result['sha256']!=ref['sha256']: raise ValueError('来源登记身份变化')
    return result

from datetime import datetime,timezone
from contextvars import ContextVar
from uuid import uuid4

_cleanup_operation=ContextVar('source_cleanup_operation',default=None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ref(item):
    kind=item['kind']
    if kind=='upload': return dict(upload_id=item['upload_id'],sha256=item['sha256'])
    if kind=='web_artifact': return dict(kind=kind,artifact_id=item['artifact_id'],snapshot_id=item['snapshot_id'],sha256=item['sha256'])
    return dict(kind=kind,output_id=item['output_id'],sha256=item['sha256'])


def assert_sources_readable(owner_id,refs,*,connection=None):
    def check(conn):
        for ref in refs:
            row=conn.execute('SELECT operation_id,state FROM source_deletions WHERE owner_id=? AND source_key=?',(owner_id,reuse.source_key(ref))).fetchone()
            if row and row[0]!=_cleanup_operation.get():
                raise ValueError('source_deleted' if row[1]=='deleted' else 'source_deleting')
    if connection is not None: check(connection)
    else:
        with closing(_connection()) as conn: check(conn)


def _public_operation(row):
    plan=json.loads(row['plan_json'])
    with closing(_connection()) as connection:
        completed=[r[0] for r in connection.execute("SELECT source_key FROM source_deletions WHERE owner_id=? AND operation_id=? AND state='deleted' ORDER BY source_key",(row['owner_id'],row['operation_id']))]
    return dict(operation_id=row['operation_id'],task_id=row['task_id'],state=row['state'],completed_source_keys=completed,retained_source_keys=[x['source_key'] for x in plan['objects'] if x['disposition']=='keep_shared'],affected_tasks=plan['affected_tasks'],error_code=row['error_code'],message=row['error_code'])


def get_operation(owner_id,operation_id):
    with closing(_connection()) as connection:
        row=connection.execute('SELECT * FROM source_deletion_operations WHERE owner_id=? AND operation_id=?',(owner_id,operation_id)).fetchone()
    if row is None: raise PermissionError('清理操作不存在或无权访问')
    return _public_operation(row)


def operation_by_key(owner_id,task_id,key):
    with closing(_connection()) as connection:
        row=connection.execute('SELECT * FROM source_deletion_operations WHERE owner_id=? AND task_id=? AND idempotency_key=?',(owner_id,task_id,key)).fetchone()
    return _public_operation(row) if row else None


def begin_operation(owner_id,task_id,token,policy,key):
    request_hash=hashlib.sha256(_json([task_id,token,policy]).encode()).hexdigest()
    with closing(_connection()) as connection:
        existing=connection.execute('SELECT * FROM source_deletion_operations WHERE owner_id=? AND task_id=? AND idempotency_key=?',(owner_id,task_id,key)).fetchone()
    if existing:
        if existing['request_hash']!=request_hash: raise ValueError('idempotency_conflict')
        return _public_operation(existing)
    plan=deletion_plan(owner_id,task_id,policy)
    if not plan['can_execute']: raise ValueError('source_unavailable')
    refs=[_ref(x) for x in plan['objects']]
    with reuse.source_locks(owner_id,refs):
        plan=deletion_plan(owner_id,task_id,policy)
        if plan['plan_token']!=token: raise ValueError('confirmation_changed')
        if not plan['can_execute']: raise ValueError('source_unavailable')
        operation_id='deletion_'+uuid4().hex
        # 持久意图先于正文校验；短锁内占位，停止Worker必须在锁外进行。
        minimal=dict(task_id=task_id,shared_policy=policy,objects=[{k:v for k,v in x.items() if k not in {'label','acquired_at','time_kind','media_type','size_bytes'}} for x in plan['objects']],affected_tasks=plan['affected_tasks'])
        with closing(_connection()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing=connection.execute('SELECT * FROM source_deletion_operations WHERE owner_id=? AND task_id=? AND idempotency_key=?',(owner_id,task_id,key)).fetchone()
            if existing:
                if existing['request_hash']!=request_hash: raise ValueError('idempotency_conflict')
                return _public_operation(existing)
            # SQLite写锁覆盖最终引用快照到删除意图，发布提交不能插入这个窗口。
            final_plan=deletion_plan(owner_id,task_id,policy)
            if final_plan['plan_token']!=token: raise ValueError('confirmation_changed')
            from src.account_execution import current_authorization,require_authorized
            authorization=current_authorization(required=True)
            if authorization.owner_user_id!=owner_id: raise PermissionError('Owner不匹配')
            require_authorized(connection,authorization)
            assert_sources_readable(owner_id,refs,connection=connection)
            connection.execute('INSERT INTO source_deletion_operations VALUES (?,?,?,?,?,?,?,?,?,?)',(operation_id,owner_id,task_id,key,request_hash,_json(minimal),'planned',None,_now(),_now()))
            for item in minimal['objects']:
                if item['disposition']=='delete':
                    connection.execute('INSERT INTO source_deletions VALUES (?,?,?,?,?,?,NULL)',(owner_id,item['source_key'],operation_id,item['sha256'],item.get('snapshot_id'),'deleting'))
            connection.commit()
    return get_operation(owner_id,operation_id)


def _set_state(owner_id,operation_id,state,error=None):
    with closing(_connection()) as connection:
        connection.execute('UPDATE source_deletion_operations SET state=?,error_code=?,updated_at=? WHERE owner_id=? AND operation_id=?',(state,error,_now(),owner_id,operation_id));connection.commit()


def source_integrity(owner_id,refs):
    with closing(_connection()) as connection:
        deleted=[reuse.source_key(ref) for ref in refs if connection.execute("SELECT 1 FROM source_deletions WHERE owner_id=? AND source_key=? AND sha256=? AND state='deleted'",(owner_id,reuse.source_key(ref),ref.get('sha256'))).fetchone()]
    return dict(state='source_deleted' if deleted else 'intact',deleted_source_keys=deleted,can_rerun=not deleted,can_reverify=not deleted)


def _remove_object(owner_id,item):
    from pathlib import Path
    key=item['source_key'];ref=_ref(item)
    with closing(_connection()) as connection:
        phase=connection.execute('SELECT state FROM source_deletions WHERE owner_id=? AND source_key=?',(owner_id,key)).fetchone()[0]
    if item['kind'] in {'upload','delivery_output'}:
        if item['kind']=='upload':
            store=reuse.uploads();base=store._user_dir(owner_id,'objects')
            path=store._confined_path(base,base/item['upload_id'])
        else:
            path=_output_delete_path(owner_id,item)
        if path.exists():
            reuse.resolve_frozen_source(owner_id,ref)
            # 每项字节已核后记录删除阶段；重启仅此阶段允许确认原路径已经缺失。
            with closing(_connection()) as connection:
                connection.execute("UPDATE source_deletions SET state='removing' WHERE owner_id=? AND source_key=?",(owner_id,key));connection.commit()
            path.unlink()
        elif phase!='removing':
            raise ValueError('source_missing_before_removal')
        if item['kind']=='upload':
            meta=store._confined_path(base,base/(item['upload_id']+'.meta'))
            if meta.exists():
                registered=store._load_sidecar(meta,user_id=owner_id,upload_id=item['upload_id'])
                if registered.sha256!=item['sha256']: raise ValueError('source_identity_changed')
                meta.unlink()
    else:
        reuse.resolve_frozen_source(owner_id,ref)
    with closing(_connection()) as connection:
        connection.execute('BEGIN IMMEDIATE')
        identity=key.split(':',1)[1]
        connection.execute('DELETE FROM source_inspection_reports WHERE user_id=? AND artifact_id=?',(owner_id,identity))
        # 绑定保存了检查报告的正文副本；保留冻结身份，清除精确来源的观察正文。
        for binding in connection.execute('SELECT plan_id,binding_revision,reports_json,result_json,bound_plan_json FROM semantic_binding_revisions WHERE user_id=?',(owner_id,)).fetchall():
            reports=json.loads(binding['reports_json'])
            changed=False
            for index,report in enumerate(reports):
                if report.get('artifact_id')==identity and report.get('artifact_sha256')==item['sha256']:
                    reports[index]={k:report[k] for k in ('artifact_id','artifact_sha256','inspection_id','inspector_version') if k in report}
                    reports[index]['source_deleted']=True
                    changed=True
            if changed:
                def redact(value,matched=False):
                    if isinstance(value,list): return [redact(item,matched) for item in value]
                    if not isinstance(value,dict): return value
                    matched=matched or value.get('artifact_id')==identity
                    return {key:([] if matched and key in {'evidence_samples','samples','evidence_reasons'} else redact(child,matched)) for key,child in value.items()}
                result=redact(json.loads(binding['result_json']))
                # 此显示候选由样例拼接，不能把旧观察保留为仍可确认的提示。
                if result.get('clarification'):
                    result['clarification']['candidates']=[]
                bound=redact(json.loads(binding['bound_plan_json'])) if binding['bound_plan_json'] else None
                connection.execute('UPDATE semantic_binding_revisions SET reports_json=?,result_json=?,bound_plan_json=? WHERE user_id=? AND plan_id=? AND binding_revision=?',(_json(reports),_json(result),_json(bound) if bound is not None else None,owner_id,binding['plan_id'],binding['binding_revision']))

        if item['kind']=='web_artifact':
            connection.execute('DELETE FROM source_artifacts WHERE owner_id=? AND artifact_id=?',(owner_id,identity))
            snapshot=item['snapshot_id']
            cached=Path(settings.semantic_execution_root).resolve()/'frozen-web-sources'/hashlib.sha256(owner_id.encode()).hexdigest()[:16]/(hashlib.sha256(identity.encode()).hexdigest()[:24]+'.html')
            if cached.is_symlink() or cached.resolve()!=cached: raise ValueError('unsafe_web_cache_path')
            if cached.exists():
                if reuse._digest(cached)!=item['sha256']: raise ValueError('web_cache_identity_changed')
                cached.unlink()
            if not connection.execute('SELECT 1 FROM source_artifacts WHERE owner_id=? AND snapshot_id=?',(owner_id,snapshot)).fetchone():
                connection.execute('DELETE FROM source_page_failures WHERE owner_id=? AND snapshot_id=?',(owner_id,snapshot))
                connection.execute("UPDATE source_snapshots SET allowed_scope_json='{}',coverage_json='{}',valid_page_count=0,failed_page_count=0 WHERE owner_id=? AND snapshot_id=?",(owner_id,snapshot))
                connection.execute("UPDATE source_acquisition_attempts SET request_url='',normalized_url='',allowed_scope_json='{}',purpose='',error_message=NULL,search_report_json=NULL WHERE owner_id=? AND snapshot_id=?",(owner_id,snapshot))
        connection.execute("UPDATE source_deletions SET state='deleted',deleted_at=? WHERE owner_id=? AND source_key=?",(_now(),owner_id,key));connection.commit()


def _cleanup_runtime_copies(owner_id,task_ids):
    from pathlib import Path
    import shutil
    root=Path(settings.semantic_execution_root).resolve()
    with closing(_connection()) as connection:
        for task_id in task_ids:
            selected=task_ids[task_id] if isinstance(task_ids,dict) else None
            # 正式结果逐项处理；仅无正式登记的精确发布副本可整目录清除。
            from src.delivery_publishing.service import _publication_lock
            intents=connection.execute('SELECT publication_key,task_revision,run_id FROM delivery_publish_intents WHERE owner_id=? AND task_id=?',(owner_id,task_id)).fetchall()
            for intent in intents:
                if selected is not None and intent['task_revision'] not in selected: continue
                with _publication_lock(root,intent['publication_key'],timeout=0):
                    if connection.execute('SELECT 1 FROM formal_delivery_runs WHERE owner_id=? AND publication_key=?',(owner_id,intent['publication_key'])).fetchone(): continue
                    safe=lambda value:hashlib.sha256(value.encode()).hexdigest()[:16]
                    base=root/safe(owner_id)/safe(task_id)/('revision-'+str(intent['task_revision']))/safe(intent['run_id'])/'publications'/intent['publication_key']
                    if not base.is_relative_to(root) or any(part in {'.','..'} for part in Path(intent['publication_key']).parts): raise ValueError('unsafe_publication_path')
                    cursor=base
                    while cursor!=root:
                        if cursor.is_symlink() or getattr(cursor,'is_junction',lambda:False)(): raise ValueError('unsafe_publication_path')
                        cursor=cursor.parent
                    if base.exists(): shutil.rmtree(base)
                    connection.execute("UPDATE delivery_publish_intents SET status='aborted',manifest_json=NULL,error_json=?,updated_at=? WHERE owner_id=? AND publication_key=?",(_json({'reason':'source_deleted'}),_now(),owner_id,intent['publication_key']))
            rows=connection.execute('SELECT revision,run_id,workspace_root FROM agentic_runtime_runs WHERE user_id=? AND task_id=?',(owner_id,task_id)).fetchall()
            if selected is not None: rows=[row for row in rows if row['revision'] in selected]
            for row in rows:
                if row['workspace_root']:
                    path=Path(row['workspace_root']).resolve()
                    owner=hashlib.sha256(owner_id.encode()).hexdigest()[:16]
                    expected=[root/family/owner/task_id/('r'+str(row['revision']))/str(row['run_id']) for family in ('agentic-vnext','coremind-runs/coremind')]
                    if path not in expected or path==root or not path.is_relative_to(root) or Path(row['workspace_root']).is_symlink():
                        raise ValueError('unsafe_runtime_path')
                    # 正式Publisher在独立publications目录；只删精确已停止Run副本。
                    if path.exists(): shutil.rmtree(path)
                connection.execute("UPDATE agentic_runtime_runs SET request_json=NULL,candidates_json='[]',verification_json=NULL,failure_json=NULL WHERE user_id=? AND task_id=? AND revision=?",(owner_id,task_id,row['revision']))
            for runtime_row in rows:
                connection.execute('DELETE FROM agentic_runtime_events WHERE user_id=? AND task_id=? AND revision=?',(owner_id,task_id,runtime_row['revision']))
            for event in connection.execute("SELECT event_id,details_json FROM semantic_workspace_events WHERE user_id=? AND task_id=? AND event_type='source.observed'",(owner_id,task_id)).fetchall():
                if selected is None or json.loads(event['details_json']).get('revision') in selected:
                    connection.execute('DELETE FROM semantic_workspace_events WHERE event_id=?',(event['event_id'],))
            for revision in connection.execute('SELECT run_id,revision FROM semantic_workspace_revisions WHERE user_id=? AND task_id=?',(owner_id,task_id)).fetchall():
                if selected is not None and revision['revision'] not in selected: continue
                if revision['run_id']:
                    legacy=connection.execute('SELECT logical_plan_id FROM semantic_harness_runs WHERE user_id=? AND run_id=?',(owner_id,revision['run_id'])).fetchone()
                    if legacy:
                        checkpoint=root/'_checkpoints'/'semantic-harness.sqlite'
                        if checkpoint.exists():
                            if checkpoint.resolve()!=checkpoint or checkpoint.is_symlink(): raise ValueError('unsafe_checkpoint_path')
                            from langgraph.checkpoint.sqlite import SqliteSaver
                            with closing(sqlite3.connect(checkpoint)) as checkpoint_connection:
                                names={r[0] for r in checkpoint_connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                                if not {'checkpoints','writes'}<=names: raise ValueError('checkpoint_schema_unknown')
                                # 复用已安装LangGraph公开删除接口，只删除本Owner登记的精确thread。
                                SqliteSaver(checkpoint_connection).delete_thread(revision['run_id'])
                        safe_owner=''.join(c for c in owner_id if c.isalnum() or c in '-_')
                        for attempt in connection.execute('SELECT attempt_number FROM semantic_harness_attempts WHERE user_id=? AND run_id=?',(owner_id,revision['run_id'])).fetchall():
                            path=root/safe_owner/legacy[0]/revision['run_id']/('attempt-'+str(attempt[0]))
                            if path.resolve()!=path or not path.is_relative_to(root): raise ValueError('unsafe_legacy_path')
                            if path.exists(): shutil.rmtree(path)
                    connection.execute("UPDATE semantic_harness_attempts SET tool_result_json=NULL,verification_json=NULL,repair_decision_json=NULL WHERE user_id=? AND run_id=?",(owner_id,revision['run_id']))
                    connection.execute('DELETE FROM semantic_harness_events WHERE user_id=? AND run_id=?',(owner_id,revision['run_id']))
        connection.commit()


async def _continue_operation(owner_id,operation_id,manager):
    operation=get_operation(owner_id,operation_id)
    if operation['state'] in {'completed','needs_confirmation'}: return operation
    with closing(_connection()) as connection:
        row=connection.execute('SELECT * FROM source_deletion_operations WHERE owner_id=? AND operation_id=?',(owner_id,operation_id)).fetchone()
    plan=json.loads(row['plan_json']);targets=[x for x in plan['objects'] if x['disposition']=='delete']
    with closing(_connection()) as connection:
        task_ids={row['task_id']:{r[0] for r in connection.execute('SELECT revision FROM semantic_workspace_revisions WHERE user_id=? AND task_id=?',(owner_id,row['task_id']))}}
    for item in targets:
        for ref in item['references']:
            if ref.get('task_id') and ref.get('revision') is not None and ref.get('reference_kind')!='producer':
                task_ids.setdefault(ref['task_id'],set()).add(ref['revision'])
    _set_state(owner_id,operation_id,'stopping')
    execution_leases=[]
    try:
        # 不持源锁等待Worker；逐历史Run确认，不能只信active_revision或终态早退。
        for task_id in sorted(task_ids):
            with closing(_connection()) as connection:
                runs=[dict(r) for r in connection.execute('SELECT r.revision,COALESCE(a.run_id,r.run_id) AS run_id FROM semantic_workspace_revisions r LEFT JOIN agentic_runtime_runs a ON a.user_id=r.user_id AND a.task_id=r.task_id AND a.revision=r.revision WHERE r.user_id=? AND r.task_id=? ORDER BY r.revision',(owner_id,task_id))]
            runs=[run for run in runs if run['revision'] in task_ids[task_id]]
            stop_identity=dict(task_id=task_id,runs=runs)
            current_task=reuse._store().get_semantic_workspace_task(owner_id,task_id)
            receipts=list(plan.get('stopped_tasks',[]))
            with closing(_connection()) as connection:
                for completed in connection.execute("SELECT plan_json FROM source_deletion_operations WHERE owner_id=? AND state='completed'",(owner_id,)):
                    receipts.extend(json.loads(completed[0]).get('stopped_tasks',[]))
            proven=[run for receipt in receipts if receipt.get('task_id')==task_id for run in receipt.get('runs',[])]
            pending=[run for run in runs if run not in proven]
            if current_task and current_task['active_revision'] in {run['revision'] for run in pending}:
                await manager.cancel(owner_id,task_id)
            for run in pending:
                if not await manager._confirm_runtime_stopped(owner_id,task_id,run['revision'],deletion_revision_only=True): raise ValueError('runtime_stop_unconfirmed')
            # 另一个Manager的真实Worker可能仍持执行锁；只关联历史版本时不碰无关当前Run。
            if current_task and current_task['active_revision'] in task_ids[task_id]:
                from src.api.execution import execution_lock
                from filelock import Timeout
                lease=execution_lock(reuse._store(),owner_id,'workspace',task_id)
                try: lease.acquire(timeout=0)
                except Timeout as error: raise ValueError('runtime_stop_unconfirmed') from error
                execution_leases.append(lease)
            if stop_identity not in plan.get('stopped_tasks',[]):
                plan.setdefault('stopped_tasks',[]).append(stop_identity)
                with closing(_connection()) as connection:
                    connection.execute('UPDATE source_deletion_operations SET plan_json=?,updated_at=? WHERE owner_id=? AND operation_id=?',(_json(plan),_now(),owner_id,operation_id));connection.commit()
        with reuse.source_locks(owner_id,[_ref(x) for x in targets]):
            for item in targets:
                current=reuse.references(owner_id,item['kind'],item['source_key'].split(':',1)[1])
                if any(r.get('use_id') for r in current): raise ValueError('source_in_use')
                def identity(ref): return _json({k:v for k,v in ref.items() if k not in {'state','in_recycle_bin','task_exists'}})
                if not {identity(r) for r in current}.issubset({identity(r) for r in item['references']}):
                    _set_state(owner_id,operation_id,'needs_confirmation','confirmation_changed')
                    return get_operation(owner_id,operation_id)
            _set_state(owner_id,operation_id,'cleaning')
            token=_cleanup_operation.set(operation_id)
            try:
                _cleanup_runtime_copies(owner_id,task_ids)
                for item in targets:
                    if item['source_key'] not in get_operation(owner_id,operation_id)['completed_source_keys']:
                        _remove_object(owner_id,item)
            finally:
                _cleanup_operation.reset(token)
            # 最后才清任务；保留对象的其他任务及用户需求不受影响。
            reuse._store().purge_semantic_workspace_task(owner_id,row['task_id'],deletion_operation_id=operation_id)
            _set_state(owner_id,operation_id,'completed')
    except Exception as error:
        _set_state(owner_id,operation_id,'incomplete',str(error) if isinstance(error,ValueError) else 'cleanup_unconfirmed')
    finally:
        for lease in reversed(execution_leases): lease.release()
    return get_operation(owner_id,operation_id)


def _reconfirm_operation(owner_id,operation_id,token,policy):
    current=get_operation(owner_id,operation_id)
    if current['state']!='needs_confirmation': raise ValueError('operation_not_awaiting_confirmation')
    with closing(_connection()) as connection:
        prior=json.loads(connection.execute('SELECT plan_json FROM source_deletion_operations WHERE owner_id=? AND operation_id=?',(owner_id,operation_id)).fetchone()[0])
    plan=deletion_plan(owner_id,current['task_id'],policy)
    with reuse.source_locks(owner_id,[_ref(x) for x in plan['objects']]):
        plan=deletion_plan(owner_id,current['task_id'],policy)
        if plan['plan_token']!=token or not plan['can_execute']: raise ValueError('confirmation_changed')
        minimal=dict(task_id=current['task_id'],shared_policy=policy,objects=[{k:v for k,v in x.items() if k not in {'label','acquired_at','time_kind','media_type','size_bytes'}} for x in plan['objects']],affected_tasks=plan['affected_tasks'])
        minimal['stopped_tasks']=prior.get('stopped_tasks',[])
        with closing(_connection()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            for item in minimal['objects']:
                existing=connection.execute('SELECT operation_id,state FROM source_deletions WHERE owner_id=? AND source_key=?',(owner_id,item['source_key'])).fetchone()
                if existing and existing['operation_id']!=operation_id: raise ValueError('source_in_use')
                if existing and existing['state']=='deleted': item['disposition']='delete'
                elif item['disposition']=='keep_shared':
                    connection.execute("DELETE FROM source_deletions WHERE owner_id=? AND source_key=? AND operation_id=? AND state='deleting'",(owner_id,item['source_key'],operation_id))
                elif not existing:
                    connection.execute('INSERT INTO source_deletions VALUES (?,?,?,?,?,?,NULL)',(owner_id,item['source_key'],operation_id,item['sha256'],item.get('snapshot_id'),'deleting'))
            connection.execute("UPDATE source_deletion_operations SET plan_json=?,state='planned',error_code=NULL,updated_at=? WHERE owner_id=? AND operation_id=?",(_json(minimal),_now(),owner_id,operation_id));connection.commit()
    return get_operation(owner_id,operation_id)


async def continue_operation(owner_id,operation_id,manager,*,plan_token=None,shared_policy=None):
    from pathlib import Path
    from filelock import FileLock,Timeout
    from src.api.execution import execution_checkpoint
    execution_checkpoint(required=True)
    get_operation(owner_id,operation_id)
    path=Path(settings.webui_db_path).resolve().parent/'.source-read-locks'
    path.mkdir(parents=True,exist_ok=True)
    lock=FileLock(str(path/('operation-'+hashlib.sha256((owner_id+'\0'+operation_id).encode()).hexdigest()+'.lock')),timeout=0,thread_local=False)
    try: lock.acquire()
    except Timeout: return get_operation(owner_id,operation_id)
    try:
        if plan_token is not None:
            _reconfirm_operation(owner_id,operation_id,plan_token,shared_policy or 'keep_shared')
        return await _continue_operation(owner_id,operation_id,manager)
    finally: lock.release()


def _output_delete_path(owner_id,item):
    from pathlib import Path
    record=reuse.verified_output_metadata(owner_id,item['output_id'])
    root=Path(settings.semantic_execution_root).resolve()
    safe=lambda value:hashlib.sha256(value.encode()).hexdigest()[:16]
    with closing(_connection()) as connection:
        formal=connection.execute('SELECT task_id,task_revision,run_id,publication_key FROM formal_delivery_runs WHERE owner_id=? AND delivery_id=?',(owner_id,record['delivery_id'])).fetchone()
        if formal:
            expected=root/safe(owner_id)/safe(formal['task_id'])/('revision-'+str(formal['task_revision']))/safe(formal['run_id'])/'publications'/formal['publication_key']/'final'/record['filename']
        else:
            run=connection.execute('SELECT logical_plan_id FROM semantic_harness_runs WHERE user_id=? AND run_id=?',(owner_id,record['run_id'])).fetchone()
            if run is None: raise ValueError('output_origin_unconfirmed')
            expected=root/safe(owner_id)/run[0]/record['run_id']/'delivery'/record['delivery_id']/record['filename']
    actual=Path(record['file_path'])
    if actual.absolute()!=expected.absolute() or not expected.absolute().is_relative_to(root): raise ValueError('unsafe_output_path')
    for parent in (expected,*expected.parents):
        if parent==root: break
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()): raise ValueError('unsafe_output_path')
    return expected
