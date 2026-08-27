# -*- coding: utf-8 -*-
"""P0-03 Python 依赖分组与生产安装策略契约。"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIN_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^]]+\])?==")


def _package_names(relative_path: str) -> set[str]:
    names: set[str] = set()
    for raw_line in (PROJECT_ROOT / relative_path).read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.split("#", 1)[0].strip()
        match = PIN_PATTERN.match(line)
        if match is not None:
            names.add(match.group("name").lower().replace("_", "-"))
    return names


def _requirement_lines(relative_path: str) -> set[str]:
    return {
        raw_line.split("#", 1)[0].strip()
        for raw_line in (PROJECT_ROOT / relative_path)
        .read_text(encoding="utf-8")
        .splitlines()
        if raw_line.split("#", 1)[0].strip()
    }


def test_scrapling_fetchers_extra_has_a_complete_compatible_constraint_set() -> None:
    runtime = _requirement_lines("requirements.txt")
    collectors = _requirement_lines("requirements-collectors.txt")
    production = runtime | collectors

    assert "scrapling[fetchers]==0.4.9" in collectors
    assert "-c requirements.txt" not in collectors
    assert {
        "anyio==4.14.0",
        "click==8.3.3",
        "curl_cffi==0.15.0",
        "playwright==1.60.0",
        "patchright==1.60.1",
        "browserforge==1.2.4",
        "apify-fingerprint-datapoints==0.13.0",
        "msgspec==0.21.1",
        "protego==0.6.1",
    }.issubset(production)
    assert "chardet==5.2.0" in collectors
    assert "pydantic-settings==2.14.2" in runtime
    assert "pydantic-settings==2.14.2" not in collectors
    assert "lxml==6.1.1" in runtime
    assert "lxml==6.1.1" not in collectors


def test_python_dependencies_are_split_at_their_installation_boundaries() -> None:
    runtime = _package_names("requirements.txt")
    collectors = _package_names("requirements-collectors.txt")
    development = _package_names("requirements-dev.txt")
    gpu = _package_names("requirements-gpu.txt")
    evaluation = _package_names("requirements-evaluation.txt")

    assert {
        "fastapi",
        "langgraph",
        "pydantic",
        "sqlalchemy",
        "uvicorn",
    }.issubset(runtime)
    assert {
        "crawl4ai",
        "ddgs",
        "firecrawl-py",
        "playwright",
        "scrapling",
        "yt-dlp",
    }.issubset(collectors)
    assert {
        "hypothesis",
        "pytest",
        "pytest-asyncio",
        "pytest-timeout",
        "testcontainers",
    }.issubset(development)
    assert gpu == set()
    assert "jiwer" in evaluation
    assert {"scikit-learn", "skops"}.isdisjoint(evaluation)

    production_forbidden = development | gpu | evaluation
    assert runtime.isdisjoint(production_forbidden)
    assert collectors.isdisjoint(production_forbidden)

    all_groups = runtime | collectors | development | gpu | evaluation
    assert {
        "databricks-sdk",
        "flask",
        "flask-cors",
        "mlflow",
        "mlflow-skinny",
        "mlflow-tracing",
        "nvidia-cublas-cu11",
        "nvidia-cudnn-cu11",
        "scikit-learn",
        "skops",
        "streamlit",
        "triton",
    }.isdisjoint(all_groups)

    gpu_policy = (PROJECT_ROOT / "requirements-gpu.txt").read_text(
        encoding="utf-8"
    )
    assert "当前没有进程内 GPU workload" in gpu_policy
    assert "远端模型连接不需要 torch、CUDA 或 Triton" in gpu_policy


def test_uvloop_is_only_installed_on_supported_platforms() -> None:
    runtime = _requirement_lines("requirements.txt")

    assert 'uvloop==0.22.1; sys_platform != "win32"' in runtime
    assert "uvloop==0.22.1" not in runtime


def test_runtime_http_auth_and_crypto_pins_meet_audited_security_floors() -> None:
    runtime = _requirement_lines("requirements.txt")

    assert {
        "aiohttp==3.14.3",
        "click==8.3.3",
        "cryptography==50.0.1",
        "fastapi==0.141.1",
        "filelock==3.20.3",
        "idna==3.15",
        "PyJWT==2.13.0",
        "python-multipart==0.0.31",
        "requests==2.33.0",
        "starlette==1.6.0",
        "urllib3==2.7.0",
    }.issubset(runtime)


def test_runtime_document_data_and_protocol_pins_meet_security_floors() -> None:
    runtime = _requirement_lines("requirements.txt")

    assert {
        "json_repair==0.60.1",
        "Mako==1.3.12",
        "mcp==1.28.1",
        "pillow==12.3.0",
        "protobuf==6.33.5",
        "pyarrow==23.0.1",
        "pyasn1==0.6.4",
        "pydantic-settings==2.14.2",
        "pypdf==6.15.0",
        "python-dotenv==1.2.2",
        "Werkzeug==3.1.6",
    }.issubset(runtime)


def test_runtime_agent_framework_cluster_is_security_fixed_and_compatible() -> None:
    runtime = _requirement_lines("requirements.txt")

    assert {
        "langchain==1.3.9",
        "langchain-community==0.4.2",
        "langchain-core==1.4.6",
        "langchain-openai==1.1.14",
        "langchain-text-splitters==1.1.2",
        "langgraph==1.2.4",
        "langgraph-checkpoint==4.1.1",
        "langgraph-checkpoint-sqlite==3.1.1",
        "langgraph-prebuilt==1.1.0",
        "langgraph-sdk==0.4.3",
        "langsmith==0.8.18",
        "openai==2.26.0",
    }.issubset(runtime)


def test_runtime_excludes_unconsumed_legacy_and_build_packages() -> None:
    runtime = _package_names("requirements.txt")

    assert {
        "gitdb",
        "gitpython",
        "setuptools",
        "smmap",
        "sqlparse",
        "tornado",
        "wheel",
    }.isdisjoint(runtime)


def test_phase4b_image_installs_runtime_and_collectors_only() -> None:
    dockerfile = (PROJECT_ROOT / "docker/phase4b/Dockerfile").read_text(
        encoding="utf-8"
    )
    dockerignore = (
        PROJECT_ROOT / "docker/phase4b/Dockerfile.dockerignore"
    ).read_text(encoding="utf-8")

    assert "COPY requirements.txt requirements-collectors.txt ./" in dockerfile
    assert (
        "python -m pip install -r requirements.txt "
        "-r requirements-collectors.txt"
    ) in dockerfile
    assert "constraints.txt" not in dockerfile
    assert "requirements-dev.txt" not in dockerfile
    assert "requirements-gpu.txt" not in dockerfile
    assert "requirements-evaluation.txt" not in dockerfile
    assert "!requirements.txt" in dockerignore
    assert "!requirements-collectors.txt" in dockerignore
    assert "constraints.txt" not in dockerignore


def test_phase4b_image_provisions_chromium_for_the_non_root_runtime() -> None:
    dockerfile = (PROJECT_ROOT / "docker/phase4b/Dockerfile").read_text(
        encoding="utf-8"
    )
    runtime = dockerfile.split(" AS runtime", maxsplit=1)[1]

    assert dockerfile.count("PLAYWRIGHT_BROWSERS_PATH=/ms-playwright") == 2
    assert "python -m playwright install chromium" in dockerfile
    assert "COPY --from=python-build /ms-playwright /ms-playwright" in runtime
    assert "python -m playwright install-deps chromium" in runtime
    assert "chmod -R a+rX /ms-playwright" in runtime
    assert runtime.index("chmod -R a+rX /ms-playwright") < runtime.index(
        "USER 10001:10001"
    )


def test_heavy_ci_installs_test_and_evaluation_groups_without_gpu() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci-heavy.yml").read_text(
        encoding="utf-8"
    )
    python_heavy = workflow.split("  python-heavy:", maxsplit=1)[1].split(
        "  dependency-group-check:", maxsplit=1
    )[0]

    assert "requirements-collectors.txt" in python_heavy
    assert "requirements-dev.txt" in python_heavy
    assert "requirements-evaluation.txt" in python_heavy
    assert "requirements-gpu.txt" not in python_heavy


def test_each_dependency_group_has_an_independent_clean_install_gate() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci-heavy.yml").read_text(
        encoding="utf-8"
    )

    assert "dependency-groups" in workflow
    for group, requirement_file in (
        ("runtime", "requirements.txt"),
        ("collectors", "requirements-collectors.txt"),
        ("dev", "requirements-dev.txt"),
        ("evaluation", "requirements-evaluation.txt"),
        ("gpu", "requirements-gpu.txt"),
    ):
        assert f"group: {group}" in workflow
        assert f"requirements: {requirement_file}" in workflow
    assert "install_args=(-r requirements.txt)" in workflow
    assert 'install_args+=(-r "${{ matrix.requirements }}")' in workflow
    assert 'python -m pip install "${install_args[@]}"' in workflow
    assert "python -m pip check" in workflow
    assert (
        'python scripts/ci/check_dependency_group_imports.py '
        '--group "${{ matrix.group }}"'
    ) in workflow


def test_dependency_group_import_smoke_cli_has_all_public_groups() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ci/check_dependency_group_imports.py",
            "--list",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "collectors",
        "dev",
        "evaluation",
        "gpu",
        "runtime",
    ]


def test_collectors_import_smoke_reaches_scrapling_runtime_seams() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ci/check_dependency_group_imports.py",
            "--group",
            "collectors",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    imported = set(__import__("json").loads(completed.stdout)["imports"])
    assert {
        "msgspec",
        "scrapling.fetchers.Fetcher",
        "scrapling.fetchers.StealthyFetcher",
        "src.collectors.scrapling_collector:available",
    }.issubset(imported)


def test_minimum_ci_checks_every_group_and_runs_the_policy_contract() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    for requirement_file in (
        "requirements.txt",
        "requirements-collectors.txt",
        "requirements-dev.txt",
        "requirements-evaluation.txt",
        "requirements-gpu.txt",
    ):
        assert f"--base {requirement_file}" in workflow
    assert "tests/test_dependency_groups.py" in workflow


def test_node_evaluation_tools_stay_out_of_production_and_minimum_ci() -> None:
    dockerfile = (PROJECT_ROOT / "docker/phase4b/Dockerfile").read_text(
        encoding="utf-8"
    )
    minimum_ci = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    production_contract = f"{dockerfile}\n{minimum_ci}".lower()

    assert "evals/promptfoo-batch8a" not in production_contract
    assert "evals/agentic-runtime-vnext" not in production_contract
    assert "promptfoo" not in production_contract


def test_node_lockfiles_are_complete_and_bound_to_their_manifests() -> None:
    for relative_directory in (
        "frontend",
        "evals/promptfoo-batch8a",
        "evals/agentic-runtime-vnext",
    ):
        directory = PROJECT_ROOT / relative_directory
        manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        lockfile = json.loads(
            (directory / "package-lock.json").read_text(encoding="utf-8")
        )

        assert lockfile["lockfileVersion"] == 3
        assert lockfile["packages"][""]["name"] == manifest["name"]
        assert lockfile["packages"][""].get("dependencies", {}) == manifest.get(
            "dependencies", {}
        )
        assert lockfile["packages"][""].get("devDependencies", {}) == manifest.get(
            "devDependencies", {}
        )
        assert all(
            package.get("version")
            for path, package in lockfile["packages"].items()
            if path
        )

    promptfoo = json.loads(
        (PROJECT_ROOT / "evals/promptfoo-batch8a/package.json").read_text(
            encoding="utf-8"
        )
    )
    assert promptfoo["devDependencies"]["promptfoo"] == "0.122.1"
