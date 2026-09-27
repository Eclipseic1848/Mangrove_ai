"""单条反馈的原任务只读追溯，复用冻结来源和正式交付校验。"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
from typing import Literal
from urllib.parse import quote, unquote

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from src import operations
from .feedback_audit import require_admin


class FeedbackTaskIn(BaseModel):
    audit_event_id: str = Field(min_length=1, max_length=128)
    action: Literal['context', 'message', 'preview', 'download'] = 'context'
    file_id: str = Field(default='', max_length=256)
    message_id: str = Field(default='', max_length=256)
    offset: int = Field(default=0, ge=0, le=100_000_000)
    limit: Literal[10, 20, 50, 100] = 10
    sheet: int = Field(default=0, ge=0, le=10000)
    row: int = Field(default=0, ge=0, le=1_048_576)
    column: int = Field(default=0, ge=0, le=16384)


def binding(conn, feedback_id, actor_id, event_id):
    require_admin(conn, actor_id)
    row = conn.execute('SELECT * FROM message_feedback WHERE id=?', (feedback_id,)).fetchone()
    event = conn.execute('SELECT * FROM feedback_content_access WHERE event_id=? AND actor_id=? AND feedback_id=? AND result=\'success\'',
                         (event_id, actor_id, feedback_id)).fetchone()
    if not row or not event or any(row[a] != event[b] for a, b in (
            ('user_id', 'owner_id'), ('task_id', 'task_id'), ('revision', 'revision'),
            ('result_id', 'result_id'), ('conv_id', 'conv_id'), ('message_id', 'message_id'))):
        raise HTTPException(403, '请先说明原因并查看这条反馈')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(event['created_at'])).total_seconds()
    if not 0 <= age <= 1800:
        raise HTTPException(403, '本次查看已过期，请关闭后重新说明原因')
    if row['task_id']:
        from .workspace_feedback import target
        target(conn, row['user_id'], row['task_id'], row['revision'], row['result_id'])
    elif not conn.execute('''SELECT 1 FROM messages m JOIN conversations c ON c.conv_id=m.conv_id
            WHERE m.id=? AND m.conv_id=? AND c.user_id=? AND m.role='assistant' ''',
            (row['message_id'], row['conv_id'], row['user_id'])).fetchone():
        raise HTTPException(404, '原会话已不可用')
    return dict(row)


def messages_query(fb):
    if not fb['task_id']:
        return ('''SELECT CAST(id AS TEXT) AS id,role,content,created_at,id AS ordering
            FROM messages WHERE conv_id=? AND id<=? AND role IN ('user','assistant')''',
                [fb['conv_id'], fb['message_id']])
    # 只读被评价修订，绝不借当前活动修订拼接另一轮回答。
    return ('''SELECT 'objective' AS id,'user' AS role,objective_text AS content,created_at,0 AS ordering
        FROM semantic_workspace_revisions WHERE user_id=? AND task_id=? AND revision=?
        UNION ALL SELECT 'question:'||t.turn_id,'user',t.text,t.created_at,1
        FROM conversation_raw_turns t JOIN conversation_steering_results s
        ON s.turn_id=t.turn_id AND s.owner_id=t.owner_id AND s.task_id=t.task_id
        WHERE s.owner_id=? AND s.task_id=? AND json_extract(s.payload_json,'$.revision')=?
        AND julianday(s.created_at)<=julianday(?)
        UNION ALL SELECT s.result_id,'assistant',json_extract(s.payload_json,'$.answer'),s.created_at,2
        FROM conversation_steering_results s WHERE s.owner_id=? AND s.task_id=?
        AND json_extract(s.payload_json,'$.revision')=? AND julianday(s.created_at)<=julianday(?)
        AND COALESCE(json_extract(s.payload_json,'$.answer'),'')!='' ''',
        [fb['user_id'], fb['task_id'], fb['revision'],
         fb['user_id'], fb['task_id'], fb['revision'], fb['created_at'],
         fb['user_id'], fb['task_id'], fb['revision'], fb['created_at']])


def messages(conn, fb, body):
    from .routes.semantic_workspace import _public_answer_text
    query, values = messages_query(fb)
    if body.action == 'message':
        # 整条先经过已有公开内容门，再分页，避免切片把内部标记或密钥切开。
        record = conn.execute(f'SELECT substr(content,1,2097153) AS content FROM ({query}) WHERE id=?', [*values, body.message_id]).fetchone()
        if not record:
            raise HTTPException(404, '消息不属于这条反馈的原任务')
        if len(record['content'] or '') > 2097152:
            raise HTTPException(413, '消息超过在线查看上限，请联系管理员核查原记录')
        text = _public_answer_text(record['content'] or '') or ''
        return {'text': text[body.offset:body.offset + 16000], 'total': len(text), 'offset': body.offset}
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', values).fetchone()[0]
    rows = conn.execute(f'SELECT id,role,length(content) AS length,created_at FROM ({query}) ORDER BY created_at,ordering,id LIMIT ? OFFSET ?',
                        [*values, body.limit, body.offset]).fetchall()
    return {'messages': [{**dict(row),
                          'evaluated': row['id'] == str(fb['message_id'] or fb['result_id'])} for row in rows], 'total': total}


def file_members(store, fb):
    if not fb['task_id']:
        # 历史会话只允许被评价消息明确给出的同任务下载链接，不枚举用户目录。
        with store._conn() as conn:
            row = conn.execute('SELECT task_id,substr(content,1,2097152) AS content,substr(meta_json,1,2097152) AS meta_json FROM messages WHERE id=? AND conv_id=?',
                               (fb['message_id'], fb['conv_id'])).fetchone()
        if not row or not row['task_id']:
            return []
        from .routes.semantic_workspace import _public_answer_text
        links = re.findall(r'/api/downloads/([^/\s]+)/([^\s<>"\x27)]+)', _public_answer_text(row['content']) or '')
        try:
            meta = json.loads(row['meta_json'] or '{}')
        except ValueError:
            meta = {}
        for item in meta.get('files', []) if isinstance(meta, dict) else []:
            match = re.fullmatch(r'/api/downloads/([^/]+)/(.+)', item.get('url', '')) if isinstance(item, dict) else None
            if match:
                links.append(match.groups())
        return [{'id': 'legacy:' + hashlib.sha256(path.encode('utf-8')).hexdigest(), 'kind': 'output',
                 'name': Path(unquote(path)).name, 'legacy_task': row['task_id'], 'legacy_path': unquote(path)}
                for task, path in dict.fromkeys(links) if unquote(task) == row['task_id']]
    from .routes.semantic_workspace import _frozen_canvas_sources
    selected = store.get_semantic_workspace_revision(fb['user_id'], fb['task_id'], fb['revision'])
    manifest = store.latest_semantic_delivery(fb['user_id'], selected['run_id']) if selected.get('run_id') else None
    files = []
    try:
        sources = _frozen_canvas_sources(fb['user_id'], fb['task_id'], selected, manifest)
    except HTTPException:
        # 个别原件被清理时，仍列出持久冻结身份；每次实际读取继续严格校验。
        sources = {identity: {'sha256': digest} for identity, digest in (manifest or {}).get('source_artifact_hashes', {}).items()}
        for ref in selected.get('source_refs', []):
            identity = ref.get('upload_id') or ref.get('artifact_id') or ref.get('output_id')
            if identity:
                sources[identity] = {'sha256': ref.get('sha256'), 'delivery_ref': ref}
    for identity, source in sources.items():
        ref = source.get('delivery_ref') or ({'kind': 'web_artifact', 'artifact_id': identity,
            'snapshot_id': source['web']['snapshot_id'], 'sha256': source['sha256']} if source.get('web') else
            {'kind': 'upload', 'upload_id': identity, 'sha256': source['sha256']})
        name = '输入资料 ' + identity
        if ref.get('upload_id') and re.fullmatch(r'[\w-]+', identity):
            from .routes.semantic_workspace import _uploads
            uploads = _uploads()
            try:
                directory = uploads._user_dir(fb['user_id'], 'objects')
                meta = uploads._confined_path(directory, directory / (identity + '.meta'))
                name = Path(uploads._load_sidecar(meta, user_id=fb['user_id'], upload_id=identity).original_name).name or name
            except (OSError, ValueError, PermissionError):
                pass
        files.append({'id': 'input:' + identity, 'kind': 'input', 'name': name, 'ref': ref})
    for output in (manifest or {}).get('outputs', []):
        files.append({'id': 'output:' + output['output_id'], 'kind': 'output', 'name': output['filename'],
            'ref': {'kind': 'delivery_output', 'output_id': output['output_id'], 'sha256': output['sha256'],
                    'run_id': selected['run_id'], 'delivery_id': manifest['delivery_id']}})
    return files


def file_response(source, body):
    from .routes.semantic_workspace import _public_answer_text
    path = Path(source['host_path']) if source.get('host_path') else None
    name = Path(source['label']).name or '原始资料'
    # 上传对象在磁盘上使用无扩展名ID；格式必须取已校验的原文件名。
    suffix = Path(name).suffix.lower() or (path.suffix.lower() if path else '.html')
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    if body.action == 'download':
        # 固定附件下载；HTML 不得作为同源页面执行。
        if path:
            return FileResponse(path, filename=name, media_type='application/octet-stream', headers=headers)
        return Response(source['content_bytes'], media_type='application/octet-stream', headers={**headers,
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe='')})
    if suffix in {'.xlsx', '.xls'}:
        from src.services.workbook_preview import workbook_preview
        result = workbook_preview(path, suffix, body.sheet, body.row, body.column)
        for row in result['cells']:
            for cell in row:
                cell['text'] = _public_answer_text(cell['text'])
        return JSONResponse({'kind': 'workbook', **result}, headers=headers)
    if suffix in {'.pdf', '.docx', '.doc', '.ppt', '.pptx', '.odt', '.odp'}:
        if source['size_bytes'] > 20 * 1024 * 1024:
            raise HTTPException(413, '文件较大，请下载原件查看')
        if suffix == '.pdf':
            return Response(path.read_bytes(), media_type='application/pdf', headers=headers)
        from src.services.office_preview import office_preview
        return Response(office_preview(path, suffix), media_type='application/pdf', headers=headers)
    if suffix not in {'.txt', '.md', '.markdown', '.json', '.jsonl', '.csv', '.html', '.htm', '.xml', '.log'}:
        raise HTTPException(422, '此格式请下载原件查看')
    # ponytail: 文本预览上限8MB，超过上限提供完整下载，不在服务进程解析巨型文件。
    if source['size_bytes'] > 8 * 1024 * 1024:
        raise HTTPException(413, '文件较大，请下载原件查看')
    text = (path.read_bytes() if path else source['content_bytes']).decode('utf-8-sig', errors='replace')
    text = _public_answer_text(text) or ''
    return JSONResponse({'kind': 'text', 'text': text[body.offset:body.offset + 16000],
                         'offset': body.offset, 'total': len(text)}, headers=headers)


def read_task(store, feedback_id, body, admin, metadata):
    from src.source_acquisition.reuse import SourceReadUse, SourceUseResponse, resolve_frozen_source
    actor = {'user_id': admin['user_id']}
    action = {'context': '查看反馈原任务', 'message': '查看反馈原消息', 'preview': '预览反馈关联文件', 'download': '下载反馈关联文件'}[body.action]
    with store._conn() as conn:
        fb = binding(conn, feedback_id, actor['user_id'], body.audit_event_id)
        actor['role'] = require_admin(conn, actor['user_id'])
        event_id = operations.begin(conn, kind='access', module='反馈管理', action=action, actor=actor,
                                    object_ref=f'反馈:{feedback_id}', metadata=metadata)
    use = None
    try:
        if body.action in {'context', 'message'}:
            with store._conn() as conn:
                data = messages(conn, fb, body)
            if body.action == 'context':
                files = file_members(store, fb)
                data.update(revision=fb['revision'], task_id=fb['task_id'],
                    files=[{key: item[key] for key in ('id', 'kind', 'name')} for item in files])
            response = JSONResponse(data, headers={'Cache-Control': 'no-store'})
        else:
            item = next((item for item in file_members(store, fb) if item['id'] == body.file_id), None)
            if not item:
                raise HTTPException(404, '文件不属于被评价的任务版本')
            if 'legacy_path' in item:
                from .routes.downloads import download
                verified = download(item['legacy_task'], item['legacy_path'], user={'user_id': fb['user_id']})
                path = Path(verified.path)
                source = {'host_path': path, 'label': path.name, 'size_bytes': path.stat().st_size}
            else:
                use = SourceReadUse(fb['user_id'], [item['ref']], operation='feedback_audit',
                                    task_id=fb['task_id'], revision=fb['revision']).start()
                source = resolve_frozen_source(fb['user_id'], item['ref'])
            response = file_response(source, body)
        # 文件解析不持有全局写锁；发出任何正文前必须复核权限并持久保存访问审计。
        with store._conn() as conn:
            binding(conn, feedback_id, actor['user_id'], body.audit_event_id)
            operations.finish(conn, event_id, actor=actor, status_code=200, changes=[{
                'audit_event_id': body.audit_event_id, 'task_id': fb['task_id'], 'revision': fb['revision'],
                'file_id': body.file_id, 'message_id': body.message_id, 'offset': body.offset}])
        if use:
            return SourceUseResponse(response, use, materialized_json=isinstance(response, JSONResponse))
        return response
    except BaseException:
        if use:
            use.finish(known=True)
        with store._conn() as conn:
            operations.finish(conn, event_id, actor=actor, result='failure', changes=[{'audit_event_id': body.audit_event_id}])
        raise
