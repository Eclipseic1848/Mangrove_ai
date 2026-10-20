"""迁移后的自动化产物读取兼容与归属边界。"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.config.settings import settings


@pytest.mark.parametrize("stored", [
    r"D:\old\downloads\run-1\report.md",
    "/old/app/downloads/run-1/report.md",
    "downloads/run-1/report.md",
    "managed:v1/run-1/report.md",
    "native",
])
def test_read_and_download_migrated_report_without_rewriting_rows(tmp_path, monkeypatch, stored):
    root = tmp_path / "artifacts"
    directory = root / "run-1"
    directory.mkdir(parents=True)
    report = directory / "report.md"
    report.write_text("# 历史报告\n内容保留", encoding="utf-8")
    data = directory / "data.json"
    data.write_text('{"preserved":true}', encoding="utf-8")
    if stored == "native":
        stored = str(report)
    row = {"report_path": stored, "json_path": stored.replace("report.md", "data.json"),
           "run_at": "2026-09-30T09:30:00", "success": 1}
    task = {"owner_user_id": "owner", "last_result": "report=" + stored,
            "last_run_at": row["run_at"], "last_success": 1}
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(root))
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: SimpleNamespace(
        get=lambda _: dict(task), get_run=lambda *args: dict(row)))
    assert tasks.task_report("plan", {"user_id": "owner"})["content"] == report.read_text(encoding="utf-8")
    assert tasks.run_report("plan", 1, {"user_id": "owner"})["content"] == report.read_text(encoding="utf-8")
    for kind, expected in [("report", report), ("json", data)]:
        assert tasks.run_download("plan", 1, kind, {"user_id": "owner"}).path == expected
    assert row["report_path"] == stored
    with pytest.raises(HTTPException) as error:
        tasks.run_report("plan", 1, {"user_id": "other"})
    assert error.value.status_code == 404


@pytest.mark.parametrize("stored", [
    "downloads/../outside.md", r"D:\old\downloads\..\outside.md",
    r"\\host\share\downloads\outside.md", "/etc/passwd",
    "managed:v1/../outside.md", "downloads/run-1/missing.md",
])
def test_unavailable_or_unsafe_artifacts_fail_closed(tmp_path, monkeypatch, stored):
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: SimpleNamespace(
        get=lambda _: {"owner_user_id": "owner"},
        get_run=lambda *args: {"report_path": stored, "json_path": stored}))
    for call in [lambda: tasks.run_report("plan", 1, {"user_id": "owner"}),
                 lambda: tasks.run_download("plan", 1, "json", {"user_id": "owner"})]:
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404


def test_invalid_download_kind_is_rejected(monkeypatch):
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: SimpleNamespace(
        get=lambda _: {"owner_user_id": "owner"}, get_run=lambda *args: {}))
    with pytest.raises(HTTPException) as error:
        tasks.run_download("plan", 1, "unknown", {"user_id": "owner"})
    assert error.value.status_code == 422
