"""只读盘点历史任务与当前合同兼容性，不把状态或合同通过算成业务重放。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

from pydantic import ValidationError

from src.data_prep.models import DataPrepTaskSpec
from src.data_prep.document_models import ExtractionSpec
from src.semantic_harness.models import SemanticTaskPlan


def check_contract(raw: str | None, model) -> dict:
    if raw is None:
        return {"status": "blocked", "reason": "missing_frozen_contract"}
    started = time.perf_counter()
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        value = model.model_validate_json(raw)
        result = {"status": "passed", "family": getattr(value, "task_family", None)}
    except ValidationError as exc:
        # 不输出input/msg：其中可能包含历史正文、来源路径或凭证引用。
        result = {"status": "failed", "reason": "current_contract_incompatible",
                  "error_types": sorted({item["type"] for item in exc.errors()})}
    return {**result, "input_sha256": fingerprint,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3)}


def check_data_prep_contract(raw: str | None) -> dict:
    try:
        payload = json.loads(raw) if raw is not None else None
    except (TypeError, ValueError):
        return {"status": "failed", "reason": "invalid_json"}
    if isinstance(payload, dict) and payload.get("task_type") == "document_extraction":
        field = "effective_extraction_spec" if payload.get("effective_extraction_spec") is not None else "extraction_spec"
        nested = payload.get(field)
        result = check_contract(json.dumps(nested, ensure_ascii=False) if nested is not None else None, ExtractionSpec)
        return {**result, "contract_type": "ExtractionSpec", "contract_field": field}
    if isinstance(payload, dict) and payload.get("task_type"):
        return {"status": "blocked", "reason": "unsupported_historical_contract_type"}
    return {**check_contract(raw, DataPrepTaskSpec), "contract_type": "DataPrepTaskSpec"}


def runtime_inventory(connection, tables) -> dict:
    """只按同Owner、同Run关联元数据；不从历史状态推断执行通过。"""
    required = {"agentic_runtime_runs", "formal_delivery_outputs", "semantic_delivery_outputs"}
    outputs = {}
    for table, owner_column in (("formal_delivery_outputs", "owner_id"), ("semantic_delivery_outputs", "user_id")):
        if table not in tables:
            continue
        for row in connection.execute(f"SELECT output_id,{owner_column},run_id FROM {table}"):
            outputs.setdefault((row[owner_column], row["run_id"]), set()).add(row["output_id"])
    rows = []
    matched = set()
    if "agentic_runtime_runs" in tables:
        for row in connection.execute("SELECT user_id,task_id,revision,run_id,status,created_at,updated_at FROM agentic_runtime_runs ORDER BY task_id,revision,user_id"):
            key = (row["user_id"], row["run_id"])
            ids = sorted(outputs.get(key, ())) if row["run_id"] else []
            if ids:
                matched.add(key)
            rows.append({"task_id": row["task_id"], "revision": row["revision"], "run_id": row["run_id"],
                         "owner_sha256": hashlib.sha256(row["user_id"].encode("utf-8")).hexdigest(),
                         "historical_status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                         "output_ids": ids, "execution": "not_run", "business_correctness": "unverified"})
    return {"run_count": len(rows), "runs": rows, "absent_tables": sorted(required - tables),
            "historical_status_counts": dict(Counter(row["historical_status"] for row in rows)),
            "unmatched_output_count": sum(len(ids) for key, ids in outputs.items() if key not in matched),
            "unmatched_outputs": [{"owner_sha256": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
                                   "run_id": run_id, "output_ids": sorted(ids), "reason": "no_same_owner_run_match"}
                                  for (owner, run_id), ids in outputs.items() if (owner, run_id) not in matched],
            "limitations": ["created_at/updated_at是记录时间，不能冒充实际执行耗时", "不同运行域ID不猜测关联；未匹配输出不等于孤儿或损坏", "关联不代表制品完整性或业务正确性"]}


def audit_database(path: Path) -> dict:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    entries = []
    absent = []
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        inventory = runtime_inventory(connection, tables)
        # 固定表与列允许列表；不读配置、密钥、聊天正文或文件内容。
        definitions = [
            ("data_prep_tasks", "task_id,user_id,status,spec_json", "spec_json", DataPrepTaskSpec),
            ("semantic_plan_revisions", "plan_id,task_id,user_id,revision,status,plan_json", "plan_json", SemanticTaskPlan),
            ("semantic_workspace_tasks", "task_id,user_id,status,active_revision,deleted_at", None, None),
        ]
        for table, columns, contract_column, model in definitions:
            if table not in tables:
                absent.append(table)
                continue
            for row in connection.execute(f"SELECT {columns} FROM {table}"):
                revision = row["revision"] if "revision" in row.keys() else row["active_revision"] if "active_revision" in row.keys() else None
                owner_hash = hashlib.sha256(row["user_id"].encode("utf-8")).hexdigest()
                if table == "data_prep_tasks":
                    contract = check_data_prep_contract(row[contract_column])
                elif contract_column:
                    contract = check_contract(row[contract_column], model)
                else:
                    contract = {"status": "not_run", "reason": "workspace_requires_frozen_runtime_source_and_delivery_validation"}
                entries.append({"entry_id": f"{table}/{owner_hash}/{row['task_id']}/{row['plan_id'] if 'plan_id' in row.keys() else ''}/{revision}",
                                "table": table, "task_id": row["task_id"], "owner_sha256": owner_hash,
                                "revision": revision, "historical_status": row["status"], "contract": contract,
                                "execution": "not_run", "business_correctness": "unverified",
                                "replay_blockers": ["independent_expected_output_missing", "isolated_execution_not_prepared"]})
    finally:
        connection.close()
    entries.sort(key=lambda row: row["entry_id"])
    return {"method": "read_only_snapshot_contract_validation", "entries": entries, "absent_tables": absent, "runtime_inventory": inventory,
            "counts": dict(Counter(row["contract"]["status"] for row in entries)),
            "task_counts": dict(Counter(row["table"] for row in entries)),
            "business_success_rate": None,
            "limitations": ["计划修订与任务不是同一分母", "不包含scheduler/Conductor checkpoint重放", "尚未核验每项来源及交付产物"]}


def write_report(database: Path, output: Path, report: dict, *, overwrite: bool = True) -> None:
    # 只读连接不能保护随后写报告的路径；同时保护数据库别名及活动日志文件。
    protected = [database.resolve()]
    protected.extend(Path(str(protected[0]) + suffix) for suffix in ("-wal", "-shm", "-journal"))
    for path in protected:
        if output.resolve() == path or (output.exists() and path.exists() and output.samefile(path)):
            raise ValueError("报告路径不能覆盖数据库或其日志文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 排他新建用于真实制品重放，防止目录外硬链接覆盖被评测的历史文件。
    with output.open("w" if overwrite else "x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_database(args.database)
    write_report(args.database, args.output, report)
    print(json.dumps({"counts": report["counts"], "task_counts": report["task_counts"], "business_success_rate": None}))
    return int(bool(report["absent_tables"]) or report["counts"].get("failed", 0) > 0)


if __name__ == "__main__":
    raise SystemExit(main())
