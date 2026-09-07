"""来源执行停用竞态；仅虚构 Owner 与假读取器。"""
import asyncio
from contextlib import closing
import sqlite3

import httpx
import pytest

from src import account_execution as execution
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition.service import AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionRequest, SourceAcquisitionService, _FetchedPage
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def source(tmp_path):
    path = migrated_webui_database(tmp_path / 'source.db')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO users(user_id,username,password_hash,created_at,disabled,pending) VALUES ('owner','owner','fake','now',0,0)")
        conn.commit()
        auth = execution.capture_authorization(conn, 'owner')
    return path, SourceAcquisitionRepository(path), auth


def hold(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        execution.update_account_status(conn, 'owner', disabled=True, actor_user_id='admin', now=1)
        execution.update_account_status(conn, 'owner', disabled=False, actor_user_id='admin', now=2)
        conn.commit()


def test_late_first_source_claim_cannot_capture_reenabled_generation(source):
    path, repository, auth = source
    with execution.execution_context(auth):
        hold(path)
        with pytest.raises(execution.ExecutionDenied):
            repository.claim_attempt(owner_id='owner', idempotency_key='late', request=SourceAcquisitionRequest('https://example.invalid/', '测试'))
    assert repository.get_by_idempotency_key('owner', 'late') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('disable', [False, True])
async def test_late_response_never_publishes_snapshot_or_follows_redirect(source, disable):
    path, repository, auth = source
    visited = []

    async def handler(request):
        visited.append(str(request.url))
        if len(visited) == 1:
            if disable:
                hold(path)
            return httpx.Response(302, headers={'location': '/next'})
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='<html><body>test</body></html>')

    fetcher = AnonymousWebFetcher(security_guard=HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']), transport=httpx.MockTransport(handler))
    service = SourceAcquisitionService(repository, fetcher)
    with execution.execution_context(auth):
        if disable:
            with pytest.raises(execution.ExecutionDenied):
                await service.acquire(owner_id='owner', idempotency_key='read', request=SourceAcquisitionRequest('https://example.invalid/', '测试'))
        else:
            result = await service.acquire(owner_id='owner', idempotency_key='read', request=SourceAcquisitionRequest('https://example.invalid/', '测试'))
            assert result['status'] == 'succeeded'
    assert len(visited) == (1 if disable else 2)
    assert repository.count_snapshots('owner') == (0 if disable else 1)


@pytest.mark.asyncio
async def test_hold_waits_for_actual_response_close_before_stop_confirmation(source):
    path, repository, auth = source
    reading = asyncio.Event()
    closing = asyncio.Event()
    allow_close = asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            reading.set()
            await asyncio.Event().wait()
            yield b'never'

        async def aclose(self):
            closing.set()
            await allow_close.wait()

    fetcher = AnonymousWebFetcher(security_guard=HttpSecurityGuard(resolver=lambda _: ['93.184.216.34']), transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/html'}, stream=SlowStream())))
    with execution.execution_context(auth):
        task = asyncio.create_task(SourceAcquisitionService(repository, fetcher).acquire(owner_id='owner', idempotency_key='slow', request=SourceAcquisitionRequest('https://example.invalid/', '测试')))
        await asyncio.wait_for(reading.wait(), 2)
        hold(path)
        await asyncio.wait_for(closing.wait(), 2)
        assert not task.done()
        with closing_connection(path) as conn:
            assert execution.list_execution_bindings(conn, 'owner')[0]['state'] == 'active'
        allow_close.set()
        with pytest.raises(execution.ExecutionDenied):
            await asyncio.wait_for(task, 2)
    with closing_connection(path) as conn:
        assert execution.list_execution_bindings(conn, 'owner')[0]['state'] == 'paused'
    assert repository.count_snapshots('owner') == 0


def closing_connection(path):
    return closing(sqlite3.connect(path))


@pytest.mark.asyncio
async def test_late_completed_page_cannot_commit_after_reenable(source):
    path, repository, auth = source

    class LateFetcher:
        async def fetch(self, url):
            hold(path)
            return _FetchedPage(url, url, 'now', 'text/html', b'late', 'a' * 64, '虚构', '迟到正文')

    with execution.execution_context(auth), pytest.raises(execution.ExecutionDenied):
        await SourceAcquisitionService(repository, LateFetcher()).acquire(owner_id='owner', idempotency_key='late-page', request=SourceAcquisitionRequest('https://example.invalid/', '测试'))
    assert repository.count_snapshots('owner') == 0
    assert repository.get_by_idempotency_key('owner', 'late-page')['status'] == 'canceled'


def test_source_needs_matching_frozen_owner(source):
    _path, repository, auth = source
    request = SourceAcquisitionRequest('https://example.invalid/', '测试')
    with pytest.raises(execution.ExecutionDenied):
        repository.claim_attempt(owner_id='owner', idempotency_key='missing-context', request=request)
    with execution.execution_context(auth), pytest.raises(execution.ExecutionDenied):
        repository.claim_attempt(owner_id='another', idempotency_key='wrong-owner', request=request)
