"""无网址公开搜索的输入授权边界。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.auth import get_execution_user
from src.api.routes import source_acquisition as routes


def _client(monkeypatch):
    calls = []

    class Service:
        async def acquire(self, **kwargs):
            calls.append(kwargs)
            return {"attempt_id": "search-attempt", "status": "acquiring"}

    monkeypatch.setattr(routes, "get_source_acquisition_service", Service)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_execution_user] = lambda: {"user_id": "owner-a"}
    return TestClient(app), calls


def test_query_without_url_reaches_existing_acquisition(monkeypatch):
    client, calls = _client(monkeypatch)
    response = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "query-one"},
        json={"query": "公开产品手册", "purpose": "比较公开功能", "time_range": "month", "domains": ["example.com"]},
    )
    assert response.status_code == 202, response.text
    assert len(calls) == 1
    request = calls[0]["request"]
    assert calls[0]["owner_id"] == "owner-a"
    assert calls[0]["idempotency_key"] == "query-one"
    assert request.query == "公开产品手册"
    assert request.url == ""
    assert request.scope_kind == "public_search"
    assert request.time_range == "month"
    assert request.domains == ("example.com",)
    assert request.page_limit == 10


@pytest.mark.parametrize("source", [
    {},
    {"url": "https://example.com", "query": "公开资料"},
    {"query": "公开资料", "allowed_scope": "same_site"},
    {"url": "https://example.com", "allowed_scope": "public_search"},
    {"url": "https://example.com", "domains": ["other.example"]},
    {"url": "https://example.com", "time_range": "day"},
    {"query": "公开资料", "page_limit": 21},
])
def test_ambiguous_or_expanded_scope_rejected_before_acquisition(monkeypatch, source):
    client, calls = _client(monkeypatch)
    response = client.post(
        "/api/semantic-workspace/source-acquisitions",
        headers={"Idempotency-Key": "invalid-query"},
        json={"purpose": "公开资料", **source},
    )
    assert response.status_code == 422
    assert calls == []
