"""首次隔离复用既有 Runtime 公共执行门，不伪造运行封存。"""
from contextlib import closing
import json
import sqlite3

import pytest

from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.deployment_quarantine import QuarantinedExecutionError, SETTING_KEY
from tests.test_runtime_run_seals import unknown_run, run_seal_database_template


@pytest.mark.parametrize("revision", [1, 2])
def test_runtime_boundary_blocks_all_revisions_of_quarantined_task_only(unknown_run, revision):
    database = unknown_run["db"]
    manifest = {
        "version": 1, "approval_reference": "synthetic-approved-first-cutover", "actor_id": "operator",
        "accepted_risk": "first_cutover_unknown_interruption",
        "targets": [{"owner_id": "owner", "kind": "task", "resource_id": "task", "generation": 2}],
        "records": [],
    }
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE app_settings SET value=? WHERE key=?", (json.dumps(manifest), SETTING_KEY))
    repository = AgenticRuntimeRepository(database)
    with pytest.raises(QuarantinedExecutionError):
        repository.require_unsealed("owner", "task", revision)
    repository.require_unsealed("owner", "new-independent-task", 1)
    repository.require_unsealed("other-owner", "task", revision)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM runtime_run_seals").fetchone()[0] == 0
        assert "MODEL_OUTCOME_UNKNOWN" in connection.execute("SELECT failure_json FROM agentic_runtime_runs").fetchone()[0]
