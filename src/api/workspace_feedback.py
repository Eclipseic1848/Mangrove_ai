"""工作台反馈绑定既有任务版本，不创建影子会话或复制业务正文。"""
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .feedback_audit import REASONS


class WorkspaceFeedbackIn(BaseModel):
    revision: int = Field(ge=1)
    result_id: str = Field(default="delivery", min_length=1, max_length=160)
    rating: Literal["up", "down"] | None
    reasons: list[str] = Field(default_factory=list, max_length=7)
    comment: str | None = Field(default=None, max_length=5000)


def target(conn, owner_id, task_id, revision, result_id):
    row = conn.execute("""SELECT substr(CAST(r.objective_text AS BLOB),1,2097153) AS question,
        substr(CAST(r.summary AS BLOB),1,2097153) AS answer,r.status,
        substr(CAST(r.objective_text AS BLOB),1,2097153) AS original_task,
        substr(CAST(t.title AS BLOB),1,2097153) AS task_title
        FROM semantic_workspace_tasks t JOIN semantic_workspace_revisions r ON r.task_id=t.task_id AND r.user_id=t.user_id
        JOIN users u ON u.user_id=t.user_id
        WHERE t.task_id=? AND t.user_id=? AND t.deleted_at IS NULL AND r.revision=?""",
        (task_id, owner_id, revision)).fetchone()
    if row is None:
        raise LookupError("反馈对象不可用")
    if result_id == "delivery":
        if row["status"] != "completed":
            raise LookupError("尚无正式结果")
        return dict(row)
    answer = conn.execute("""SELECT substr(CAST(json_extract(s.payload_json,'$.answer') AS BLOB),1,2097153) AS answer,
        substr(CAST(t.text AS BLOB),1,2097153) AS question
        FROM conversation_steering_results s JOIN conversation_raw_turns t ON t.turn_id=s.turn_id
        AND t.owner_id=s.owner_id AND t.task_id=s.task_id
        WHERE s.owner_id=? AND s.task_id=? AND json_extract(s.payload_json,'$.revision')=? AND s.result_id=?""",
        (owner_id, task_id, revision, result_id)).fetchone()
    if answer is None or not answer["answer"]:
        raise LookupError("回答不存在")
    return {**dict(row), **dict(answer)}


def read_feedback(store, owner_id, task_id, revision, result_id):
    with store._conn() as conn:
        target(conn, owner_id, task_id, revision, result_id)
        row = conn.execute("""SELECT rating,reasons,comment FROM message_feedback
            WHERE user_id=? AND task_id=? AND revision=? AND result_id=?""",
            (owner_id, task_id, revision, result_id)).fetchone()
    return {"feedback": {"current": {**dict(row), "reasons": json.loads(row["reasons"] or "[]")} if row else None}}


def write_feedback(store, owner_id, task_id, body):
    if any(reason not in REASONS for reason in body.reasons):
        raise HTTPException(422, "反馈原因无效")
    with store._lock, store._conn() as conn:
        # 校验与写入共用事务，不能借别人的任务、其他版本或无回答的结果挂反馈。
        conn.execute("BEGIN IMMEDIATE")
        target(conn, owner_id, task_id, body.revision, body.result_id)
        key = (task_id, body.revision, body.result_id, owner_id)
        if body.rating is None:
            conn.execute("DELETE FROM message_feedback WHERE task_id=? AND revision=? AND result_id=? AND user_id=?", key)
        else:
            conn.execute("""INSERT INTO message_feedback(task_id,revision,result_id,user_id,rating,reasons,comment,created_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(task_id,revision,result_id,user_id) DO UPDATE SET
                rating=excluded.rating,reasons=excluded.reasons,comment=excluded.comment,created_at=excluded.created_at,status='pending'
                WHERE message_feedback.rating IS NOT excluded.rating
                OR COALESCE(message_feedback.reasons,'[]') != COALESCE(excluded.reasons,'[]')
                OR COALESCE(message_feedback.comment,'') != COALESCE(excluded.comment,'')""",
                (*key, body.rating, json.dumps(body.reasons, ensure_ascii=False), body.comment, datetime.now(timezone.utc).isoformat()))
    return {"ok": True}
