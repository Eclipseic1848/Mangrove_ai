"""通过巡检启动和库接口验证新工作台草稿的生命周期。"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.auth import get_current_user
from src.api.library_dedup_scanner import LibraryDedupScanner
from src.api.routes import library_dedup_routes, templates_routes, lessons_routes
from src.config.settings import settings
from src.memory import templates, lessons
from src.timezone import now


@pytest.mark.parametrize("model_fails", [False, True])
def test_patrol_semantic_merge_reports_only_persisted_results(tmp_path, monkeypatch, model_fails):
    from src.memory import embeddings
    from src.api import library_dedup_scanner
    monkeypatch.setattr(settings, "library_dedup_scan_enabled", True)
    monkeypatch.setattr(settings, "library_dedup_scan_max_merges_per_run", 1)
    monkeypatch.setattr(settings, "embedding_enabled", True)
    monkeypatch.setattr(library_dedup_scanner, "_BETWEEN_ITEM_SECONDS", 0)
    monkeypatch.setattr(embeddings, "is_rerank_configured", lambda: True)
    monkeypatch.setattr(embeddings, "embed_texts_with_model", lambda texts: ("test", [[1.0, 0.0] for _ in texts]))
    monkeypatch.setattr(embeddings, "rerank_scores", lambda query, texts, **kwargs: [1.0 for _ in texts])

    model_requests = []

    async def model(messages, **kwargs):
        model_requests.append(messages[-1]["content"])
        if model_fails:
            raise RuntimeError("模拟模型不可用")
        return '{"title":"融合方法","keywords":["核对"],"body":"保留核对与异常标记"}'

    originals = {}
    for module, field in ((templates, "TEMPLATES_DIR"), (lessons, "LESSONS_DIR")):
        directory = tmp_path / field
        directory.mkdir()
        monkeypatch.setattr(module, field, directory)
        monkeypatch.setattr(module, "achat", model)
        for slug, owner, count in [("survivor", "a", 4), ("loser", "a", 1), ("foreign", "b", 1)]:
            meta = dict(owner_id=owner, scope="owner", title=slug, status="draft",
                        data_type="workspace_table", keywords=["核对"], created_at=now().isoformat(),
                        uses=count, occurrences=count, helped_avoid=0)
            body = "其他用户秘密" if owner == "b" else "核对与异常标记"
            path = directory / f"{slug}.md"
            path.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n" + body + "\n", encoding="utf-8")
            originals[path] = path.read_bytes()
    rows = []
    store = SimpleNamespace(
        library_dedup_scan_log_add=lambda **row: rows.append({"id": 1, "ran_at": now().isoformat(), **row}),
        library_dedup_scan_log_recent=lambda **_: rows,
    )
    monkeypatch.setattr(auth, "get_store", lambda: store)
    monkeypatch.setattr(library_dedup_routes, "get_store", lambda: store)

    async def run():
        scanner = LibraryDedupScanner()
        scanner.start()
        try:
            async with asyncio.timeout(5):
                while not rows:
                    await asyncio.sleep(0.01)
        finally:
            await scanner.stop()
    asyncio.run(run())
    assert len(model_requests) >= 2
    assert all("其他用户秘密" not in request for request in model_requests)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "a", "role": "admin"}
    for router in (templates_routes.router, lessons_routes.router, library_dedup_routes.router):
        app.include_router(router)
    with TestClient(app) as client:
        for kind in ("templates", "lessons"):
            entries = client.get(f"/api/{kind}").json()[kind]
            assert {entry["slug"] for entry in entries} == ({"survivor", "loser"} if model_fails else {"survivor"})
            if not model_fails:
                assert entries[0]["body"].strip() == "保留核对与异常标记"
        report = client.get("/api/library-dedup-log").json()["log"][0]
        assert report["templates_merged"] == (0 if model_fails else 1)
        assert report["lessons_merged"] == (0 if model_fails else 1)
    for path, original in originals.items():
        if model_fails or path.stem == "foreign":
            assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["templates", "lessons"])
def test_workspace_merge_rejects_other_owner_and_changed_snapshot(tmp_path, monkeypatch, kind):
    module = templates if kind == "templates" else lessons
    monkeypatch.setattr(module, "TEMPLATES_DIR" if kind == "templates" else "LESSONS_DIR", tmp_path)
    load = module.load_templates if kind == "templates" else module.load_lessons
    apply = module.apply_patrol_merge if kind == "templates" else module.apply_patrol_merge_lesson
    for slug, owner in [("survivor", "a"), ("loser", "a"), ("foreign", "b")]:
        meta = dict(owner_id=owner, scope="owner", title=slug, status="draft",
                    data_type="workspace_table", keywords=["核对"], created_at=now().isoformat(),
                    uses=0, occurrences=1, helped_avoid=0)
        (tmp_path / f"{slug}.md").write_text(
            "---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n原始正文\n", encoding="utf-8")
    entries = {entry["slug"]: entry for entry in load(owner_id="a")}
    foreign = load(owner_id="b")[0]
    original = {path: path.read_bytes() for path in tmp_path.glob("*.md")}
    fused = dict(title="融合", keywords=["核对"], body="融合正文")
    assert not apply("survivor", fused, owner_id="a",
                     expected_source_digest=entries["survivor"]["content_digest"],
                     loser_slug="foreign", expected_loser_digest=foreign["content_digest"])
    assert all(path.read_bytes() == data for path, data in original.items())
    changed = tmp_path / "loser.md"
    changed.write_text(changed.read_text(encoding="utf-8").replace("原始正文", "用户新改正文"), encoding="utf-8")
    assert not apply("survivor", fused, owner_id="a",
                     expected_source_digest=entries["survivor"]["content_digest"],
                     loser_slug="loser", expected_loser_digest=entries["loser"]["content_digest"])
    assert (tmp_path / "survivor.md").read_bytes() == original[tmp_path / "survivor.md"]
    assert "用户新改正文" in changed.read_text(encoding="utf-8")


@pytest.mark.parametrize("data_type", ["workspace_document", "workspace_table", "workspace_web", "workspace_mixed"])
def test_patrol_cleans_only_expired_owned_workspace_drafts(tmp_path, monkeypatch, data_type):
    monkeypatch.setattr(settings, "library_dedup_scan_enabled", True)
    monkeypatch.setattr(settings, "library_stale_draft_days", 30)
    # 本例只验证停滞清理，禁用语义合并以避免任何模型或网络请求。
    monkeypatch.setattr(settings, "library_dedup_scan_max_merges_per_run", 0)
    originals = {}
    for module, field in ((templates, "TEMPLATES_DIR"), (lessons, "LESSONS_DIR")):
        directory = tmp_path / field
        directory.mkdir()
        monkeypatch.setattr(module, field, directory)
        for slug, owner, status, created in [
            ("expired", "a", "draft", (now() - timedelta(days=60)).isoformat()),
            ("fresh", "b", "draft", now().isoformat()),
            ("active", "a", "active", (now() - timedelta(days=60)).isoformat()),
            ("unknown-time", "a", "draft", "2000-01-01T00:00:00"),
            ("unknown-owner", None, "draft", "2000-01-01T00:00:00+08:00"),
        ]:
            path = directory / f"{slug}.md"
            meta = dict(owner_id=owner, scope="owner", title=slug, status=status,
                        data_type=data_type, keywords=["核对"], created_at=created,
                        uses=0, verified_uses=0, occurrences=1, helped_avoid=0)
            path.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n合成经验正文\n", encoding="utf-8")
            if slug != "expired":
                originals[path] = path.read_bytes()

    rows = []
    store = SimpleNamespace(
        library_dedup_scan_log_add=lambda **row: rows.append({"id": 1, "ran_at": now().isoformat(), **row}),
        library_dedup_scan_log_recent=lambda **_: rows,
    )
    monkeypatch.setattr(auth, "get_store", lambda: store)
    monkeypatch.setattr(library_dedup_routes, "get_store", lambda: store)

    async def run_scan():
        scanner = LibraryDedupScanner()
        scanner.start()
        try:
            async with asyncio.timeout(5):
                while not rows:
                    await asyncio.sleep(0.01)
        finally:
            await scanner.stop()

    asyncio.run(run_scan())
    app = FastAPI()
    actor = {"user_id": "a", "role": "admin"}
    app.dependency_overrides[get_current_user] = lambda: actor
    for router in (templates_routes.router, lessons_routes.router, library_dedup_routes.router):
        app.include_router(router)
    with TestClient(app) as client:
        for kind in ("templates", "lessons"):
            response = client.get(f"/api/{kind}")
            assert response.status_code == 200
            assert {entry["slug"] for entry in response.json()[kind]} == {"active", "unknown-time"}
        report = client.get("/api/library-dedup-log")
        assert report.status_code == 200
        assert report.json()["log"][0]["stale_drafts_deleted"] == 2
        assert "合成经验正文" not in report.text
        assert "details" not in report.json()["log"][0]
        actor["role"] = "user"
        assert client.get("/api/library-dedup-log").status_code == 403
    for path, original in originals.items():
        assert path.read_bytes() == original
