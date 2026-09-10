"""复用既有Connector的内部冻结读取候选，不接受HTTP客户端配置。"""
import asyncio
import hashlib
import json
from dataclasses import dataclass
from src.data_prep.models import SourceSpec
from src.connectors.http_security import SsrfError


class ReadCancelled(asyncio.CancelledError):
    """底层迭代器与连接已真实收口的取消事实。"""


class SourceReadError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code=code


def _encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)


@dataclass(frozen=True)
class FrozenConnection:
    owner: str
    connection_id: str
    version: str
    spec_json: str
    configuration_version: str


def freeze(owner, row):
    if row is None or row['owner'] != owner:
        raise PermissionError('connection_not_found')
    spec = row['spec'].model_copy(deep=True)
    if spec.source_type.value == 'database':
        if spec.options.get('mode','table') != 'table' or spec.options.get('sql'):
            raise ValueError('read_only_table_required')
    elif spec.source_type.value == 'http_api':
        from src.source_acquisition.service import normalize_public_url
        # 冻结前拒绝凭据和非公开URL语法，避免敏感URL进入持久范围。
        spec.locator=normalize_public_url(spec.locator)
        if 'url' in spec.options:
            if normalize_public_url(spec.options['url'])!=spec.locator:
                raise ValueError('http_url_mismatch')
            spec.options['url']=spec.locator
        if spec.options.get('method','GET').upper() != 'GET' or spec.options.get('body'):
            raise ValueError('read_only_get_required')
    else:
        raise ValueError('unsupported_connection')
    encoded = _encoded(spec.model_dump(mode='json'))
    identity_spec=json.loads(encoded)
    identity_spec['options'].pop('task_id',None)
    version = hashlib.sha256(_encoded([row['connection_id'],row['version'],identity_spec]).encode('utf-8')).hexdigest()
    return FrozenConnection(owner,row['connection_id'],version,encoded,str(row['version']))


async def read_frozen(owner, frozen, load_current, connector, artifact_store, *, checkpoint=None, pause_after_batches=None, prior_http_hashes=()):
    """内部load_current必须重读Owner登记；调用者负责已有attempt锁与持久checkpoint。"""
    try:
        if owner != frozen.owner or freeze(owner,load_current()) != frozen:
            raise PermissionError('connection_version_changed')
    except asyncio.CancelledError:
        raise ReadCancelled() from None
    if checkpoint is not None:
        from src.data_prep.checkpoints import Checkpoint
        if not isinstance(checkpoint,dict) or checkpoint.get('connection_version')!=frozen.version:
            raise ValueError('checkpoint_binding')
        checkpoint=Checkpoint(**checkpoint['value'])
    def saved_checkpoint(value):
        return {'connection_version':frozen.version,'value':value.to_dict()} if value else None
    stop = asyncio.Event()
    async def collect():
        artifacts=[];warnings=[];last=checkpoint;total=0;records=0;seen=set(prior_http_hashes);batch_count=0;unaccepted=None
        stream=connector.read(SourceSpec.model_validate_json(frozen.spec_json),checkpoint)
        try:
            async for batch in stream:
                if stop.is_set():
                    raise asyncio.CancelledError()
                if freeze(owner,load_current()) != frozen:
                    raise PermissionError('connection_version_changed')
                last=batch.checkpoint
                warnings.extend(batch.warnings)
                if batch.retryable_error or batch.fatal_error:
                    return {'status':'failed','error_code':'source_network' if batch.retryable_error else 'source_rejected','artifacts':artifacts,'checkpoint':saved_checkpoint(last)}
                for artifact in batch.artifacts:
                    if SourceSpec.model_validate_json(frozen.spec_json).source_type.value=='http_api' and artifact.sha256 in seen:
                        raise SourceReadError('pagination_repeated')
                    seen.add(artifact.sha256)
                    unaccepted=artifact
                    content=artifact_store.read_raw_bytes(artifact.task_id,artifact.storage_path)
                    if len(content)!=artifact.size_bytes or hashlib.sha256(content).hexdigest()!=artifact.sha256:
                        raise ValueError('source_integrity')
                    try:
                        if artifact.media_type == 'application/x-ndjson':
                            records += len([json.loads(line) for line in content.splitlines() if line])
                        elif artifact.media_type == 'application/json':
                            data=json.loads(content)
                            records += len(data) if isinstance(data,list) else bool(data)
                        else:
                            raise SourceReadError('unsupported_media')
                    except (UnicodeDecodeError,json.JSONDecodeError):
                        raise SourceReadError('local_parse') from None
                    total+=len(content)
                    if total>16*1024*1024 or len(artifacts)>=100:
                        raise SourceReadError('source_response_limit')
                    artifacts.append(artifact.model_dump(mode='json'))
                    unaccepted=None
                batch_count+=1
                if pause_after_batches is not None and batch_count>=pause_after_batches and not last.is_final:
                    return {'status':'paused','artifacts':artifacts,'checkpoint':saved_checkpoint(last),'connection_version':frozen.version}
            return {'status':'succeeded' if records else 'no_results','artifacts':artifacts,'checkpoint':saved_checkpoint(last),'warnings':warnings,'connection_version':frozen.version}
        except (SsrfError,httpx.RequestError) as exc:
            return {'status':'failed','error_code':'source_scope_denied' if isinstance(exc,SsrfError) else 'source_network','artifacts':artifacts,'checkpoint':saved_checkpoint(last)}
        except SourceReadError as exc:
            if unaccepted is not None:
                remove_connector_raw({'artifact_namespace':unaccepted.task_id},unaccepted.sha256,artifact_store=artifact_store)
            return {'status':'failed','error_code':exc.code,'artifacts':artifacts,'checkpoint':saved_checkpoint(last)}
        finally:
            try:
                await stream.aclose()
            finally:
                await connector.close()
    # 取消等待真实读取收口；不取消底层to_thread后假称线程已退出。
    reading=asyncio.create_task(collect())
    try:
        return await asyncio.shield(reading)
    except asyncio.CancelledError:
        stop.set()
        while not reading.done():
            try:
                await asyncio.shield(reading)
            except asyncio.CancelledError:
                continue
        if not reading.cancelled():
            reading.result()
        raise ReadCancelled() from None

from urllib.parse import urlsplit
import httpx
from src.connectors.http_api_connector import HttpApiConnector
from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport
from src.source_acquisition.service import AnonymousWebFetcher
from src.api.execution import execution_to_thread


class ReadOnlyHttpConnector(HttpApiConnector):
    """仅替换真实发送接缝，分页/制品/错误状态沿既有Connector。"""
    def __init__(self, *, url, authorize, **kwargs):
        super().__init__(**kwargs)
        self._url=urlsplit(url)
        self._authorize=authorize
        self._cleanup=AnonymousWebFetcher(security_guard=self._guard)

    async def _fetch_with_retry(self,client,page_request,max_retries):
        # 原attempt未知结果不自动重发；本接入固定零重试。
        return await self._send(client,page_request)

    async def _send(self, client, page_request):
        parts=urlsplit(page_request.url)
        if page_request.method!='GET' or (parts.scheme,parts.netloc,parts.path)!=(self._url.scheme,self._url.netloc,self._url.path):
            raise SourceReadError('source_scope_denied')
        target=await execution_to_thread(self._guard.validate,page_request.url)
        self._authorize()
        transport=PinnedAsyncHTTPTransport(target=target,transport=self._transport)
        async with self._cleanup._client(transport,dict(trust_env=False,follow_redirects=False,timeout=10)) as safe_client:
            request=safe_client.build_request('GET',page_request.url,params=page_request.params,headers=dict(client.headers))
            self._authorize()
            response=await safe_client.send(request,stream=True)
            try:
                self._authorize()
                if response.status_code in (401,403):
                    raise SourceReadError('authorization_expired' if response.status_code==401 else 'permission_denied')
                if 300<=response.status_code<400:
                    raise SourceReadError('source_redirect_denied')
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    self._authorize()
                    content.extend(chunk)
                    if len(content)>1024*1024:
                        raise SourceReadError('source_response_limit')
                return httpx.Response(response.status_code,headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-encoding','content-length','transfer-encoding'}},content=bytes(content),request=request)
            finally:
                await self._cleanup._close_safely(response.stream.aclose)

@dataclass(frozen=True)
class ConnectorRequest:
    connection: FrozenConnection
    purpose: str
    request_context: str = ''
    scope_kind: str = 'connector'

    @property
    def url(self):
        return 'connector:'+self.connection.connection_id

    def normalized(self):
        if not self.purpose.strip() or len(self.purpose)>500:
            raise ValueError('invalid_purpose')
        return self

    def allowed_scope(self):
        spec=SourceSpec.model_validate_json(self.connection.spec_json)
        selection={'source_type':spec.source_type.value,**{key:value for key,value in spec.options.items() if key in {'table','fields','filters','time_range','incremental','pagination'}}}
        selection['connection_id' if spec.source_type.value=='database' else 'url']=self.connection.connection_id if spec.source_type.value=='database' else spec.locator
        return {'kind':'connector','artifact_namespace':spec.options.get('task_id'),'selection':selection,'protocol':spec.source_type.value,'connection_id':self.connection.connection_id,'connection_version':self.connection.version,'configuration_version':self.connection.configuration_version,'selection_sha256':hashlib.sha256(self.connection.spec_json.encode()).hexdigest()}

    def request_hash(self):
        return hashlib.sha256(_encoded([self.allowed_scope(),self.purpose,self.request_context]).encode()).hexdigest()

    def legacy_exact_page_hash(self):
        return None


@dataclass(frozen=True)
class ConnectorArtifact:
    request_url: str
    final_url: str
    read_at: str
    media_type: str
    content: bytes
    content_sha256: str
    title: str = ''
    text_preview: str = ''


async def acquire_connector(repository, *, owner, key, request, load_current, connector, artifact_store, resume_checkpoint=False, pause_after_batches=None):
    """沿既有attempt领取/执行锁/封存；重复在途请求不自动重发。"""
    if request.connection.owner!=owner:
        raise PermissionError('connection_not_found')
    attempt,created=repository.claim_attempt(owner_id=owner,idempotency_key=key,request=request)
    if not created and not resume_checkpoint:
        return attempt
    identity=attempt['attempt_id']
    with repository.execution_lock(owner,identity):
        previous=[];checkpoint=None
        if not created:
            with repository._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                repository._require_execution(connection,owner,identity)
                saved=connection.execute("SELECT connector_progress_json,error_code,status,cancel_requested_at FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?",(owner,identity)).fetchone()
                if not saved or saved[1]!='connector_checkpoint_ready' or not saved[0] or saved[2]!='acquiring' or saved[3] is not None:
                    return repository.get_attempt(owner,identity)
                progress=json.loads(saved[0]);previous=progress['artifacts'];checkpoint=progress['checkpoint']
                connection.execute("UPDATE source_acquisition_attempts SET error_code='connector_resume_unknown' WHERE owner_id=? AND attempt_id=?",(owner,identity))
        def current():
            repository.check_execution(owner,identity)
            if repository.cancellation_requested(owner,identity):
                raise asyncio.CancelledError()
            return load_current()
        if isinstance(connector,ReadOnlyHttpConnector):
            connector._authorize=current
        try:
            result=await read_frozen(owner,request.connection,current,connector,artifact_store,checkpoint=checkpoint,pause_after_batches=pause_after_batches,prior_http_hashes=[item["sha256"] for item in previous])
            try:current()
            except asyncio.CancelledError:raise ReadCancelled() from None
        except ReadCancelled:
            with repository._connect() as connection:
                connection.execute("UPDATE source_acquisition_attempts SET error_code=NULL WHERE owner_id=? AND attempt_id=? AND error_code='connector_resume_unknown'",(owner,identity))
            repository.cancel_attempt(owner,identity)
            repository._confirm_cancel(owner,identity)
            raise
        except BaseException:
            # 未取得可靠返回不能让重启后的空闲锁冒充资源已静默。
            with repository._connect() as connection:
                connection.execute("UPDATE source_acquisition_attempts SET error_code='connector_cleanup_unknown' WHERE owner_id=? AND attempt_id=? AND status='acquiring'",(owner,identity))
            raise
        result['artifacts']=previous+result['artifacts']
        if len(result['artifacts'])>100 or sum(a['size_bytes'] for a in result['artifacts'])>16*1024*1024:
            return repository.complete_failure(owner,identity,error_code='source_response_limit',error_message='连接读取超过冻结上限')
        if result['status']=='paused':
            with repository._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                repository._require_execution(connection,owner,identity)
                connection.execute("UPDATE source_acquisition_attempts SET connector_progress_json=?,error_code='connector_checkpoint_ready' WHERE owner_id=? AND attempt_id=? AND status='acquiring' AND cancel_requested_at IS NULL",(_encoded({'artifacts':result['artifacts'],'checkpoint':result['checkpoint']}),owner,identity))
            return repository.get_attempt(owner,identity)
        if result['status']=='no_results' and previous:
            result['status']='succeeded'
        if result['status']=='no_results' or (result['status']!='succeeded' and not result['artifacts']):
            for raw in result['artifacts']:
                remove_connector_raw(request.allowed_scope(),raw['sha256'],artifact_store=artifact_store)
            return repository.complete_failure(owner,identity,error_code=result.get('error_code',result['status']),error_message='连接来源未形成可用完整结果')
        artifacts=[]
        for raw in result['artifacts']:
            content=artifact_store.read_raw_bytes(raw['task_id'],raw['storage_path'])
            if hashlib.sha256(content).hexdigest()!=raw['sha256']:
                raise ValueError('source_integrity')
            artifacts.append(ConnectorArtifact(request.url,request.url,raw['created_at'],raw['media_type'],content,raw['sha256']))
        current()
        return repository.complete_connector(owner,identity,artifacts=tuple(artifacts),error_code=result.get('error_code'))



async def refresh_connector(repository, *, owner, key, scope, purpose, request_context, cancel_if, resolver=None, factory=None, artifact_store=None):
    """刷新只重用已冻结选择；仍由原source-refresh事务创建新修订。"""
    from src.api.routes.connector_sources import SourceIn,resolve_registered_source
    from src.config.settings import settings
    from src.connectors.database_connector import DatabaseConnector
    from src.services.db_connections import resolve_credential
    from src.data_prep.artifact_store import ArtifactStore
    resolver=resolver or resolve_registered_source
    store=artifact_store or ArtifactStore()
    source=SourceIn(**scope['selection'])
    def load():
        if cancel_if():raise asyncio.CancelledError()
        return resolver(owner,source,key)
    row=load();frozen=freeze(owner,row)
    # task_id只是本次原始制品目录，不是外部连接版本/选择；刷新须另核连接配置。
    old_scope_version=scope.get('configuration_version')
    if old_scope_version is not None and row['version']!=old_scope_version:
        raise ValueError('connection_version_changed')
    connector=factory(row) if factory else (DatabaseConnector(store,credential_resolver=resolve_credential) if source.source_type=='database' else ReadOnlyHttpConnector(url=row['spec'].locator,artifact_store=store,authorize=lambda:None))
    return await acquire_connector(repository,owner=owner,key=key,request=ConnectorRequest(frozen,purpose,request_context),load_current=load,connector=connector,artifact_store=store)


def remove_connector_raw(scope, sha256, *, artifact_store=None):
    """只清已冻结命名空间内同摘要原始副本，不扫描其它任务目录。"""
    from pathlib import Path
    from src.data_prep.artifact_store import ArtifactStore
    store=artifact_store or ArtifactStore()
    namespace=scope.get('artifact_namespace')
    if not isinstance(namespace,str) or not namespace or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in namespace):
        raise ValueError('connector_namespace_invalid')
    root=store.root.resolve();directory=root/namespace/'raw'
    for parent in (root/namespace,directory):
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)() or parent.resolve()!=parent:
            raise ValueError('connector_raw_path_invalid')
    for path in directory.glob('raw-'+sha256[:16]+'.*'):
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha256:
            raise ValueError('connector_raw_identity_changed')
        path.unlink()
