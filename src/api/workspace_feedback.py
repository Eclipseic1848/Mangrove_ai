"""工作台反馈精确引用原任务版本与正式输出；不伪造会话或消息。"""
import json
from datetime import datetime,timezone
from fastapi import HTTPException
from src.api.feedback_audit import REASONS,digest

SCHEMA="""
CREATE TABLE workspace_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,task_id TEXT NOT NULL,
 revision INTEGER NOT NULL,output_id TEXT NOT NULL,output_sha256 TEXT NOT NULL,
 rating TEXT NOT NULL CHECK(rating IN ('up','down')),reasons TEXT NOT NULL,
 comment TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
 admin_note TEXT,version INTEGER NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 deleted_at TEXT,UNIQUE(user_id,task_id,revision,output_id),UNIQUE(user_id,request_key)
);
CREATE TABLE workspace_feedback_receipts (
 user_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 task_id TEXT NOT NULL,revision INTEGER NOT NULL,output_id TEXT NOT NULL,
 feedback_id INTEGER NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,
 result TEXT NOT NULL CHECK(result='saved'),PRIMARY KEY(user_id,request_key)
);
CREATE VIEW feedback_management AS
 SELECT id,message_id,conv_id,user_id,rating,reasons,comment,created_at,status,admin_note,
 'message' AS source_kind,NULL AS task_id,NULL AS revision,NULL AS output_id,NULL AS output_sha256
 FROM message_feedback
 UNION ALL
 SELECT -id,NULL,NULL,user_id,rating,reasons,comment,created_at,status,admin_note,
 'workspace',task_id,revision,output_id,output_sha256
 FROM workspace_feedback WHERE deleted_at IS NULL;
CREATE TABLE workspace_feedback_content_access (
 event_id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,actor_role TEXT NOT NULL CHECK(actor_role IN ('admin','super_admin')),
 idempotency_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(reason) BETWEEN 5 AND 1000),
 action TEXT NOT NULL CHECK(action='feedback_content_read'),feedback_id INTEGER NOT NULL CHECK(feedback_id<0),
 message_id INTEGER CHECK(message_id IS NULL),conv_id TEXT CHECK(conv_id IS NULL),owner_id TEXT,
 request_digest TEXT NOT NULL,response_digest TEXT NOT NULL,
 content_bytes INTEGER NOT NULL CHECK(content_bytes BETWEEN 0 AND 2097152),
 truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),result TEXT NOT NULL CHECK(result IN ('success','failure')),
 failure_code TEXT,created_at TEXT NOT NULL,source_identity_json TEXT,
 CHECK((result='success' AND owner_id IS NOT NULL AND source_identity_json IS NOT NULL AND failure_code IS NULL)
 OR (result='failure' AND owner_id IS NULL AND source_identity_json IS NULL AND failure_code IS 'feedback_unavailable')),
 UNIQUE(actor_id,idempotency_key)
);
CREATE TRIGGER workspace_feedback_audit_no_update BEFORE UPDATE ON workspace_feedback_content_access BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER workspace_feedback_audit_no_delete BEFORE DELETE ON workspace_feedback_content_access BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER workspace_feedback_audit_no_replace BEFORE INSERT ON workspace_feedback_content_access WHEN EXISTS(SELECT 1 FROM workspace_feedback_content_access WHERE event_id=NEW.event_id OR (actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key)) BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
"""

def _public(row):
    if row is None:return None
    return {key:row[key] for key in ("task_id","revision","output_id","output_sha256","rating","comment","version","created_at","request_key")}|{"id":-row["id"],"source_kind":"workspace","reasons":json.loads(row["reasons"])}

def get_feedback(store,owner,task_id,revision,output_id):
    with store._conn() as conn:
        row=conn.execute("SELECT * FROM workspace_feedback WHERE user_id=? AND task_id=? AND revision=? AND output_id=? AND deleted_at IS NULL",(owner,task_id,revision,output_id)).fetchone()
    return _public(row)


def _receipt(row):
    if row is None:return None
    return {key:row[key] for key in ('task_id','revision','output_id','version','created_at','request_key','result')}|{'id':-row['feedback_id'],'receipt_only':True}


def get_receipt(store,owner,task_id,revision,output_id,key):
    with store._conn() as conn:
        row=conn.execute('SELECT * FROM workspace_feedback_receipts WHERE user_id=? AND request_key=? AND task_id=? AND revision=? AND output_id=?',(owner,key,task_id,revision,output_id)).fetchone()
    return _receipt(row)

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
        conn.execute('INSERT INTO workspace_feedback_receipts VALUES (?,?,?,?,?,?,?,?,?,?)',(owner,key,payload_hash,task_id,body.revision,body.output_id,row['id'],row['version'],now,'saved'))
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
