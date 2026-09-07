"""反馈管理的固定元数据和单条正文审计边界。"""
import json
import hashlib
import uuid
from datetime import datetime, timezone

REASONS = ('理解错误', '上下文错误', '回答不清晰', '代码错误', '回答不专业', '格式错误', '其他')
CONTENT_LIMIT = 2 * 1024 * 1024


class AuditedFeedbackUnavailable(LookupError):
    def __init__(self, event_id):
        super().__init__('反馈正文不可用')
        self.event_id = event_id


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def require_admin(conn, actor_id):
    actor = conn.execute('SELECT role,disabled,pending FROM users WHERE user_id=?', (actor_id,)).fetchone()
    if actor is None or actor['role'] not in ('admin', 'super_admin') or actor['disabled'] or actor['pending']:
        raise PermissionError('需要管理员权限')
    return actor['role']


def feedback_content(conn, feedback_id):
    # 关联必须来自同一快照；损坏历史反馈不能借新的消息归属补成可读。
    row = conn.execute('''SELECT f.id,f.message_id,f.conv_id,f.user_id,
        substr(CAST(f.comment AS BLOB),1,2097153) AS comment,
        substr(CAST(f.admin_note AS BLOB),1,2097153) AS admin_note,
        substr(CAST(m.content AS BLOB),1,2097153) AS answer,
        (SELECT substr(CAST(content AS BLOB),1,2097153) FROM messages WHERE conv_id=f.conv_id AND role='user'
         AND id<f.message_id ORDER BY id DESC LIMIT 1) AS question
        FROM message_feedback f JOIN messages m ON m.id=f.message_id AND m.conv_id=f.conv_id
        JOIN conversations c ON c.conv_id=m.conv_id AND c.user_id=f.user_id
        JOIN users owner ON owner.user_id=c.user_id
        WHERE f.id=? AND m.role='assistant' ''', (feedback_id,)).fetchone()
    if row is None:
        raise LookupError('反馈正文不可用')
    # SQL 每字段至多读取上限加一字节，避免历史超大正文占满进程内存。
    content = {key: row[key][:CONTENT_LIMIT].decode('utf-8', errors='ignore') if row[key] is not None else None
               for key in ('question', 'answer', 'comment', 'admin_note')}
    truncated = any(row[key] is not None and len(row[key]) > CONTENT_LIMIT for key in content)
    # 限制实际 JSON 字节，中文和转义字符也算入；给响应信封预留空间。
    budget = CONTENT_LIMIT - 1024
    for key in content:
        if len(encoded(content)) <= budget:
            break
        value = content[key]
        if not value:
            continue
        original = value
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            content[key] = original[:mid]
            if len(encoded(content)) <= budget:
                lo = mid
            else:
                hi = mid - 1
        content[key] = original[:lo]
        truncated = truncated or lo < len(original)
    payload = {'content': content, 'truncated': truncated, 'content_bytes': len(encoded(content))}
    return row, payload


def audit_content(conn, feedback_id, actor_id, reason, idempotency_key):
    role = require_admin(conn, actor_id)
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000:
        raise ValueError('查看原因须为5至1000字')
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 128:
        raise ValueError('幂等键无效')
    reason = reason.strip()
    try:
        row, payload = feedback_content(conn, feedback_id)
        message_id, conv_id, owner_id = row['message_id'], row['conv_id'], row['user_id']
        result, failure_code = 'success', None
    except LookupError:
        # 读取失败也留低敏事实；未知或损坏关联不能被审计记录伪造成真实 Owner。
        message_id = conv_id = owner_id = None
        result, failure_code = 'failure', 'feedback_unavailable'
        payload = {'result': result, 'failure_code': failure_code}
    request_digest = digest([actor_id, role, feedback_id, message_id, conv_id, owner_id, reason, 'feedback_content_read'])
    response_digest = digest(payload)
    old = conn.execute('SELECT event_id,request_digest,response_digest FROM feedback_content_access WHERE actor_id=? AND idempotency_key=?', (actor_id, idempotency_key)).fetchone()
    if old:
        if old['request_digest'] != request_digest or old['response_digest'] != response_digest:
            raise ValueError('审计幂等键冲突')
        return {'event_id': old['event_id'], **payload}
    event_id = uuid.uuid4().hex
    conn.execute('''INSERT INTO feedback_content_access
        (event_id,actor_id,actor_role,idempotency_key,reason,action,feedback_id,message_id,
         conv_id,owner_id,request_digest,response_digest,content_bytes,truncated,result,failure_code,created_at)
        VALUES (?,?,?,?,?,'feedback_content_read',?,?,?,?,?,?,?,?,?,?,?)''',
        (event_id,actor_id,role,idempotency_key,reason,feedback_id,message_id,conv_id,owner_id,
         request_digest,response_digest,payload.get('content_bytes',0),int(payload.get('truncated',False)),
         result,failure_code,datetime.now(timezone.utc).isoformat()))
    return {'event_id': event_id, **payload}


def fixed_reasons(raw):
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        return ['其他']
    if not isinstance(values, list):
        return ['其他']
    return list(dict.fromkeys(value if isinstance(value, str) and value in REASONS else '其他' for value in values))
