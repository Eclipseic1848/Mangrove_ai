"""合成原件和隔离数据库：预览、冻结与运行读取可并行，删除仍排他。"""
from pathlib import Path

import pytest

from tests.test_workspace_canvas import execution_owner


@pytest.fixture
def source(tmp_path, monkeypatch):
    from tests.test_semantic_workspace_api import _client
    from src.source_acquisition.reuse import uploads
    client, owner = _client(tmp_path, monkeypatch)
    upload = uploads().save_bytes('user-a', 'source.csv', b'name,value\nA,2\n', media_type='text/csv')
    created = client.post('/api/semantic-workspace/tasks', json={
        'objective_text': '整理资料', 'upload_ids': [upload.upload_id],
        'output_formats': ['json'], 'runtime_version': 'legacy', 'provider': 'local',
    })
    assert created.status_code == 202, created.text
    return client, owner, created.json()['task_id'], upload


@pytest.mark.parametrize('reader', ['pi', 'coremind', 'freeze'])
def test_preview_does_not_block_runtime_or_task_freeze(source, tmp_path, reader):
    from src.source_acquisition.reuse import SourceReadUse, workspace_source_read_context
    from src.agentic_runtime.models import SourceInput
    from src.agentic_runtime.pi_runtime import PiRuntime
    from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
    from tests.test_coremind_agent_kernel_adapter import _request
    client, _, task_id, upload = source
    use = SourceReadUse('user-a', [{'upload_id': upload.upload_id}], operation='preview').start()
    try:
        request = _request(tmp_path).model_copy(update={
            'task_id': task_id, 'user_id': 'user-a', 'revision': 1,
            'sources': (SourceInput(upload_id=upload.upload_id, original_name=upload.original_name,
                                   host_path=Path(upload.storage_path), sha256=upload.sha256),),
        })
        if reader == 'pi':
            runtime = object.__new__(PiRuntime)
            runtime._source_read_context = workspace_source_read_context
            staged = tmp_path / 'staged'
            staged.mkdir()
            names = runtime._read_frozen_sources(request, staged)
            assert (staged / names[0]).read_bytes() == Path(upload.storage_path).read_bytes()
            runtime._read_frozen_sources(request, staged, resume=True)
        elif reader == 'coremind':
            runtime = CoreMindAgentKernelAdapter(execution_root=tmp_path, source_read_context=workspace_source_read_context)
            assert runtime._read_source(request, {'source_id': upload.upload_id})
        else:
            response = client.post('/api/semantic-workspace/tasks', json={
                'objective_text': '再次整理', 'upload_ids': [upload.upload_id],
                'output_formats': ['json'], 'runtime_version': 'legacy', 'provider': 'local',
            })
            assert response.status_code == 202, response.text
        assert client.get(f'/api/data-sources/uploads/{upload.upload_id}/content').status_code == 200
        assert client.get(f'/api/semantic-workspace/tasks/{task_id}').status_code == 200
    finally:
        use.finish(known=True)


def test_runtime_read_allows_preview_but_blocks_delete_and_closes_use(source, tmp_path):
    import sqlite3
    from src.config.settings import settings
    from src.source_acquisition.reuse import workspace_source_read_context, source_locks
    from src.agentic_runtime.models import SourceInput
    from tests.test_coremind_agent_kernel_adapter import _request
    client, _, task_id, upload = source
    refs = [{'upload_id': upload.upload_id}]
    request = _request(tmp_path).model_copy(update={
        'task_id': task_id, 'user_id': 'user-a', 'revision': 1,
        'sources': (SourceInput(upload_id=upload.upload_id, original_name=upload.original_name,
                               host_path=Path(upload.storage_path), sha256=upload.sha256),),
    })
    with workspace_source_read_context(request, (upload.upload_id,)):
        response = client.get(f'/api/data-sources/uploads/{upload.upload_id}/content')
        assert response.status_code == 200, response.text
        assert response.content == Path(upload.storage_path).read_bytes()
        with pytest.raises(ValueError, match='source_in_use'):
            with source_locks('user-a', refs):
                pytest.fail('执行读取尚未收口，不得取得删除锁')
        assert client.delete(f'/api/data-sources/uploads/{upload.upload_id}').status_code == 409
    with source_locks('user-a', refs):
        pass
    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute("SELECT count(*) FROM source_read_uses WHERE state='active'").fetchone()[0] == 0


def test_read_registration_is_shared_across_processes_until_last_reader_finishes(source):
    import subprocess
    import sys
    from src.config.settings import settings
    from src.source_acquisition.reuse import SourceReadUse, source_locks
    _, _, _, upload = source
    refs = [{'upload_id': upload.upload_id}]
    use = SourceReadUse('user-a', refs, operation='preview').start()
    # 子进程使用同一个隔离库与合成上传目录，不连接真实服务。
    script = '''
import sys
from src.config.settings import settings
settings.webui_db_path, settings.data_prep_upload_root = sys.argv[1:3]
from src.source_acquisition.reuse import SourceReadUse
use = SourceReadUse('user-a', [{'upload_id': sys.argv[3]}], operation='preview').start()
print('ready', flush=True)
try:
    sys.stdin.readline()
finally:
    use.finish(known=True)
'''
    with subprocess.Popen([sys.executable, '-c', script, settings.webui_db_path,
                           settings.data_prep_upload_root, upload.upload_id],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8') as child:
        from concurrent.futures import ThreadPoolExecutor
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                ready = executor.submit(child.stdout.readline)
                try:
                    assert ready.result(timeout=20).strip() == 'ready'
                except BaseException:
                    child.kill()
                    raise
            use.finish(known=True)
            with pytest.raises(ValueError, match='source_in_use'):
                with source_locks('user-a', refs):
                    pytest.fail('另一个进程仍在读取')
        finally:
            use.finish(known=True)
            _, error = child.communicate('\n', timeout=20)
        assert child.returncode == 0, error
    with source_locks('user-a', refs):
        pass
