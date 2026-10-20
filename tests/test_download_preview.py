"""采集快照预览与原下载共享权限，原件始终不改写。"""
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.api.routes import downloads
from src.data_prep.artifact_store import ArtifactStore


def make_client(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(downloads.router)
    app.dependency_overrides[downloads.get_current_user] = lambda: {"user_id": "owner"}
    quality = {}
    monkeypatch.setattr(downloads, "get_store", lambda: SimpleNamespace(
        user_owns_task=lambda owner, task: owner == "owner" and task == "mine",
        get_data_prep_task=lambda task: {"quality": quality},
    ))
    monkeypatch.setattr(downloads, "ArtifactStore", lambda: ArtifactStore(str(tmp_path)))
    root = tmp_path / "mine"
    root.mkdir()
    return TestClient(app), app, root, quality


def test_preview_joins_selected_benefits_without_changing_downloads(tmp_path, monkeypatch):
    client, _, root, _ = make_client(tmp_path, monkeypatch)
    snapshot = {"schema_version": "bank-benefits-v2", "outcome": {"status": "partial"}, "candidates": [
        {"status": "selected", "url": "https://example.com/note", "metadata": {"note_id": "n1"},
         "assessment": {"benefits": [{"benefit_name": "停车", "threshold": "达标", "usage_period": None,
                                      "evidence_status": "quoted_unreviewed"}]}},
        {"status": "excluded", "assessment": {"benefits": [{"benefit_name": "不应显示"}]}},
    ]}
    path = root / "data.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = client.get("/api/downloads/mine", params={"preview": "data.json"}).json()
    table = data["benefit_table"]
    assert table["total_rows"] == 1 and not table["truncated"]
    assert table["rows"][0]["note_url"] == "https://example.com/note"
    assert table["rows"][0]["benefit_name"] == "停车"
    assert table["rows"][0]["usage_period"] is None and table["rows"][0]["review_required"] is True
    assert data["outcome"]["status"] == "partial"
    assert client.get("/api/downloads/mine/data.json").content == path.read_bytes()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_preview_owner_path_and_quality_fail_closed(tmp_path, monkeypatch):
    client, app, root, quality = make_client(tmp_path, monkeypatch)
    (root / "data.json").write_text("{}", encoding="utf-8")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    for name in ("../outside.json", "missing.json"):
        assert client.get("/api/downloads/mine", params={"preview": name}).status_code == 404
    assert client.get("/api/downloads/other", params={"preview": "data.json"}).status_code == 404
    app.dependency_overrides[downloads.get_current_user] = lambda: {"user_id": "other"}
    assert client.get("/api/downloads/mine", params={"preview": "data.json"}).status_code == 404
    app.dependency_overrides[downloads.get_current_user] = lambda: {"user_id": "owner"}
    quality["overall"] = "fail"
    extraction = root / "extraction"
    extraction.mkdir()
    (extraction / "extracted_fields.json").write_text("{}", encoding="utf-8")
    for path in ("extraction/extracted_fields.json", "extraction/./extracted_fields.json", str(extraction / "extracted_fields.json")):
        assert client.get("/api/downloads/mine", params={"preview": path}).status_code == 409
    assert client.get("/api/downloads/mine/extraction/./extracted_fields.json").status_code == 409
    app.dependency_overrides.clear()
    assert client.get("/api/downloads/mine", params={"preview": "data.json"}).status_code == 403


def test_preview_limits_and_bad_formats_preserve_downloads(tmp_path, monkeypatch):
    client, _, root, _ = make_client(tmp_path, monkeypatch)
    path = root / "data.json"
    path.write_text(json.dumps({"text": "a" * 17000}), encoding="utf-8")
    data = client.get("/api/downloads/mine", params={"preview": "data.json"}).json()
    assert data["truncated"] and len(data["text"]) == 16000 and data["benefit_table"] is None
    path.write_text('{"schema_version":"bank-benefits-v2","candidates":42}', encoding="utf-8")
    assert client.get("/api/downloads/mine", params={"preview": "data.json"}).status_code == 422
    for value in ("NaN", "Infinity", "-Infinity", "1e309"):
        path.write_text('{"outcome":' + value + '}', encoding="utf-8")
        assert client.get("/api/downloads/mine", params={"preview": "data.json"}).status_code == 422
        assert client.get("/api/downloads/mine/data.json").status_code == 200
    path.write_bytes(b"\xff")
    assert client.get("/api/downloads/mine", params={"preview": "data.json"}).status_code == 422
    path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    assert client.get("/api/downloads/mine", params={"preview": "data.json"}).status_code == 422
    assert client.get("/api/downloads/mine/data.json").status_code == 200
    (root / "evidence.zip").write_bytes(b"original")
    assert client.get("/api/downloads/mine", params={"preview": "evidence.zip"}).status_code == 415
    assert client.get("/api/downloads/mine/evidence.zip").content == b"original"


def test_preview_workbook_windows_all_sheets_and_safe_errors(tmp_path, monkeypatch):
    client, _, root, _ = make_client(tmp_path, monkeypatch)
    book = Workbook()
    book.active.title = "笔记"
    book.create_sheet("权益")["A101"] = "末行"
    book.save(root / "data.xlsx")
    response = client.get("/api/downloads/mine", params={"preview": "data.xlsx", "sheet": 1, "row": 100})
    assert response.status_code == 200, response.text
    assert response.json()["sheets"] == ["笔记", "权益"]
    assert response.json()["cells"][0][0]["text"] == "末行"
    assert client.get("/api/downloads/mine", params={"preview": "data.xlsx", "row": -1}).status_code == 422
    (root / "data.xlsx").write_bytes(b"broken")
    response = client.get("/api/downloads/mine", params={"preview": "data.xlsx"})
    assert response.status_code == 422 and str(root) not in response.text


def test_benefit_preview_is_bounded_and_keeps_missing_fields_unknown(tmp_path, monkeypatch):
    client, _, root, _ = make_client(tmp_path, monkeypatch)
    snapshot = {"schema_version": "bank-benefits-v2", "candidates": [
        {"status": "selected", "assessment": {"benefits": [{"benefit_name": str(i)} for i in range(201)]}},
        {"status": "selected", "assessment": "invalid"},
    ]}
    (root / "data.json").write_text(json.dumps(snapshot), encoding="utf-8")
    data = client.get("/api/downloads/mine", params={"preview": "data.json"}).json()
    assert data["benefit_table"]["total_rows"] == 201
    assert len(data["benefit_table"]["rows"]) == 200 and data["benefit_table"]["truncated"]
    assert data["benefit_table"]["rows"][0]["note_url"] is None
    assert "usage_period" not in data["benefit_table"]["rows"][0]
