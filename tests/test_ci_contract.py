# -*- coding: utf-8 -*-
"""P0-04A：通过公开命令和 workflow 文本验证最小 CI 契约。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, str(PROJECT_ROOT / script), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_utf8_check_accepts_valid_files_and_rejects_invalid_bytes(tmp_path) -> None:
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.py"
    good.write_text("中文 UTF-8\n", encoding="utf-8")
    bad.write_bytes(b"valid\n\xff\n")

    accepted = _run(
        "scripts/ci/check_utf8.py",
        "--root",
        str(tmp_path),
        "--paths",
        good.name,
    )
    rejected = _run(
        "scripts/ci/check_utf8.py",
        "--root",
        str(tmp_path),
        "--paths",
        bad.name,
    )

    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["checked_files"] == 1
    assert rejected.returncode == 1
    assert "bad.py" in rejected.stderr


def test_utf8_default_scan_covers_extensionless_and_dotfiles(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / ".gitleaksignore").write_bytes(b"valid\n\xff\n")
    (tmp_path / "image.bin").write_bytes(b"\x00\xff\x00\xff")
    subprocess.run(
        ["git", "add", "-f", "Dockerfile", ".gitleaksignore", "image.bin"],
        cwd=tmp_path,
        check=True,
    )

    rejected = _run("scripts/ci/check_utf8.py", "--root", str(tmp_path))
    (tmp_path / ".gitleaksignore").write_text("fingerprint\n", encoding="utf-8")
    accepted = _run("scripts/ci/check_utf8.py", "--root", str(tmp_path))

    assert rejected.returncode == 1
    assert ".gitleaksignore" in rejected.stderr
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["checked_files"] == 2


def test_ci_requirements_must_match_authoritative_pins(tmp_path) -> None:
    base = tmp_path / "requirements.txt"
    subset = tmp_path / "requirements-ci.txt"
    base.write_text("pydantic==2.12.5\npytest==9.1.1\n", encoding="utf-8")
    subset.write_text("pytest==9.1.1\n", encoding="utf-8")

    accepted = _run(
        "scripts/ci/check_requirement_consistency.py",
        "--base",
        str(base),
        "--subset",
        str(subset),
    )
    subset.write_text("pytest==9.0.0\n", encoding="utf-8")
    rejected = _run(
        "scripts/ci/check_requirement_consistency.py",
        "--base",
        str(base),
        "--subset",
        str(subset),
    )

    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["matched_requirements"] == 1
    assert rejected.returncode == 1
    assert "pytest==9.0.0" in rejected.stderr


def test_minimum_ci_workflow_is_pinned_bounded_and_evidence_producing() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    required_fragments = (
        "pull_request:",
        "workflow_dispatch:",
        "permissions:\n  contents: read",
        "defaults:\n  run:\n    shell: bash",
        "timeout-minutes:",
        "python-version: '3.13'",
        "node-version: '22'",
        "python -m pip check",
        "python scripts/ci/check_utf8.py",
        "python scripts/ci/check_requirement_consistency.py",
        ".artifacts/ci/backend-install.log",
        "tests/test_candidate_verification_migration.py",
        "--junitxml=.artifacts/ci/python-fast.xml",
        "npm ci",
        ".artifacts/ci/frontend-install.log",
        "npm run build",
        "GITLEAKS_VERSION: '8.30.1'",
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
        "--redact",
        "if: always()",
        "path: .artifacts/ci",
    )
    for fragment in required_fragments:
        assert fragment in workflow
    assert workflow.count("set -euo pipefail") >= 4

    pinned_actions = (
        "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405",
        "actions/setup-node@53b83947a5a98c8d113130e565377fae1a50d02f",
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f",
    )
    for action in pinned_actions:
        assert action in workflow

    assert "secrets." not in workflow
    assert "data/webui.db" not in workflow
    assert "provider" not in workflow.lower()


def test_heavy_ci_is_manual_only_and_never_receives_secrets() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci-heavy.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "schedule:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "python -m pytest" in workflow
    assert "tests/test_g1_" in workflow
    assert "docker build" in workflow
    assert "npm run test:e2e" in workflow
    assert "secrets." not in workflow
    assert "provider" not in workflow.lower()
    assert "if: always()" in workflow


def test_gitleaks_allowlist_is_narrow_and_does_not_skip_commits() -> None:
    config = (PROJECT_ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    ignored = [
        line.strip()
        for line in (PROJECT_ROOT / ".gitleaksignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert "useDefault = true" in config
    assert config.count('targetRules = ["generic-api-key"]') == 3
    assert config.count('condition = "AND"') == 3
    assert 'regexTarget = "line"' in config
    assert "commits =" not in config
    assert "tests/.*" not in config
    assert "evals/.*" not in config
    assert len(ignored) == 9
    assert all("*" not in fingerprint for fingerprint in ignored)
    assert all(fingerprint.count(":") >= 3 for fingerprint in ignored)
