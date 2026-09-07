"""临时文件库验证页面 API 与分享入口的 Owner 边界。"""
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.routes import templates_routes, lessons_routes
from src.memory import templates, lessons


@pytest.fixture
def library_api(tmp_path, monkeypatch):
    for module, attr in ((templates, "TEMPLATES_DIR"), (lessons, "LESSONS_DIR")):
        directory = tmp_path / attr
        directory.mkdir()
        monkeypatch.setattr(module, attr, directory)
        for slug, owner in (("a", "owner-a"), ("b", "owner-b"), ("legacy", None)):
            meta = {"title": f"私有标题-{slug}", "keywords": [f"私有关键词-{slug}"], "data_type": "article", "status": "active"}
            if owner:
                meta.update(owner_id=owner, scope="owner")
            (directory / f"{slug}.md").write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + f"---\n私有正文-{slug}\n", encoding="utf-8")
    templates._templates_cache.invalidate()
    lessons._lessons_cache.invalidate()
    actor = {"user_id": "owner-a", "role": "user"}
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: actor
    app.include_router(templates_routes.router)
    app.include_router(lessons_routes.router)
    with TestClient(app) as client:
        yield client, actor, tmp_path


@pytest.mark.parametrize("kind", ["templates", "lessons"])
@pytest.mark.parametrize("role", ["user", "admin", "super_admin"])
def test_library_api_reads_only_own_content_for_every_role(library_api, kind, role):
    client, actor, _ = library_api
    actor["role"] = role
    response = client.get(f"/api/{kind}")
    assert response.status_code == 200
    assert [entry["slug"] for entry in response.json()[kind]] == ["a"]
    assert "私有正文-b" not in response.text
    assert "私有标题-legacy" not in response.text


@pytest.mark.parametrize("kind", ["templates", "lessons"])
def test_only_owner_confirmed_copy_is_shared_and_can_be_withdrawn(library_api, kind):
    client, actor, root = library_api
    source = client.get(f"/api/{kind}").json()[kind][0]
    original = (root / ("TEMPLATES_DIR" if kind == "templates" else "LESSONS_DIR") / "a.md").read_bytes()
    payload = {"title": "通用方法", "keywords": ["方法"], "body": "不含个人信息的通用步骤", "data_type": "article", "expected_source_digest": source["content_digest"], "confirmed": True}
    assert client.post(f"/api/{kind}/a/share", json={**payload, "confirmed": False}).status_code == 422
    actor["user_id"] = "owner-b"
    assert client.post(f"/api/{kind}/a/share", json=payload).status_code == 404
    actor["user_id"] = "owner-a"
    shared = client.post(f"/api/{kind}/a/share", json=payload)
    assert shared.status_code == 200
    entry = shared.json()["entry"]
    assert entry["scope"] == "platform"
    assert entry["body"] == payload["body"]
    assert client.post(f"/api/{kind}/a/share", json=payload).json()["entry"]["slug"] == entry["slug"]
    assert (root / ("TEMPLATES_DIR" if kind == "templates" else "LESSONS_DIR") / "a.md").read_bytes() == original
    actor["user_id"] = "owner-b"
    listed = client.get(f"/api/{kind}").json()[kind]
    assert {e["slug"] for e in listed} == {"b", entry["slug"]}
    assert all("source_slug" not in e and "source_quality" not in e for e in listed)
    assert client.delete(f"/api/{kind}/{entry['slug']}").status_code == 404
    actor["user_id"] = "owner-a"
    assert client.delete(f"/api/{kind}/{entry['slug']}").status_code == 200
    assert [e["slug"] for e in client.get(f"/api/{kind}").json()[kind]] == ["a"]


def test_scan_log_does_not_publish_legacy_private_details(monkeypatch):
    from src.api.routes import library_dedup_routes
    from types import SimpleNamespace

    row = {"id": 1, "ran_at": "2026-09-07", "templates_scanned": 2, "templates_merged": 1, "lessons_scanned": 0, "lessons_merged": 0, "stale_drafts_deleted": 0, "details": '[{"title":"私有历史标题","body":"秘密"}]'}
    monkeypatch.setattr(library_dedup_routes, "get_store", lambda: SimpleNamespace(library_dedup_scan_log_recent=lambda **kw: [row]))
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "role": "admin"}
    app.include_router(library_dedup_routes.router)
    with TestClient(app) as client:
        response = client.get("/api/library-dedup-log")
    assert response.status_code == 200
    assert response.json()["log"][0]["templates_scanned"] == 2
    assert "私有历史标题" not in response.text
    assert "details" not in response.json()["log"][0]


@pytest.mark.parametrize("kind", ["templates", "lessons"])
def test_share_rejects_stale_source_and_client_owner_override(library_api, kind):
    client, _, root = library_api
    source = client.get(f"/api/{kind}").json()[kind][0]
    payload = {"title": "通用", "keywords": [], "body": "通用内容", "data_type": "article", "confirmed": True, "expected_source_digest": source["content_digest"]}
    assert client.post(f"/api/{kind}/a/share", json={**payload, "owner_id": "owner-b"}).status_code == 422
    path = root / ("TEMPLATES_DIR" if kind == "templates" else "LESSONS_DIR") / "a.md"
    path.write_text(path.read_text(encoding="utf-8").replace("私有正文-a", "改变后的本人原文"), encoding="utf-8")
    assert client.post(f"/api/{kind}/a/share", json=payload).status_code == 409
    assert not list(path.parent.glob("shared-*.md"))
    assert client.delete(f"/api/{kind}/legacy").status_code == 404
    assert (path.parent / "legacy.md").exists()
