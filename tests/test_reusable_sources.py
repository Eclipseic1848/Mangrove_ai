"""持久使用事实与同源并发边界。"""
import pytest
from tests.test_workspace_canvas import canvas, execution_owner


def test_active_source_use_is_visible_and_blocks_concurrent_delete(canvas):
    from src.source_acquisition.reuse import SourceReadUse
    client, _, task_id, _, upload = canvas
    use = SourceReadUse("user-a", [{"upload_id":upload.upload_id,"sha256":upload.sha256}], operation="export", task_id=task_id, revision=1)
    use.start()
    try:
        refs=client.get("/api/semantic-workspace/source-references",params={"kind":"upload","id":upload.upload_id})
        assert refs.status_code==200,refs.text
        assert any(item.get("use_id")==use.use_id and item["state"]=="active" for item in refs.json()["items"])
        denied=client.delete("/api/data-sources/uploads/"+upload.upload_id)
        assert denied.status_code==409
    finally:
        use.finish(known=True)


def test_guarded_response_resolves_original_forward_signature_and_finishes_rejection(monkeypatch):
    import inspect
    from fastapi import HTTPException
    from src.source_acquisition import reuse
    class Use:
        def __init__(self,*args,**kwargs): self.finished=[]
        def start(self): return self
        def finish(self,*,known): self.finished.append(known)
    use=Use()
    monkeypatch.setattr(reuse,"SourceReadUse",lambda *args,**kwargs:use)
    namespace={"HTTPException":HTTPException,"Choice":str}
    exec("def endpoint(choice: 'Choice', user):\n    raise HTTPException(422, '明确拒绝')",namespace)
    wrapped=reuse.guarded_response("preview",lambda values:[])(namespace["endpoint"])
    assert inspect.signature(wrapped).parameters["choice"].annotation is str
    with pytest.raises(HTTPException): wrapped("x",user={"user_id":"a"})
    assert use.finished==[True]


@pytest.mark.asyncio
async def test_response_known_disconnect_releases_completed_use(monkeypatch):
    monkeypatch.setattr("src.api.execution.execution_checkpoint",lambda:None)
    from starlette.requests import ClientDisconnect
    from src.source_acquisition.reuse import SourceUseResponse
    from starlette.responses import Response
    class Use:
        def recheck(self): pass
        def finish(self,*,known): self.known=known
    class Disconnected(Response):
        async def __call__(self,*args): raise ClientDisconnect()
    use=Use()
    with pytest.raises(ClientDisconnect):
        await SourceUseResponse(Disconnected(),use)({},None,None)
    assert use.known is True


def test_new_upload_records_acquired_time_and_old_metadata_stays_unknown(tmp_path):
    import io,json
    from src.services.upload_store import UploadStore
    store=UploadStore(str(tmp_path),max_bytes=1000)
    item=store.save_bytes('owner','data.csv',b'a,b\n1,2\n',media_type='text/csv')
    assert item.created_at
    path=tmp_path/'owner'/'objects'/(item.upload_id+'.meta')
    data=json.loads(path.read_text(encoding='utf-8'));data.pop('created_at')
    path.write_text(json.dumps(data),encoding='utf-8')
    assert store.resolve('owner',item.upload_id).created_at is None


@pytest.mark.asyncio
async def test_response_cancellation_waits_for_actual_reader_thread(monkeypatch):
    import asyncio,threading
    from starlette.responses import Response
    from src.source_acquisition.reuse import SourceUseResponse
    monkeypatch.setattr('src.api.execution.execution_checkpoint',lambda:None)
    entered=threading.Event();release=threading.Event();finished=[]
    class Use:
        def recheck(self): entered.set();release.wait(5)
        def finish(self,*,known): finished.append(known)
    task=asyncio.create_task(SourceUseResponse(Response(),Use())({},None,None))
    await asyncio.to_thread(entered.wait,5)
    task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
    assert not finished and not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert finished==[True]


def test_source_use_is_registered_before_read_and_failed_read_closes(canvas,monkeypatch):
    import sqlite3
    from contextlib import closing
    from src.source_acquisition import reuse
    from src.config.settings import settings
    client,_,_,_,upload=canvas
    def inspect(owner,ref):
        with closing(sqlite3.connect(settings.webui_db_path)) as connection:
            assert connection.execute("SELECT count(*) FROM source_read_uses WHERE state='active'").fetchone()[0]==1
        raise ValueError('原件失效')
    monkeypatch.setattr(reuse,'resolve_frozen_source',inspect)
    with pytest.raises(ValueError): reuse.SourceReadUse('user-a',[{'upload_id':upload.upload_id}],operation='preview').start()
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        assert connection.execute("SELECT state FROM source_read_uses ORDER BY started_at DESC LIMIT 1").fetchone()[0]=='completed'


@pytest.mark.asyncio
async def test_response_cleanup_failure_remains_unknown(monkeypatch):
    from src.source_acquisition.reuse import SourceUseResponse
    from starlette.responses import Response
    monkeypatch.setattr('src.api.execution.execution_checkpoint',lambda:None)
    finished=[]
    class Use:
        def recheck(self): pass
        def finish(self,*,known): finished.append(known)
    class Broken(Response):
        async def __call__(self,*args): raise RuntimeError('清理结果未知')
    with pytest.raises(RuntimeError): await SourceUseResponse(Broken(),Use())({},None,None)
    assert finished==[False]


def test_failed_usage_registration_performs_no_body_read_and_releases_lock(canvas,monkeypatch):
    import sqlite3
    from contextlib import closing
    from src.source_acquisition import reuse
    from src.config.settings import settings
    _,_,_,_,upload=canvas
    calls=[]
    monkeypatch.setattr(reuse,'resolve_frozen_source',lambda *args:calls.append(args))
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        connection.execute("CREATE TRIGGER synthetic_use_failure BEFORE INSERT ON source_read_uses BEGIN SELECT RAISE(ABORT,'模拟登记故障'); END");connection.commit()
    try:
        with pytest.raises(sqlite3.IntegrityError): reuse.SourceReadUse('user-a',[{'upload_id':upload.upload_id}],operation='preview').start()
        assert calls==[]
        with reuse.source_locks('user-a',[{'upload_id':upload.upload_id}]): pass
    finally:
        with closing(sqlite3.connect(settings.webui_db_path)) as connection:
            connection.execute('DROP TRIGGER synthetic_use_failure');connection.commit()


@pytest.mark.asyncio
async def test_async_preview_cancel_after_joined_worker_completes_usage(monkeypatch):
    import asyncio,threading
    from src.source_acquisition import reuse
    from src.api.execution import execution_to_thread
    monkeypatch.setattr('src.api.execution.execution_checkpoint',lambda:None)
    entered=threading.Event();release=threading.Event();finished=[]
    class Use:
        def start(self): return self
        def finish(self,*,known): finished.append(known)
    monkeypatch.setattr(reuse,'SourceReadUse',lambda *args,**kwargs:Use())
    @reuse.guarded_response('preview',lambda values:[],joined_reader=True)
    async def preview(user):
        def read(): entered.set();release.wait(5)
        await execution_to_thread(read)
        return {}
    task=asyncio.create_task(preview(user={'user_id':'a'}))
    await asyncio.to_thread(entered.wait,5);task.cancel();await asyncio.sleep(0)
    assert not finished
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert finished==[True]


@pytest.mark.parametrize('asynchronous',[False,True])
@pytest.mark.parametrize('explicit_revision',[None,1])
def test_response_body_uses_revision_captured_before_active_revision_changes(monkeypatch,asynchronous,explicit_revision):
    import asyncio,json,threading
    from concurrent.futures import ThreadPoolExecutor
    from src.source_acquisition import reuse
    active={'revision':1};registered=[]
    captured=threading.Event();release=threading.Event()
    class Store:
        def get_semantic_workspace_task(self,owner,task): return {'active_revision':active['revision'],'deleted_at':None}
        def get_semantic_workspace_revision(self,owner,task,revision): return {'source_refs':[{'upload_id':'source-'+str(revision),'sha256':str(revision)*64}]}
    class Use:
        def __init__(self,owner,refs,**kwargs): registered.append((kwargs['revision'],refs[0]['upload_id']))
        def start(self):
            captured.set()
            assert release.wait(5)
            return self
        def finish(self,*,known): pass
    monkeypatch.setattr(reuse,'_store',lambda:Store())
    monkeypatch.setattr(reuse,'SourceReadUse',Use)
    def body(task_id,user,revision=None): return {'revision':revision or active['revision']}
    async def async_body(task_id,user,revision=None): return body(task_id,user,revision)
    wrapped=reuse.guarded_response('preview',reuse.workspace_response_refs)(async_body if asynchronous else body)
    def invoke():
        result=wrapped('task',user={'user_id':'owner'},revision=explicit_revision)
        return asyncio.run(result) if asynchronous else result
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(invoke)
        try:
            assert captured.wait(5)
            active['revision']=2
        finally:
            release.set()
        response=pending.result(timeout=5)
    assert registered==[(1,'source-1')]
    assert json.loads(response.response.body)=={'revision':1}
