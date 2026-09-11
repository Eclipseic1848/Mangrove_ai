import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Modal } from "@/components/ui/modal";
import { api, getSessionState, authenticatedFetch, readAuthenticatedJson } from "@/lib/api";
import type { WorkspaceTask } from "@/types/semanticWorkspace";

type Pending = { key: string; payload: Record<string, unknown> };
export type Draft = {repeatExternal?:boolean; name: string; frequency: string; time: string; hours: string; once: string; timezone: string; output: string; rating: string; comment: string; version: number };
const button="rounded-lg border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50";
const field="mt-1 w-full rounded-lg border bg-background px-3 py-2 text-sm";

// 计划与反馈都先持久化原请求，跨页旧响应只能更新自己仍持有的草稿。
export function useLifecycleDraft(ownerId:string,identity:string,initial:Draft) {
  const storageKey=`mangrove.lifecycle.${ownerId}.${identity}`;
  const saved=useRef(localStorage.getItem(storageKey));
  const [record,setRecord]=useState<{draft:Draft;pending:Pending|null}>(()=>{
    try { const value=JSON.parse(saved.current??"null");return value?.draft?value:{draft:initial,pending:null}; }
    catch { return {draft:initial,pending:null}; }
  });
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(""),[obsolete,setObsolete]=useState(false);
  const [result,setResult]=useState<any>(null);
  const latest=useRef(record);latest.current=record;
  const mounted=useRef(true),flight=useRef(false);
  const current=()=>mounted.current&&getSessionState().user?.user_id===ownerId;
  function commit(next:typeof record) {
    if(!current()||localStorage.getItem(storageKey)!==saved.current){setObsolete(true);setMessage("此草稿已在另一页面更新，请重新打开后查看。");return false;}
    const text=JSON.stringify(next);try{localStorage.setItem(storageKey,text);}catch{setMessage("无法保存本机恢复身份，本次未发送。请保留草稿并检查浏览器存储。");return false;}saved.current=text;latest.current=next;setRecord(next);return true;
  }
  useEffect(()=>{
    mounted.current=true;
    const changed=(event:StorageEvent)=>{if(event.key===storageKey&&event.newValue!==saved.current){setObsolete(true);setMessage("此草稿已在另一页面更新，请重新打开后查看。");}};
    window.addEventListener("storage",changed);
    return()=>{mounted.current=false;window.removeEventListener("storage",changed);};
  },[storageKey]);
  async function perform(pending:Pending,request:()=>Promise<{data:any;done:boolean}>) {
    if(flight.current||obsolete||!current())return;
    flight.current=true;setBusy(true);setMessage("");
    try {
      const value=await request();
      if(!current())return;
      if(!commit({...latest.current,pending:value.done?null:pending}))return;
      setResult(value.data);setMessage(value.done?"已确认保存。":"原操作仍在处理中或需要确认；不会新建替代请求。");
    } catch(error) {
      if(current()) {
        if(error instanceof Error&&"rejected" in error&&error.rejected===true) {
          if(commit({...latest.current,pending:null}))setMessage(`未保存，可修改后重新确认。${error.message}`);
        } else setMessage(`结果尚未确认，请查询原请求。${error instanceof Error?error.message:"读取失败"}`);
      }
    } finally {flight.current=false;if(current())setBusy(false);}
  }
  async function send(path:string,payload:Record<string,unknown>) {
    if(latest.current.pending||flight.current||obsolete)return;
    const pending={key:crypto.randomUUID(),payload};
    if(!commit({...latest.current,pending}))return;
    await perform(pending,async()=>{
      const response=await authenticatedFetch(path,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":pending.key},body:JSON.stringify(payload)});
      const data=await readAuthenticatedJson(response);
      if(!response.ok)throw Object.assign(new Error(typeof data.detail==="string"?data.detail:"请求未确认"),{rejected:response.headers.get("X-Mangrove-Lifecycle-Outcome")==="rejected"});
      if(path.endsWith("/run_now")&&!data.occurrence?.occurrence_id)throw new Error("尚未确认原次执行");
      return {data,done:true};
    });
  }
  return {draft:record.draft,pending:record.pending,busy,message,obsolete,result,
    change:(value:Partial<Draft>)=>{if(!latest.current.pending&&!flight.current&&!obsolete)commit({...latest.current,draft:{...latest.current.draft,...value}});},
    abandon:()=>{if(!flight.current&&!obsolete&&commit({...latest.current,pending:null})){setResult(null);setMessage("已停止本机等待；服务端反馈可能已保存，未撤销任何反馈。");}},
    editAgain:()=>{if(!latest.current.pending&&!flight.current&&!obsolete){setResult(null);setMessage("");}},
    send,check:(request:(pending:Pending)=>Promise<{data:any;done:boolean}>)=>record.pending?perform(record.pending,()=>request(record.pending!)):Promise.resolve(),
  };
}

function validPlanDraft(draft:Draft) {
  if(!draft.name.trim()||draft.name.length>160||!draft.timezone.trim()||draft.timezone.length>80)return false;
  try{new Intl.DateTimeFormat("zh-CN",{timeZone:draft.timezone});}catch{return false;}
  if(draft.frequency==="daily")return /^([01]\d|2[0-3]):[0-5]\d$/.test(draft.time);
  if(draft.frequency==="interval")return Number.isInteger(Number(draft.hours))&&Number(draft.hours)>=1&&Number(draft.hours)<=8760;
  return draft.frequency==="once"&&Boolean(draft.once);
}

function initialDraft(task:Pick<WorkspaceTask,"title"|"delivery">):Draft {
  return {name:task.title,frequency:"daily",time:"09:00",hours:"24",once:"",timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,output:task.delivery?.outputs[0]?.output_id??"",rating:"up",comment:"",version:0};
}

export function WorkspaceLifecycleActions({task,ownerId}:{task:WorkspaceTask;ownerId:string}) {
  const revision=task.viewing_revision??task.active_revision;
  return <div className="flex flex-wrap gap-2 py-2">
    <LifecycleDialog key={`plan:${task.task_id}:${revision}`} task={task} ownerId={ownerId} mode="plan" />
    {Boolean(task.delivery?.outputs.length)&&<LifecycleDialog key={`feedback:${task.task_id}:${revision}`} task={task} ownerId={ownerId} mode="feedback" />}
  </div>;
}

function LifecycleDialog({task,ownerId,mode}:{task:WorkspaceTask;ownerId:string;mode:"plan"|"feedback"}) {
  const revision=task.viewing_revision??task.active_revision;
  const state=useLifecycleDraft(ownerId,`${mode}.${task.task_id}.${revision}`,initialDraft(task));
  const [open,setOpen]=useState(false),[abandon,setAbandon]=useState(false);
  const locked=state.busy||Boolean(state.pending)||state.obsolete;
  const draft=state.draft;
  const [feedback,setFeedback]=useState<{output:string;version:number;comment:string}|null>(null),[feedbackError,setFeedbackError]=useState(""),[reload,setReload]=useState(0);
  useEffect(()=>{
    if(!open||mode!=="feedback"||state.pending)return;
    let active=true;setFeedback(null);setFeedbackError("");
    api.get(`/api/semantic-workspace/tasks/${encodeURIComponent(task.task_id)}/feedback?revision=${revision}&output_id=${encodeURIComponent(draft.output)}`).then(data=>{
      if(active&&getSessionState().user?.user_id===ownerId)setFeedback({output:draft.output,version:data.feedback?.version??0,comment:data.feedback?.comment??""});
    }).catch(()=>{if(active)setFeedbackError("当前反馈读取失败，请重试读取；草稿仍保留。");});
    return()=>{active=false;};
  },[open,mode,draft.output,Boolean(state.pending),reload]);
  const feedbackReady=feedback?.output===draft.output;
  function payload() {
    if(mode==="feedback")return {revision,output_id:draft.output,rating:draft.rating,reasons:[],comment:draft.comment,expected_version:feedback?.version??draft.version};
    const [hour,minute]=draft.time.split(":").map(Number);
    const trigger=draft.frequency==="daily"?{type:"cron",cron_expr:`${minute} ${hour} * * *`}:draft.frequency==="interval"?{type:"interval",interval_seconds:Number(draft.hours)*3600}:{type:"once",run_at:draft.once};
    return {task_id:task.task_id,revision,name:draft.name,trigger,timezone:draft.timezone,repeat_external_confirmed:Boolean(draft.repeatExternal)};
  }
  async function query() {
    await state.check(async pending=>{
      if(mode==="plan") {
        const data=await api.get(`/api/tasks/workspace-plans/by-key?idempotency_key=${encodeURIComponent(pending.key)}`);
        if(data.workspace?.source_task_id!==task.task_id||data.workspace?.source_revision!==revision)throw new Error("原计划身份不匹配");
        return {data,done:true};
      }
      const response=await api.get(`/api/semantic-workspace/tasks/${encodeURIComponent(task.task_id)}/feedback?revision=${revision}&output_id=${encodeURIComponent(String(pending.payload.output_id))}&idempotency_key=${encodeURIComponent(pending.key)}`);
      const original=response.receipt??response.feedback;
      if(original?.request_key!==pending.key)throw new Error("尚未确认原反馈版本");
      if(response.receipt&&(original.task_id!==task.task_id||original.revision!==revision||original.output_id!==pending.payload.output_id))throw new Error("原反馈收据身份不匹配");
      if(response.receipt&&original.result==="rejected")throw Object.assign(new Error("原反馈请求已明确拒绝，可修改后重试。"),{rejected:true});
      if(response.receipt&&original.result!=="saved")throw new Error("原反馈收据状态未知");
      return {data:original,done:true};
    });
  }
  return <>
    <button type="button" className={button} onClick={()=>setOpen(true)} disabled={mode==="plan"&&revision!==task.active_revision}>{mode==="plan"?"设为自动任务":"反馈正式结果"}</button>
    <Modal open={open} onClose={()=>setOpen(false)} title={mode==="plan"?"创建自动任务":"反馈当前版本的正式结果"}>
      <div onKeyDownCapture={event=>{if(event.key==="Escape"&&event.nativeEvent.isComposing)event.stopPropagation();}}>
      <p className="mb-3 text-sm text-foreground">{task.title} · V{revision}。关闭面板会保留本机草稿和待确认请求。</p>
      {mode==="plan"?<div className="space-y-3">
        <p className="text-sm">沿用当前版本的全部资料、模板与个人记忆、模型和输出要求。</p>
        <p className="text-sm">网页沿用已确认快照，不会自动重新采集。</p>
        {task.external_api_confirmed&&<label className="flex gap-2 text-sm"><input type="checkbox" checked={Boolean(draft.repeatExternal)} disabled={locked} onChange={event=>state.change({repeatExternal:event.target.checked})}/>按计划重复使用原模型连接和已批准的外发范围，可能产生费用</label>}
        <label className="block text-sm">计划名称<input maxLength={160} className={field} value={draft.name} disabled={locked} onChange={event=>state.change({name:event.target.value})}/></label>
        <label className="block text-sm">执行频率<select className={field} value={draft.frequency} disabled={locked} onChange={event=>state.change({frequency:event.target.value})}><option value="daily">每天</option><option value="interval">固定间隔</option><option value="once">仅执行一次</option></select></label>
        {draft.frequency==="daily"&&<label className="block text-sm">每日时间<input className={field} type="time" value={draft.time} disabled={locked} onChange={event=>state.change({time:event.target.value})}/></label>}
        {draft.frequency==="interval"&&<label className="block text-sm">间隔小时<input className={field} type="number" min="1" max="8760" value={draft.hours} disabled={locked} onChange={event=>state.change({hours:event.target.value})}/></label>}
        {draft.frequency==="once"&&<label className="block text-sm">执行日期和时间<input className={field} type="datetime-local" value={draft.once} disabled={locked} onChange={event=>state.change({once:event.target.value})}/></label>}
        <label className="block text-sm">所在时区<input maxLength={80} className={field} value={draft.timezone} disabled={locked} onChange={event=>state.change({timezone:event.target.value})}/></label>
      </div>:<div className="space-y-3">
        {feedback&&<p>当前已保存反馈 V{feedback.version}</p>}
        {state.result?.receipt_only&&<p>原请求已保存为反馈 V{state.result.version}；当前反馈可能已有更新。</p>}
        {feedback?.comment&&<p className="whitespace-pre-wrap break-words text-sm">已保存说明：{feedback.comment}</p>}
        {feedbackError&&<p role="alert">{feedbackError}<button className={button} onClick={()=>setReload(value=>value+1)}>重新读取反馈</button></p>}
        <label className="block text-sm">评价哪个正式文件<select className={field} value={draft.output} disabled={locked} onChange={event=>state.change({output:event.target.value,version:0})}>{task.delivery?.outputs.map(output=><option key={output.output_id} value={output.output_id}>{output.filename}</option>)}</select></label>
        <label className="block text-sm">评价<select className={field} value={draft.rating} disabled={locked} onChange={event=>state.change({rating:event.target.value})}><option value="up">有帮助</option><option value="down">需要改进</option></select></label>
        <label className="block text-sm">补充说明<textarea className={field} maxLength={10000} value={draft.comment} disabled={locked} onChange={event=>state.change({comment:event.target.value})}/></label>
        <p className="text-xs text-muted-foreground">反馈绑定此任务版本和正式文件。管理员默认只看管理信息；查看问题、版本回答摘要和说明需填写原因并留审计记录。</p>
      </div>}
      {mode==="feedback"&&state.pending&&<div className="mt-3 space-y-2">
        {abandon?<><p>原反馈可能已保存，此操作不会撤销服务端反馈。只放弃本机等待，之后先读取当前版本；不会重发原请求。</p><button type="button" className={button} autoFocus onClick={()=>setAbandon(false)}>保留原请求继续等待</button><button type="button" className={button} disabled={state.busy||state.obsolete} onClick={()=>{state.abandon();setAbandon(false);setReload(value=>value+1);}}>明确放弃本地等待</button></>:<button type="button" className={button} disabled={state.busy||state.obsolete} onClick={()=>setAbandon(true)}>放弃本地待确认</button>}
      </div>}
      {state.message&&<p role="status" className="mt-3 text-sm">{state.message}</p>}
      {state.result&&mode==="plan"&&<Link className="mt-3 block underline" to="/tasks">查看自动任务</Link>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className={button} onClick={()=>setOpen(false)}>返回任务</button>
        {mode==="feedback"&&state.result&&!state.pending&&<button type="button" className={button} onClick={()=>{state.editAgain();setReload(value=>value+1);}}>修改反馈</button>}
        {state.pending?<button type="button" className={button} disabled={state.busy||state.obsolete} onClick={()=>void query()}>查询原请求</button>:!state.result&&<button type="button" className={button} disabled={locked||(mode==="plan"&&!validPlanDraft(draft))||(mode==="plan"&&task.external_api_confirmed&&!draft.repeatExternal)||!draft.name.trim()||(mode==="feedback"&&(!draft.output||!feedbackReady))||(mode==="plan"&&draft.frequency==="once"&&!draft.once)} onClick={()=>void state.send(mode==="plan"?"/api/tasks/from-workspace":`/api/semantic-workspace/tasks/${encodeURIComponent(task.task_id)}/feedback`,payload())}>{mode==="plan"?"确认创建计划":"提交反馈"}</button>}
      </div>
      </div>
    </Modal>
  </>;
}


type Occurrence = {occurrence_id:string;state:string;workspace_task_id:string|null;workspace_revision:number|null;runtime_run_id:string|null;output_ids:string[];error_code?:string|null};
const occurrenceLabels:Record<string,string>={claimed:"已领取，等待原任务",running:"原任务执行中",awaiting_action:"原任务需要补充或确认",unknown:"执行结果尚未确认",blocked:"冻结资料或配置不可用",completed:"正式结果已生成",failed:"原任务执行失败",cancelled:"原任务已停止",incomplete:"正式结果暂不可用"};
export function WorkspaceScheduleActions({scheduleId,ownerId,canRun=true}:{scheduleId:string;ownerId:string;canRun?:boolean}) {
  const state=useLifecycleDraft(ownerId,`occurrence.${scheduleId}`,initialDraft({title:"立即执行",delivery:null}));
  const [items,setItems]=useState<Occurrence[]>([]),[readError,setReadError]=useState(""),[readBusy,setReadBusy]=useState(false);
  const alive=useRef(true),serial=useRef(0),readFlight=useRef(false);
  const current=()=>alive.current&&getSessionState().user?.user_id===ownerId;
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;serial.current++;};},[]);
  async function read() {
    if(readFlight.current||state.busy)return;readFlight.current=true;
    const request=++serial.current;setReadBusy(true);setReadError("");setItems([]);
    try {const data=await api.get(`/api/tasks/${encodeURIComponent(scheduleId)}/occurrences`);if(current()&&request===serial.current)setItems(data.items);}
    catch {if(current()&&request===serial.current)setReadError("执行记录读取失败；请查询原次，不会补建任务。");}
    finally{readFlight.current=false;if(current()&&request===serial.current)setReadBusy(false);}
  }
  async function check() {
    if(readFlight.current||state.busy)return;readFlight.current=true;
    try {await state.check(async pending=>{
      const data=await api.get(`/api/tasks/${encodeURIComponent(scheduleId)}/occurrences?idempotency_key=${encodeURIComponent(pending.key)}`);
      if(data.items?.length!==1)throw new Error("原次执行尚未登记，这不代表执行失败");
      if(current())setItems([]);
      return {data:{occurrence:data.items[0]},done:true};
    });}finally{readFlight.current=false;}
  }
  const occurrence=state.result?.occurrence as Occurrence|undefined;
  const rows=occurrence?[items.find(row=>row.occurrence_id===occurrence.occurrence_id)??occurrence,...items.filter(row=>row.occurrence_id!==occurrence.occurrence_id)]:items;
  return <div className="mt-3 space-y-2">
    <p className="text-xs text-muted-foreground">沿用冻结资料与模型。暂停只停止后续计划；已领取的执行请到原任务处理。</p>
    <div className="flex flex-wrap gap-2">
      {state.pending?<button className={button} disabled={state.busy||state.obsolete||readBusy} onClick={()=>void check()}>查询原次执行</button>:<button className={button} disabled={state.busy||state.obsolete||!canRun||readBusy} onClick={()=>void state.send(`/api/tasks/${encodeURIComponent(scheduleId)}/run_now`,{})}>立即执行一次</button>}
      <button className={button} disabled={readBusy||state.busy} onClick={()=>void read()}>查看逐次执行</button>
    </div>
    {state.message&&<p role="status" className="text-sm">{state.message}</p>}
    {readError&&<p role="alert" className="text-sm">{readError}</p>}
    {rows.map(row=><div key={row.occurrence_id} className="rounded border p-2 text-sm">
      <p>{occurrenceLabels[row.state]??"状态尚未确认"}</p>
      {row.workspace_task_id&&row.workspace_revision&&<Link className="underline" to={`/data-prep?task=${encodeURIComponent(row.workspace_task_id)}&revision=${row.workspace_revision}`}>{row.state==="completed"&&row.output_ids.length?"查看本次正式结果":"查看并处理原任务"}</Link>}
      {!row.workspace_task_id&&<p className="text-xs text-muted-foreground">尚未取得原任务身份，不能创建替代执行。</p>}
    </div>)}
  </div>;
}
