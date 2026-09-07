"""仅虚构账号与 fake 动作验证待确认执行；绝不调用真实外部服务。"""
import asyncio
import threading

import pytest
from fastapi import HTTPException

from src import account_execution as execution
from src.api import auth
from src.api.routes import confirm
from src.api.schemas import ConfirmIn
from src.api.session_store import PendingStore
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def pending(tmp_path, monkeypatch):
    path = migrated_webui_database(tmp_path / 'confirm.db')
    frozen = seed_execution_owner(path)
    store = WebUIStore(str(path))
    actions = PendingStore()
    monkeypatch.setattr(auth, 'get_store', lambda: store)
    monkeypatch.setattr(confirm, 'pending_store', actions)
    return store, frozen, actions


def test_pending_cannot_borrow_reenabled_owner(pending):
    store, old, actions = pending
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'email': {'to': ['fake@example.invalid']}})
    store.update_user(old.owner_user_id, disabled=True)
    store.update_user(old.owner_user_id, disabled=False)
    with execution.execution_context(store.capture_account_execution(old.owner_user_id)):
        with pytest.raises(execution.ExecutionDenied):
            with actions.claim_action(old.owner_user_id, 'task', 'email'):
                pytest.fail('旧动作不能开始')


def test_late_put_is_rejected(pending):
    store, old, actions = pending
    store.update_user(old.owner_user_id, pending=True)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        actions.put(old.owner_user_id, 'task', {'db': {'items': ['fake']}})
    assert actions.get(old.owner_user_id, 'task') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('flag', ['disabled', 'pending'])
async def test_authenticated_then_held_confirmation_never_starts_action(pending, monkeypatch, flag):
    store, old, actions = pending
    calls = []
    monkeypatch.setattr(confirm, 'write_items', lambda *args, **kwargs: calls.append(1) or 1)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'db': {'task_id': 'task', 'items': ['fake']}})
        store.update_user(old.owner_user_id, **{flag: True})
        with pytest.raises(execution.ExecutionDenied):
            await confirm.confirm_db(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id})
    assert calls == []


@pytest.mark.asyncio
async def test_valid_internal_db_once(pending, monkeypatch):
    store, old, actions = pending
    calls = []
    monkeypatch.setattr(confirm, 'write_items', lambda *args, **kwargs: calls.append(args) or 1)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'db': {'task_id': 'task', 'items': ['fake']}})
        await confirm.confirm_db(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id})
        with pytest.raises(Exception):
            await confirm.confirm_db(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancelled_wait_keeps_real_thread_and_unknown_binding(pending, monkeypatch):
    store, old, actions = pending
    reached, release = threading.Event(), threading.Event()
    def fake_send(*args, **kwargs):
        reached.set()
        assert release.wait(5)
        return 1
    monkeypatch.setattr(confirm, 'write_items', fake_send)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'db': {'task_id': 'task', 'items': ['fake']}})
        task = asyncio.create_task(confirm.confirm_db(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id}))
        try:
            assert await asyncio.to_thread(reached.wait, 3)
            task.cancel()
            await asyncio.sleep(0.03)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    with store._conn() as conn:
        assert conn.execute("SELECT state FROM account_execution_bindings WHERE resource_kind='chat'").fetchone()[0] == 'cleanup_failed'


@pytest.mark.asyncio
async def test_template_late_result_never_reaches_save(pending, monkeypatch):
    store, old, actions = pending
    reached, release = asyncio.Event(), asyncio.Event()
    saved = []
    async def distill(*args, **kwargs):
        reached.set()
        await release.wait()
        return {'title': '虚构', 'keywords': [], 'body': '虚构'}
    async def save(**kwargs):
        saved.append(kwargs)
        return 'fake'
    monkeypatch.setattr(confirm, 'distill_template', distill)
    monkeypatch.setattr(confirm, 'save_template', save)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'template': {'intent': '虚构', 'data_type': 'generic', 'analysis': '虚构'}})
        task = asyncio.create_task(confirm.confirm_template(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id}))
        await reached.wait()
        store.update_user(old.owner_user_id, disabled=True)
        store.update_user(old.owner_user_id, disabled=False)
        release.set()
        with pytest.raises(execution.ExecutionDenied):
            await task
    assert saved == []


@pytest.mark.asyncio
async def test_unknown_action_cannot_be_registered_or_consumed_again(pending, monkeypatch):
    store, old, actions = pending
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('fake unknown')
    monkeypatch.setattr(confirm, 'write_items', send)
    with execution.execution_context(old):
        payload = {'db': {'task_id': 'task', 'items': ['fake']}}
        actions.put(old.owner_user_id, 'task', payload)
        with pytest.raises(Exception):
            await confirm.confirm_db(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id})
        with pytest.raises(execution.ExecutionDenied):
            actions.put(old.owner_user_id, 'task', payload)
        with pytest.raises(HTTPException) as consumed:
            await confirm.confirm_db(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id})
        assert consumed.value.status_code == 404
    assert calls == [1]


@pytest.mark.asyncio
async def test_normal_db_and_template_keep_business_results(pending, monkeypatch):
    store, old, actions = pending
    calls = []
    monkeypatch.setattr(confirm, 'write_items', lambda *args, **kwargs: calls.append('db') or 2)
    async def distill(*args, **kwargs):
        return {'title': '虚构', 'keywords': [], 'body': '虚构'}
    async def save(**kwargs):
        calls.append('template')
        return 'fake-template'
    monkeypatch.setattr(confirm, 'distill_template', distill)
    monkeypatch.setattr(confirm, 'save_template', save)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'db': {'task_id': 'task', 'items': ['fake']}, 'template': {'intent': '虚构', 'data_type': 'generic', 'analysis': '虚构'}})
        for handler in (confirm.confirm_db, confirm.confirm_template):
            assert (await handler(ConfirmIn(task_id='task'), user={'user_id': old.owner_user_id}))['ok']
    assert calls == ['db', 'template']


def test_schedule_confirmation_claim_retains_context(pending, monkeypatch):
    from types import SimpleNamespace
    from src.api.routes import tasks
    from src.api.schemas import ScheduleIn
    store, old, actions = pending
    calls = []
    def add(**kwargs):
        calls.append(execution.current_authorization())
        return 'fake-schedule'
    monkeypatch.setattr(tasks, 'pending_store', actions)
    monkeypatch.setattr(tasks, 'get_schedule_store', lambda: SimpleNamespace(add=add))
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'schedule': {'schedule': '0 9 * * *', 'user_input': '虚构'}})
        result = tasks.create_task(ScheduleIn(task_id='task'), user={'user_id': old.owner_user_id})
    assert result['task_id'] == 'fake-schedule'
    assert calls == [old]


@pytest.mark.asyncio
async def test_http_dependency_freezes_legitimate_owner(pending, monkeypatch):
    from fastapi import FastAPI
    import httpx
    store, old, actions = pending
    calls = []
    monkeypatch.setattr(confirm, 'write_items', lambda *args, **kwargs: calls.append(execution.current_authorization()) or 1)
    app = FastAPI()
    app.include_router(confirm.router)
    app.dependency_overrides[auth.get_current_user] = lambda: store.get_user(old.owner_user_id)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'db': {'task_id': 'task', 'items': ['fake']}})
    assert execution.current_authorization(required=False) is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test.invalid') as client:
        response = await client.post('/api/confirm/db', json={'task_id': 'task'})
        missing = await client.post('/api/confirm/db', json={'task_id': 'unknown'})
    assert response.status_code == 200
    assert missing.status_code == 404
    assert calls == [old]


@pytest.mark.asyncio
async def test_parallel_confirmation_returns_409_without_second_action(pending, monkeypatch):
    from fastapi import FastAPI
    import httpx
    store, old, actions = pending
    reached, release = threading.Event(), threading.Event()
    calls = []
    def write(*args, **kwargs):
        calls.append(1)
        reached.set()
        assert release.wait(5)
        return 1
    monkeypatch.setattr(confirm, 'write_items', write)
    app = FastAPI()
    app.include_router(confirm.router)
    app.dependency_overrides[auth.get_current_user] = lambda: store.get_user(old.owner_user_id)
    with execution.execution_context(old):
        actions.put(old.owner_user_id, 'task', {'db': {'task_id': 'task', 'items': ['fake']}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test.invalid') as client:
        first = asyncio.create_task(client.post('/api/confirm/db', json={'task_id': 'task'}))
        try:
            assert await asyncio.to_thread(reached.wait, 3)
            second = await client.post('/api/confirm/db', json={'task_id': 'task'})
            assert second.status_code == 409
        finally:
            release.set()
            response = await first
        assert response.status_code == 200
    assert calls == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['new', 'merge'])
async def test_curator_late_return_cannot_write_template(pending, monkeypatch, tmp_path, decision):
    from src.memory import templates
    store, old, actions = pending
    reached, release = asyncio.Event(), asyncio.Event()
    writes = []
    async def curate(*args, **kwargs):
        reached.set()
        await release.wait()
        return {'decision': decision, 'slug': 'existing', 'title': '虚构', 'keywords': [], 'body': '虚构'}
    directory = tmp_path / 'synthetic-templates'
    directory.mkdir()
    (directory / 'existing.md').write_text('---\ntitle: 虚构\n---\n旧正文\n', encoding='utf-8')
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', directory)
    monkeypatch.setattr(templates, 'curate_template', curate)
    monkeypatch.setattr(templates, 'atomic_write', lambda *args: writes.append(args))
    monkeypatch.setattr(templates, '_invalidate_and_rebuild_templates', lambda: None)
    monkeypatch.setattr(templates, '_load_vectors', lambda: {})
    with execution.execution_context(old):
        task = asyncio.create_task(templates.save_template('虚构', 'generic', [], '虚构', owner_id=old.owner_user_id))
        await reached.wait()
        store.update_user(old.owner_user_id, disabled=True)
        store.update_user(old.owner_user_id, disabled=False)
        release.set()
        with pytest.raises(execution.ExecutionDenied):
            await task
    assert writes == []
