"""已配置任务的冻结计划与 occurrence；沿原工作台创建，不生成第二条执行链。"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import HTTPException
from src import account_execution as execution

def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()

def binding(store,schedule_id):
    with store._conn() as conn:
        row=conn.execute("SELECT * FROM scheduled_workspace_bindings WHERE schedule_id=?",(schedule_id,)).fetchone()
    return dict(row) if row else None

def public_binding(row):
    frozen=json.loads(row["contract_json"])
    return {"source_task_id":row["source_task_id"],"source_revision":row["source_revision"],"source_refs":frozen["source_refs"],"source_policy":"frozen","timezone":row["timezone"]}

def freeze_workspace(user,task_id,revision):
    from src.api.auth import get_store
    from src.api.routes import semantic_workspace as workspace
    from src.config.settings import settings
    from src.task_context import TaskContextRepository
    web=get_store();task=web.get_semantic_workspace_task(user["user_id"],task_id)
    if not task or task.get("deleted_at"):raise HTTPException(404,"任务不存在或无权访问")
    if task["active_revision"]!=revision:raise HTTPException(409,"请在当前任务版本创建计划，不以当前模型补全历史配置")
    rev=web.get_semantic_workspace_revision(user["user_id"],task_id,revision)
    runtime=workspace._runtime_repository().get(user["user_id"],task_id,revision)
    if not rev or not runtime:raise HTTPException(409,"任务尚无完整冻结执行配置")
    refs=rev["source_refs"]
    contract=web.get_source_contract(user["user_id"],task_id,revision) or {}
    goal=contract.get("goal_contract") or {}
    payload={"objective_text":goal.get("objective") or rev["objective_text"],"output_formats":rev["output_formats"],"table_output_contracts":rev["table_output_contracts"],
      "upload_ids":[ref["upload_id"] for ref in refs if ref.get("upload_id")],
      "source_snapshot_ids":list(dict.fromkeys(ref["snapshot_id"] for ref in refs if ref.get("kind")=="web_artifact")),
      "delivery_output_ids":[ref["output_id"] for ref in refs if ref.get("kind")=="delivery_output"],
      "provider":task["provider"],"model":(runtime.get("request") or {}).get("model") or task["model"],
      "runtime_version":runtime["runtime_version"].value,"permission_profile":runtime["permission_profile"].value,
      "model_connection_id":runtime["model_connection_id"],"model_connection_model":runtime["model_connection_model"],"external_api_confirmed":runtime["external_api_confirmed"]}
    for key in ("must_include","explicit_exclusions","quantity_requirement","completeness_requirement"):
        if key in goal:payload[key]=goal[key]
    context=TaskContextRepository(settings.webui_db_path).get_frozen(user["user_id"],task_id,revision)
    if context:
        payload.update(context_purpose=context.purpose,context_selection={"template":({"template_id":context.template.template_id,"version":context.template.version} if context.template else None),"memories":[{"memory_id":item.memory_id} for item in context.memories]},context_preview_sha256=context.preview_sha256)
    from src.capability_catalog.sqlite_repository import SqliteCapabilityCatalogRepository
    selection=SqliteCapabilityCatalogRepository(settings.webui_db_path).get_selection(user["user_id"],task_id,revision)
    if selection:
        if selection.procedure_refs:raise HTTPException(409,"该任务含尚未接线计划冻结的过程选择，请保留原任务手动执行")
        payload["capability_pack_refs"]=[item.model_dump(mode="json") for item in selection.pack_refs]
    frozen={"source_refs":refs,"source_contract":contract,"model_connection_version":runtime["model_connection_version"],"local_base_url":(runtime.get("request") or {}).get("base_url") if not runtime["model_connection_id"] else None}
    current,_=workspace._resolve_mixed_sources(user["user_id"],payload["upload_ids"],payload["source_snapshot_ids"],payload["delivery_output_ids"])
    if current!=refs:raise HTTPException(409,"来源身份已变化，请明确创建新任务配置")
    return payload,frozen

def create_plan(store,user,body,key):
    from src.api.routes.tasks import _trigger_to_schedule_str
    from src.scheduler.cron import parse_schedule,compute_next_run
    if not key or len(key)>200:raise HTTPException(422,"创建计划需要原幂等键")
    try:ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError:raise HTTPException(422,"时区无效") from None
    request_hash=digest(body.model_dump(mode="json"))
    with store._conn() as conn:
        old=conn.execute("SELECT * FROM scheduled_workspace_bindings WHERE owner_id=? AND request_key=?",(user["user_id"],key)).fetchone()
    if old:
        if old["request_hash"]!=request_hash:raise HTTPException(409,"原计划请求已使用该键，请保留原请求")
        return {**store.get(old["schedule_id"]),"workspace":public_binding(old)}
    payload,frozen=freeze_workspace(user,body.task_id,body.revision)
    if payload["external_api_confirmed"] and not body.repeat_external_confirmed:raise HTTPException(409,"请明确确认按计划重复使用原模型连接和已批准外发范围，调用可能产生费用")
    sched=parse_schedule(_trigger_to_schedule_str(body.trigger))
    if body.trigger.type=="once":
        sched=__import__("dataclasses").replace(sched,run_at=datetime.fromisoformat(body.trigger.run_at))
        if sched.run_at.tzinfo is None:
            # 先验证DST歧义，再把保存值固定为绝对时刻，后续恢复不重解释。
            next_workspace_time({"trigger_type":"once","run_at":sched.run_at},body.timezone)
            sched=__import__("dataclasses").replace(sched,run_at=sched.run_at.replace(tzinfo=ZoneInfo(body.timezone)))
    next_run=next_workspace_time({"trigger_type":sched.trigger_type,"cron_expr":sched.cron_expr,"run_at":sched.run_at,"interval_seconds":sched.interval_seconds},body.timezone)
    if next_run is None:raise HTTPException(422,"执行时间已过或无后续")
    def attach(conn,schedule_id):
        conn.execute("INSERT INTO scheduled_workspace_bindings VALUES (?,?,?,?,?,?,?,?,?)",(schedule_id,user["user_id"],body.task_id,body.revision,encoded(payload),encoded(frozen),key,request_hash,body.timezone))
    schedule_id=store.add(user_input=payload["objective_text"],provider=payload["provider"],model=payload["model"],trigger_type=sched.trigger_type,cron_expr=sched.cron_expr,run_at=sched.run_at,next_run_at=next_run,owner_user_id=user["user_id"],name=body.name,source="workspace",interval_seconds=sched.interval_seconds,transaction_hook=attach)
    return {**store.get(schedule_id),"workspace":public_binding(binding(store,schedule_id))}


def claim_occurrence(conn,task,manual,generation):
    frozen=conn.execute("SELECT * FROM scheduled_workspace_bindings WHERE schedule_id=?",(task["task_id"],)).fetchone()
    if frozen is None:raise execution.ExecutionDenied("计划冻结配置缺失")
    config_hash=digest({key:value for key,value in task.items() if not key.startswith("_") and not key.startswith("last_") and key not in {"run_count"}})
    if manual and not task.get("_manual_request_key"):raise execution.ExecutionDenied("立即执行需要原请求身份")
    key="schedule-occurrence:"+digest([task["task_id"],task.get("_manual_request_key") if manual else [config_hash,task["next_run_at"]]])
    previous=conn.execute("SELECT occurrence_id FROM scheduled_workspace_occurrences WHERE owner_id=? AND request_key=?",(task["owner_user_id"],key)).fetchone()
    if previous:raise execution.ExecutionDenied("该次执行已领取，请查看原运行")
    occurrence_id="occ_"+digest([key])[:32];now=datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO scheduled_workspace_occurrences (occurrence_id,schedule_id,owner_id,config_hash,due_at,manual,request_key,state,generation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'claimed',?,?,?)",(occurrence_id,task["task_id"],task["owner_user_id"],config_hash,task["next_run_at"] or now,int(manual),key,generation,now,now))
    return occurrence_id

def occurrences(store,owner,schedule_id):
    with store._conn() as conn:
        rows=conn.execute("SELECT * FROM scheduled_workspace_occurrences WHERE owner_id=? AND schedule_id=? ORDER BY created_at DESC",(owner,schedule_id)).fetchall()
    return [{**dict(row),"output_ids":json.loads(row["output_ids_json"])} for row in rows]

def prior_manual(store,owner,schedule_id,key):
    expected="schedule-occurrence:"+digest([schedule_id,key])
    return next((row for row in occurrences(store,owner,schedule_id) if row["request_key"]==expected),None)

def observe_occurrence(row):
    """只查原幂等领取和原任务，缺失不等于成功，也不创建替代任务。"""
    from src.api.auth import get_store
    from src.api.routes import semantic_workspace as workspace
    from src.source_acquisition.reuse import verified_output
    web=get_store();result=dict(row)
    with web._conn() as conn:
        claim=conn.execute("SELECT task_id FROM agentic_runtime_idempotency WHERE user_id=? AND idempotency_key=?",(row["owner_id"],row["request_key"])).fetchone()
    task_id=row["workspace_task_id"] or (claim["task_id"] if claim else None)
    if not task_id:return result
    result["workspace_task_id"]=task_id
    task=web.get_semantic_workspace_task(row["owner_id"],task_id)
    if task is None:return result
    revision=row.get('workspace_revision') or 1
    frozen=web.get_semantic_workspace_revision(row['owner_id'],task_id,revision)
    if frozen is None:return result
    # occurrence只观察自己冻结的修订；后续修订的状态和问题不能回写旧执行。
    result.update(workspace_task_id=task_id,workspace_revision=revision)
    run=workspace._runtime_repository().get(row["owner_id"],task_id,revision)
    result["runtime_run_id"]=run["run_id"] if run else None
    if frozen["status"]=="completed" and run:
        manifest=web.latest_semantic_delivery(row["owner_id"],run["run_id"])
        try:
            ids=[item["output_id"] for item in (manifest or {}).get("outputs",[])]
            if not ids:raise ValueError("缺少正式输出")
            for output_id in ids:verified_output(row["owner_id"],output_id)
        except (ValueError,PermissionError,OSError):
            result.update(state="incomplete",error_code="delivery_unavailable",output_ids=[])
        else:result.update(state="completed",error_code=None,output_ids=ids)
    elif frozen["status"] in {"failed","cancelled"}:result.update(state=frozen["status"],error_code="workspace_"+frozen["status"])
    elif frozen["status"] in {"needs_input","awaiting_clarification","candidate_ready"}:result.update(state="awaiting_action")
    else:result.update(state="running")
    return result

def settle_occurrence(store,auth,observed):
    """原代授权、同锁序与终态收据保证收口重试不重复计数。"""
    from src.api.auth import get_store
    web=get_store();schedule_id=observed["schedule_id"]
    terminal=observed["state"] in {"completed","failed","cancelled","incomplete"}
    with web.account_execution_transaction(auth,"schedule",schedule_id) as web_conn:
        with store._lock,store._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old=conn.execute("SELECT * FROM scheduled_workspace_occurrences WHERE occurrence_id=? AND owner_id=? AND generation=?",(observed["occurrence_id"],auth.owner_user_id,auth.generation)).fetchone()
            if old is None:raise execution.ExecutionDenied("原次执行授权不可恢复")
            old_terminal=old["state"] in {"completed","failed","cancelled","incomplete"}
            if not old_terminal:
                conn.execute("UPDATE scheduled_workspace_occurrences SET state=?,workspace_task_id=?,workspace_revision=?,runtime_run_id=?,output_ids_json=?,error_code=?,updated_at=? WHERE occurrence_id=?",(observed["state"],observed["workspace_task_id"],observed["workspace_revision"],observed["runtime_run_id"],encoded(observed.get("output_ids",[])),observed.get("error_code"),datetime.now(timezone.utc).isoformat(),observed["occurrence_id"]))
                if terminal:
                    task=dict(conn.execute("SELECT * FROM scheduled_tasks WHERE task_id=?",(schedule_id,)).fetchone())
                    frozen=dict(conn.execute("SELECT * FROM scheduled_workspace_bindings WHERE schedule_id=?",(schedule_id,)).fetchone())
                    next_run=next_workspace_time(task,frozen["timezone"]) if not old["manual"] else None
                    next_iso=next_run.isoformat(timespec="seconds") if next_run else None
                    success=observed["state"]=="completed"
                    conn.execute("UPDATE scheduled_tasks SET last_run_at=?,last_success=?,last_result=?,last_error=?,run_count=run_count+1,next_run_at=CASE WHEN ? OR status!='active' THEN next_run_at ELSE ? END,status=CASE WHEN ? OR status!='active' THEN status ELSE ? END WHERE task_id=?",(datetime.now().isoformat(timespec="seconds"),int(success),"已形成统一工作台正式结果" if success else "","" if success else "本次运行未形成可用正式交付",old["manual"],next_iso,old["manual"],"active" if next_iso else "done",schedule_id))
            # 两库提交窗口可凭原终态收据再收口，绝不重复任务或运行计数。
            unresolved=conn.execute("SELECT 1 FROM scheduled_workspace_occurrences WHERE schedule_id=? AND owner_id=? AND generation=? AND state NOT IN ('completed','failed','cancelled','incomplete') LIMIT 1",(schedule_id,auth.owner_user_id,auth.generation)).fetchone()
            # 历史终态只能收口自己的领取，不能解除同代后来尚未完成的occurrence。
            if (terminal or old_terminal) and unresolved is None:execution.set_execution_state(web_conn,auth,"schedule",schedule_id,state="idle",now=__import__("time").time())

def reconcile_pending(store):
    from src.api.auth import get_store
    with get_store()._conn() as web_conn:
        bindings={(row["owner_user_id"],row["resource_id"]):row["generation"] for row in web_conn.execute("SELECT * FROM account_execution_bindings WHERE resource_kind='schedule' AND state='active'")}
        with store._conn() as conn:
            rows=[dict(row) for row in conn.execute("SELECT * FROM scheduled_workspace_occurrences")]
    resumable=[]
    for row in rows:
        if bindings.get((row["owner_id"],row["schedule_id"]))!=row["generation"]:continue
        row["output_ids"]=json.loads(row["output_ids_json"])
        try:
            observed=observe_occurrence(row)
            settle_occurrence(store,execution.ExecutionAuthorization(row["owner_id"],row["generation"]),observed)
            if observed["state"] in {"claimed","creating"} and not observed.get("workspace_task_id"):resumable.append(observed)
        except execution.ExecutionDenied:continue
    return resumable


async def resume_occurrence(service,row,now):
    """仅续跑尚未建立工作台幂等领取的原 occurrence。"""
    task=service.store.get(row["schedule_id"])
    if task is None:return
    task["_workspace_occurrence"]=row["occurrence_id"]
    auth=execution.ExecutionAuthorization(row["owner_id"],row["generation"])
    with execution.execution_context(auth):
        await execute_occurrence(service,task,now,bool(row["manual"]))

def next_workspace_time(task,zone_name,now=None):
    """新计划使用显式时区；旧计划的宿主本地时间合同不变。"""
    from src.scheduler.cron import cron_matches
    zone=ZoneInfo(zone_name);instant=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if task["trigger_type"]=="interval":candidate=instant+timedelta(seconds=task["interval_seconds"])
    elif task["trigger_type"]=="once":
        candidate=datetime.fromisoformat(str(task["run_at"]))
        if candidate.tzinfo is None:
            local=candidate.replace(tzinfo=zone)
            # 夏令时不存在或歧义的墙上时刻必须带明确偏移，不能猜另一时刻。
            if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=candidate or local.utcoffset()!=local.replace(fold=1).utcoffset():raise ValueError("该本地时刻不存在或有歧义，请使用带偏移的时间")
            candidate=local
        candidate=candidate.astimezone(timezone.utc)
        if candidate<=instant:return None
    else:
        candidate=instant.replace(second=0,microsecond=0)+timedelta(minutes=1)
        for _ in range(60*24*366):
            if cron_matches(task["cron_expr"],candidate.astimezone(zone)):break
            candidate+=timedelta(minutes=1)
        else:return None
    # 存储仍使用现调度器的宿主本地格式，时区只用于算出正确的绝对时刻。
    return candidate.astimezone().replace(tzinfo=None)

def update_occurrence(store,auth,schedule_id,occurrence_id,**changes):
    allowed={"state","workspace_task_id","workspace_revision","runtime_run_id","output_ids_json","error_code"}
    if not set(changes)<=allowed:raise ValueError("非法执行状态字段")
    with execution.execution_context(auth),store._execution_write(schedule_id) as conn:
        conn.execute("UPDATE scheduled_workspace_occurrences SET "+",".join(key+"=?" for key in changes)+",updated_at=? WHERE occurrence_id=? AND owner_id=?",[*changes.values(),datetime.now(timezone.utc).isoformat(),occurrence_id,auth.owner_user_id])

async def execute_occurrence(service,task,now,manual):
    import asyncio
    from src.api.auth import get_store
    from src.api.routes import semantic_workspace as workspace
    from src.config.settings import settings
    auth=execution.current_authorization();web=get_store();row=binding(service.store,task["task_id"])
    frozen=json.loads(row["contract_json"]);payload=json.loads(row["payload_json"])
    occurrence=next(item for item in occurrences(service.store,auth.owner_user_id,task["task_id"]) if item["occurrence_id"]==task["_workspace_occurrence"])
    def update(**changes):update_occurrence(service.store,auth,task["task_id"],occurrence["occurrence_id"],**changes)
    claim_started=False
    def mark_claim_started():
        nonlocal claim_started
        claim_started=True
    def guard(current,source_refs,connection):
        if source_refs!=frozen["source_refs"]:raise HTTPException(409,"计划来源合同已变化")
        for key in ("runtime_version","permission_profile","model_connection_id","model_connection_model","model","provider"):
            value=getattr(current,key);value=value.value if hasattr(value,"value") else value
            if value!=payload[key]:raise HTTPException(409,"计划执行配置已变化，请保留原计划并重新明确配置")
        if connection and connection.connection_version!=frozen["model_connection_version"]:raise HTTPException(409,"计划模型版本已变化")
        if not connection and frozen["local_base_url"] and frozen["local_base_url"]!=settings.llm_base_url:raise HTTPException(409,"计划模型端点已变化")
    try:
        web.require_account_execution(auth,"schedule",task["task_id"])
        refs,_=workspace._resolve_mixed_sources(auth.owner_user_id,payload["upload_ids"],payload["source_snapshot_ids"],payload["delivery_output_ids"])
        if refs!=frozen["source_refs"]:raise HTTPException(409,"来源身份已变化")
        if payload["model_connection_id"]:
            connection=workspace.get_default_broker().freeze_connection(auth.owner_user_id,payload["model_connection_id"])
            if connection.connection_version!=frozen["model_connection_version"]:raise HTTPException(409,"模型连接版本已变化")
        elif frozen["local_base_url"] and frozen["local_base_url"]!=settings.llm_base_url:raise HTTPException(409,"本地模型端点已变化")
        update(state="creating")
        # 原API认领键先于prepare；未知不得用新的Task或新Run补跑。
        created=await workspace._create_task(workspace.WorkspaceTaskCreateIn(**payload),occurrence["request_key"],web.get_user(auth.owner_user_id),mark_claim_started=mark_claim_started,frozen_config_guard=guard)
        update(state="running",workspace_task_id=created["task_id"],workspace_revision=1)
        for _ in range(max(1,int(settings.scheduler_task_timeout_seconds*10))):
            web.require_account_execution(auth,"schedule",task["task_id"])
            latest=next(item for item in occurrences(service.store,auth.owner_user_id,task["task_id"]) if item["occurrence_id"]==occurrence["occurrence_id"])
            observed=observe_occurrence(latest)
            settle_occurrence(service.store,auth,observed)
            if observed["state"] in {"completed","failed","cancelled","incomplete","awaiting_action"}:return
            await asyncio.sleep(.1)
        update(state="unknown",error_code="waiting_timeout")
    except execution.ExecutionDenied:
        # 旧Owner代数不能继续回写或拿新代授权补建；由原账号治理收口。
        return
    except HTTPException as exc:
        # 只有任何领取/prepare前的明确拒绝才标配置失效；已开始的错误仍保留未知身份。
        update(state="blocked" if not claim_started and exc.status_code in {403,404,409,422} else "unknown",error_code="configuration_unavailable" if not claim_started else "execution_unknown")
    except (Exception,asyncio.CancelledError):
        update(state="unknown",error_code="execution_unknown")
        if asyncio.current_task().cancelling():raise

def update_plan(store,user,task,body):
    """冻结内容不随计划编辑漂移；领取后只能暂停未来触发，不能重排原次身份。"""
    from src.api.routes.tasks import _trigger_to_schedule_str
    from src.scheduler.cron import parse_schedule
    from src.api.auth import get_store
    from dataclasses import asdict,replace
    requested=body.model_dump(exclude_unset=True)
    for key,original in (("prompt","user_input"),("provider","provider"),("model","model"),("start_date","start_date"),("end_date","end_date")):
        if key in requested and requested[key]!=task.get(original):raise HTTPException(409,"该计划的资料、目标与模型已冻结；如需改变，请在工作台明确创建新版本后另建计划")
    if body.status is not None and body.status not in {"active","paused"}:raise HTTPException(422,"计划状态无效")
    frozen=binding(store,task["task_id"])
    if frozen is None:raise HTTPException(409,"冻结配置不可用")
    changes={"name":body.name if body.name is not None else task["name"]}
    if body.trigger is not None:
        try:
            sched=parse_schedule(_trigger_to_schedule_str(body.trigger))
            if body.trigger.type=="once":
                date=datetime.fromisoformat(body.trigger.run_at)
                if date.tzinfo is None:
                    next_workspace_time({"trigger_type":"once","run_at":date},frozen["timezone"])
                    date=date.replace(tzinfo=ZoneInfo(frozen["timezone"]))
                sched=replace(sched,run_at=date)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        changes.update(trigger_type=sched.trigger_type,cron_expr=sched.cron_expr,interval_seconds=sched.interval_seconds,run_at=sched.run_at.isoformat(timespec="seconds") if sched.run_at else None)
    else:changes.update({key:task[key] for key in ("trigger_type","cron_expr","interval_seconds","run_at")})
    auth=execution.current_authorization()
    with get_store().account_execution_transaction(auth) as web_conn:
        authority=execution.require_binding(web_conn,auth,"schedule",task["task_id"])
        with store._lock,store._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current=dict(conn.execute("SELECT * FROM scheduled_tasks WHERE task_id=?",(task["task_id"],)).fetchone())
            if current!=task:raise HTTPException(409,"计划已变化，请先读取当前计划")
            change_config=any(current[key]!=value for key,value in changes.items())
            if authority["state"]=="active" and (change_config or body.status=="active"):
                raise HTTPException(409,"原次执行尚未收口，请查看原任务；可以暂停后续计划")
            if change_config or (body.status=="active" and current["status"]!="active"):
                upcoming=next_workspace_time(changes,frozen["timezone"])
                if upcoming is None:raise HTTPException(422,"执行时间已过或无后续")
                changes["next_run_at"]=upcoming.isoformat(timespec="seconds")
            if body.status is not None:changes["status"]=body.status
            conn.execute("UPDATE scheduled_tasks SET "+",".join(key+"=?" for key in changes)+" WHERE task_id=?",[*changes.values(),task["task_id"]])
    return {"ok":True}
