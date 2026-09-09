# -*- coding: utf-8 -*-
"""P0-04A：通过公开命令和 workflow 文本验证最小 CI 契约。"""
from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess
import sys
import tomllib


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
    runtime = tmp_path / "requirements.txt"
    development = tmp_path / "requirements-dev.txt"
    subset = tmp_path / "requirements-ci.txt"
    runtime.write_text(
        'pydantic==2.12.5\nuvloop==0.22.1; sys_platform != "win32"\n',
        encoding="utf-8",
    )
    development.write_text("pytest==9.1.1\n", encoding="utf-8")
    subset.write_text("pydantic==2.12.5\npytest==9.1.1\n", encoding="utf-8")

    accepted = _run(
        "scripts/ci/check_requirement_consistency.py",
        "--base",
        str(runtime),
        "--base",
        str(development),
        "--subset",
        str(subset),
    )
    development.write_text("pydantic==2.11.0\npytest==9.1.1\n", encoding="utf-8")
    rejected = _run(
        "scripts/ci/check_requirement_consistency.py",
        "--base",
        str(runtime),
        "--base",
        str(development),
        "--subset",
        str(subset),
    )

    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["matched_requirements"] == 2
    assert rejected.returncode == 1
    assert "跨分组版本冲突" in rejected.stderr


def test_authoritative_requirements_reject_non_exact_pins(tmp_path) -> None:
    subset = tmp_path / "requirements-ci.txt"
    subset.write_text("pydantic==2.12.5\n", encoding="utf-8")

    for invalid in ("example>=1\n", "example\n", "--index-url https://example.invalid\n"):
        base = tmp_path / "requirements.txt"
        base.write_text(f"pydantic==2.12.5\n{invalid}", encoding="utf-8")
        rejected = _run(
            "scripts/ci/check_requirement_consistency.py",
            "--base",
            str(base),
            "--subset",
            str(subset),
        )

        assert rejected.returncode == 1
        assert "权威清单只允许精确版本或安全的 -c/-r 引用" in rejected.stderr


def test_authoritative_requirement_references_must_be_safe_and_exist(tmp_path) -> None:
    base = tmp_path / "requirements.txt"
    constraints = tmp_path / "constraints.txt"
    included = tmp_path / "included.txt"
    subset = tmp_path / "requirements-ci.txt"
    constraints.write_text("shared==1.0\n", encoding="utf-8")
    included.write_text("extra==2.0\n", encoding="utf-8")
    subset.write_text("pydantic==2.12.5\n", encoding="utf-8")
    base.write_text(
        "pydantic==2.12.5\n-c constraints.txt\n-r included.txt\n",
        encoding="utf-8",
    )

    accepted = _run(
        "scripts/ci/check_requirement_consistency.py",
        "--base",
        str(base),
        "--subset",
        str(subset),
    )
    assert accepted.returncode == 0

    for unsafe in ("-c missing.txt\n", "-r ../outside.txt\n", f"-c {constraints.resolve()}\n"):
        base.write_text(f"pydantic==2.12.5\n{unsafe}", encoding="utf-8")
        rejected = _run(
            "scripts/ci/check_requirement_consistency.py",
            "--base",
            str(base),
            "--subset",
            str(subset),
        )
        assert rejected.returncode == 1
        assert "依赖引用" in rejected.stderr


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
        '-k "not collectors_import_smoke_reaches_scrapling_runtime_seams"',
        "--junitxml=.artifacts/ci/python-fast.xml",
        "npm ci",
        ".artifacts/ci/frontend-install.log",
        "npm run build",
        "GITLEAKS_VERSION: '8.30.1'",
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
        "--redact",
        "--config .gitleaks.toml",
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


def test_ci_subset_contains_migration_test_runtime_dependencies() -> None:
    requirements = (PROJECT_ROOT / "requirements-ci.txt").read_text(
        encoding="utf-8"
    )

    assert "alembic==1.18.3" in requirements
    assert "SQLAlchemy==2.0.45" in requirements


def test_alembic_environment_is_not_hidden_by_local_env_ignore_rule() -> None:
    ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--error-unmatch",
            "src/database_migrations/alembic/env.py",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "!src/database_migrations/alembic/env.py" in ignore
    assert tracked.returncode == 0


def test_unified_workspace_gate_includes_canvas_migration_and_existing_siblings() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci-heavy.yml").read_text(encoding="utf-8")
    selected = workflow.split('elif [[ "${{ inputs.gate }}" == "unified-workspace" ]]; then', 1)[1].split("elif", 1)[0]
    for name in ("test_progressive_clarification.py", "test_workspace_answer_persistence.py", "test_workspace_revision_safety.py", "test_progressive_clarification_evaluation.py", "test_semantic_plan_compiler.py", "test_issue98_workbench_closeout.py", "test_web_source_delivery_api.py", "test_workspace_canvas.py", "test_workspace_canvas_migration.py", "test_semantic_table_execution.py", "test_semantic_delivery.py", "test_database_migrations.py", "test_database_migrations_components.py", "test_runtime_config_secret_refs.py", "test_workspace_conversation_stream.py"):
        assert f"tests/{name}" in selected
    assert "--randomly-seed=0 --timeout=120" in selected
    assert "data/webui.db" not in selected


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
    # 只允许已审计的配置测试及强制 MockTransport 的撤权回归，仍拒绝真实 Provider 接线。
    offline = workflow.lower().replace("tests/test_llm_provider.py", "")
    offline = offline.replace("tests/test_account_execution_providers.py", "")
    # 候选重验只用临时库和 Verifier/Broker 替身，真实 Broker 仅查询合成账本。
    offline = offline.replace("tests/test_candidate_reverification_provider.py", "")
    assert "provider" not in offline
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
    assert config.count('targetRules = ["generic-api-key"]') == 5
    assert config.count('condition = "AND"') == 5
    assert 'regexTarget = "line"' in config
    assert '^src/database_migrations/schema_manifest\\.json$' in config
    assert 'model_connection_secrets|runtime_config_secrets' in config
    assert "commits =" not in config
    assert "tests/.*" not in config
    assert "evals/.*" not in config
    assert len(ignored) == 12
    assert (
        "8f23acbdcb69890cc94c733bb47baa3a75d5de22:"
        "tests/test_source_account_generation.py:generic-api-key:19"
    ) in ignored
    # 新例外必须精确到已审计提交、测试文件、规则与行号，且只出现一次。
    assert ignored.count(
        "6268599f307d327e0015f8e78df1606b4b565ff8:"
        "tests/test_workspace_conversation_stream.py:generic-api-key:121"
    ) == 1
    # 结构摘要例外也只允许已审计历史行，不豁免整个清单或未来提交。
    assert ignored.count(
        "9a428600063e4e56e2c626dea6690b6461f85eb8:"
        "src/database_migrations/schema_manifest.json:generic-api-key:100"
    ) == 1
    assert all("*" not in fingerprint for fingerprint in ignored)
    assert all(fingerprint.count(":") >= 3 for fingerprint in ignored)


def test_feedback_schema_digest_exception_rejects_adjacent_credentials() -> None:
    config = tomllib.loads((PROJECT_ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    rule = next(item for item in config["allowlists"] if "feedback_content_access" in item["regexes"][0])
    pattern = re.compile(rule["regexes"][0])
    digest = "1234567890abcdef" * 4
    for name in ("feedback_content_access", "trigger:feedback_content_access_no_delete",
                 "trigger:feedback_content_access_no_replace", "trigger:feedback_content_access_no_update"):
        line = f'  "{name}": "{digest}",'
        assert pattern.search(line)
        assert not pattern.search(line + ' "api_key": "synthetic-key"')
    assert not pattern.search(f'"api_key": "{digest}"')
    assert not pattern.search('"feedback_content_access": "synthetic-key"')
    assert re.search(rule["paths"][0], "src/database_migrations/schema_manifest.json")
    assert not re.search(rule["paths"][0], "other/schema_manifest.json")
