"""已冻结Run的认证来源前置阶段；没有启动Worker时才可等待认证。"""
import hashlib,json,sqlite3
from pathlib import Path
from contextlib import contextmanager,closing
from src.account_execution import current_authorization
from src.api.execution import execution_checkpoint
from src.agentic_runtime.kernel import PiAgentKernelAdapter,AgentKernelResultUnknownError
from src.agentic_runtime.models import RuntimeStatus,PiRuntimeResult,PiRuntimeCheckpoint,RuntimeEvent,SourceInput
from src.source_acquisition.reuse import SourceReadUse,source_locks,resolve_frozen_source,workspace_source_read_context
from src.source_acquisition.authenticated_event import authentication_wait_event
from src.source_acquisition.authenticated_continuation import SourceContinuation

def effective_refs(connection,owner,refs,contract):
    """冻结的原attempt成功后只投影其不可变snapshot；不授权新的采集。"""
    handle=(contract or {}).get('authenticated_source')
    if not handle:return list(refs)
    cursor=connection.execute('SELECT request_hash,allowed_scope_json,purpose,status,snapshot_id FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(owner,handle['attempt_id']))
    row=cursor.fetchone()
    if handle.get('owner')!=owner or row is None or row[0]!=handle['request_hash']:raise ValueError('authenticated_source_handle_changed')
    items=connection.execute('SELECT artifact_id,content_sha256 FROM source_artifacts WHERE owner_id=? AND snapshot_id=?',(owner,row[4])).fetchall() if row[4] else []
    deleted=connection.execute("SELECT d.source_key,d.sha256 FROM source_deletions d JOIN source_deletion_operations o ON o.operation_id=d.operation_id AND o.owner_id=d.owner_id WHERE d.owner_id=? AND d.snapshot_id=? AND d.state='deleted' AND o.state='completed'",(owner,row[4])).fetchall() if row[4] else []
    erased=bool(deleted) and not items and row[1]=='{}' and row[2]==''
    if not erased and (hashlib.sha256(row[1].encode()).hexdigest()!=handle['scope_sha256'] or hashlib.sha256(row[2].encode()).hexdigest()!=handle['purpose_sha256']):raise ValueError('authenticated_source_handle_changed')
    if row[3]!='succeeded':return list(refs)
    items += [(item[0].split(':',1)[1],item[1]) for item in deleted]
    if not items:raise ValueError('authenticated_source_snapshot_missing')
    resolved=list(refs)
    for identity,digest in items:
        item=dict(kind='web_artifact',artifact_id=identity,snapshot_id=row[4],sha256=digest)
        if item not in resolved:resolved.append(item)
    return resolved

def project_contract(connection,owner,contract):
    if not contract or not contract.get('authenticated_source'):return contract
    result=dict(contract);groups=list(contract.get('web_sources',[]))
    for ref in effective_refs(connection,owner,[],contract):
        group={'source_snapshot_id':ref['snapshot_id']}
        if group not in groups:groups.append(group)
    result['web_sources']=groups
    return result

def freeze_handle(sources,authentication,owner,attempt_id):
    """内部创建入口重读原授权；客户端只可选择本人原attempt ID。"""
    with sources._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        digest=sources.auth_digest(db,owner,attempt_id)
        row=db.execute('SELECT * FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(owner,attempt_id)).fetchone()
        if row is None or not row['current_auth_request_id']:raise ValueError('authenticated_attempt_required')
        auth=db.execute('SELECT * FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(owner,row['current_auth_request_id'])).fetchone()
        if auth is None or auth['authorization_digest']!=digest or auth['state']!='pending':raise ValueError('authentication_binding_changed')
        return dict(owner=owner,attempt_id=attempt_id,request_id=auth['request_id'],website=auth['website'],driver_version=auth['driver_version'],account=auth['account'],connection_version=auth['expected_version'],authorization_digest=digest,request_hash=row['request_hash'],scope_sha256=hashlib.sha256(row['allowed_scope_json'].encode()).hexdigest(),purpose_sha256=hashlib.sha256(row['purpose'].encode()).hexdigest(),generation=current_authorization().generation)

def recheck_creation(connection,sources,owner,handle):
    # 在原任务事务内重核，不能把prepare前的观察当最终授权。
    digest=sources.auth_digest(connection,owner,handle['attempt_id'])
    row=connection.execute('SELECT current_auth_request_id,request_hash FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(owner,handle['attempt_id'])).fetchone()
    request=connection.execute('SELECT state,expires FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(owner,handle['request_id'])).fetchone()
    import time
    if digest!=handle['authorization_digest'] or row is None or row[0]!=handle['request_id'] or row[1]!=handle['request_hash'] or request is None or request[0]!='pending' or request[1]<=time.time():raise ValueError('authenticated_source_handle_changed')

def public_contract(contract):
    if not contract or not contract.get('authenticated_source'):return contract
    result=dict(contract)
    result['authenticated_source']={key:value for key,value in contract['authenticated_source'].items() if key in ('attempt_id','request_id','website','account','connection_version')}
    return result

class RunSourceGate:
    def __init__(self,store,sources,authentication,runtime_repository,execution_root):
        self.store=store;self.sources=sources;self.authentication=authentication;self.runtime_repository=runtime_repository;self.root=Path(execution_root)
    def handle(self,request):
        auth=current_authorization(required=True)
        self.store.require_account_execution(auth,'workspace',request.task_id)
        task=self.store.get_semantic_workspace_task(request.user_id,request.task_id)
        row=self.store.get_semantic_workspace_revision(request.user_id,request.task_id,request.revision)
        if auth.owner_user_id!=request.user_id or task is None or task.get('deleted_at') or task.get('cancel_requested') or task['active_revision']!=request.revision:raise PermissionError('run_source_not_authorized')
        handle=((row or {}).get('source_contract') or {}).get('authenticated_source')
        if not isinstance(handle,dict) or handle.get('owner')!=request.user_id or handle.get('generation')!=auth.generation:raise ValueError('authenticated_source_handle_required')
        with self.sources._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            digest=self.sources.auth_digest(db,request.user_id,handle['attempt_id'])
            attempt=db.execute('SELECT * FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(request.user_id,handle['attempt_id'])).fetchone()
            saved=db.execute('SELECT * FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(request.user_id,attempt['current_auth_request_id'])).fetchone()
        if attempt is None or saved is None or digest!=handle['authorization_digest'] or saved['authorization_digest']!=digest or attempt['request_hash']!=handle['request_hash'] or hashlib.sha256(attempt['allowed_scope_json'].encode()).hexdigest()!=handle['scope_sha256'] or hashlib.sha256(attempt['purpose'].encode()).hexdigest()!=handle['purpose_sha256'] or saved['website']!=handle['website'] or saved['driver_version']!=handle['driver_version'] or saved['expected_version']!=handle['connection_version'] or json.loads(saved['binding_json'])!={'attempt_id':handle['attempt_id']} or (handle['account'] is not None and saved['account']!=handle['account']):
            raise ValueError('authenticated_source_handle_changed')
        return handle
    def workspace(self,request,run_id):
        if any('/' in value or '\\' in value or value in ('.','..') for value in (request.task_id,run_id)):raise ValueError('invalid_run_identity')
        return self.root.resolve()/'agentic-vnext'/hashlib.sha256(request.user_id.encode()).hexdigest()[:16]/request.task_id/f'r{request.revision}'/run_id
    def refs(self,request):
        handle=self.handle(request)
        attempt=self.sources.get_attempt(request.user_id,handle['attempt_id'])
        if attempt['status']!='succeeded':return []
        snapshot=self.sources.get_snapshot(request.user_id,attempt['snapshot_id'],include_preview=False)
        if not snapshot or not snapshot['artifacts']:raise ValueError('authenticated_source_snapshot_missing')
        return [dict(kind='web_artifact',snapshot_id=snapshot['snapshot_id'],artifact_id=item['artifact_id'],sha256=item['content_sha256']) for item in snapshot['artifacts']]
    def stage(self,request,run_id):
        refs=self.refs(request)
        if not refs:raise ValueError('authenticated_source_not_ready')
        use=SourceReadUse(request.user_id,refs,operation='runtime',task_id=request.task_id,revision=request.revision).start()
        try:
            directory=self.workspace(request,run_id)/'authenticated-input'
            if directory.resolve()!=directory:raise ValueError('unsafe_authenticated_source_path')
            existing={item.upload_id:item for item in request.sources}
            result=[]
            for ref in refs:
                if ref['artifact_id'] in existing:
                    if existing[ref['artifact_id']].sha256!=ref['sha256']:raise ValueError('authenticated_source_hash_changed')
                    continue
                item=self.sources.get_artifact(request.user_id,ref['artifact_id'],include_content=True)
                content=bytes(item['content_blob'])
                if hashlib.sha256(content).hexdigest()!=ref['sha256']:raise ValueError('authenticated_source_hash_changed')
                path=directory/(hashlib.sha256(ref['artifact_id'].encode()).hexdigest()+'.html')
                if path.is_symlink() or directory.resolve()!=directory:raise ValueError('unsafe_authenticated_source_path')
                directory.mkdir(parents=True,exist_ok=True)
                path.write_bytes(content)
                result.append(SourceInput(upload_id=ref['artifact_id'],original_name='已授权认证来源',host_path=path,sha256=ref['sha256'],media_type=item['media_type']))
            return request.model_copy(update={'sources':tuple(request.sources)+tuple(result)})
        finally:use.finish(known=True)
    @contextmanager
    def read_context(self,request,source_ids):
        """最终原件复核到Pi实际staging交接，不能持锁跨模型运行。"""
        row=self.store.get_semantic_workspace_revision(request.user_id,request.task_id,request.revision)
        if not ((row or {}).get('source_contract') or {}).get('authenticated_source'):
            with workspace_source_read_context(request,source_ids):yield
            return
        mapping={r['artifact_id']:r for r in self.refs(request)}
        original=[identity for identity in source_ids if identity not in mapping]
        extra=[mapping[identity] for identity in source_ids if identity in mapping]
        from contextlib import ExitStack
        with ExitStack() as stack:
            if original:stack.enter_context(workspace_source_read_context(request,original))
            stack.enter_context(source_locks(request.user_id,extra))
            execution_checkpoint(required=True)
            self.handle(request)
            for ref in extra:
                source=resolve_frozen_source(request.user_id,ref)
                if next(item.sha256 for item in request.sources if item.upload_id==ref['artifact_id'])!=source['sha256']:raise ValueError('authenticated_source_hash_changed')
            yield

class AuthenticatedPreflightAdapter(PiAgentKernelAdapter):
    def __init__(self,runtime,gate):
        super().__init__(runtime);self.gate=gate
        self.manifest=self.manifest.model_copy(update={'adapter_id':'pi-authenticated-preflight','adapter_version':'1.0.0'})
    def has_handle(self,request):
        row=self.gate.store.get_semantic_workspace_revision(request.user_id,request.task_id,request.revision)
        return bool(((row or {}).get('source_contract') or {}).get('authenticated_source'))
    async def start(self,request,*,binding,on_event):
        if not self.has_handle(request):return await super().start(request,binding=binding,on_event=on_event)
        handle=self.gate.handle(request)
        if self.gate.refs(request):return await self._start_worker(request,binding=binding,on_event=on_event)
        result=PiRuntimeResult(status=RuntimeStatus.NEEDS_INPUT,run_id=binding.external_run_id,workspace_root=self.gate.workspace(request,binding.external_run_id))
        checkpoint=PiRuntimeCheckpoint(run_id=result.run_id,workspace_root=result.workspace_root)
        await on_event(authentication_wait_event(request,binding,checkpoint,result,request_id=handle['request_id'],website=handle['website'],driver_version=handle['driver_version']))
        return result
    async def resume(self,request,*,binding,checkpoint,on_event):
        if not self.has_handle(request):return await super().resume(request,binding=binding,checkpoint=checkpoint,on_event=on_event)
        self.gate.handle(request)
        if checkpoint.run_id!=binding.external_run_id or checkpoint.workspace_root!=self.gate.workspace(request,binding.external_run_id):raise ValueError('authentication_checkpoint_mismatch')
        events=self.gate.runtime_repository.list_events(request.user_id,request.task_id,request.revision)
        waits=[e for e in events if e['event_type']=='source.authentication_required']
        if any(e['event_type']=='source.authentication.worker_claimed' for e in events):
            receipts=[e for e in events if e['event_type']=='source.authentication.worker_returned']
            if receipts and receipts[-1]['details'].get('run_id')==binding.external_run_id and receipts[-1]['details'].get('binding_digest')==binding.capability_digest:
                # 已取得实际Worker回执才进入原Pi恢复；未知启动绝不重发。
                return await super().resume(self.gate.stage(request,binding.external_run_id),binding=binding,checkpoint=checkpoint,on_event=on_event)
            raise AgentKernelResultUnknownError('认证后的Worker启动结果未知，禁止自动再次启动')
        if not waits or waits[-1]['details']['run_id']!=binding.external_run_id or waits[-1]['details']['binding_digest']!=binding.capability_digest:raise ValueError('authentication_wait_missing')
        return await self._start_worker(request,binding=binding,on_event=on_event)
    async def _start_worker(self,request,*,binding,on_event):
        enriched=self.gate.stage(request,binding.external_run_id)
        # 先持久领取，启动异常不能再次创建Worker；后续普通Pi恢复是另一明确阶段。
        await on_event(RuntimeEvent(event_type='source.authentication.worker_claimed',summary='认证来源已就绪，开始原Run',details={'run_id':binding.external_run_id,'binding_digest':binding.capability_digest,'source_refs':self.gate.refs(request)}))
        result=await super().start(enriched,binding=binding,on_event=on_event)
        if result.run_id!=binding.external_run_id:raise ValueError('authentication_worker_identity_changed')
        await on_event(RuntimeEvent(event_type='source.authentication.worker_returned',summary='原Run已取得Worker回执',details={'run_id':result.run_id,'binding_digest':binding.capability_digest,'status':result.status.value}))
        return result
    async def cancel(self,user_id,task_id,revision):
        events=self.gate.runtime_repository.list_events(user_id,task_id,revision)
        row=self.gate.store.get_semantic_workspace_revision(user_id,task_id,revision)
        handle=((row or {}).get('source_contract') or {}).get('authenticated_source')
        if handle:
            with self.gate.sources._connect() as db:
                source=db.execute('SELECT status FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(user_id,handle['attempt_id'])).fetchone()
            # 终态来源无需重读正文；删除意图不能反过来阻塞原Worker停止。
            if source and source[0]=='acquiring':self.gate.sources.cancel_attempt(user_id,handle['attempt_id'])
        if not handle or any(e['event_type']=='source.authentication.worker_claimed' for e in events):await super().cancel(user_id,task_id,revision)

class RunSourceContinuation(SourceContinuation):
    """来源完成后直接消费原Run等待；复用原Kernel和workspace执行锁。"""
    def __init__(self,repository,authentication,drivers,*,store,runtime_repository,kernel,manager=None):
        super().__init__(repository,authentication,drivers)
        self.store=store;self.runtime_repository=runtime_repository;self.kernel=kernel;self.manager=manager
    async def resume(self,owner,identity):
        source_result=await super().resume(owner,identity)
        if source_result['continuation_state']!='completed':return source_result
        from src.api.execution import execution_lock
        from src.agentic_runtime.models import PiRuntimeRequest
        with closing(sqlite3.connect(self.store.db_path)) as db:
            targets=db.execute("SELECT task_id,revision FROM semantic_workspace_revisions WHERE user_id=? AND json_extract(source_contract_json,'$.authenticated_source.attempt_id')=?",(owner,source_result['attempt_id'])).fetchall()
        resumed=[]
        for task_id,revision in targets:
            with execution_lock(self.store,owner,'workspace',task_id):
                self.store.require_account_execution(current_authorization(),'workspace',task_id)
                task=self.store.get_semantic_workspace_task(owner,task_id)
                row=self.runtime_repository.get(owner,task_id,revision)
                if task is None or task.get('deleted_at') or task.get('cancel_requested') or task['active_revision']!=revision or row is None or row['status']!=RuntimeStatus.NEEDS_INPUT:raise ValueError('run_no_longer_waiting')
                values=dict(row['request'])
                # 沿工作台本地模式的固定非秘密占位值；外部连接仍仅用冻结引用。
                if not values.get('model_connection_id'):values['api_key']='local-runtime'
                request=PiRuntimeRequest.model_validate(values)
                frozen=self.kernel._adapter.gate.handle(request)
                binding=self.kernel.query(owner,task_id,revision).binding
                events=self.runtime_repository.list_events(owner,task_id,revision)
                if binding is None or binding.external_run_id!=row['run_id'] or not any(e['event_type']=='source.authentication_required' and e['details'].get('authentication_request_id')==frozen['request_id'] and e['details'].get('run_id')==row['run_id'] and e['details'].get('binding_digest')==binding.capability_digest for e in events):raise ValueError('run_authentication_binding_changed')
                if self.manager is not None:
                    # 正式工作台复用原队列及发布链；queued不冒称模型已经完成。
                    self.manager.enqueue(owner,task_id)
                    resumed.append({'task_id':task_id,'revision':revision,'run_id':row['run_id'],'status':'queued'})
                    continue
                checkpoint=PiRuntimeCheckpoint(run_id=row['run_id'],workspace_root=Path(row['workspace_root']),session_file=row['session_file'],container_name=row['container_name'])
                async def sink(event):pass
                self.runtime_repository.update(owner,task_id,revision,status=RuntimeStatus.RUNNING)
                result=await self.kernel.resume(request,checkpoint=checkpoint,on_event=sink)
                self.runtime_repository.update(owner,task_id,revision,status=result.status,workspace_root=result.workspace_root,session_file=result.session_file)
                resumed.append({'task_id':task_id,'revision':revision,'run_id':result.run_id,'binding_digest':binding.capability_digest,'status':result.status.value})
        source_result['run_receipts']=resumed
        return source_result
