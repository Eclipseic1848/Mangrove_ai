from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.database_migrations import DatabaseTarget, apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = PROJECT_ROOT / "scripts" / "check_dev_database_migrations.py"


def _create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY)")


def test_preflight_reports_every_outdated_database_before_startup(tmp_path: Path) -> None:
    webui_database = tmp_path / "webui.db"
    scheduler_database = tmp_path / "scheduler.db"
    _create_legacy_database(webui_database)
    _create_legacy_database(scheduler_database)

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PREFLIGHT_SCRIPT),
            "--webui-database",
            str(webui_database),
            "--scheduler-database",
            str(scheduler_database),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 1
    assert "数据库迁移预检失败" in completed.stdout
    assert "webui: legacy -> webui_0009" in completed.stdout
    assert "scheduler: legacy -> scheduler_0001" in completed.stdout
    assert "python -m src.database_migrations apply --profile webui" in completed.stdout
    assert "python -m src.database_migrations apply --profile scheduler" in completed.stdout
    assert f"--expected-source-sha256 {hashlib.sha256(webui_database.read_bytes()).hexdigest()}" in completed.stdout
    assert f"--expected-source-sha256 {hashlib.sha256(scheduler_database.read_bytes()).hexdigest()}" in completed.stdout
    assert "<timestamp>" not in completed.stdout
    assert re.search(r"webui-before-startup-\d{8}-\d{6}\.db", completed.stdout)
    assert re.search(r"scheduler-before-startup-\d{8}-\d{6}\.db", completed.stdout)


def test_preflight_allows_startup_when_every_database_is_current(tmp_path: Path) -> None:
    webui_database = tmp_path / "webui.db"
    scheduler_database = tmp_path / "scheduler.db"
    apply_migrations(
        DatabaseTarget("webui", webui_database),
        tmp_path / "backups" / "webui-before.db",
    )
    apply_migrations(
        DatabaseTarget("scheduler", scheduler_database),
        tmp_path / "backups" / "scheduler-before.db",
    )
    before = {
        webui_database: webui_database.read_bytes(),
        scheduler_database: scheduler_database.read_bytes(),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PREFLIGHT_SCRIPT),
            "--webui-database",
            str(webui_database),
            "--scheduler-database",
            str(scheduler_database),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    assert "数据库预检通过" in completed.stdout
    assert {path: path.read_bytes() for path in before} == before
