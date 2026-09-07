"""真实临时库与MockTransport验证账号撤销穿过Grant和Relay边界。"""
import asyncio
import json
from contextvars import Context
from pathlib import Path

import httpx
import pytest

from src.account_execution import ExecutionAuthorization, ExecutionDenied, execution_context
from src.api.store import WebUIStore
from src.model_connections import ConnectionBroker, GrantError
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def grants(tmp_path):
    database = migrated_webui_database(tmp_path / "grants.db")
    authorization = seed_execution_owner(database, "synthetic-owner")
    store = WebUIStore(str(database))
    repository = ModelConnectionRepository(str(database))
    vault = FernetCredentialVault.generate()
    connection = repository.upsert_personal(owner_user_id=authorization.owner_user_id,
        preset_id="synthetic", preset_version="1", display_name="虚构连接", base_url="https://synthetic.example/v1",
        model="synthetic-model", api_format="openai_chat_completions", ciphertext=vault.encrypt("synthetic-secret"),
        key_hint="fake", verified_at="2026-01-01")
    with execution_context(authorization):
        yield store, repository, vault, connection


def broker_and_grant(grants, handler, resolver=None):
    _, repository, vault, connection = grants
    broker = ConnectionBroker(repository=repository, vault=vault, transport=httpx.MockTransport(handler), resolver=resolver or (lambda _: ["8.8.8.8"]))
    binding = broker.freeze_connection("synthetic-owner", connection["connection_id"])
    grant = broker.issue_grant(owner_user_id="synthetic-owner", connection_id=binding.connection_id,
        connection_version=binding.connection_version, task_id="synthetic-task", revision=1, run_id="synthetic-run", purpose="agent_inference")
    return broker, grant


async def relay(broker, grant):
    return await broker.relay(grant_token=grant.token, protocol_path="chat/completions", method="POST", headers={},
        body=json.dumps({"model": "synthetic-model", "messages": [], "stream": True}).encode("utf-8"))


def test_late_grant_issue_cannot_use_old_authorization_after_reenable(grants):
    store, _, _, _ = grants
    store.update_user("synthetic-owner", disabled=True)
    store.update_user("synthetic-owner", disabled=False)
    with pytest.raises(ExecutionDenied):
        broker_and_grant(grants, lambda _: httpx.Response(200))


@pytest.mark.asyncio
async def test_reenable_does_not_restore_revoked_grant(grants):
    store, _, _, _ = grants
    broker, grant = broker_and_grant(grants, lambda _: httpx.Response(200, content=b"synthetic"))
    store.update_user("synthetic-owner", disabled=True)
    store.update_user("synthetic-owner", disabled=False)
    with pytest.raises(GrantError):
        response = await relay(broker, grant)
        await response.aclose()


@pytest.mark.asyncio
async def test_disable_during_dns_prevents_actual_provider_send(grants):
    store, _, _, _ = grants
    sent = []

    def resolver(_):
        store.update_user("synthetic-owner", disabled=True)
        return ["8.8.8.8"]

    def handler(_):
        sent.append(True)
        return httpx.Response(200)

    broker, grant = broker_and_grant(grants, handler, resolver)
    with pytest.raises(GrantError):
        response = await relay(broker, grant)
        await response.aclose()
    assert sent == []


@pytest.mark.asyncio
async def test_idle_stream_revocation_closes_provider_without_more_body(grants):
    store, _, _, _ = grants
    release, closed, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            waiting.set()
            await release.wait()
            yield b"late"

        async def aclose(self):
            closed.set()

    broker, grant = broker_and_grant(grants, lambda _: httpx.Response(200, stream=Stream()))
    response = await relay(broker, grant)
    stream = response.iter_bytes()
    try:
        assert await anext(stream) == b"first"
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(waiting.wait(), 1)
        store.update_user("synthetic-owner", disabled=True)
        with pytest.raises(GrantError):
            await asyncio.wait_for(pending, 0.8)
        assert closed.is_set()
    finally:
        release.set()
        await stream.aclose()
        await response.aclose()


@pytest.mark.asyncio
async def test_revocation_while_send_awaits_discards_response_and_records_unknown(grants):
    store, _, _, _ = grants
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"late-result"

        async def aclose(self):
            closed.set()

    async def handler(_):
        entered.set()
        await release.wait()
        return httpx.Response(200, stream=Stream())

    broker, grant = broker_and_grant(grants, handler)
    pending = asyncio.create_task(relay(broker, grant))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        store.update_user("synthetic-owner", pending=True)
    finally:
        release.set()
        with pytest.raises(GrantError):
            await asyncio.wait_for(pending, 1)
    assert closed.is_set()
    with store._conn() as conn:
        rows = conn.execute("SELECT status FROM model_provider_usage WHERE grant_id=?", (grant.grant_id,)).fetchall()
    assert [row[0] for row in rows] == ["unknown"]


@pytest.mark.asyncio
async def test_other_owner_disable_does_not_revoke_this_grant(grants):
    store, _, _, _ = grants
    seed_execution_owner(store.db_path, "other-owner")
    broker, grant = broker_and_grant(grants, lambda _: httpx.Response(200, content=b"valid-result"))
    store.update_user("other-owner", disabled=True)
    response = await relay(broker, grant)
    assert b"".join([chunk async for chunk in response.iter_bytes()]) == b"valid-result"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["logout", "password"])
async def test_login_session_changes_do_not_revoke_business_grant(grants, action):
    store, _, _, _ = grants
    broker, grant = broker_and_grant(grants, lambda _: httpx.Response(200, content=b"valid-result"))
    _, logged_in = store.platform_login_commit(username="synthetic-owner", expected_password_hash="synthetic-hash",
        session_id="synthetic-session", refresh_digest="synthetic-digest", now=1000, access_expires_at=2000,
        absolute_expires_at=3000, account_key="synthetic-account", source_key="synthetic-source")
    assert logged_in is not None
    if action == "logout":
        store.platform_logout_all("synthetic-owner", now=1001)
    else:
        assert store.platform_change_password(owner_user_id="synthetic-owner", session_id="synthetic-session",
            expected_password_hash="synthetic-hash", password_hash="synthetic-new-hash", now=1001)
    response = await relay(broker, grant)
    assert b"".join([chunk async for chunk in response.iter_bytes()]) == b"valid-result"


def test_grant_issuer_cannot_impersonate_other_owner(grants):
    store, _, _, _ = grants
    seed_execution_owner(store.db_path, "other-owner")
    with execution_context(ExecutionAuthorization("other-owner", 0)):
        with pytest.raises(ExecutionDenied):
            broker_and_grant(grants, lambda _: httpx.Response(200))


@pytest.mark.parametrize("state", ["active", "missing", "disabled", "late"])
def test_real_qualification_entry_freezes_existing_owner_once(grants, tmp_path, state):
    from scripts.verify_g4_provider_safety import QualificationError, execute_qualification, freeze_manifest

    store, repository, vault, _ = grants
    broker = ConnectionBroker(repository=repository, vault=vault, resolver=lambda _: ["8.8.8.8"],
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})))
    asyncio.run(broker.configure_platform_preset(actor_user_id="synthetic-owner", display_name="虚构平台连接", preset_id="deepseek", api_key="synthetic-provider-secret", model="deepseek-v4-flash"))
    database = Path(store.db_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(freeze_manifest(db_path=database, presets=["deepseek"])), encoding="utf-8")
    sent = []

    def post(*args, **kwargs):
        sent.append(True)
        return httpx.Response(200, json={"choices": [{"message": {"content": "MANGROVE_G4_OK"}}]})

    if state in {"disabled", "late"}:
        store.update_user("synthetic-owner", disabled=True)
    if state == "late":
        store.update_user("synthetic-owner", disabled=False)
    values = dict(db_path=database, manifest_path=manifest, output_path=tmp_path / "report.json", relay_base_url="http://synthetic-relay", timeout_seconds=20, broker=broker, relay_post=post,
                  owner_user_id="missing-owner" if state == "missing" else "synthetic-owner")
    if state == "active":
        Context().run(execute_qualification, **values)
        assert sent == [True]
        with store._conn() as conn:
            assert conn.execute("SELECT owner_user_id FROM model_connection_grants").fetchone()[0] == "synthetic-owner"
    else:
        with pytest.raises(QualificationError, match="账号"):
            if state == "late":
                execute_qualification(**values)
            else:
                Context().run(execute_qualification, **values)
        assert sent == []
