"""自动化公开接口与北京时间约定，外部执行均使用合成数据。"""
from datetime import datetime, timezone

from src.scheduler import compute_next_run, parse_schedule
from tests.test_account_execution_scheduler import fixture


def test_migration_preserves_legacy_values_and_replays(tmp_path):
    import sqlite3
    from sqlalchemy import create_engine
    from alembic import command
    from src.database_migrations import _alembic_config, DatabaseTarget, apply_migrations
    path = tmp_path / 'legacy.db'
    engine = create_engine('sqlite:///' + path.as_posix())
    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), 'scheduler_0003')
    engine.dispose()
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO scheduled_tasks (task_id,user_input,trigger_type,status,created_at,next_run_at) VALUES ('legacy','保留原任务','cron','active','2026-09-01T09:00:00','2026-09-02T09:00:00')")
        conn.commit()
        before = conn.execute('SELECT * FROM scheduled_tasks').fetchone()
    finally:
        conn.close()
    target = DatabaseTarget(profile='scheduler', path=path)
    apply_migrations(target, tmp_path / 'backup')
    apply_migrations(target, tmp_path / 'backup-replay')
    conn = sqlite3.connect(path)
    try:
        after = conn.execute('SELECT * FROM scheduled_tasks').fetchone()
        assert after[:-1] == before
        assert after[-1] is None
    finally:
        conn.close()


def test_manual_creation_requires_explicit_model(fixture, monkeypatch):
    import pytest
    from fastapi import HTTPException
    from src.api.routes import tasks
    from src.api.schemas import ManualTaskIn
    from src.account_execution import execution_context
    web, store, owner, authorization = fixture
    monkeypatch.setattr(tasks, 'get_schedule_store', lambda: store)
    body = ManualTaskIn(name='合成计划', prompt='合成内容', trigger={'type': 'cron', 'cron_expr': '0 9 * * *'})
    with execution_context(authorization), pytest.raises(HTTPException) as error:
        tasks.create_manual_task(body, {'user_id': owner, 'role': 'user'})
    assert error.value.status_code == 422
    assert store.list_active(owner_user_id=owner) == []


def test_begin_failure_releases_claim_without_running_worker(fixture, monkeypatch):
    import asyncio
    import pytest
    from tests.test_account_execution_scheduler import add
    from src.scheduler.service import SchedulerService
    from src.account_execution import execution_context
    web, store, owner, auth = fixture
    task_id = add(store, auth, 'cron')
    service = SchedulerService(store)
    def fail(_task):
        raise OSError('模拟写入失败')
    monkeypatch.setattr(store, 'begin_run', fail)
    with execution_context(auth), pytest.raises(OSError):
        asyncio.run(service.run_task_now(task_id, background=True))
    assert not service.is_running(task_id)
    assert web.account_execution_binding(owner, 'schedule', task_id)['state'] == 'idle'


def test_crashed_run_is_unknown_not_running(fixture, monkeypatch):
    from tests.test_account_execution_scheduler import add
    from src.scheduler.service import SchedulerService
    from src.account_execution import execution_context
    from src.api.routes import tasks
    web, store, owner, auth = fixture
    task_id = add(store, auth, 'cron')
    with execution_context(auth):
        claimed, task = store.claim_execution(task_id, expected_task=store.get(task_id), manual=True)
        with execution_context(claimed):
            store.begin_run(task)
    monkeypatch.setattr(tasks, 'get_schedule_store', lambda: store)
    monkeypatch.setattr(tasks, 'get_scheduler_service', lambda: SchedulerService(store))
    assert tasks.list_tasks({'user_id': owner})[0]['execution_state'] == 'blocked'
    assert tasks.list_tasks({'user_id': owner})[0]['can_recreate'] is False
    legacy = {**store.get(task_id), 'time_zone': None}
    assert store.execution_status(legacy)['can_recreate'] is False
    assert tasks.recent_runs(user={'user_id': owner})['items'][0]['state'] == 'unknown'


def test_manual_create_and_edit_freezes_selected_connection(fixture, monkeypatch):
    from types import SimpleNamespace
    from src.api.routes import tasks
    from src.api.schemas import ManualTaskIn, TaskPatchIn
    from src.account_execution import execution_context
    from src.scheduler.service import SchedulerService
    import src.model_connections
    web, store, owner, auth = fixture
    connection = {'connection_id': 'synthetic', 'status': 'verified', 'preset_id': 'deepseek', 'locality': 'public_external', 'models': [
        {'model_id': model, 'enabled': True, 'status': 'available'} for model in ('model-a', 'model-b')]}
    broker = SimpleNamespace(list_connections=lambda owner: [connection], freeze_connection=lambda owner, cid: SimpleNamespace(connection_id=cid, connection_version='frozen-v1'))
    monkeypatch.setattr(src.model_connections, 'get_default_broker', lambda: broker)
    monkeypatch.setattr(tasks, 'get_schedule_store', lambda: store)
    monkeypatch.setattr(tasks, 'get_scheduler_service', lambda: SchedulerService(store))
    with execution_context(auth):
        result = tasks.create_manual_task(ManualTaskIn(name='合成', prompt='合成', model='model-a', model_connection_id='synthetic', external_api_confirmed=True, trigger={'type': 'cron', 'cron_expr': '0 9 * * *'}), {'user_id': owner})
        task_id = result['task_id']
        assert store.get(task_id)['model_connection_version'] == 'frozen-v1'
        tasks.update_task(task_id, TaskPatchIn(model='model-b', model_connection_id='synthetic', external_api_confirmed=True), {'user_id': owner})
        assert store.get(task_id)['model'] == 'model-b'


def test_schedule_uses_beijing_and_respects_start_date():
    assert compute_next_run(parse_schedule('cron@0 9 * * *'), datetime(2026, 9, 18, 0, tzinfo=timezone.utc)) == datetime(2026, 9, 18, 9)
    assert parse_schedule('once@2026-09-18T01:00:00Z').run_at == datetime(2026, 9, 18, 9)
    assert compute_next_run(parse_schedule('cron@0 9 * * *'), datetime(2026, 9, 17, 10), start_date='2026-09-20') == datetime(2026, 9, 20, 9)


def test_legacy_plan_is_visible_as_blocked(fixture, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import auth
    from src.api.routes import tasks
    from src.account_execution import execution_context
    web, store, owner, authorization = fixture
    with execution_context(authorization):
        tid = store.add(user_input='合成旧计划', provider='local', model='synthetic', trigger_type='cron', cron_expr='0 9 * * *', run_at=None, next_run_at=datetime(2099, 1, 1), owner_user_id=owner)
    # 模拟升级前数据，不触碰原库。
    with store._conn() as conn:
        conn.execute('UPDATE scheduled_tasks SET time_zone=NULL WHERE task_id=?', (tid,))
    monkeypatch.setattr(tasks, 'get_schedule_store', lambda: store)
    monkeypatch.setattr(tasks, 'get_scheduler_service', lambda: SimpleNamespace(is_running=lambda task_id: False))
    app = FastAPI(); app.include_router(tasks.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {'user_id': owner}
    with TestClient(app) as client:
        item = client.get('/api/tasks').json()[0]
    assert item['execution_state'] == 'blocked'
    assert '北京时间' in item['blocked_reason']


def test_manual_execution_returns_before_completion(fixture, monkeypatch):
    import asyncio
    from src.api.routes import tasks
    from src.account_execution import execution_context
    from src.scheduler.service import SchedulerService
    web, store, owner, authorization = fixture
    async def probe():
        entered, release = asyncio.Event(), asyncio.Event()
        async def runner(*args, **kwargs):
            entered.set(); await release.wait()
            return {'reply': '合成结果'}
        service = SchedulerService(store, runner=runner)
        monkeypatch.setattr(tasks, 'get_schedule_store', lambda: store)
        monkeypatch.setattr(tasks, 'get_scheduler_service', lambda: service)
        with execution_context(authorization):
            tid = store.add(user_input='合成测试', provider='local', model='synthetic', trigger_type='cron', cron_expr='0 9 * * *', run_at=None, next_run_at=datetime(2099, 1, 1), owner_user_id=owner)
            try:
                result = await asyncio.wait_for(tasks.run_task_now_endpoint(tid, {'user_id': owner}), 1)
                assert result['run_id']
                await asyncio.wait_for(entered.wait(), 1)
                rows = tasks.recent_runs(task_id=tid, user={'user_id': owner})
                assert rows['items'][0]['state'] == 'running'
            finally:
                release.set()
                await asyncio.sleep(0.05)
                await service.stop()
            assert tasks.recent_runs(task_id=tid, user={'user_id': owner})['items'][0]['state'] == 'succeeded'
    asyncio.run(probe())
