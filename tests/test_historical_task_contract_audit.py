"""历史盘点不得改写数据库或把完成状态当成重放成功。"""
import hashlib
import json
import sqlite3
import os
import pytest

from scripts.audit_historical_task_contracts import audit_database, check_contract, check_data_prep_contract, write_report
from src.semantic_harness.models import SemanticTaskPlan


def test_audit_is_read_only_and_reports_incomplete_coverage(tmp_path):
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE data_prep_tasks (task_id TEXT,user_id TEXT,status TEXT,spec_json TEXT)")
        connection.execute("INSERT INTO data_prep_tasks VALUES (?,?,?,?)",
                           ("task", "private-owner", "COMPLETED", json.dumps({"intent": "synthetic-private-body"})))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = audit_database(database)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert report["counts"] == {"passed": 1}
    assert report["entries"][0]["execution"] == "not_run"
    assert report["business_success_rate"] is None
    assert report["absent_tables"] == ["semantic_plan_revisions", "semantic_workspace_tasks"]
    encoded = json.dumps(report)
    assert "synthetic-private-body" not in encoded and "private-owner" not in encoded


def test_invalid_or_missing_contract_never_becomes_success():
    assert check_contract(None, SemanticTaskPlan)["status"] == "blocked"
    result = check_contract('{"task_family":"private-invalid-value"}', SemanticTaskPlan)
    assert result["status"] == "failed"
    assert "private-invalid-value" not in json.dumps(result)


def test_document_extraction_uses_its_real_discriminator():
    extraction = {"goal": {"objective": "文档"}, "discovery": {"artifact_ids": ["fixture"]},
                  "result_contract": {"shape": "document"}}
    result = check_data_prep_contract(json.dumps({"task_type": "document_extraction", "extraction_spec": extraction}))
    assert result["status"] == "passed"
    assert result["contract_type"] == "ExtractionSpec"
    assert check_data_prep_contract('{"task_type":"unknown"}')["status"] == "blocked"
    assert check_data_prep_contract('{"task_type":"document_extraction"}')["status"] == "blocked"


@pytest.mark.parametrize("alias", ["same", "hardlink", "wal", "shm", "journal"])
def test_report_cannot_overwrite_database_or_journal(tmp_path, alias):
    database = tmp_path / "history.db"
    database.write_bytes(b"protected-database")
    output = database
    if alias == "hardlink":
        output = tmp_path / "alias.json"
        os.link(database, output)
    elif alias not in {"same", "hardlink"}:
        output = tmp_path / f"history.db-{alias}"
        output.write_bytes(b"protected-journal")
    before = output.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        write_report(database, output, {"test": True})
    assert output.read_bytes() == before
    assert database.read_bytes() == b"protected-database"


def test_runtime_inventory_keeps_owner_boundary_and_unmatched_outputs(tmp_path):
    database=tmp_path/"runs.db"
    with sqlite3.connect(database) as c:
        c.execute("CREATE TABLE agentic_runtime_runs(user_id TEXT,task_id TEXT,revision INTEGER,run_id TEXT,status TEXT,created_at TEXT,updated_at TEXT)")
        c.executemany("INSERT INTO agentic_runtime_runs VALUES(?,?,?,?,?,?,?)",[("owner-a","task-a",1,"same-run","completed","2026-01-01","2026-01-02"),("owner-b","task-b",2,"same-run","failed","2026-01-01","2026-01-03")])
        c.execute("CREATE TABLE formal_delivery_outputs(output_id TEXT,owner_id TEXT,run_id TEXT)")
        c.executemany("INSERT INTO formal_delivery_outputs VALUES(?,?,?)",[("out-a","owner-a","same-run"),("out-x","owner-x","same-run")])
    before=database.read_bytes()
    inventory=audit_database(database)["runtime_inventory"]
    assert database.read_bytes()==before
    assert inventory["run_count"]==2
    assert inventory["runs"][0]["output_ids"]==["out-a"]
    assert inventory["runs"][1]["output_ids"]==[]
    assert inventory["unmatched_output_count"]==1
    assert inventory["unmatched_outputs"] == [{"owner_sha256": hashlib.sha256(b"owner-x").hexdigest(),
        "run_id": "same-run", "output_ids": ["out-x"], "reason": "no_same_owner_run_match"}]
    assert all(row["execution"]=="not_run" for row in inventory["runs"])
    assert "owner-a" not in json.dumps(inventory)
