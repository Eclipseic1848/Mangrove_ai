"""关联清理贯通：真实临时发布与文件，生成及网页传输为受控替身。"""
import hashlib
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from src.api.routes import source_acquisition as source_routes
from src.api.routes import semantic_workspace as workspace_routes
from src.api.auth import get_store
from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.services.upload_store import UploadStore
from src.source_acquisition import AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionService
from tests.test_pi_runtime_workspace_api import _wait_for_delivery
from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime, _client


class DeletionFixtureRuntime(CoverageAwareWebPiRuntime):
    """生成仍为替身，目录从首次写入就遵守真实Run身份，避免放宽清理边界。"""

    def _workspace_root(self, request, run_id):
        owner = hashlib.sha256(request.user_id.encode('utf-8')).hexdigest()[:16]
        return Path(settings.semantic_execution_root) / 'agentic-vnext' / owner / request.task_id / f'r{request.revision}' / run_id


def _task(client, key, uploads, snapshots=(), outputs=()):
    response = client.post('/api/semantic-workspace/tasks', headers={'Idempotency-Key': key}, json={
        'objective_text': '汇总这些资料，输出JSON并保留依据',
        'upload_ids': list(uploads), 'source_snapshot_ids': list(snapshots),
        'delivery_output_ids': list(outputs), 'output_formats': ['json'],
        'quantity_requirement': '尽可能多', 'completeness_requirement': '允许披露缺口后交付',
        'runtime_version': 'pi', 'provider': 'local',
    })
    assert response.status_code == 202, response.text
    task_id = response.json()['task_id']
    delivery = _wait_for_delivery(client, task_id)['delivery']
    return task_id, {**delivery['outputs'][0], 'delivery_id': delivery['delivery_id']}


def _delete(client, task_id, policy):
    recycled = client.delete(f'/api/semantic-workspace/tasks/{task_id}')
    assert recycled.status_code == 200, recycled.text
    plan = client.get(f'/api/semantic-workspace/tasks/{task_id}/deletion-plan', params={'shared_policy': policy})
    assert plan.status_code == 200, plan.text
    assert plan.json()['can_execute'], plan.json()
    request = {'plan_token': plan.json()['plan_token'], 'shared_policy': policy}
    headers = {'Idempotency-Key': 'delete-' + task_id}
    created = client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations', json=request, headers=headers)
    assert created.status_code == 202, created.text
    operation_id = created.json()['operation_id']
    deadline = time.monotonic() + 20
    while True:
        state = client.get(f'/api/semantic-workspace/deletion-operations/{operation_id}')
        assert state.status_code == 200, state.text
        if state.json()['state'] not in {'planned', 'stopping', 'cleaning'}:
            break
        assert time.monotonic() < deadline, state.json()
        time.sleep(0.05)
    assert state.json()['state'] == 'completed', state.json()
    # 原任务已清理仍可按原请求拿到原操作，不以404猜成功。
    replay = client.post(f'/api/semantic-workspace/tasks/{task_id}/deletion-operations', json=request, headers=headers)
    assert replay.status_code == 202, replay.text
    assert replay.json()['operation_id'] == operation_id
    assert client.get(f'/api/semantic-workspace/tasks/{task_id}').status_code == 404
    return plan.json(), state.json()


@pytest.mark.parametrize('delete_producer', [False, True])
def test_related_cleanup_preserves_independent_results_and_clears_real_sources(tmp_path, monkeypatch, delete_producer):
    runtime = DeletionFixtureRuntime()
    client = _client(tmp_path, monkeypatch, role='admin', pi_runtime=runtime)
    client.app.include_router(source_routes.router)
    uploads = UploadStore(root=settings.data_prep_upload_root, max_bytes=1024 * 1024)
    original = uploads.save_bytes('user-a', 'original.csv', '名称,数量\n青林,2\n'.encode('utf-8'), media_type='text/csv')
    fresh = uploads.save_bytes('user-a', 'fresh.csv', '名称,数量\n青林,3\n'.encode('utf-8'), media_type='text/csv')
    page = b'<html><body>Deletion source marker 137: Qinglin 2</body></html>'
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=page, headers={'content-type': 'text/html'})
    service = SourceAcquisitionService(SourceAcquisitionRepository(settings.webui_db_path), AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']), transport=httpx.MockTransport(response)))
    monkeypatch.setattr(source_routes, 'get_source_acquisition_service', lambda: service)
    monkeypatch.setattr(workspace_routes, '_source_acquisition_service', lambda: service)
    with client:
        acquired = client.post('/api/semantic-workspace/source-acquisitions', headers={'Idempotency-Key': 'deletion-web'},
                               json={'url': 'https://deletion.example.com/data', 'purpose': '保存本次合成资料'})
        assert acquired.status_code == 202, acquired.text
        snapshot = acquired.json()['snapshot_id']
        producer, produced = _task(client, 'deletion-producer', [original.upload_id], [snapshot])
        consumer, consumed = _task(client, 'deletion-consumer', [fresh.upload_id, original.upload_id], [snapshot], [produced['output_id']])
        survivor, output = (consumer, consumed) if delete_producer else (producer, produced)
        original_output = client.get(f"/api/semantic-deliveries/outputs/{output['output_id']}")
        assert original_output.status_code == 200, original_output.text
        assert hashlib.sha256(original_output.content).hexdigest() == output['sha256']
        removed_output = produced if delete_producer else consumed
        removed_path = Path(get_store().get_semantic_delivery_output('user-a', removed_output['output_id'])['file_path'])
        assert removed_path.is_file()
        assert hashlib.sha256(removed_path.read_bytes()).hexdigest() == removed_output['sha256']
        with sqlite3.connect(settings.webui_db_path) as conn:
            before_dump = '\n'.join(conn.iterdump())
        assert page.hex() in before_dump.lower()
        target = producer if delete_producer else consumer
        _, first_operation = _delete(client, target, 'delete_shared' if delete_producer else 'keep_shared')
        retained = client.get(f'/api/semantic-workspace/tasks/{survivor}')
        assert retained.status_code == 200, retained.text
        downloaded = client.get(f"/api/semantic-deliveries/outputs/{output['output_id']}")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == original_output.content
        assert not removed_path.exists()
        assert client.get(f"/api/semantic-deliveries/outputs/{removed_output['output_id']}").status_code in {404, 409, 410}
        if delete_producer:
            assert not Path(original.storage_path).exists()
            with pytest.raises(PermissionError):
                uploads.resolve('user-a', original.upload_id)
            # 真正清除网页本体；墓碑映射不能继续保有可下载正文。
            with sqlite3.connect(settings.webui_db_path) as conn:
                assert conn.execute('SELECT COUNT(*) FROM source_artifacts WHERE snapshot_id=?', (snapshot,)).fetchone()[0] == 0
                remaining_dump = '\n'.join(conn.iterdump())
            # 同一合成正文的数据库副本也不能藏在观察报告或旧事件里。
            assert page.hex() not in remaining_dump.lower()
            assert 'Deletion source marker 137' not in remaining_dump
            unavailable = client.post('/api/semantic-workspace/reusable-sources/resolve', json={
                'upload_ids': [original.upload_id], 'source_snapshot_ids': [snapshot], 'delivery_output_ids': [produced['output_id']],
            })
            assert unavailable.status_code == 200, unavailable.text
            items = unavailable.json()['items']
            assert len(items) == 3
            assert {item['source_key'] for item in items} == {
                'upload:' + original.upload_id, 'snapshot:' + snapshot, 'delivery_output:' + produced['output_id'],
            }
            assert all(item['availability'] != 'available' for item in items)
            integrity = retained.json()['source_integrity']
            assert integrity['state'] == 'source_deleted'
            assert integrity['can_rerun'] is False and integrity['can_reverify'] is False
            references = client.get('/api/semantic-workspace/source-references', params={'kind': 'snapshot', 'id': snapshot})
            assert references.status_code == 200, references.text
            assert any(item['reference_kind'] == 'delivery' and item['task_id'] == consumer
                       and item['delivery_id'] == consumed['delivery_id'] for item in references.json()['items'])
            assert uploads.resolve('user-a', fresh.upload_id).sha256 == fresh.sha256
            # 保留任务日后也能清理；旧墓碑不可改归属、重读或重新删除。
            with sqlite3.connect(settings.webui_db_path) as conn:
                old_facts = conn.execute('SELECT * FROM source_deletions WHERE operation_id=? ORDER BY source_key',
                                         (first_operation['operation_id'],)).fetchall()
            assert len(old_facts) == 3
            survivor_path = Path(get_store().get_semantic_delivery_output('user-a', consumed['output_id'])['file_path'])
            later_plan, later_operation = _delete(client, consumer, 'keep_shared')
            assert later_operation['operation_id'] != first_operation['operation_id']
            assert {item['source_key'] for item in later_plan['objects']} == {
                'upload:' + fresh.upload_id, 'delivery_output:' + consumed['output_id'],
            }
            assert not Path(fresh.storage_path).exists()
            assert not survivor_path.exists()
            with sqlite3.connect(settings.webui_db_path) as conn:
                assert conn.execute('SELECT * FROM source_deletions WHERE operation_id=? ORDER BY source_key',
                                    (first_operation['operation_id'],)).fetchall() == old_facts
            assert client.get(f"/api/semantic-workspace/deletion-operations/{first_operation['operation_id']}").json() == first_operation
        else:
            assert not Path(fresh.storage_path).exists()
            with pytest.raises(PermissionError):
                uploads.resolve('user-a', fresh.upload_id)
            assert uploads.resolve('user-a', original.upload_id).sha256 == original.sha256
            assert service.repository.get_snapshot('user-a', snapshot) is not None
        assert len(calls) == 1
