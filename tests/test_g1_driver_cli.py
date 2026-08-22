# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import importlib.util
import subprocess
import sys
from types import SimpleNamespace

import pytest


def _load_driver():
    project_root = Path(__file__).resolve().parents[1]
    driver_path = project_root / "evals/generalization-g1/run_g1.py"
    spec = importlib.util.spec_from_file_location("g1_run_driver", driver_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(driver_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(driver_path.parent))
    return module


def test_g1_cli_exposes_relaxed_configurable_timeout() -> None:
    project_root = Path(__file__).resolve().parents[1]
    driver = project_root / "evals/generalization-g1/run_g1.py"

    completed = subprocess.run(
        [sys.executable, str(driver), "--help"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "--timeout-seconds" in completed.stdout
    assert "default: 1800" in completed.stdout
    assert "--diagnostic" in completed.stdout
    assert "--freeze-model-route" in completed.stdout


@pytest.mark.parametrize(
    ("status_line", "expected"),
    [
        (" M evals/generalization-g1/fixtures.json", []),
        (
            "?? evals/generalization-g1/fixtures.json.bak",
            ["?? evals/generalization-g1/fixtures.json.bak"],
        ),
        (
            "R  old-fixtures.json -> evals/generalization-g1/fixtures.json",
            ["R  old-fixtures.json -> evals/generalization-g1/fixtures.json"],
        ),
    ],
)
def test_dirty_worktree_only_allows_exact_frozen_fixture(
    monkeypatch: pytest.MonkeyPatch,
    status_line: str,
    expected: list[str],
) -> None:
    driver = _load_driver()
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=status_line + "\n"),
    )

    assert driver._dirty_worktree_paths() == expected


def test_dirty_worktree_accepts_explicit_frozen_metadata_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _load_driver()
    status = "\n".join(
        [
            " M evals/generalization-g1/fixtures.json",
            " M evals/generalization-g1-independent/freeze.json",
            " M evals/generalization-g1-independent/heldout_manifest.json",
            " M evals/generalization-g1-independent/oracles.json",
        ]
    )
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=status + "\n"),
    )

    dirty = driver._dirty_worktree_paths(
        allowed_paths={
            "evals/generalization-g1/fixtures.json",
            "evals/generalization-g1-independent/freeze.json",
            "evals/generalization-g1-independent/heldout_manifest.json",
        }
    )

    assert dirty == [" M evals/generalization-g1-independent/oracles.json"]
