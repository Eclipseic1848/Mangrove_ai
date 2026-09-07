"""缓存模型客户端每次真正发包都复核账号；仅使用 MockTransport。"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from src import account_execution as execution
from src.api.store import WebUIStore
from src.llm.provider import MultiModelProvider, ResolvedModelConnection
from src.services.llm_provider import LLMProvider
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize("kind", ["multi-local", "multi-cloud", "legacy-chat", "legacy-sync", "legacy-async", "document"])
def test_cached_clients_refuse_old_generation_before_transport(tmp_path, monkeypatch, kind):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "providers.db")))
    owner = store.create_user("synthetic", "synthetic-hash", pending=False)["user_id"]
    auth = store.capture_account_execution(owner)
    monkeypatch.setattr("src.api.auth.get_store", lambda: store)
    sent = []
    clients = []
    stop_on_response = False

    def reply(request):
        sent.append(request.method)
        if stop_on_response:
            store.update_user(owner, disabled=True)
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
            "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "虚构"}}]})

    sync_init, async_init = httpx.Client.__init__, httpx.AsyncClient.__init__

    def sync_init_mock(self, **kwargs):
        kwargs.update(transport=httpx.MockTransport(reply), trust_env=False)
        sync_init(self, **kwargs)
        clients.append(self)

    def async_init_mock(self, **kwargs):
        kwargs.update(transport=httpx.MockTransport(reply), trust_env=False)
        async_init(self, **kwargs)
        clients.append(self)

    # SDK 自带默认子类也必须走假网络；保留原类型以免绕过客户端检查。
    monkeypatch.setattr(httpx.Client, "__init__", sync_init_mock)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", async_init_mock)
    connection = ResolvedModelConnection(provider="local" if kind == "multi-local" else "deepseek",
        requested_model="synthetic", model="synthetic", base_url="http://provider.test/v1",
        api_key="synthetic-key", timeout=1, trust_env=False, extra_body=None)
    if kind.startswith("multi"):
        provider = object.__new__(MultiModelProvider)
        provider._cache = {}
        provider.resolve_model = lambda *a, **kw: connection
        client = provider.get_chat_model()
        client.max_retries = 0
        invoke = lambda: client.invoke("虚构")
    elif kind == "document":
        from src.services.document_extraction import _build_instructor_client
        monkeypatch.setattr("src.services.document_extraction.get_provider", lambda: SimpleNamespace(resolve_model=lambda *a, **kw: connection))
        monkeypatch.setattr("instructor.from_openai", lambda raw, **kw: raw)
        client, _, _ = _build_instructor_client(provider="local", model="synthetic", base_url=None, api_key=None)
        invoke = lambda: client.chat.completions.create(model="synthetic", messages=[{"role": "user", "content": "虚构"}])
    else:
        from src.config import settings
        monkeypatch.setattr(settings, "llm_base_url", "http://provider.test/v1")
        monkeypatch.setattr(settings, "llm_api_key", "synthetic-key")
        provider = object.__new__(LLMProvider)
        provider._llm = provider._openai = provider._async_openai = None
        if kind == "legacy-chat":
            client = provider.llm
            client.max_retries = 0
            invoke = lambda: client.invoke("虚构")
        elif kind == "legacy-sync":
            client = provider.openai
            client.max_retries = 0
            invoke = lambda: client.chat.completions.create(model="synthetic", messages=[{"role": "user", "content": "虚构"}])
        else:
            client = provider.async_openai
            client.max_retries = 0
            invoke = lambda: asyncio.run(client.chat.completions.create(model="synthetic", messages=[{"role": "user", "content": "虚构"}]))
    try:
        with execution.execution_context(auth):
            invoke()
        assert sent == ["POST"]
        store.update_user(owner, disabled=True)
        store.update_user(owner, disabled=False)
        with execution.execution_context(auth):
            try:
                invoke()
            except Exception as error:
                # SDK 可以封装权限异常，但底层拒绝必须保留且不发包。
                while error is not None and not isinstance(error, execution.ExecutionDenied):
                    error = error.__cause__
                assert isinstance(error, execution.ExecutionDenied)
        assert sent == ["POST"]
        with execution.execution_context(store.capture_account_execution(owner)):
            invoke()
        assert sent == ["POST", "POST"]
        current = store.capture_account_execution(owner)
        stop_on_response = True
        with execution.execution_context(current), pytest.raises(Exception) as caught:
            invoke()
        error = caught.value
        while error is not None and not isinstance(error, execution.ExecutionDenied):
            error = error.__cause__
        assert isinstance(error, execution.ExecutionDenied)
        assert sent == ["POST", "POST", "POST"]
    finally:
        for transport in clients:
            if isinstance(transport, httpx.AsyncClient):
                asyncio.run(transport.aclose())
            else:
                transport.close()
