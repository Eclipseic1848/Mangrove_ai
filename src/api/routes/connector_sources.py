"""候选API仅装配现有公开Source DTO，不接受凭据或回调。"""
from fastapi import APIRouter,Depends,HTTPException,Header
from pydantic import BaseModel,ConfigDict,Field
from src.api.auth import get_execution_user
from src.api.routes.data_tasks import TaskCreateSourceIn
from src.source_acquisition.connection_source import freeze,ConnectorRequest,acquire_connector


class SourceIn(TaskCreateSourceIn):
    model_config=ConfigDict(extra='forbid')


class AcquireIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source: SourceIn
    resume_checkpoint: bool=False
    purpose: str=Field(min_length=1,max_length=500)
    expected_connection_version: str|None=Field(default=None,pattern='^[0-9a-f]{64}$')


def router_for(repository, resolve_source, connector_factory, artifact_store, *, batch_budget=10):
    router=APIRouter(prefix='/api/connector-sources')
    @router.post('/resolve')
    def resolve(payload:SourceIn,user=Depends(get_execution_user)):
        try:
            frozen=freeze(user['user_id'],resolve_source(user['user_id'],payload,'resolve'))
            scope=ConnectorRequest(frozen,'连接来源核验').allowed_scope()
            return {key:value for key,value in scope.items() if key!='artifact_namespace'}
        except PermissionError:raise HTTPException(404,'连接来源不存在或无权读取') from None
        except ValueError:raise HTTPException(422,'连接来源配置无效') from None

    @router.post('/acquisitions',status_code=202)
    async def acquire(payload:AcquireIn,idempotency_key:str=Header(min_length=1,max_length=200),user=Depends(get_execution_user)):
        owner=user['user_id']
        try:
            def load():return resolve_source(owner,payload.source,idempotency_key)
            row=load();frozen=freeze(owner,row)
            if payload.expected_connection_version and payload.expected_connection_version!=frozen.version:
                raise HTTPException(409,'连接版本变化，请重新确认')
            from src.source_acquisition.service import SourceAcquisitionRepository
            from src.config.settings import settings
            from src.data_prep.artifact_store import ArtifactStore
            active_repository=repository or SourceAcquisitionRepository(settings.webui_db_path)
            return await acquire_connector(active_repository,owner=owner,key=idempotency_key,request=ConnectorRequest(frozen,payload.purpose),load_current=load,connector=connector_factory(row),artifact_store=artifact_store or ArtifactStore(),resume_checkpoint=payload.resume_checkpoint,pause_after_batches=batch_budget)
        except PermissionError:
            raise HTTPException(404,'连接来源不存在或无权读取') from None
        except ValueError:
            raise HTTPException(422,'连接来源配置或内容无效') from None
    return router


def resolve_registered_source(owner, source, key):
    """直接沿旧公开SourceSpec转换/Owner连接登记，不创建新连接系统。"""
    import hashlib,json
    from src.api.routes.data_tasks import _source_spec
    from src.api.auth import get_store
    spec=_source_spec(source,owner)
    if source.source_type=='database':
        row=get_store().get_db_connection(source.connection_id)
        if not row or row['user_id']!=owner:
            raise PermissionError('connection_not_found')
        version=hashlib.sha256(json.dumps(dict(row),sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
        identity=source.connection_id
    elif source.source_type=='http_api':
        version=hashlib.sha256(spec.model_dump_json().encode()).hexdigest()
        identity='http-'+version[:32]
    else:
        raise ValueError('connector_source_required')
    spec.options['task_id']='connector-'+hashlib.sha256((owner+'\0'+key).encode()).hexdigest()
    return {'owner':owner,'connection_id':identity,'version':version,'spec':spec}


def connector_for(row):
    """固定内部装配；公开请求不能选择transport或凭据resolver。"""
    from src.data_prep.artifact_store import ArtifactStore
    from src.connectors.database_connector import DatabaseConnector
    from src.services.db_connections import resolve_credential
    from src.source_acquisition.connection_source import ReadOnlyHttpConnector
    store=ArtifactStore()
    if row['spec'].source_type.value=='database':
        return DatabaseConnector(store,credential_resolver=resolve_credential)
    return ReadOnlyHttpConnector(url=row['spec'].locator,artifact_store=store,authorize=lambda:None)


router=router_for(None,resolve_registered_source,connector_for,None)
