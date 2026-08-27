# -*- coding: utf-8 -*-
"""语义 Harness 与 Delivery 持久化路径的迁移兼容测试。"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from src.api.store import WebUIStore
from src.semantic_harness.harness_models import HarnessRun
from src.services.managed_paths import ManagedPathCodec
from tests.database_migration_helpers import migrated_webui_database


def _codec(root: Path) -> ManagedPathCodec:
    return ManagedPathCodec(
        root,
        legacy_anchor=("data", "semantic-executions"),
    )


def _run(run_id: str = "run-a") -> HarnessRun:
    return HarnessRun(
        run_id=run_id,
        user_id="user-a",
        thread_id=run_id,
        logical_plan_id="plan-a",
        logical_plan_revision=1,
        logical_plan_hash="0" * 64,
        binding_revision=1,
        binding_hash="1" * 64,
        capability_id="table.duckdb",
        capability_version="1.0.0",
        runtime_profile="windows_local",
    )


class _Manifest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


def _manifest() -> _Manifest:
    return _Manifest(
        {
            "delivery_id": "delivery-a",
            "status": "succeeded",
            "outputs": [
                {
                    "output_id": "output-a",
                    "format": "csv",
                    "filename": "result.csv",
                    "media_type": "text/csv",
                    "sha256": "2" * 64,
                    "size_bytes": 5,
                    "qa": {"status": "pass"},
                }
            ],
        }
    )


def test_new_semantic_paths_are_managed_and_survive_root_move(
    tmp_path: Path,
) -> None:
    db_path = migrated_webui_database(tmp_path / "webui.db")
    old_root = tmp_path / "old" / "semantic-executions"
    artifact = old_root / "runs" / "run-a" / "result.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("id\n1\n", encoding="utf-8")
    output_dir = old_root / "deliveries" / "delivery-a"
    output_dir.mkdir(parents=True)
    (output_dir / "result.csv").write_text("id\n1\n", encoding="utf-8")

    store = WebUIStore(str(db_path), semantic_paths=_codec(old_root))
    store.create_semantic_harness_run(_run())
    saved_attempt = store.save_semantic_harness_attempt(
        "user-a",
        "run-a",
        attempt_id="attempt-a",
        node="execute",
        attempt_number=1,
        idempotency_key="attempt-key-a",
        input_hash="3" * 64,
        status="succeeded",
        artifact_paths={"result": str(artifact)},
    )
    store.save_semantic_delivery(
        user_id="user-a",
        run_id="run-a",
        manifest=_manifest(),
        output_dir=output_dir,
    )

    with sqlite3.connect(db_path) as conn:
        attempt_raw = conn.execute(
            "SELECT artifact_paths_json FROM semantic_harness_attempts"
        ).fetchone()[0]
        delivery_raw = conn.execute(
            "SELECT output_dir FROM semantic_delivery_runs"
        ).fetchone()[0]
        output_raw = conn.execute(
            "SELECT file_path FROM semantic_delivery_outputs"
        ).fetchone()[0]
    assert json.loads(attempt_raw) == {
        "result": "managed:v1/runs/run-a/result.csv"
    }
    assert delivery_raw == "managed:v1/deliveries/delivery-a"
    assert output_raw == "managed:v1/deliveries/delivery-a/result.csv"
    assert Path(saved_attempt["artifact_paths"]["result"]) == artifact.resolve()

    new_root = tmp_path / "new" / "semantic-executions"
    new_root.parent.mkdir(parents=True)
    shutil.move(str(old_root), str(new_root))
    moved_store = WebUIStore(str(db_path), semantic_paths=_codec(new_root))

    moved_attempt = moved_store.get_semantic_harness_attempt_by_key(
        "user-a", "attempt-key-a"
    )
    moved_output = moved_store.get_semantic_delivery_output(
        "user-a", "output-a"
    )
    assert moved_attempt is not None
    assert Path(moved_attempt["artifact_paths"]["result"]).is_file()
    assert moved_output is not None
    assert Path(moved_output["file_path"]).is_file()


def test_legacy_semantic_paths_map_without_rewriting_database(
    tmp_path: Path,
) -> None:
    db_path = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "current" / "semantic-executions"
    artifact = root / "runs" / "run-a" / "result.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("id\n1\n", encoding="utf-8")
    output_dir = root / "deliveries" / "delivery-a"
    output_dir.mkdir(parents=True)
    (output_dir / "result.csv").write_text("id\n1\n", encoding="utf-8")
    store = WebUIStore(str(db_path), semantic_paths=_codec(root))
    store.create_semantic_harness_run(_run())
    store.save_semantic_harness_attempt(
        "user-a",
        "run-a",
        attempt_id="attempt-a",
        node="execute",
        attempt_number=1,
        idempotency_key="attempt-key-a",
        input_hash="3" * 64,
        status="succeeded",
        artifact_paths={"result": str(artifact)},
    )
    store.save_semantic_delivery(
        user_id="user-a",
        run_id="run-a",
        manifest=_manifest(),
        output_dir=output_dir,
    )
    legacy_attempt = json.dumps(
        {
            "result": (
                r"D:\old\data\semantic-executions\runs\run-a\result.csv"
            )
        }
    )
    legacy_output = (
        r"D:\old\data\semantic-executions\deliveries\delivery-a\result.csv"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE semantic_harness_attempts SET artifact_paths_json=?",
            (legacy_attempt,),
        )
        conn.execute(
            "UPDATE semantic_delivery_outputs SET file_path=?",
            (legacy_output,),
        )
        conn.commit()

    attempt = store.get_semantic_harness_attempt_by_key(
        "user-a", "attempt-key-a"
    )
    output = store.get_semantic_delivery_output("user-a", "output-a")

    assert attempt is not None
    assert Path(attempt["artifact_paths"]["result"]) == artifact.resolve()
    assert output is not None
    assert Path(output["file_path"]) == (output_dir / "result.csv").resolve()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT artifact_paths_json FROM semantic_harness_attempts"
        ).fetchone()[0] == legacy_attempt
        assert conn.execute(
            "SELECT file_path FROM semantic_delivery_outputs"
        ).fetchone()[0] == legacy_output
