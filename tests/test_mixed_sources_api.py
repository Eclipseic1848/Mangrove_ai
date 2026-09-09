"""混合来源API的冻结输入合同。"""
import pytest
from src.api.routes.semantic_workspace import WorkspaceTaskCreateIn


def test_create_accepts_files_and_independent_snapshots():
    value = WorkspaceTaskCreateIn(objective_text='按实体ID对比',upload_ids=('A',),source_snapshot_ids=('B','C'),quantity_requirement='尽可能多',completeness_requirement='探索性',runtime_version='pi')
    assert value.source_snapshot_ids == ('B','C')


def test_create_rejects_ambiguous_old_and_new_snapshot_fields():
    with pytest.raises(ValueError):
        WorkspaceTaskCreateIn(objective_text='读取',source_snapshot_id='B',source_snapshot_ids=('C',),quantity_requirement='尽可能多',completeness_requirement='探索性',runtime_version='pi')


def test_revision_source_contract_has_no_fake_snapshot_anchor(tmp_path):
    from tests.database_migration_helpers import migrated_webui_database
    from tests.account_execution_helpers import seed_execution_owner
    from src.account_execution import execution_context
    from src.api.store import WebUIStore
    database = migrated_webui_database(tmp_path/'contract.db')
    auth = seed_execution_owner(database)
    store = WebUIStore(str(database))
    contract = {'schema_version':1,'goal_contract':{'objective':'保留目标'},'web_sources':[]}
    with execution_context(auth):
        store.create_semantic_workspace_task('owner-a',task_id='mixed',title='合成',objective_text='保留目标',upload_ids=['A'],output_formats=['csv'],provider='local',model=None,external_api_confirmed=False,source_refs=[{'upload_id':'A','sha256':'a'}],source_contract=contract)
    assert store.get_source_contract('owner-a','mixed',1) == contract
    assert store.get_source_contract('owner-b','mixed',1) is None
    assert store.get_web_task_contract('owner-a','mixed',1) is None


def test_source_resolver_keeps_upload_and_each_snapshot(monkeypatch):
    from types import SimpleNamespace
    from src.api.routes import semantic_workspace as routes
    monkeypatch.setattr(routes,'_uploads',lambda:SimpleNamespace(resolve=lambda owner,id:SimpleNamespace(upload_id=id,sha256='file-hash')))
    class Repository:
        def __init__(self,*args): pass
        def get_snapshot(self,owner,id):
            return {'snapshot_id':id,'valid_page_count':1,'failed_page_count':0,'allowed_scope':{'url':id},'coverage':{'status':'scope_complete'},'artifacts':[{'artifact_id':id+'-page','content_sha256':id+'-hash'}]}
    monkeypatch.setattr(routes,'SourceAcquisitionRepository',Repository)
    refs,snapshots = routes._resolve_mixed_sources('owner',('A',),('B','C'))
    assert [ref.get('upload_id') or ref['artifact_id'] for ref in refs] == ['A','B-page','C-page']
    assert [item['snapshot_id'] for item in snapshots] == ['B','C']


def test_create_persists_multiple_web_groups_and_file(tmp_path,monkeypatch):
    import sqlite3
    from pathlib import Path
    from src.config.settings import settings
    from src.api.auth import get_store
    from tests.test_web_source_delivery_api import _client, _seed_snapshot
    from tests.test_pi_runtime_workspace_api import _uploads
    client = _client(tmp_path,monkeypatch,role='admin')
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def no_worker(app):
        yield
    # 本纵切面只核API事务；运行与关闭由独立Runtime测试验证。
    client.app.router.lifespan_context = no_worker
    first,_ = _seed_snapshot(Path(settings.webui_db_path))
    with sqlite3.connect(settings.webui_db_path) as connection:
        for table in ('source_acquisition_attempts','source_snapshots','source_artifacts'):
            row = connection.execute(f'SELECT * FROM {table}').fetchone()
            columns = [item[1] for item in connection.execute(f'PRAGMA table_info({table})')]
            values = [value+'-C' if column in {'attempt_id','snapshot_id','artifact_id','idempotency_key'} and value is not None else value for column,value in zip(columns,row)]
            connection.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in values)})',values)
    _,document = _uploads(tmp_path)
    with client:
        response = client.post('/api/semantic-workspace/tasks',json={'objective_text':'按实体ID比较资料','upload_ids':[document],'source_snapshot_ids':[first,first+'-C'],'quantity_requirement':'尽可能多','completeness_requirement':'探索性','output_formats':['json'],'runtime_version':'pi'})
        assert response.status_code == 202, response.text
        task = response.json()
        revision = get_store().get_semantic_workspace_revision('user-a',task['task_id'],1)
        assert len(revision['source_refs']) == 3
        assert len(revision['source_contract']['web_sources']) == 2
        from src.api.routes import semantic_workspace as routes
        findings = routes._workspace_source_findings('user-a',task)
        assert {item.get('snapshot_id') for item in findings if item.get('snapshot_id')} == {first,first+'-C'}
        details = client.get('/api/semantic-workspace/tasks/'+task['task_id'])
        assert details.status_code == 200, details.text
        assert len(details.json()['web_sources']) == 2

@pytest.mark.parametrize('state',['other_owner','hard_insufficient','empty'])
def test_each_snapshot_owner_and_hard_gate_is_independent(monkeypatch,state):
    from src.api.routes import semantic_workspace as routes
    class Repository:
        def __init__(self,*args): pass
        def get_snapshot(self,owner,id):
            if state=='other_owner': return None
            return {'valid_page_count':0 if state=='empty' else 9,'coverage':{'status':'hard_insufficient' if state=='hard_insufficient' else 'scope_complete'}}
    monkeypatch.setattr(routes,'SourceAcquisitionRepository',Repository)
    with pytest.raises(routes.HTTPException) as error:
        routes._resolve_mixed_sources('owner',(),('bad',))
    assert error.value.status_code in {404,409}


def test_revision_category_omission_and_explicit_empty_are_distinct():
    from src.api.routes.semantic_workspace import WorkspaceRevisionIn
    default=WorkspaceRevisionIn(instruction='调整',expected_active_revision=1)
    removed=WorkspaceRevisionIn(instruction='明确移除网页',expected_active_revision=1,source_snapshot_ids=())
    assert default.source_snapshot_ids is None and default.upload_ids is None
    assert removed.source_snapshot_ids==() and removed.upload_ids is None


def test_confirmed_field_rule_preserves_frozen_refs_and_new_run(tmp_path,monkeypatch):
    from pathlib import Path
    from src.api.routes import semantic_workspace as route
    from src.api.auth import get_store
    from src.config.settings import settings
    from src.conversation_steering import ContextDelta, DeltaConfidence, TurnIntent
    from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime, _client, _seed_snapshot, _wait_for_delivery
    runtime=CoverageAwareWebPiRuntime()
    client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
    snapshot,_=_seed_snapshot(Path(settings.webui_db_path))
    class Rewriter:
        async def rewrite(self,turn,request):
            return ContextDelta(delta_id='field-'+turn.turn_id,owner_id=turn.owner_id,task_id=turn.task_id,inherited_revision=turn.revision,source_turn_ids=(turn.turn_id,),intent=TurnIntent.TASK_REFINEMENT,confidence=DeltaConfidence.HIGH,normalized_text=request.text,field_semantics_delta={'公开规模':'同实体ID冲突以来源C为准'})
    monkeypatch.setattr(route,'build_context_rewriter',lambda request,**kwargs:Rewriter())
    with client:
        created=client.post('/api/semantic-workspace/tasks',json={'objective_text':'按实体ID比较公开规模','source_snapshot_ids':[snapshot],'quantity_requirement':'尽可能多','completeness_requirement':'探索性','output_formats':['json'],'runtime_version':'pi'})
        assert created.status_code==202,created.text
        task_id=created.json()['task_id'];_wait_for_delivery(client,task_id)
        store=get_store();old=store.get_semantic_workspace_revision('user-a',task_id,1)
        path='/api/semantic-workspace/tasks/'+task_id
        proposed=client.post(path+'/turns',json={'text':'同实体ID冲突以来源C为准'})
        assert proposed.status_code==200,proposed.text
        assert store.get_semantic_workspace_task('user-a',task_id)['active_revision']==1
        assert store.get_source_contract('user-a',task_id,1)==old['source_contract']
        confirmed=client.post(path+'/revision-proposals/'+proposed.json()['proposal_id']+'/decision',json={'mode':'cancel_now'})
        assert confirmed.status_code==202,confirmed.text
        _wait_for_delivery(client,task_id)
        new=store.get_semantic_workspace_revision('user-a',task_id,2)
        assert new['source_refs']==old['source_refs']
        assert new['source_contract']['goal_contract']['field_semantics']['公开规模']=='同实体ID冲突以来源C为准'
        assert runtime.requests[-1].goal_contract==new['source_contract']['goal_contract']
        assert runtime.start_calls==2

@pytest.mark.parametrize('axis',['source_scope_delta','permission_delta'])
def test_field_business_rule_cannot_grant_scope_or_permission(monkeypatch,axis):
    from types import SimpleNamespace
    from src.api.routes import semantic_workspace as route
    monkeypatch.setattr(route,'get_store',lambda:SimpleNamespace(get_source_contract=lambda *args:{'goal_contract':{'objective':'旧目标'},'web_sources':[]}))
    delta=SimpleNamespace(source_scope_delta=(),permission_delta=(),field_semantics_delta={'公开规模':'以C为准'},selection_delta={},coverage_delta={})
    setattr(delta,axis,('扩权',))
    with pytest.raises(route.HTTPException) as error:
        route._inherit_web_contract_hook(None,owner_id='owner',source_task_id='task',source_revision=1,target_task_id='task',target_revision=2,objective_text='已确认业务目标',output_formats=('json',),runtime_binding=None,capability_manifest=None,semantic_delta=delta)
    assert error.value.status_code==422


def test_confirmed_field_rules_accumulate_without_changing_sources(monkeypatch):
    import json
    from types import SimpleNamespace
    from src.api.routes import semantic_workspace as route
    contract={'schema_version':1,'goal_contract':{'objective':'旧目标','field_semantics':{'实体ID':'唯一实体身份'}},'web_sources':[]}
    monkeypatch.setattr(route,'get_store',lambda:SimpleNamespace(get_source_contract=lambda *args:contract))
    monkeypatch.setattr(route,'_runtime_repository',lambda:SimpleNamespace(freeze_runtime_binding=lambda *args,**kwargs:None))
    binding=SimpleNamespace(external_run_id='synthetic',model_dump=lambda **kwargs:{})
    delta=SimpleNamespace(source_scope_delta=(),permission_delta=(),field_semantics_delta={'公开规模':'以C为准'},selection_delta={},coverage_delta={})
    hook=route._inherit_web_contract_hook(None,owner_id='owner',source_task_id='task',source_revision=1,target_task_id='task',target_revision=2,objective_text='确认后的完整目标',output_formats=('json',),runtime_binding=binding,capability_manifest=binding,semantic_delta=delta)
    writes=[]
    hook(SimpleNamespace(execute=lambda sql,values:writes.append((sql,values))))
    saved=json.loads(writes[0][1][0])
    assert saved['goal_contract']['field_semantics']=={'实体ID':'唯一实体身份','公开规模':'以C为准'}
    assert saved['web_sources']==[]


def test_revision_idempotency_replays_exact_receipt_without_re_resolving(tmp_path,monkeypatch):
    from pathlib import Path
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime,_client,_seed_snapshot,_wait_for_delivery
    runtime=CoverageAwareWebPiRuntime();client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
    snapshot,_=_seed_snapshot(Path(settings.webui_db_path))
    with client:
        initial=client.post('/api/semantic-workspace/tasks',json={'objective_text':'整理资料','source_snapshot_id':snapshot,'quantity_requirement':'尽可能多','completeness_requirement':'探索性','output_formats':['json'],'runtime_version':'pi'})
        assert initial.status_code==202,initial.text
        task_id=initial.json()['task_id'];_wait_for_delivery(client,task_id)
        path='/api/semantic-workspace/tasks/'+task_id+'/revisions'
        payload={'instruction':'仅调整表述','expected_active_revision':1,'source_snapshot_ids':[snapshot],'upload_ids':[]}
        first=client.post(path,json=payload,headers={'Idempotency-Key':'same-revision'})
        assert first.status_code==202,first.text
        _wait_for_delivery(client,task_id)
        monkeypatch.setattr(route,'_resolve_mixed_sources',lambda *args:pytest.fail('完成回执不重新解析来源'))
        replay=client.post(path,json=payload,headers={'Idempotency-Key':'same-revision'})
        assert replay.status_code==202,replay.text
        assert replay.json()['revision']==first.json()['revision']==2
        assert runtime.start_calls==2
        conflict=client.post(path,json={**payload,'instruction':'不同请求'},headers={'Idempotency-Key':'same-revision'})
        assert conflict.status_code==409


def test_revision_concurrent_claim_prepares_one_binding(tmp_path,monkeypatch):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime,_client,_seed_snapshot,_wait_for_delivery
    runtime=CoverageAwareWebPiRuntime();client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
    snapshot,_=_seed_snapshot(Path(settings.webui_db_path))
    entered=threading.Event();release=threading.Event();calls=[]
    with client:
        initial=client.post('/api/semantic-workspace/tasks',json={'objective_text':'整理资料','source_snapshot_id':snapshot,'quantity_requirement':'尽可能多','completeness_requirement':'探索性','output_formats':['json'],'runtime_version':'pi'})
        assert initial.status_code==202,initial.text
        task_id=initial.json()['task_id'];_wait_for_delivery(client,task_id)
        manager=route.get_semantic_workspace_manager();original=manager.prepare_runtime_binding
        async def controlled(**kwargs):
            calls.append(kwargs);entered.set()
            assert await asyncio.to_thread(release.wait,10)
            return await original(**kwargs)
        monkeypatch.setattr(manager,'prepare_runtime_binding',controlled)
        path='/api/semantic-workspace/tasks/'+task_id+'/revisions'
        payload={'instruction':'并发确认同一请求','expected_active_revision':1}
        headers={'Idempotency-Key':'concurrent-revision'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(client.post,path,json=payload,headers=headers)
            try:
                assert entered.wait(10)
                pending=client.post(path,json=payload,headers=headers)
                assert pending.status_code==409 and '未知' in pending.text
                assert len(calls)==1
            finally:
                release.set()
            saved=first.result(timeout=15)
        assert saved.status_code==202,saved.text
        _wait_for_delivery(client,task_id)
        replay=client.post(path,json=payload,headers=headers)
        assert replay.status_code==202 and replay.json()['revision']==2
        assert len(calls)==1 and runtime.start_calls==2


def test_file_only_context_confirmation_is_not_silently_dropped(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from src.config.settings import settings
    from src.task_context import TaskContextRepository
    from tests.test_web_source_delivery_api import _client
    from tests.test_pi_runtime_workspace_api import _uploads
    from tests.test_task_context_api import _seed_context_options
    client=_client(tmp_path,monkeypatch,role='admin')
    @asynccontextmanager
    async def no_worker(app): yield
    client.app.router.lifespan_context=no_worker
    memory,_=_seed_context_options();_,upload=_uploads(tmp_path)
    selection={'memories':[{'memory_id':memory}]}
    with client:
        preview=client.post('/api/semantic-workspace/context-preview',json={'objective_text':'整理文件','output_formats':['json'],'selection':selection})
        assert preview.status_code==200,preview.text
        created=client.post('/api/semantic-workspace/tasks',json={'objective_text':'整理文件','upload_ids':[upload],'output_formats':['json'],'runtime_version':'pi','context_selection':selection,'context_preview_sha256':preview.json()['preview_sha256']})
        assert created.status_code==202,created.text
        assert TaskContextRepository(settings.webui_db_path).get_frozen('user-a',created.json()['task_id'],1) is not None


@pytest.mark.parametrize("bad", ["upload", "stale"])
def test_revision_proven_rejection_releases_claim(tmp_path, monkeypatch, bad):
    from contextlib import asynccontextmanager
    from tests.test_web_source_delivery_api import _client
    from tests.test_pi_runtime_workspace_api import _uploads
    client = _client(tmp_path, monkeypatch, role="admin")
    @asynccontextmanager
    async def no_worker(app): yield
    client.app.router.lifespan_context = no_worker
    _, upload = _uploads(tmp_path)
    with client:
        created = client.post("/api/semantic-workspace/tasks", json={"objective_text":"整理文件", "upload_ids":[upload], "output_formats":["json"], "runtime_version":"pi"})
        assert created.status_code == 202, created.text
        path = "/api/semantic-workspace/tasks/" + created.json()["task_id"] + "/revisions"
        payload = {"instruction":"修改资料", "expected_active_revision": 2 if bad == "stale" else 1, "upload_ids":["missing"] if bad == "upload" else [upload]}
        headers = {"Idempotency-Key":"rejected-request"}
        first = client.post(path, json=payload, headers=headers)
        assert first.status_code == (404 if bad == "upload" else 409)
        assert first.headers.get("X-Mangrove-Revision-Outcome") == "rejected"
        second = client.post(path, json=payload, headers=headers)
        assert second.status_code == first.status_code
        assert second.headers.get("X-Mangrove-Revision-Outcome") == "rejected"


@pytest.mark.parametrize("with_event, change", [(False, "web"), (True, "web"), (False, "bad-format"), (False, "compatible-format")])
def test_source_edit_rejects_inherited_file_reuse_before_prepare(tmp_path, monkeypatch, with_event, change):
    from contextlib import asynccontextmanager
    from pathlib import Path
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    from tests.test_web_source_delivery_api import _client, _seed_snapshot
    from tests.test_pi_runtime_workspace_api import _uploads
    client = _client(tmp_path, monkeypatch, role="admin")
    @asynccontextmanager
    async def no_worker(app): yield
    client.app.router.lifespan_context = no_worker
    _, upload = _uploads(tmp_path)
    snapshot, _ = _seed_snapshot(Path(settings.webui_db_path))
    with client:
        created = client.post("/api/semantic-workspace/tasks", json={"objective_text":"整理文件", "upload_ids":[upload], "output_formats":["json"], "runtime_version":"pi"})
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        from src.capability_catalog import CapabilityCatalog, SqliteCapabilityCatalogRepository
        from src.capability_catalog.models import CatalogActor, CapabilityPackRef
        from src.conversation_steering import CapabilityPack, ProcedureScope, CapabilityMaturity
        from src.semantic_harness.capabilities import TABLE_DUCKDB_MANIFEST
        repository = SqliteCapabilityCatalogRepository(settings.webui_db_path)
        ref = CapabilityPackRef(pack_id="table.duckdb", version="1.0.0", digest="sha256:" + "a" * 64)
        repository.save_pack(CapabilityPack(**ref.model_dump(), scope=ProcedureScope.PERSONAL, owner_id="user-a", maturity=CapabilityMaturity.VERIFIED, source_provenance=("https://github.com/duckdb/duckdb",), manifest=(("reuse_contract", TABLE_DUCKDB_MANIFEST.model_dump_json()), ("license", "MIT"))))
        catalog = CapabilityCatalog(repository)
        actor = CatalogActor(owner_id="user-a", role="admin")
        catalog.freeze_selection(actor, task_id="original-task", revision=1, pack_refs=(ref,))
        assert route._inherit_capability_selection("user-a", source_task_id="original-task", source_revision=1, target_task_id=task_id, target_revision=1)
        if with_event:
            route.get_store().append_semantic_workspace_event("user-a", task_id, stage="queued", event_type="capability_reuse_planned", summary="合成冻结复用合同", details={"matches":[{"compatibility":{"input_formats":["csv"], "output_formats":["json"], "operations":["read"]}}]})

        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(settings.webui_db_path)) as connection:
            connection.execute("UPDATE semantic_workspace_tasks SET status='completed' WHERE task_id=?", (task_id,))
            connection.commit()
        async def forbidden(**kwargs): raise RuntimeError("不兼容来源不得准备binding")
        monkeypatch.setattr(route.get_semantic_workspace_manager(), "prepare_runtime_binding", forbidden)
        payload = {"instruction":"修改来源或输出", "expected_active_revision":1}
        payload.update({"source_snapshot_ids":[snapshot]} if change == "web" else {"output_formats":["parquet" if change == "compatible-format" else "pdf"]})
        result = client.post("/api/semantic-workspace/tasks/"+task_id+"/revisions", json=payload, headers={"Idempotency-Key":"incompatible-source"})
        if change == "compatible-format":
            assert result.status_code == 202, result.text
        else:
            assert result.status_code == 409, result.text
            assert "重新匹配" in result.text
            assert result.headers.get("X-Mangrove-Revision-Outcome") == "rejected"


@pytest.mark.parametrize("existing_claim", [False, True])
def test_create_preclaim_rejection_is_explicit(tmp_path, monkeypatch, existing_claim):
    from contextlib import asynccontextmanager
    from tests.test_web_source_delivery_api import _client
    client = _client(tmp_path, monkeypatch, role="admin")
    @asynccontextmanager
    async def no_worker(app): yield
    client.app.router.lifespan_context = no_worker
    with client:
        if existing_claim:
            from src.api.routes import semantic_workspace as route
            route._runtime_repository().claim_idempotency("user-a", "preclaim-rejection", request_hash="original", proposed_task_id="unknown-task")
        result = client.post("/api/semantic-workspace/tasks", json={"objective_text":"整理文件", "upload_ids":["missing"], "output_formats":["json"], "runtime_version":"pi"}, headers={"Idempotency-Key":"preclaim-rejection"})
        assert result.status_code == 404
        assert result.headers.get("X-Mangrove-Task-Outcome") == (None if existing_claim else "rejected")


@pytest.mark.asyncio
async def test_revision_after_prepare_rejection_keeps_unknown_claim(monkeypatch):
    from types import SimpleNamespace
    from src.api.routes import semantic_workspace as route
    released = []
    repository = SimpleNamespace(claim_idempotency=lambda *args, **kwargs:("task", True), release_idempotency=lambda *args, **kwargs:released.append(args))
    monkeypatch.setattr(route, "_task_or_404", lambda *args:{})
    monkeypatch.setattr(route, "get_store", lambda:SimpleNamespace(find_source_revision_receipt=lambda *args:None))
    monkeypatch.setattr(route, "_runtime_repository", lambda:repository)
    async def fail_after_prepare(*args, mark_execution_started, **kwargs):
        mark_execution_started()
        raise route.HTTPException(409, "准备后状态冲突")
    monkeypatch.setattr(route, "_create_revision", fail_after_prepare)
    with pytest.raises(route.HTTPException) as error:
        await route.create_revision("task", route.WorkspaceRevisionIn(instruction="修改", expected_active_revision=1), {"user_id":"owner"}, "same-key")
    assert released == []
    assert not error.value.headers


@pytest.mark.parametrize("outcome", ["cancelling", "revision_changed", "unknown"])
def test_revision_confirmed_cancelling_is_replayable_rejection(tmp_path, monkeypatch, outcome):
    from contextlib import asynccontextmanager, closing
    import sqlite3
    from src.api.routes import semantic_workspace as route
    from src.config.settings import settings
    from tests.test_web_source_delivery_api import _client
    from tests.test_pi_runtime_workspace_api import _uploads
    client = _client(tmp_path, monkeypatch, role="admin")
    @asynccontextmanager
    async def no_worker(app): yield
    client.app.router.lifespan_context = no_worker
    _, upload = _uploads(tmp_path)
    calls = []
    async def cancelling(owner, task_id, **kwargs):
        calls.append(task_id)
        if outcome == "unknown":
            raise route.HTTPException(409, "取消结果未知")
        with closing(sqlite3.connect(settings.webui_db_path)) as connection:
            connection.execute("UPDATE semantic_workspace_tasks SET status=?, active_revision=? WHERE task_id=?", ("cancelling" if outcome == "cancelling" else "completed", 1 if outcome == "cancelling" else 2, task_id))
            connection.commit()
    monkeypatch.setattr(route.get_semantic_workspace_manager(), "cancel", cancelling)
    async def forbidden(**kwargs): raise AssertionError("清理等待不得准备binding")
    monkeypatch.setattr(route.get_semantic_workspace_manager(), "prepare_runtime_binding", forbidden)
    with client:
        created = client.post("/api/semantic-workspace/tasks", json={"objective_text":"整理文件", "upload_ids":[upload], "output_formats":["json"], "runtime_version":"pi"})
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        path = "/api/semantic-workspace/tasks/" + task_id + "/revisions"
        for _ in range(2):
            result = client.post(path, json={"instruction":"调整目标", "expected_active_revision":1}, headers={"Idempotency-Key":"waiting-cleanup"})
            assert result.status_code == 409, result.text
            assert ("未知" if outcome == "unknown" else ("旧任务仍在停止" if outcome == "cancelling" else "活动版本已变化")) in result.text
            assert result.headers.get("X-Mangrove-Revision-Outcome") == (None if outcome == "unknown" else "rejected")
        assert len(calls) == (2 if outcome == "cancelling" else 1)
        assert route.get_store().get_semantic_workspace_task("user-a", task_id)["active_revision"] == (2 if outcome == "revision_changed" else 1)
