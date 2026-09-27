"""真实清洗重放可比较旧产物，但不能写回历史数据或虚报业务正确。"""
import asyncio
import hashlib
import json
import os
import sqlite3

import pytest

from scripts.replay_historical_cleaning import replay
from scripts.audit_historical_task_contracts import write_report
from src.data_prep.models import DataPrepTaskSpec, SourceSpec, SourceType


@pytest.mark.parametrize("scenario,status", [("match", "passed"), ("different", "failed"), ("tampered", "blocked"), ("escape", "blocked")])
def test_replay_real_node_preserves_history(tmp_path, scenario, status):
    artifacts = tmp_path / "artifacts"
    task = artifacts / "history"
    (task / "parsed").mkdir(parents=True)
    (task / "parsed/part-00000.jsonl").write_text(json.dumps({"record_id": "row-1", "data": {"amount": 3}, "meta": {}}) + "\n", encoding="utf-8")
    golden = json.dumps({"amount": 4 if scenario == "different" else 3, "_record_id": "row-1"}).encode("utf-8")
    (task / "expected.jsonl").write_bytes(golden)
    (task / "manifest.json").write_text(json.dumps({"outputs": [{"format": "jsonl", "path": "history/expected.jsonl", "sha256": "invalid" if scenario == "tampered" else hashlib.sha256(golden).hexdigest()}]}), encoding="utf-8")
    spec = DataPrepTaskSpec(intent="保留数据", sources=[SourceSpec(source_id="source", source_type=SourceType.UPLOAD_FILE, locator="frozen")])
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE data_prep_tasks(task_id TEXT,spec_json TEXT,manifest_path TEXT)")
        connection.execute("INSERT INTO data_prep_tasks VALUES(?,?,?)", (str(task) if scenario == "escape" else "history", spec.model_dump_json(), "history/manifest.json"))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = asyncio.run(replay(database, artifacts))
    assert report["counts"] == {status: 1}
    assert report["business_success_rate"] is None
    assert report["entries"][0]["business_correctness"] == "unverified"
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_report_refuses_external_hardlink_to_historical_output(tmp_path):
    original = tmp_path / "historical.jsonl"
    original.write_text('原始产物\n', encoding="utf-8")
    alias = tmp_path / "report.json"
    os.link(original, alias)
    with pytest.raises(FileExistsError):
        write_report(tmp_path / "database.db", alias, {}, overwrite=False)
    assert original.read_text(encoding="utf-8") == '原始产物\n'


@pytest.mark.parametrize("scenario,status", [("match", "passed"), ("tampered", "blocked"), ("missing", "blocked"), ("missing_multiple", "blocked"), ("different", "failed"), ("xlsx_different", "failed"), ("coverage", "passed"), ("coverage_tampered", "blocked")])
def test_document_replay_preserves_history_and_checks_quality(tmp_path, scenario, status):
    from src.data_prep.artifact_store import ArtifactStore
    from src.data_prep.document_models import DiscoverySpec, ExtractedField, ExtractionFieldSpec, ExtractionSpec, TaskGoal
    from src.services.document_delivery import write_document_delivery

    artifacts = tmp_path / "artifacts"
    store = ArtifactStore(str(artifacts))
    raw = store.write_raw("history", "source", b"synthetic", uri="synthetic.txt", media_type="text/plain")
    spec = ExtractionSpec(goal=TaskGoal(objective="提取编号"), discovery=DiscoverySpec(artifact_ids=[raw.artifact_id]), fields=[ExtractionFieldSpec(name="id")])
    result = write_document_delivery(store, "history", spec=spec, raw_artifacts=[raw],
        fields=[ExtractedField(name="id", value=None, status="not_found")], review_tasks=[],
        coverage={"elements_total": 3, "elements_processed": 2} if scenario.startswith("coverage") else None)
    manifest_path = artifacts / result.manifest_path
    manifest = json.loads(manifest_path.read_bytes())
    if scenario.startswith("coverage"):
        saved = next(row for row in manifest["artifacts"] if row["kind"] == "extraction_coverage")
        assert json.loads((artifacts / saved["path"]).read_bytes()) == {"elements_total": 3, "elements_processed": 2}
        if scenario == "coverage_tampered":
            saved["sha256"] = "invalid"
    item = next(row for row in manifest["artifacts"] if row["kind"] == "quality")
    if scenario == "tampered":
        item["sha256"] = "invalid"
    elif scenario.startswith("missing"):
        missing = ["extracted_documents"] if scenario == "missing" else [
            "extracted_records", "extracted_tables", "extracted_documents", "lineage"]
        manifest["artifacts"] = [row for row in manifest["artifacts"] if row["kind"] not in missing]
    elif scenario == "different":
        quality_path = artifacts / item["path"]
        quality = json.loads(quality_path.read_bytes())
        quality["overall"] = "pass" if quality["overall"] != "pass" else "fail"
        quality_path.write_text(json.dumps(quality), encoding="utf-8")
        item["sha256"] = hashlib.sha256(quality_path.read_bytes()).hexdigest()
    if scenario == "xlsx_different":
        from openpyxl import load_workbook
        xlsx = next(row for row in manifest["outputs"] if row["format"] == "xlsx")
        path = artifacts / xlsx["path"]
        workbook = load_workbook(path)
        workbook.worksheets[0]["A1"] = "错误的表头"
        workbook.save(path)
        workbook.close()
        for row in manifest["outputs"] + manifest["artifacts"]:
            if row["path"] == xlsx["path"]:
                row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE data_prep_tasks(task_id TEXT,spec_json TEXT,manifest_path TEXT)")
        connection.execute("INSERT INTO data_prep_tasks VALUES(?,?,?)", ("history", json.dumps({
            "task_type": "document_extraction", "raw_artifacts": [raw.model_dump(mode="json")]}), result.manifest_path))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = asyncio.run(replay(database, artifacts))
    assert report["counts"] == {status: 1}
    assert report["business_success_rate"] is None
    if scenario.startswith("missing"):
        assert report["entries"][0]["missing_snapshot_kinds"] == missing
        assert report["entries"][0]["missing_snapshot_kind"] == missing[0]
        assert report["entries"][0]["business_correctness"] == "unverified"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
