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


def delivery_preview(conn, row):
    from fastapi import HTTPException
    from pathlib import Path
    from .routes.semantic_workspace import get_store, _verified_canvas_output
    selected = conn.execute('SELECT run_id FROM semantic_workspace_revisions WHERE user_id=? AND task_id=? AND revision=?',
                            (row['user_id'], row['task_id'], row['revision'])).fetchone()
    if not selected or not selected['run_id']:
        return '历史记录未关联正式文件，仅保留结果摘要。'
    manifest = get_store().latest_semantic_delivery(row['user_id'], selected['run_id'])
    if not manifest or not manifest.get('outputs'):
        return '正式文件暂不可预览，未找到该版本的交付记录。'
    try:
        # 只在显式审计后读取；复用正式交付的 Owner、QA、清理状态与文件完整性门。
        # ponytail: 优先预览文本产物；办公文档不在审计写事务中解包，避免阻塞任务写入。
        output = next((item for item in manifest['outputs'] if Path(item['filename']).suffix.lower()
                       in {'.json', '.jsonl', '.csv', '.md', '.markdown', '.txt'}), None)
        if output is None:
            return '当前交付不含可预览的文本文件，仅显示结果摘要；办公文档暂不支持在此预览。'
        # 先限制登记大小；既有完整性门会先比对实际大小，再计算摘要。
        if output['size_bytes'] > 8 * 1024 * 1024:
            return '正式文件超过8MB，暂不可预览；已保留结果摘要。'
        _, path = _verified_canvas_output(row['user_id'], selected['run_id'], manifest, output)
        with path.open('rb') as handle:
            raw = handle.read(512 * 1024 + 1)
        note = '，内容已截断' if len(raw) > 512 * 1024 else ''
        return f'正式结果首个文本文件预览（最多512KB{note}）\n' + raw[:512 * 1024].decode('utf-8-sig', errors='replace')
    except (HTTPException, OSError, ValueError, KeyError):
        # 不把文件路径或内部校验异常暴露给管理员，也不影响读取用户说明。
        return '正式文件暂不可预览：文件不可用、校验未通过或格式不支持。'


def feedback_content(conn, feedback_id):
    # 关联必须来自同一快照；损坏历史反馈不能借新的消息归属补成可读。
    row = conn.execute('''SELECT f.id,f.message_id,f.conv_id,f.user_id,NULL AS task_id,NULL AS revision,NULL AS result_id,
        substr(CAST(f.comment AS BLOB),1,2097153) AS comment,
        substr(CAST(f.admin_note AS BLOB),1,2097153) AS admin_note,
        substr(CAST(m.content AS BLOB),1,2097153) AS answer,
        substr(CAST(c.title AS BLOB),1,2097153) AS task_title,
        (SELECT substr(CAST(content AS BLOB),1,2097153) FROM messages WHERE conv_id=f.conv_id AND role='user'
         AND id<f.message_id ORDER BY id LIMIT 1) AS original_task,
        (SELECT substr(CAST(content AS BLOB),1,2097153) FROM messages WHERE conv_id=f.conv_id AND role='user'
         AND id<f.message_id ORDER BY id DESC LIMIT 1) AS question
        FROM message_feedback f JOIN messages m ON m.id=f.message_id AND m.conv_id=f.conv_id
        JOIN conversations c ON c.conv_id=m.conv_id AND c.user_id=f.user_id
        JOIN users owner ON owner.user_id=c.user_id
        WHERE f.id=? AND m.role='assistant' ''', (feedback_id,)).fetchone()
    if row is None:
        from .workspace_feedback import target
        saved = conn.execute('''SELECT id,message_id,conv_id,user_id,task_id,revision,result_id,
            substr(CAST(comment AS BLOB),1,2097153) AS comment,
            substr(CAST(admin_note AS BLOB),1,2097153) AS admin_note
            FROM message_feedback WHERE id=? AND task_id IS NOT NULL''', (feedback_id,)).fetchone()
        if saved is None:
            raise LookupError('反馈正文不可用')
        source = target(conn, saved['user_id'], saved['task_id'], saved['revision'], saved['result_id'])
        row = {**dict(saved), **{key: source[key] for key in ('question', 'answer', 'original_task', 'task_title')}}
    # SQL 每字段至多读取上限加一字节，避免历史超大正文占满进程内存。
    content = {key: row[key][:CONTENT_LIMIT].decode('utf-8', errors='ignore') if row[key] is not None else None
               for key in ('question', 'answer', 'original_task', 'task_title', 'comment', 'admin_note')}
    truncated = any(row[key] is not None and len(row[key]) > CONTENT_LIMIT for key in content)
    if row['task_id'] is not None and row['result_id'] == 'delivery':
        preview = delivery_preview(conn, row).encode('utf-8')
        content['result_preview'] = preview[:CONTENT_LIMIT].decode('utf-8', errors='ignore')
        truncated = truncated or len(preview) > CONTENT_LIMIT
    if row['task_id'] is not None:
        from .routes.semantic_workspace import _public_answer_text
        # 管理员审计也不能读取工作台已隐藏的内部通道、密钥和宿主路径。
        for key in ('question', 'answer', 'original_task', 'task_title', 'result_preview'):
            if key not in content:
                continue
            content[key] = _public_answer_text(content[key])
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
    payload = {'content': content, 'truncated': truncated, 'content_bytes': len(encoded(content)),
               'context': {key: row[key] for key in ('task_id', 'revision', 'result_id', 'message_id', 'conv_id')}}
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
        task_id, revision, result_id = row['task_id'], row['revision'], row['result_id']
        result, failure_code = 'success', None
    except LookupError:
        # 读取失败也留低敏事实；未知或损坏关联不能被审计记录伪造成真实 Owner。
        message_id = conv_id = owner_id = None
        task_id = revision = result_id = None
        result, failure_code = 'failure', 'feedback_unavailable'
        payload = {'result': result, 'failure_code': failure_code}
    request_digest = digest([actor_id, role, feedback_id, message_id, conv_id, owner_id, reason, 'feedback_content_read'])
    if task_id is not None:
        request_digest = digest([request_digest, task_id, revision, result_id])
    response_digest = digest(payload)
    old = conn.execute('SELECT event_id,request_digest,response_digest FROM feedback_content_access WHERE actor_id=? AND idempotency_key=?', (actor_id, idempotency_key)).fetchone()
    if old:
        if old['request_digest'] != request_digest or old['response_digest'] != response_digest:
            raise ValueError('审计幂等键冲突')
        return {'event_id': old['event_id'], **payload}
    event_id = uuid.uuid4().hex
    conn.execute('''INSERT INTO feedback_content_access
        (event_id,actor_id,actor_role,idempotency_key,reason,action,feedback_id,message_id,
         conv_id,owner_id,request_digest,response_digest,content_bytes,truncated,result,failure_code,created_at,task_id,revision,result_id)
        VALUES (?,?,?,?,?,'feedback_content_read',?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (event_id,actor_id,role,idempotency_key,reason,feedback_id,message_id,conv_id,owner_id,
         request_digest,response_digest,payload.get('content_bytes',0),int(payload.get('truncated',False)),
         result,failure_code,datetime.now(timezone.utc).isoformat(),task_id,revision,result_id))
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
