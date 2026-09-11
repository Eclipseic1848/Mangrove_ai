"""本人冻结资料的直接读取；不采集、不复制为另一份上传。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from contextlib import closing
import sqlite3

from src.config.settings import settings
from src.services.upload_store import UploadStore
from src.source_acquisition.service import SourceAcquisitionRepository


def _store():
    from src.api.auth import get_store
    return get_store()


def uploads():
    return UploadStore(settings.data_prep_upload_root, max_bytes=settings.data_prep_max_upload_bytes)


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verified_output_metadata(owner_id, output_id):
    store = _store()
    record = store.get_semantic_delivery_output(owner_id, output_id)
    if record is None:
        raise PermissionError("正式输出不存在或无权访问")
    manifest = store.get_semantic_delivery(owner_id, record["delivery_id"])
    output = next((item for item in (manifest or {}).get("outputs", []) if item["output_id"] == output_id), None)
    qa = (output or {}).get("qa") or {}
    if not output or manifest.get("status") != "succeeded" or manifest.get("run_id") != record["run_id"] or qa.get("openable") is not True:
        raise ValueError("正式输出缺少发布质量证据")
    for key in ("sha256", "size_bytes"):
        if output[key] != record[key] or qa.get(key) != record[key]:
            raise ValueError("正式输出登记与质量证据不一致")
    return record


def verified_output(owner_id, output_id):
    from src.source_acquisition.deletion import assert_sources_readable
    assert_sources_readable(owner_id,[dict(kind='delivery_output',output_id=output_id)])
    record=verified_output_metadata(owner_id,output_id)
    path = Path(record["file_path"]).resolve()
    if not path.is_relative_to(Path(settings.semantic_execution_root).resolve()) or not path.is_file() or path.stat().st_size != record["size_bytes"] or _digest(path) != record["sha256"]:
        raise ValueError("正式输出本体完整性校验失败")
    return record, path


def resolve_frozen_source(owner_id, ref):
    from src.source_acquisition.deletion import assert_sources_readable
    assert_sources_readable(owner_id,[ref])
    kind = ref.get("kind", "upload")
    origin = dict(task_id=None, revision=None, run_id=None, delivery_id=None)
    if kind == "delivery_output":
        item, path = verified_output(owner_id, ref["output_id"])
        with closing(sqlite3.connect(settings.webui_db_path)) as connection:
            row = connection.execute("SELECT task_id,task_revision FROM formal_delivery_runs WHERE owner_id=? AND delivery_id=?", (owner_id, item["delivery_id"])).fetchone()
        origin.update(task_id=row[0] if row else None, revision=row[1] if row else None, run_id=item["run_id"], delivery_id=item["delivery_id"])
        frozen = dict(kind=kind, output_id=item["output_id"], delivery_id=item["delivery_id"], run_id=item["run_id"], source_task_id=origin["task_id"], source_revision=origin["revision"], sha256=item["sha256"])
        for key in ("delivery_id", "run_id", "source_task_id", "source_revision"):
            if key in ref and ref[key] != frozen[key]:
                raise ValueError("正式输出冻结出处变化")
        result = dict(source_key="delivery_output:"+item["output_id"], kind=kind, identity="derived", label=item["filename"], acquired_at=item.get("created_at"), time_kind="generated", media_type=item["media_type"], size_bytes=item["size_bytes"], sha256=item["sha256"], output_id=item["output_id"], host_path=path, frozen_ref=frozen, origin=origin)
    elif kind in {"web_artifact", "connector_artifact"}:
        item = SourceAcquisitionRepository(settings.webui_db_path).get_artifact(owner_id, ref["artifact_id"], include_content=True)
        if item is None or item["snapshot_id"] != ref["snapshot_id"]:
            raise PermissionError("网页原件不存在或无权访问")
        raw = bytes(item["content_blob"])
        if hashlib.sha256(raw).hexdigest() != item["content_sha256"]:
            raise ValueError("网页原件完整性校验失败")
        result = dict(source_key=kind+":"+item["artifact_id"], kind=kind, identity="original", label=item.get("title") or ("source.jsonl" if item["media_type"]=="application/x-ndjson" else "source.json" if kind=="connector_artifact" else "source.html"), acquired_at=item["read_at"], time_kind="acquired", media_type=item["media_type"], size_bytes=item["size_bytes"], sha256=item["content_sha256"], content_bytes=raw, frozen_ref=dict(ref), origin=origin, web=item)
    else:
        item = uploads().resolve(owner_id, ref["upload_id"])
        result = dict(source_key="upload:"+item.upload_id, kind="upload", identity="original", label=item.original_name, acquired_at=getattr(item,"created_at",None), time_kind="acquired" if getattr(item,"created_at",None) else "unknown", media_type=item.media_type, size_bytes=item.size_bytes, sha256=item.sha256, upload_id=item.upload_id, host_path=Path(item.storage_path), frozen_ref=dict(upload_id=item.upload_id,sha256=item.sha256), origin=origin)
    if ref.get("sha256") is not None and ref["sha256"] != result["sha256"]:
        raise ValueError("冻结来源摘要变化")
    result.update(availability="available", reason_code=None, limitations=[])
    return result


def public_source(item):
    return {key:value for key,value in item.items() if key not in {"host_path","content_bytes","frozen_ref","web"}}


def resolve_choices(owner_id, upload_ids=(), snapshot_ids=(), output_ids=()):
    items = []
    for kind, ids, field in (("upload",upload_ids,"upload_id"),("snapshot",snapshot_ids,"source_snapshot_id"),("delivery_output",output_ids,"output_id")):
        for identity in dict.fromkeys(ids):
            try:
                if kind == "snapshot":
                    snapshot = SourceAcquisitionRepository(settings.webui_db_path).get_snapshot(owner_id,identity,include_preview=False)
                    if snapshot is None:
                        raise PermissionError("来源不可用")
                    for artifact in snapshot["artifacts"]:
                        resolve_frozen_source(owner_id,dict(kind="connector_artifact" if snapshot.get("source_kind")=="connector" else "web_artifact",snapshot_id=identity,artifact_id=artifact["artifact_id"],sha256=artifact["content_sha256"]))
                    item = dict(source_key="snapshot:"+identity,kind=kind,identity="original",label=snapshot.get("request_url") or "网页资料",source_snapshot_id=identity, acquired_at=snapshot.get("created_at"),time_kind="acquired",media_type=None,size_bytes=sum(x["size_bytes"] for x in snapshot["artifacts"]),sha256=None,origin=dict(task_id=None,revision=None,run_id=None,delivery_id=None),availability="available" if snapshot["valid_page_count"] and snapshot["coverage"]["status"] != "hard_insufficient" else "unavailable",reason_code=None if snapshot["valid_page_count"] and snapshot["coverage"]["status"] != "hard_insufficient" else "source_coverage_insufficient",limitations=[],attempt_id=snapshot.get("attempt_id"),allowed_scope=snapshot["allowed_scope"],coverage=snapshot["coverage"])
                else:
                    item = public_source(resolve_frozen_source(owner_id,{"kind":kind,field:identity}))
            except (PermissionError,FileNotFoundError,ValueError,OSError):
                item = dict(source_key=kind+":"+identity,kind=kind,identity="derived" if kind=="delivery_output" else "original",label="资料不可用",**{field:identity},availability="unavailable",reason_code="source_unavailable",limitations=[],acquired_at=None,time_kind="unknown",media_type=None,size_bytes=None,sha256=None,origin=dict(task_id=None,revision=None,run_id=None,delivery_id=None))
            items.append(item)
    return items


def page(items, *, cursor=None, limit=30, snapshot_token=None):
    import json
    encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    token = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if snapshot_token is not None and snapshot_token != token:
        raise ValueError("references_changed")
    if cursor and not snapshot_token:
        raise ValueError("references_changed")
    offset = int(cursor or 0)
    if offset < 0:
        raise ValueError("invalid_cursor")
    end = min(offset+limit,len(items))
    return dict(items=items[offset:end],total=len(items),snapshot_token=token,next_cursor=str(end) if end<len(items) else None,page_complete=end>=len(items))


def history(owner_id):
    # 列表仅读登记元数据；实际选择/预览/执行必须重新核字节，不授永久可读资格。
    import json
    values = {}
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute("BEGIN")
        for row in connection.execute("SELECT snapshot_id FROM source_snapshots WHERE owner_id=?",(owner_id,)):
            identity=row[0]
            snapshot=SourceAcquisitionRepository(settings.webui_db_path).get_snapshot(owner_id,identity,include_preview=False)
            if snapshot is None:
                values['snapshot:'+identity]=dict(source_key='snapshot:'+identity,kind='snapshot',identity='original',label='已清理网页资料',source_snapshot_id=identity,acquired_at=None,time_kind='unknown',sha256=None,media_type=None,size_bytes=0,origin=dict(task_id=None,revision=None,run_id=None,delivery_id=None),availability='unavailable',reason_code='source_deleted',limitations=[])
                continue
            values["snapshot:"+identity]=dict(source_key="snapshot:"+identity,kind="snapshot",identity="original",label="连接资料" if snapshot.get("source_kind")=="connector" else "网页资料",source_snapshot_id=identity,acquired_at=snapshot.get("created_at"),time_kind="acquired",sha256=None,media_type=None,size_bytes=sum(x["size_bytes"] for x in snapshot["artifacts"]),origin=dict(task_id=None,revision=None,run_id=None,delivery_id=None),availability="available",reason_code=None,limitations=["使用时重新核验"],attempt_id=snapshot.get("attempt_id"),allowed_scope=snapshot["allowed_scope"],coverage=snapshot["coverage"])
            if not snapshot["valid_page_count"] or snapshot["coverage"]["status"]=="hard_insufficient":
                values["snapshot:"+identity].update(availability="unavailable",reason_code="source_coverage_insufficient")
        for table,column in (("formal_delivery_outputs","owner_id"),("semantic_delivery_outputs","user_id")):
            for row in connection.execute(f"SELECT output_id,delivery_id,run_id,filename,media_type,size_bytes,sha256,created_at FROM {table} WHERE {column}=?",(owner_id,)):
                item=dict(row);identity=item.pop("output_id")
                producer=connection.execute("SELECT task_id,task_revision FROM formal_delivery_runs WHERE owner_id=? AND delivery_id=?",(owner_id,item["delivery_id"])).fetchone()
                values["delivery_output:"+identity]=dict(source_key="delivery_output:"+identity,kind="delivery_output",identity="derived",label=item["filename"],output_id=identity,acquired_at=item["created_at"],time_kind="generated",media_type=item["media_type"],size_bytes=item["size_bytes"],sha256=item["sha256"],origin=dict(task_id=producer[0] if producer else None,revision=producer[1] if producer else None,run_id=item["run_id"],delivery_id=item["delivery_id"]),availability="available",reason_code=None,limitations=["使用时重新核验"])
                try:
                    from src.source_acquisition.deletion import assert_sources_readable
                    assert_sources_readable(owner_id,[dict(kind='delivery_output',output_id=identity)],connection=connection)
                    verified_output_metadata(owner_id,identity)
                except (PermissionError,ValueError,KeyError,TypeError):
                    values["delivery_output:"+identity].update(availability="unavailable",reason_code="formal_evidence_invalid")
    store=uploads()
    for path in store._user_dir(owner_id,"objects").glob("*.meta"):
        try:
            item=store._load_sidecar(path,user_id=owner_id,upload_id=path.stem)
            values["upload:"+item.upload_id]=dict(source_key="upload:"+item.upload_id,kind="upload",identity="original",label=item.original_name,upload_id=item.upload_id,acquired_at=getattr(item,"created_at",None),time_kind="acquired" if getattr(item,"created_at",None) else "unknown",media_type=item.media_type,size_bytes=item.size_bytes,sha256=item.sha256,origin=dict(task_id=None,revision=None,run_id=None,delivery_id=None),availability="available",reason_code=None,limitations=["使用时重新核验"])
        except (OSError,ValueError,PermissionError):
            continue
    return [values[key] for key in sorted(values)]


from contextlib import contextmanager
from datetime import datetime, timezone
import json
import uuid
from filelock import FileLock, Timeout
from starlette.responses import Response


def source_key(ref):
    if ref.get("kind") == "delivery_output":
        return "delivery_output:"+ref["output_id"]
    if ref.get("kind") in {"web_artifact", "connector_artifact"}:
        return ref["kind"]+":"+ref["artifact_id"]
    return "upload:"+ref["upload_id"]


@contextmanager
def source_locks(owner_id, refs, *, timeout=0):
    locks=[]
    directory=Path(settings.webui_db_path).parent/".source-read-locks"
    directory.mkdir(parents=True,exist_ok=True)
    try:
        for key in sorted({source_key(ref) for ref in refs}):
            identity="\0".join((str(Path(settings.webui_db_path).resolve()),owner_id,key))
            lock=FileLock(directory/(hashlib.sha256(identity.encode()).hexdigest()+".lock"),timeout=timeout,thread_local=False)
            lock.acquire()
            locks.append(lock)
        yield
    except Timeout as exc:
        raise ValueError("source_in_use") from exc
    finally:
        for lock in reversed(locks):
            lock.release()


class SourceReadUse:
    """独立锁可跨Response线程释放；只有实际读取收口后才能完成。"""
    def __init__(self,owner_id,refs,*,operation,task_id=None,revision=None):
        self.owner_id=owner_id;self.refs=list(refs);self.operation=operation
        self.task_id=task_id;self.revision=revision;self.use_id="source_use_"+uuid.uuid4().hex
        self._locks=None

    def recheck(self):
        for ref in self.refs:
            resolve_frozen_source(self.owner_id,ref)

    def start(self, *, lock_timeout=0):
        self._locks=source_locks(self.owner_id,self.refs,timeout=lock_timeout)
        self._locks.__enter__()
        registered=False
        try:
            # 先登记实际使用，再做任何原件读取；崩溃不能留下无事实的读取窗口。
            with closing(sqlite3.connect(settings.webui_db_path)) as connection:
                connection.execute("INSERT INTO source_read_uses VALUES (?,?,?,?,?,?,?,?,NULL)",(self.use_id,self.owner_id,self.task_id,self.revision,self.operation,json.dumps(self.refs,ensure_ascii=False),"active",datetime.now(timezone.utc).isoformat()))
                connection.commit()
            registered=True
            self.refs=[resolve_frozen_source(self.owner_id,ref)["frozen_ref"] for ref in self.refs]
            with closing(sqlite3.connect(settings.webui_db_path)) as connection:
                connection.execute("UPDATE source_read_uses SET source_refs_json=? WHERE use_id=? AND owner_id=?",(json.dumps(self.refs,ensure_ascii=False),self.use_id,self.owner_id))
                connection.commit()
        except BaseException:
            if registered:
                # 同步校验已经返回，没有后台读取；失败记录仍保留。
                self.finish(known=True)
            elif self._locks is not None:
                self._locks.__exit__(None,None,None);self._locks=None
            raise
        return self

    def finish(self,*,known):
        try:
            with closing(sqlite3.connect(settings.webui_db_path)) as connection:
                connection.execute("UPDATE source_read_uses SET state=?,finished_at=? WHERE owner_id=? AND use_id=?",("completed" if known else "unknown",datetime.now(timezone.utc).isoformat(),self.owner_id,self.use_id))
                connection.commit()
        finally:
            if self._locks is not None:
                self._locks.__exit__(None,None,None);self._locks=None


class SourceUseResponse(Response):
    def __init__(self,response,use,*,materialized_json=False):
        self.response=response;self.use=use;self.materialized_json=materialized_json
        super().__init__(status_code=response.status_code)
        self.raw_headers=response.raw_headers

    async def __call__(self,scope,receive,send):
        from src.api.execution import execution_to_thread
        from starlette.requests import ClientDisconnect
        known=False
        response_started=False
        try:
            if self.materialized_json:
                # 此JSON已在源锁内读完并序列化；发送前不再让出执行权重读原件。
                # 权限和删除意图仍同步复核，锁与使用事实直到实际发送退出才释放。
                from src.api.execution import execution_checkpoint
                from src.source_acquisition.deletion import assert_sources_readable
                execution_checkpoint()
                assert_sources_readable(self.use.owner_id,self.use.refs)
            else:
                await execution_to_thread(self.use.recheck)
            response_started=True
            await self.response(scope,receive,send)
            known=True
        except ClientDisconnect:
            # 原响应栈已退出且受保护线程已收口；断连不等于读取仍在后台。
            known=True
            raise
        except BaseException:
            # recheck的执行线程已由execution_to_thread等待收口，响应尚未开始。
            known=not response_started
            raise
        finally:
            # 已交出响应但无法证明清理完成的异常保留未知。
            self.use.finish(known=known)


def references(owner_id,kind,identity):
    def matches(ref):
        return (ref.get("snapshot_id")==identity if kind=="snapshot" else source_key(ref)==kind+":"+identity)
    items=[]
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute("BEGIN")
        for row in connection.execute("SELECT r.task_id,r.revision,r.source_refs_json,t.deleted_at FROM semantic_workspace_revisions r JOIN semantic_workspace_tasks t ON t.task_id=r.task_id AND t.user_id=r.user_id WHERE r.user_id=? ORDER BY r.task_id,r.revision",(owner_id,)):
            if any(matches(ref) for ref in json.loads(row["source_refs_json"] or "[]")):
                items.append(dict(task_id=row["task_id"],revision=row["revision"],reference_kind="revision",use_id=None,state="retained",in_recycle_bin=row["deleted_at"] is not None))
                runtime=connection.execute("SELECT status FROM agentic_runtime_runs WHERE user_id=? AND task_id=? AND revision=?",(owner_id,row["task_id"],row["revision"])).fetchone()
                if runtime and runtime[0] in {"preparing","running","needs_input","cancelling"}:
                    items.append(dict(task_id=row["task_id"],revision=row["revision"],reference_kind="runtime",use_id=None,state="active",in_recycle_bin=row["deleted_at"] is not None))
        for row in connection.execute("SELECT delivery_id,run_id,task_id,task_revision,manifest_json,status FROM formal_delivery_runs WHERE owner_id=? ORDER BY delivery_id",(owner_id,)):
            manifest=json.loads(row["manifest_json"])
            hashes=manifest.get("source_artifact_hashes") or {}
            if kind=="snapshot":
                identities=list(connection.execute("SELECT artifact_id,content_sha256 FROM source_artifacts WHERE owner_id=? AND snapshot_id=?",(owner_id,identity)))
                identities.extend((row[0].split(':',1)[1],row[1]) for row in connection.execute("SELECT source_key,sha256 FROM source_deletions WHERE owner_id=? AND snapshot_id=?",(owner_id,identity)))
                linked=any(hashes.get(artifact[0])==artifact[1] for artifact in identities)
            else:
                linked=identity in hashes
            if linked:
                task=connection.execute("SELECT deleted_at FROM semantic_workspace_tasks WHERE user_id=? AND task_id=?",(owner_id,row["task_id"])).fetchone()
                items.append(dict(task_id=row["task_id"],revision=row["task_revision"],reference_kind="delivery",delivery_id=row["delivery_id"],run_id=row["run_id"],task_exists=task is not None,use_id=None,state="published",in_recycle_bin=task[0] is not None if task else None))
        for row in connection.execute("SELECT * FROM source_read_uses WHERE owner_id=? AND state!='completed' ORDER BY use_id",(owner_id,)):
            if any(matches(ref) for ref in json.loads(row["source_refs_json"])):
                items.append(dict(task_id=row["task_id"],revision=row["revision"],reference_kind=row["operation"],use_id=row["use_id"],state=row["state"],in_recycle_bin=None))
    return items


def assert_no_open_uses(owner_id,ref):
    if any(item.get("use_id") for item in references(owner_id,"upload",ref["upload_id"])):
        raise ValueError("source_in_use")


def freeze_source_call(owner_id,refs,call,*args,**kwargs):
    from fastapi import HTTPException
    from contextlib import ExitStack
    with ExitStack() as stack:
        try:
            stack.enter_context(source_locks(owner_id,refs))
            for ref in refs:
                resolve_frozen_source(owner_id,ref)
        except PermissionError as exc:
            raise HTTPException(404,"来源不存在或无权访问") from exc
        except (ValueError,OSError) as exc:
            raise HTTPException(409,"source_in_use" if str(exc)=="source_in_use" else "来源完整性变化，请重新核验") from exc
        # 写事务自身的异常保持原阶段语义，不伪装成读取前拒绝。
        return call(*args,**kwargs)


def guarded_response(operation,refs_factory,*,joined_reader=False,lock_timeout=0,materialized_json=False):
    """路由仅装配身份，锁与使用事实覆盖正文读取及完整响应。"""
    import asyncio,functools,inspect
    from fastapi import HTTPException
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse
    def decorate(function):
        from typing import get_type_hints
        signature=inspect.signature(function)
        hints=get_type_hints(function)
        signature=signature.replace(parameters=[parameter.replace(annotation=hints.get(name,parameter.annotation)) for name,parameter in signature.parameters.items()],return_annotation=hints.get("return",signature.return_annotation))
        def begin(arguments):
            bound=signature.bind_partial(*arguments[0],**arguments[1])
            values=bound.arguments
            owner=values["user"]["user_id"]
            try:
                refs=refs_factory(values)
            except PermissionError as exc:
                raise HTTPException(404,"来源不存在或无权访问") from exc
            try:
                use=SourceReadUse(owner,refs,operation=operation,task_id=values.get("task_id"),revision=values.get("revision"))
                return use.start(lock_timeout=lock_timeout),bound
            except PermissionError as exc:
                # 已确认属于Owner冻结任务的成员消失属于完整性缺口，不冒充未授权身份。
                raise HTTPException(409 if values.get("task_id") else 404,"冻结来源不可用" if values.get("task_id") else "来源不存在或无权访问") from exc
            except (ValueError,OSError) as exc:
                raise HTTPException(409,"source_in_use" if str(exc)=="source_in_use" else "来源不可用") from exc
        def wrap(value,use):
            if materialized_json and not isinstance(value,JSONResponse):
                raise TypeError("已物化响应必须在受保护函数内生成JSONResponse")
            return SourceUseResponse(value if isinstance(value,Response) else JSONResponse(jsonable_encoder(value)),use,materialized_json=materialized_json)
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def wrapped(*args,**kwargs):
                use,bound=begin((args,kwargs))
                try:
                    return wrap(await function(*bound.args,**bound.kwargs),use)
                except HTTPException:
                    # 明确业务拒绝已退出读取函数，未交出响应，不留下悬置使用。
                    use.finish(known=True)
                    raise
                except asyncio.CancelledError:
                    # 仅显式采用execution_to_thread收口的入口可确认取消已结束读取。
                    use.finish(known=joined_reader)
                    raise
                except BaseException:
                    use.finish(known=False)
                    raise
        else:
            @functools.wraps(function)
            def wrapped(*args,**kwargs):
                use,bound=begin((args,kwargs))
                try:
                    return wrap(function(*bound.args,**bound.kwargs),use)
                except HTTPException:
                    # 明确业务拒绝已退出读取函数，未交出响应，不留下悬置使用。
                    use.finish(known=True)
                    raise
                except BaseException:
                    use.finish(known=False)
                    raise
        wrapped.__signature__=signature
        return wrapped
    return decorate


def workspace_response_refs(values):
    owner=values["user"]["user_id"];task=_store().get_semantic_workspace_task(owner,values["task_id"])
    if task is None or task.get("deleted_at"):
        raise PermissionError("任务不存在")
    revision=values.get("revision") or task["active_revision"]
    values["revision"]=revision
    row=_store().get_semantic_workspace_revision(owner,values["task_id"],revision)
    if row is None:
        raise PermissionError("修订不存在")
    refs=row["source_refs"]
    if values.get("artifact_id"):
        refs=[ref for ref in refs if values["artifact_id"] in {ref.get("upload_id"),ref.get("artifact_id"),ref.get("output_id")}]
        if not refs:
            raise PermissionError("来源不属于所选版本")
    return refs


@contextmanager
def workspace_source_read_context(request,source_ids):
    from src.api.execution import execution_checkpoint
    from src.account_execution import current_authorization
    auth=current_authorization(required=True)
    if auth.owner_user_id!=request.user_id:
        raise PermissionError("读取执行身份不一致")
    execution_checkpoint(required=True)
    row=_store().get_semantic_workspace_revision(request.user_id,request.task_id,request.revision)
    if row is None:
        raise ValueError("冻结来源修订不存在")
    mapping={ref.get("upload_id") or ref.get("artifact_id") or ref.get("output_id"):ref for ref in row["source_refs"]}
    if any(identity not in mapping for identity in source_ids):
        raise ValueError("来源不属于冻结修订")
    refs=[mapping[identity] for identity in source_ids]
    with source_locks(request.user_id,refs):
        execution_checkpoint(required=True)
        task=_store().get_semantic_workspace_task(request.user_id,request.task_id)
        if task is None or task.get("deleted_at"):
            raise PermissionError("任务已不可执行")
        for identity,ref in zip(source_ids,refs):
            source=resolve_frozen_source(request.user_id,ref)
            frozen=next((item for item in request.sources if item.upload_id==identity),None)
            if frozen is None or frozen.sha256!=source["sha256"]:
                raise ValueError("读取请求与原实体不一致")
        yield


def bundle_response_refs(values):
    owner=values["user"]["user_id"]
    task=_store().get_semantic_workspace_task(owner,values["task_id"])
    if task is None or task.get("deleted_at"): raise PermissionError("任务不存在")
    revision=values.get("revision") or task["active_revision"]
    values["revision"]=revision
    row=_store().get_semantic_workspace_revision(owner,values["task_id"],revision)
    if row is None: raise PermissionError("修订不存在")
    manifest=_store().latest_semantic_delivery(owner,row["run_id"]) if row.get("run_id") else None
    refs=[{"kind":"delivery_output","output_id":item["output_id"]} for item in (manifest or {}).get("outputs",[])]
    if values.get("output_id"):
        refs=[ref for ref in refs if ref["output_id"]==values["output_id"]]
    return refs+(row["source_refs"] if values.get("include_sources") else [])
