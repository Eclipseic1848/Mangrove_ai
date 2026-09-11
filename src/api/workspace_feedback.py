"""工作台反馈精确引用原任务版本与正式输出；不伪造会话或消息。"""
import json
from datetime import datetime,timezone
from fastapi import HTTPException
from src.api.feedback_audit import REASONS,digest

def _public(row):
    if row is None:return None
    return {key:row[key] for key in ("task_id","revision","output_id","output_sha256","rating","comment","version","created_at","request_key")}|{"id":-row["id"],"source_kind":"workspace","reasons":json.loads(row["reasons"])}

def get_feedback(store,owner,task_id,revision,output_id):
    with store._conn() as conn:
        row=conn.execute("SELECT * FROM workspace_feedback WHERE user_id=? AND task_id=? AND revision=? AND output_id=? AND deleted_at IS NULL",(owner,task_id,revision,output_id)).fetchone()
    return _public(row)


def _receipt(row):
    if row is None:return None
    result={key:row[key] for key in ('task_id','revision','output_id','version','created_at','request_key','result')}|{'id':-row['feedback_id'],'receipt_only':True}
    if row['failure_code']:result['failure_code']=row['failure_code']
    return result


def get_receipt(store,owner,task_id,revision,output_id,key):
    with store._conn() as conn:
        row=conn.execute('SELECT * FROM workspace_feedback_receipts WHERE user_id=? AND request_key=? AND task_id=? AND revision=? AND output_id=?',(owner,key,task_id,revision,output_id)).fetchone()
    return _receipt(row)


def record_rejection(store,user,task_id,body,key,status_code):
    if not key or len(key)>128:return True
    owner=user["user_id"];payload_hash=digest([task_id,body.model_dump(mode="json")])
    with store._lock,store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task=conn.execute("SELECT 1 FROM semantic_workspace_tasks WHERE user_id=? AND task_id=? AND deleted_at IS NULL",(owner,task_id)).fetchone()
        if task is None:return True
        if conn.execute("SELECT 1 FROM workspace_feedback_receipts WHERE user_id=? AND request_key=?",(owner,key)).fetchone():return False
        conn.execute(
            "INSERT INTO workspace_feedback_receipts (user_id,request_key,request_hash,task_id,revision,output_id,feedback_id,version,created_at,result,failure_code) VALUES (?,?,?,?,?,?,0,0,?,'rejected',?)",
            (owner,key,payload_hash,task_id,body.revision,body.output_id,datetime.now(timezone.utc).isoformat(),f"http_{status_code}"),
        )
    return True

def submit_feedback(store,user,task_id,body,key):
    from src.source_acquisition.reuse import verified_output
    from src import account_execution as execution
    owner=user["user_id"]
    if not key or len(key)>128:raise HTTPException(422,"反馈需要原请求身份")
    if any(value not in REASONS for value in body.reasons):raise HTTPException(422,"反馈原因必须使用固定选项")
    payload_hash=digest([task_id,body.model_dump(mode="json")])
    def prior(conn):
        row=conn.execute('SELECT * FROM workspace_feedback_receipts WHERE user_id=? AND request_key=?',(owner,key)).fetchone()
        if row and row['request_hash']!=payload_hash:raise HTTPException(409,'原反馈请求与当前内容不一致')
        if row and row['result']=='rejected':
            status_code=int(row['failure_code'].removeprefix('http_'))
            raise HTTPException(status_code,'原反馈请求已明确拒绝',headers={'X-Mangrove-Lifecycle-Outcome':'rejected'})
        return _receipt(row)
    with store._conn() as conn:
        previous=prior(conn)
    if previous:return previous
    try:output,_=verified_output(owner,body.output_id)
    except (PermissionError,ValueError,OSError):raise HTTPException(404,"正式结果不存在或无权访问") from None
    with store._lock,store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        execution.require_binding(conn,execution.current_authorization(),"workspace",task_id)
        previous=prior(conn)
        if previous:return previous
        # 正式登记是关联权威；同Owner其他任务的output也不能绑到当前反馈。
        formal=conn.execute("SELECT 1 FROM formal_delivery_runs WHERE owner_id=? AND task_id=? AND task_revision=? AND delivery_id=? AND run_id=?",(owner,task_id,body.revision,output["delivery_id"],output["run_id"])).fetchone()
        revision=conn.execute("SELECT 1 FROM semantic_workspace_revisions r JOIN semantic_workspace_tasks t ON t.user_id=r.user_id AND t.task_id=r.task_id WHERE r.user_id=? AND r.task_id=? AND r.revision=? AND t.deleted_at IS NULL",(owner,task_id,body.revision)).fetchone()
        if formal is None or revision is None:raise HTTPException(409,"反馈结果不属于该任务版本")
        old=conn.execute("SELECT * FROM workspace_feedback WHERE user_id=? AND task_id=? AND revision=? AND output_id=?",(owner,task_id,body.revision,body.output_id)).fetchone()
        if (old["version"] if old else 0)!=body.expected_version:raise HTTPException(409,"反馈已变化，请先读取当前版本")
        now=datetime.now(timezone.utc).isoformat()
        values=(body.rating,json.dumps(body.reasons,ensure_ascii=False),body.comment,now,body.expected_version+1,key,payload_hash)
        if old:
            conn.execute("UPDATE workspace_feedback SET rating=?,reasons=?,comment=?,created_at=?,version=?,request_key=?,request_hash=?,deleted_at=NULL,status='pending' WHERE id=?",(*values,old["id"]))
        else:
            conn.execute("INSERT INTO workspace_feedback (user_id,task_id,revision,output_id,output_sha256,rating,reasons,comment,created_at,version,request_key,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(owner,task_id,body.revision,body.output_id,output["sha256"],*values))
        row=conn.execute("SELECT * FROM workspace_feedback WHERE user_id=? AND task_id=? AND revision=? AND output_id=?",(owner,task_id,body.revision,body.output_id)).fetchone()
        # 只保留原请求的提交事实，不复制历史反馈正文或覆盖旧键。
        conn.execute('INSERT INTO workspace_feedback_receipts (user_id,request_key,request_hash,task_id,revision,output_id,feedback_id,version,created_at,result,failure_code) VALUES (?,?,?,?,?,?,?,?,?,"saved",NULL)',(owner,key,payload_hash,task_id,body.revision,body.output_id,row['id'],row['version'],now))
        return _public(row)

def content_row(conn,feedback_id):
    """审计读取版本回答摘要，正式文件仍走既有完整性/下载门。"""
    row=conn.execute("""SELECT -f.id AS id,NULL AS message_id,NULL AS conv_id,f.user_id,
      substr(CAST(f.comment AS BLOB),1,2097153) AS comment,
      substr(CAST(f.admin_note AS BLOB),1,2097153) AS admin_note,
      substr(CAST(r.objective_text AS BLOB),1,2097153) AS question,
      substr(CAST(r.summary AS BLOB),1,2097153) AS answer,
      f.task_id,f.revision,f.output_id,f.output_sha256,'workspace' AS source_kind
      FROM workspace_feedback f JOIN semantic_workspace_tasks t ON t.user_id=f.user_id AND t.task_id=f.task_id
      JOIN semantic_workspace_revisions r ON r.user_id=f.user_id AND r.task_id=f.task_id AND r.revision=f.revision
      JOIN formal_delivery_outputs o ON o.output_id=f.output_id AND o.sha256=f.output_sha256
      JOIN formal_delivery_runs d ON d.delivery_id=o.delivery_id AND d.owner_id=f.user_id AND d.task_id=f.task_id AND d.task_revision=f.revision
      WHERE f.id=? AND f.deleted_at IS NULL AND t.deleted_at IS NULL""",(-feedback_id,)).fetchone()
    if row is not None:
        from src.source_acquisition.deletion import assert_sources_readable
        from src.source_acquisition.reuse import verified_output
        refs=conn.execute("SELECT source_refs_json FROM semantic_workspace_revisions WHERE user_id=? AND task_id=? AND revision=?",(row["user_id"],row["task_id"],row["revision"])).fetchone()
        try:
            assert_sources_readable(row["user_id"],json.loads(refs[0]),connection=conn)
            verified_output(row["user_id"],row["output_id"])
        except (ValueError,PermissionError,OSError):return None
    return row


def audit_response(store,feedback_id,read,request,actor_id):
    """管理员仍按原审计授权；正文所属Owner的来源使用覆盖实际HTTP发送。"""
    from src.source_acquisition.reuse import SourceReadUse,SourceUseResponse
    class AuditedResponse(SourceUseResponse):
        async def __call__(self,scope,receive,send):
            from src.api.auth import access_identity
            from src.api.feedback_audit import require_admin
            try:
                # 发送前重核原管理员的真实会话和当前角色，不能借正文Owner授权。
                actor,_,_=access_identity(request)
                if actor['user_id']!=actor_id:raise HTTPException(403,'审计身份已变化')
                with store._conn() as conn:
                    try:require_admin(conn,actor_id)
                    except PermissionError:raise HTTPException(403,'管理员权限已变化') from None
            except BaseException:
                self.use.finish(known=True)
                raise
            return await super().__call__(scope,receive,send)
    with store._conn() as conn:
        row=conn.execute('SELECT f.user_id,f.task_id,f.revision,f.output_id,r.source_refs_json FROM workspace_feedback f JOIN semantic_workspace_revisions r ON r.user_id=f.user_id AND r.task_id=f.task_id AND r.revision=f.revision WHERE f.id=? AND f.deleted_at IS NULL',(-feedback_id,)).fetchone()
    if row is None:
        # 原审计入口负责记录不可用事件，不伪造关联身份。
        return read()
    refs=json.loads(row['source_refs_json'])+[{'kind':'delivery_output','output_id':row['output_id']}]
    use=SourceReadUse(row['user_id'],refs,operation='preview',task_id=row['task_id'],revision=row['revision'])
    try:use.start()
    except (ValueError,PermissionError,OSError):raise HTTPException(409,'反馈来源暂不可读，请保留原审计请求') from None
    try:return AuditedResponse(read(),use,materialized_json=True)
    except BaseException:
        # 同步读取已退出且没有响应交给发送器，不留下虚假的在途读取。
        use.finish(known=True)
        raise
