"""统一计划的临时库/真实API纵切面，Runtime为明确工程替身。"""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest
from tests.database_migration_helpers import migrated_profile_database
from src.scheduler.store import ScheduleStore
import sqlite3
from src.config.settings import settings
from tests.test_web_source_delivery_api import _client,_seed_snapshot,CoverageAwareWebPiRuntime
from tests.test_pi_runtime_workspace_api import _uploads,_wait_for_delivery

BASE="/api/semantic-workspace"

def _audit_session(client,monkeypatch,owner='user-a'):
    import time
    from starlette.requests import Request
    from src.api.auth import get_store,create_token,ACCESS_COOKIE
    monkeypatch.setattr(settings,'jwt_secret','synthetic-lifecycle-audit-value-'*2)
    store=get_store();user=store.get_user(owner);now=time.time();session_id='lifecycle-audit-session-'+owner
    _,logged=store.platform_login_commit(username=user['username'],expected_password_hash=user['password_hash'],session_id=session_id,refresh_digest='synthetic-refresh-digest',now=now,access_expires_at=now+300,absolute_expires_at=now+3600,account_key='synthetic-account',source_key='synthetic-source')
    assert logged is not None
    token=create_token(owner,session_id=session_id,issued_at=now,expires_at=now+300)
    client.cookies.set(ACCESS_COOKIE,token)
    return Request({'type':'http','method':'POST','path':'/api/feedback/audit-content','headers':[(b'cookie',f'{ACCESS_COOKIE}={token}'.encode())]})

@pytest.mark.parametrize("source_kind",["file","web","mixed"])
def test_plan_freezes_exact_workspace_revision(tmp_path,monkeypatch,source_kind):
    runtime=CoverageAwareWebPiRuntime()
    client=_client(tmp_path,monkeypatch,role="admin",pi_runtime=runtime)
    from src.api.routes import tasks as task_routes
    client.app.include_router(task_routes.router)
    # 计划后台读取真实账号角色，测试身份必须与HTTP认证替身一致。
    with sqlite3.connect(settings.webui_db_path) as conn:
        conn.execute("UPDATE users SET role='admin' WHERE user_id='user-a'")
    scheduler_path=migrated_profile_database(tmp_path/"scheduler.db",profile="scheduler")
    # 官方显式迁移已创建0019/0002；Repository只检查，不隐式建表。
    schedules=ScheduleStore(str(scheduler_path))
    monkeypatch.setattr("src.api.routes.tasks.get_schedule_store",lambda:schedules)
    document,_=_uploads(tmp_path)
    snapshot,_=_seed_snapshot(Path(settings.webui_db_path))
    payload={"objective_text":"按当前证据汇总费用","output_formats":["json"],"runtime_version":"pi","provider":"local"}
    if source_kind!="web":payload["upload_ids"]=[document]
    if source_kind!="file":payload.update(source_snapshot_ids=[snapshot],quantity_requirement="当前证据",completeness_requirement="披露缺口")
    with client:
        from src.api.auth import get_store
        from src.task_context import TaskContextRepository,TaskTemplateDraft
        TaskContextRepository(settings.webui_db_path).save_template("user-a",TaskTemplateDraft(template_id="scheduled-summary",version=1,title="资料汇总",source="owner_created",purpose="general",goal_contract_draft="按证据汇总",delivery_spec_draft={"formats":["json"]},method_draft="核对证据"))
        memory=get_store().memory_add("user-a","保留原始单位",purpose="general")
        selected={"template":{"template_id":"scheduled-summary","version":1},"memories":[{"memory_id":memory["id"]}]}
        preview=client.post(BASE+"/context-preview",json={"purpose":"general","objective_text":payload["objective_text"],"output_formats":["json"],"selection":selected})
        assert preview.status_code==200,preview.text
        payload.update(context_purpose="general",context_selection=selected,context_preview_sha256=preview.json()["preview_sha256"])
        created=client.post(BASE+"/tasks",json=payload)
        assert created.status_code==202,created.text
        detail=_wait_for_delivery(client,created.json()["task_id"])
        plan=client.post("/api/tasks/from-workspace",headers={"Idempotency-Key":"plan-synthetic-1"},json={"task_id":detail["task_id"],"revision":1,"name":"证据汇总计划","trigger":{"type":"interval","interval_seconds":3600},"timezone":"UTC"})
        assert plan.status_code==201,plan.text
        binding=plan.json()["workspace"]
        assert binding["source_task_id"]==detail["task_id"] and binding["source_revision"]==1
        assert binding["source_refs"]==detail["source_refs"]
        assert binding["source_policy"]=="frozen"
        assert len(runtime.requests)==1
        # 计划编辑不能把冻结配置留在旧值而伪装接受新的提示或模型。
        rejected=client.patch(f"/api/tasks/{plan.json()['task_id']}",json={"prompt":"替换冻结目标"})
        assert rejected.status_code==409,rejected.text
        timing=client.patch(f"/api/tasks/{plan.json()['task_id']}",json={"name":"改期保持证据","trigger":{"type":"interval","interval_seconds":7200}})
        assert timing.status_code==200,timing.text
        assert schedules.get(plan.json()["task_id"])["interval_seconds"]==7200
        assert schedules.get(plan.json()["task_id"])["user_input"]==plan.json()["user_input"]
        from src.scheduler.service import SchedulerService
        async def reject_legacy(*args,**kwargs):
            raise AssertionError("冻结工作台计划不得调用旧Conductor")
        service=SchedulerService(schedules,runner=reject_legacy)
        monkeypatch.setattr("src.api.routes.tasks.get_scheduler_service",lambda:service)
        if source_kind=="file":
            import asyncio,threading
            from concurrent.futures import ThreadPoolExecutor
            entered,released=threading.Event(),threading.Event()
            original_body=service._run_one_body
            async def barrier(*args,**kwargs):
                entered.set();await asyncio.to_thread(released.wait);return await original_body(*args,**kwargs)
            monkeypatch.setattr(service,"_run_one_body",barrier)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(client.post,f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-synthetic-1"})
                try:
                    assert entered.wait(5)
                    same=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-synthetic-1"})
                    assert same.status_code==200 and same.json()["occurrence"]["state"]=="claimed"
                    assert len(runtime.requests)==1
                    edit_active=client.patch(f"/api/tasks/{plan.json()['task_id']}",json={"trigger":{"type":"interval","interval_seconds":1800}})
                    assert edit_active.status_code==409
                    assert client.patch(f"/api/tasks/{plan.json()['task_id']}",json={"status":"paused"}).status_code==200
                finally:released.set()
                run=pending.result(timeout=15)
            monkeypatch.setattr(service,"_run_one_body",original_body)
            assert schedules.get(plan.json()["task_id"])["status"]=="paused"
        else:
            run=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-synthetic-1"})
        assert run.status_code==200,run.text
        assert "occurrence" in run.json(),run.text
        occurrence=run.json()["occurrence"]
        consumer=_wait_for_delivery(client,occurrence["workspace_task_id"])
        assert consumer["task_id"]!=detail["task_id"]
        assert consumer["source_refs"]==detail["source_refs"]
        assert consumer["task_context"]["template"]["version"]==1
        assert consumer["task_context"]["memories"][0]["memory_id"]==memory["id"]
        assert occurrence["state"]=="completed",occurrence
        assert occurrence["runtime_run_id"]==consumer["delivery"]["run_id"]
        assert occurrence["output_ids"]==[item["output_id"] for item in consumer["delivery"]["outputs"]]
        feedback=client.post(BASE+f"/tasks/{consumer['task_id']}/feedback",headers={"Idempotency-Key":"feedback-synthetic"},json={"revision":1,"output_id":occurrence["output_ids"][0],"rating":"up","reasons":[],"comment":"单位清晰","expected_version":0})
        assert feedback.status_code==200,feedback.text
        assert feedback.json()["task_id"]==consumer["task_id"]
        assert feedback.json()["output_id"]==occurrence["output_ids"][0]
        from src.api.routes import feedback_routes
        client.app.include_router(feedback_routes.router)
        _audit_session(client,monkeypatch)
        listing=client.get("/api/feedback/list")
        assert listing.status_code==200,listing.text
        item=next(item for item in listing.json()["items"] if item["id"]==feedback.json()["id"])
        assert item["source_kind"]=="workspace" and item["revision"]==1
        assert item["message_id"] is None and item["conv_id"] is None
        assert "comment" not in item and "单位清晰" not in listing.text
        audited=client.post(f"/api/feedback/{item['id']}/audit-content",json={"reason":"核对本次结果反馈","idempotency_key":"audit-workspace-1"})
        assert audited.status_code==200,audited.text
        assert audited.json()["source"]["output_id"]==occurrence["output_ids"][0]
        assert audited.json()["source"]["task_id"]==consumer["task_id"]
        assert audited.json()["content"]["question"]==consumer["objective_text"]
        assert audited.json()["content"]["comment"]=="单位清晰"
        assert audited.json()["answer_kind"]=="revision_summary"
        with get_store()._conn() as connection:
            receipt=connection.execute("SELECT * FROM workspace_feedback_content_access WHERE event_id=?",(audited.json()["event_id"],)).fetchone()
            assert receipt["owner_id"]=="user-a" and receipt["feedback_id"]==item["id"]
            assert receipt["message_id"] is None and receipt["conv_id"] is None
        if source_kind=="file":
            assert client.patch(f"/api/feedback/{item['id']}",json={"admin_note":"已经存在的处理备注"}).status_code==200
            assert client.post(f"/api/feedback/{item['id']}/audit-content",json={"reason":"核对已有处理备注","idempotency_key":"audit-current-note"}).status_code==200
            original_feedback={"revision":1,"output_id":occurrence["output_ids"][0],"rating":"up","reasons":[],"comment":"单位清晰","expected_version":0}
            replay_feedback=client.post(BASE+f"/tasks/{consumer['task_id']}/feedback",json=original_feedback,headers={"Idempotency-Key":"feedback-synthetic"})
            # 原键读取已保存版本；另一任务的正式输出不能借同Owner身份关联。
            assert replay_feedback.status_code==200,replay_feedback.text
            wrong=client.post(BASE+f"/tasks/{consumer['task_id']}/feedback",json={**original_feedback,"output_id":detail["delivery"]["outputs"][0]["output_id"]},headers={"Idempotency-Key":"wrong-output"})
            assert wrong.status_code==409 and wrong.headers.get("X-Mangrove-Lifecycle-Outcome")=="rejected"
            updated=client.post(BASE+f"/tasks/{consumer['task_id']}/feedback",json={**original_feedback,"comment":"更新的说明","expected_version":1},headers={"Idempotency-Key":"feedback-update"})
            assert updated.status_code==200 and updated.json()["version"]==2,updated.text
            old=client.post(BASE+f"/tasks/{consumer['task_id']}/feedback",json=original_feedback,headers={"Idempotency-Key":"feedback-synthetic"})
            assert old.status_code==200 and old.json()['version']==1 and old.json()['receipt_only'] is True
            note=client.patch(f"/api/feedback/{item['id']}",json={"admin_note":"不能用旧审计覆盖新反馈"})
            assert note.status_code==403,note.text
            owner_read=client.get(BASE+f"/tasks/{consumer['task_id']}/feedback?revision=1&output_id={occurrence['output_ids'][0]}")
            assert owner_read.json()["feedback"]["version"]==2
        again=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-synthetic-1"})
        assert again.status_code==200,again.text
        assert again.json()["occurrence"]["occurrence_id"]==occurrence["occurrence_id"]
        assert len(runtime.requests)==2
        from src.api.routes import semantic_workspace
        create_task=semantic_workspace._create_task
        calls=[]
        async def lost_response(*args,**kwargs):
            calls.append(1)
            await create_task(*args,**kwargs)
            raise TimeoutError("合成已创建但响应丢失")
        monkeypatch.setattr(semantic_workspace,"_create_task",lost_response)
        uncertain=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-response-lost"})
        assert uncertain.status_code==200,uncertain.text
        assert uncertain.json()["occurrence"]["state"]=="unknown"
        query=client.get(f"/api/tasks/{plan.json()['task_id']}/occurrences")
        assert query.status_code==200,query.text
        recovered=next(item for item in query.json()["items"] if item["occurrence_id"]==uncertain.json()["occurrence"]["occurrence_id"])
        assert recovered["workspace_task_id"]
        _wait_for_delivery(client,recovered["workspace_task_id"])
        for _ in range(2):
            replay=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-response-lost"})
            assert replay.status_code==200
        assert len(calls)==1
        assert len(runtime.requests)==3
        # 新服务只核原领取，终态收据重放不能重复创建或累加执行次数。
        from src.scheduler.workspace import reconcile_pending
        reconcile_pending(schedules);reconcile_pending(schedules)
        assert schedules.get(plan.json()["task_id"])["run_count"]==2
        monkeypatch.setattr(semantic_workspace,"_create_task",create_task)
        get_store().memory_delete("user-a",memory["id"])
        blocked=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"occurrence-context-deleted"})
        assert blocked.status_code==200,blocked.text
        assert blocked.json()["occurrence"]["state"]=="blocked",blocked.text
        assert len(runtime.requests)==3
        # 旧已完成收据不能把后来的阻塞/未知领取解除为idle。
        reconcile_pending(schedules);reconcile_pending(schedules)
        assert get_store().account_execution_binding("user-a","schedule",plan.json()["task_id"])["state"]=="active"
        denied=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={"Idempotency-Key":"must-not-reclaim"})
        assert denied.status_code==409,denied.text
        assert denied.headers.get("X-Mangrove-Lifecycle-Outcome")=="rejected"
        assert client.get(f"/api/tasks/{plan.json()['task_id']}/occurrences?idempotency_key=must-not-reclaim").json()["items"]==[]
        if source_kind=="file":
            from src.api.auth import get_current_user
            old_override=client.app.dependency_overrides[get_current_user]
            client.app.dependency_overrides[get_current_user]=lambda:{"user_id":"other-owner","role":"admin"}
            try:
                assert client.get(f"/api/tasks/{plan.json()['task_id']}/occurrences").status_code==404
                assert client.get("/api/tasks/workspace-plans/by-key?idempotency_key=plan-synthetic-1").status_code==404
                assert client.get(BASE+f"/tasks/{consumer['task_id']}/feedback?revision=1&output_id={occurrence['output_ids'][0]}").status_code==404
            finally:client.app.dependency_overrides[get_current_user]=old_override
            before=client.get(f"/api/tasks/{plan.json()['task_id']}/occurrences").json()
            get_store().update_user("user-a",disabled=True);get_store().update_user("user-a",disabled=False)
            client.portal.call(service.tick,datetime.now()+timedelta(days=2))
            assert client.get(f"/api/tasks/{plan.json()['task_id']}/occurrences").json()==before
            assert len(runtime.requests)==3


@pytest.mark.parametrize("instant,expected",[
    ("2026-03-08T09:00:00+00:00","2026-03-09T09:30:00+00:00"),
    ("2026-11-01T07:00:00+00:00","2026-11-01T10:30:00+00:00"),
])
def test_explicit_timezone_cron_dst(instant,expected):
    from src.scheduler.workspace import next_workspace_time
    value=next_workspace_time({"trigger_type":"cron","cron_expr":"30 2 * * *"},"America/Los_Angeles",datetime.fromisoformat(instant))
    assert value.astimezone(timezone.utc)==datetime.fromisoformat(expected)

@pytest.mark.parametrize("value",["2026-03-08T02:30:00","2026-11-01T01:30:00"])
def test_ambiguous_or_missing_once_time_requires_offset(value):
    from src.scheduler.workspace import next_workspace_time
    with pytest.raises(ValueError,match="歧义"):
        next_workspace_time({"trigger_type":"once","run_at":value},"America/Los_Angeles",datetime(2026,1,1,tzinfo=timezone.utc))


def _scheduled_formal(client,tmp_path,monkeypatch):
    from src.api.routes import tasks as task_routes
    from src.scheduler.service import SchedulerService
    client.app.include_router(task_routes.router)
    with sqlite3.connect(settings.webui_db_path) as conn:
        conn.execute("UPDATE users SET role='admin' WHERE user_id='user-a'")
    schedules=ScheduleStore(str(migrated_profile_database(tmp_path/'scheduler.db',profile='scheduler')))
    service=SchedulerService(schedules,runner=lambda *args:pytest.fail('不能绕过统一工作台'))
    monkeypatch.setattr(task_routes,'get_schedule_store',lambda:schedules)
    monkeypatch.setattr(task_routes,'get_scheduler_service',lambda:service)
    document,_=_uploads(tmp_path)
    created=client.post(BASE+'/tasks',json={'objective_text':'按证据汇总费用','upload_ids':[document],'output_formats':['json'],'runtime_version':'pi','provider':'local'})
    assert created.status_code==202,created.text
    source=_wait_for_delivery(client,created.json()['task_id'])
    plan=client.post('/api/tasks/from-workspace',headers={'Idempotency-Key':'plan-boundary'},json={'task_id':source['task_id'],'revision':1,'name':'边界计划','trigger':{'type':'interval','interval_seconds':3600},'timezone':'UTC'})
    assert plan.status_code==201,plan.text
    run=client.post(f"/api/tasks/{plan.json()['task_id']}/run_now",headers={'Idempotency-Key':'occurrence-boundary'})
    assert run.status_code==200,run.text
    occurrence=run.json()['occurrence']
    assert occurrence['state']=='completed',occurrence
    return plan.json()['task_id'],occurrence,_wait_for_delivery(client,occurrence['workspace_task_id'])


def test_feedback_prior_request_remains_queryable_after_another_update(tmp_path,monkeypatch):
    client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=CoverageAwareWebPiRuntime())
    with client:
        _,occurrence,consumer=_scheduled_formal(client,tmp_path,monkeypatch)
        path=BASE+f"/tasks/{consumer['task_id']}/feedback"
        original={'revision':1,'output_id':occurrence['output_ids'][0],'rating':'up','reasons':[],'comment':'第一版反馈','expected_version':0}
        first=client.post(path,json=original,headers={'Idempotency-Key':'feedback-key-a'})
        assert first.status_code==200,first.text
        second=client.post(path,json={**original,'comment':'第二版反馈','expected_version':1},headers={'Idempotency-Key':'feedback-key-b'})
        assert second.status_code==200 and second.json()['version']==2,second.text
        query=client.get(path,params={'revision':1,'output_id':original['output_id'],'idempotency_key':'feedback-key-a'})
        assert query.status_code==200,query.text
        assert query.json().get('receipt',{}).get('version')==1,'当前反馈更新不能抹去原请求成功事实'
        assert query.json()['feedback']['version']==2 and query.json()['feedback']['comment']=='第二版反馈'
        replay=client.post(path,json=original,headers={'Idempotency-Key':'feedback-key-a'})
        assert replay.status_code==200 and replay.json()['version']==1,replay.text
        assert replay.json()['receipt_only'] is True and 'comment' not in replay.json()
        altered=client.post(path,json={**original,'comment':'不得借旧键新建第三版','expected_version':2},headers={'Idempotency-Key':'feedback-key-a'})
        assert altered.status_code==409,altered.text
        assert client.get(path,params={'revision':1,'output_id':original['output_id']}).json()['feedback']['version']==2

        rejected_payload={**original,'comment':'过期版本不得覆盖','expected_version':0}
        rejected=client.post(path,json=rejected_payload,headers={'Idempotency-Key':'feedback-key-rejected'})
        assert rejected.status_code==409 and rejected.headers.get('X-Mangrove-Lifecycle-Outcome')=='rejected'
        receipt=client.get(path,params={'revision':1,'output_id':original['output_id'],'idempotency_key':'feedback-key-rejected'})
        assert receipt.status_code==200,receipt.text
        assert receipt.json()['receipt']=={
            'task_id':consumer['task_id'],'revision':1,'output_id':original['output_id'],
            'version':0,'created_at':receipt.json()['receipt']['created_at'],
            'request_key':'feedback-key-rejected','result':'rejected','failure_code':'http_409',
            'id':0,'receipt_only':True,
        }
        replay_rejected=client.post(path,json=rejected_payload,headers={'Idempotency-Key':'feedback-key-rejected'})
        assert replay_rejected.status_code==409
        assert replay_rejected.headers.get('X-Mangrove-Lifecycle-Outcome')=='rejected'
        assert replay_rejected.json()['detail']=='原反馈请求已明确拒绝'


def test_service_restart_resumes_claimed_occurrence_before_workspace_task(tmp_path,monkeypatch):
    from src import account_execution as execution
    from src.api.auth import get_store
    from src.api.routes import tasks as task_routes
    from src.scheduler.service import SchedulerService
    from src.scheduler.workspace import occurrences

    runtime=CoverageAwareWebPiRuntime()
    client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
    with client:
        schedule,_,_=_scheduled_formal(client,tmp_path,monkeypatch)
        schedules=task_routes.get_schedule_store()
        task=schedules.get(schedule)
        task['_manual_request_key']='restart-before-workspace-task'
        binding=get_store().account_execution_binding('user-a','schedule',schedule)
        auth=execution.ExecutionAuthorization('user-a',binding['generation'])
        with execution.execution_context(auth):
            _,claimed=schedules.claim_execution(schedule,expected_task=task,manual=True)
        occurrence_id=claimed['_workspace_occurrence']
        assert next(row for row in occurrences(schedules,'user-a',schedule) if row['occurrence_id']==occurrence_id)['state']=='claimed'

        before=len(runtime.requests)
        restarted=SchedulerService(schedules,runner=lambda *args:pytest.fail('不能绕过统一工作台'))
        client.portal.call(restarted.tick,datetime.now())
        recovered=next(row for row in occurrences(schedules,'user-a',schedule) if row['occurrence_id']==occurrence_id)
        assert recovered['state']=='completed' and recovered['workspace_task_id']
        assert len(runtime.requests)==before+1
        client.portal.call(restarted.tick,datetime.now())
        assert len(runtime.requests)==before+1


def test_occurrence_retains_first_revision_when_later_revision_runs_and_fails(tmp_path,monkeypatch):
    import asyncio,threading
    from tests.test_pi_runtime_workspace_api import _wait_for_status
    runtime=CoverageAwareWebPiRuntime()
    client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=runtime)
    entered,released=threading.Event(),threading.Event()
    with client:
        schedule,occurrence,consumer=_scheduled_formal(client,tmp_path,monkeypatch)
        original=runtime._complete
        async def later_failure(request,**kwargs):
            if request.task_id==consumer['task_id'] and request.revision==2:
                entered.set()
                await asyncio.to_thread(released.wait,15)
                raise RuntimeError('合成第二修订失败')
            return await original(request,**kwargs)
        monkeypatch.setattr(runtime,'_complete',later_failure)
        revised=client.post(BASE+f"/tasks/{consumer['task_id']}/revisions",json={'instruction':'以相同资料重新核对','expected_active_revision':1})
        assert revised.status_code==202,revised.text
        try:
            assert entered.wait(10)
            from src.api.auth import get_store
            current=get_store().get_semantic_workspace_task('user-a',consumer['task_id'])
            assert current['active_revision']==2 and current['status']!='completed'
            during=client.get(f'/api/tasks/{schedule}/occurrences').json()['items'][0]
        finally:released.set()
        _wait_for_status(client,consumer['task_id'],'failed')
        projected=client.get(f'/api/tasks/{schedule}/occurrences').json()['items'][0]
        assert {key:during[key] for key in ('state','workspace_revision','runtime_run_id','output_ids')}=={key:occurrence[key] for key in ('state','workspace_revision','runtime_run_id','output_ids')}
        assert projected['state']=='completed' and projected['workspace_revision']==1
        assert projected['runtime_run_id']==occurrence['runtime_run_id'] and projected['output_ids']==occurrence['output_ids']
        assert len(runtime.requests)==3


@pytest.mark.parametrize('boundary',['send','purge','shared_purge','disabled','demoted','logout'])
def test_workspace_feedback_respects_source_response_and_task_purge(tmp_path,monkeypatch,boundary):
    import asyncio,json
    from tests.test_source_deletion_delivery import DeletionFixtureRuntime,_delete
    from src.api.auth import get_store
    from src.api.routes import feedback_routes
    from src.source_acquisition.reuse import SourceUseResponse,source_locks
    client=_client(tmp_path,monkeypatch,role='admin',pi_runtime=DeletionFixtureRuntime())
    client.app.include_router(feedback_routes.router)
    with client:
        _,occurrence,consumer=_scheduled_formal(client,tmp_path,monkeypatch)
        audit_request=_audit_session(client,monkeypatch)
        response=client.post(BASE+f"/tasks/{consumer['task_id']}/feedback",headers={'Idempotency-Key':'feedback-cleanup'},json={'revision':1,'output_id':occurrence['output_ids'][0],'rating':'up','reasons':[],'comment':'待清理的用户反馈正文','expected_version':0})
        assert response.status_code==200,response.text
        feedback_id=response.json()['id']
        assert client.patch(f'/api/feedback/{feedback_id}',json={'admin_note':'待清理的管理员处理正文'}).status_code==200
        if boundary in {'send','shared_purge','disabled','demoted','logout'}:
            actor_id='user-a'
            if boundary in {'disabled','demoted','logout'}:
                actor_id=get_store().create_user('audit-admin','synthetic-password-digest',role='admin')['user_id']
                audit_request=_audit_session(client,monkeypatch,actor_id)
            if boundary=='shared_purge':
                kept=client.post(BASE+'/tasks',json={'objective_text':'关联原资料和正式结果','upload_ids':consumer['upload_ids'],'delivery_output_ids':occurrence['output_ids'],'output_formats':['json'],'runtime_version':'pi','provider':'local'})
                assert kept.status_code==202,kept.text
                keeper=kept.json()['task_id']
                _wait_for_delivery(client,keeper)
            # 直接走真实路由返回的ASGI响应，屏障覆盖正文已经物化但尚未发送的窗口。
            response=feedback_routes.audit_feedback(feedback_id,feedback_routes.FeedbackAuditIn(reason='核验实际响应使用边界',idempotency_key='audit-send'),request=audit_request,admin={'user_id':actor_id})
            assert isinstance(response,SourceUseResponse),'工作台审计正文必须持有来源使用直到HTTP发送退出'
            refs=consumer['source_refs']+[{'kind':'delivery_output','output_id':occurrence['output_ids'][0]}]
            messages=[]
            if boundary in {'disabled','demoted','logout'}:
                from fastapi import HTTPException
                if boundary=='disabled':get_store().update_user(actor_id,disabled=True)
                elif boundary=='demoted':get_store().update_user(actor_id,role='user')
                else:get_store().platform_logout_all(actor_id,now=__import__('time').time())
                assert not get_store().get_user('user-a')['disabled']
                async def unexpected_send(message):messages.append(message)
                with pytest.raises(HTTPException):asyncio.run(response({'type':'http'},None,unexpected_send))
                assert messages==[],'权限或会话变化后不能发送旧正文'
                with get_store()._conn() as conn:
                    assert conn.execute('SELECT state FROM source_read_uses WHERE use_id=?',(response.use.use_id,)).fetchone()[0]=='completed'
                return
            async def send(message):
                with get_store()._conn() as conn:
                    use=conn.execute('SELECT * FROM source_read_uses WHERE use_id=?',(response.use.use_id,)).fetchone()
                    assert use['state']=='active' and use['task_id']==consumer['task_id'] and use['revision']==1
                with pytest.raises(ValueError,match='source_in_use'):
                    with source_locks('user-a',refs):pass
                if boundary=='shared_purge' and message['type']=='http.response.body':
                    assert client.delete(BASE+f"/tasks/{consumer['task_id']}").status_code==200
                    plan=client.get(BASE+f"/tasks/{consumer['task_id']}/deletion-plan").json()
                    assert plan['can_execute'] and all(row['disposition']=='keep_shared' for row in plan['objects']),plan
                    removed=client.post(BASE+f"/tasks/{consumer['task_id']}/deletion-operations",headers={'Idempotency-Key':'delete-during-audit'},json={'plan_token':plan['plan_token'],'shared_policy':'keep_shared'})
                    assert removed.status_code==409 and removed.json()['detail']=='source_in_use',removed.text
                    assert removed.headers.get('X-Mangrove-Deletion-Outcome')=='rejected'
                    with get_store()._conn() as conn:
                        assert conn.execute('SELECT comment FROM workspace_feedback WHERE id=?',(-feedback_id,)).fetchone()[0]=='待清理的用户反馈正文'
                messages.append(message)
            asyncio.run(response({'type':'http'},None,send))
            assert json.loads(messages[-1]['body'])['content']['comment']=='待清理的用户反馈正文'
            with get_store()._conn() as conn:
                assert conn.execute('SELECT state FROM source_read_uses WHERE use_id=?',(response.use.use_id,)).fetchone()[0]=='completed'
            if boundary=='shared_purge':
                plan=client.get(BASE+f"/tasks/{consumer['task_id']}/deletion-plan").json()
                removed=client.post(BASE+f"/tasks/{consumer['task_id']}/deletion-operations",headers={'Idempotency-Key':'delete-during-audit'},json={'plan_token':plan['plan_token'],'shared_policy':'keep_shared'})
                assert removed.status_code==202 and removed.json()['state']=='completed',removed.text
                with get_store()._conn() as conn:
                    assert conn.execute('SELECT COUNT(*) FROM workspace_feedback WHERE id=?',(-feedback_id,)).fetchone()[0]==0
                assert client.get(BASE+f'/tasks/{keeper}').status_code==200
        else:
            audit=client.post(f'/api/feedback/{feedback_id}/audit-content',json={'reason':'删除之前核对反馈正文','idempotency_key':'audit-before-delete'})
            assert audit.status_code==200,audit.text
            event_id=audit.json()['event_id']
            with get_store()._conn() as conn:
                original=dict(conn.execute('SELECT * FROM workspace_feedback_content_access WHERE event_id=?',(event_id,)).fetchone())
            _delete(client,consumer['task_id'],'keep_shared')
            with get_store()._conn() as conn:
                assert conn.execute('SELECT COUNT(*) FROM workspace_feedback WHERE user_id=? AND task_id=?',('user-a',consumer['task_id'])).fetchone()[0]==0,'永久清理不能遗留反馈和管理员备注正文'
                assert conn.execute('SELECT COUNT(*) FROM feedback_management WHERE id=?',(feedback_id,)).fetchone()[0]==0
                assert conn.execute('SELECT COUNT(*) FROM workspace_feedback_receipts WHERE user_id=? AND task_id=?',('user-a',consumer['task_id'])).fetchone()[0]==0
                assert dict(conn.execute('SELECT * FROM workspace_feedback_content_access WHERE event_id=?',(event_id,)).fetchone())==original
