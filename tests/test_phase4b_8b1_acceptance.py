# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import scripts.acceptance.run_phase4b_8b1 as acceptance
from scripts.acceptance.run_phase4b_8b1 import (
    AcceptanceError,
    _exclusive_run_lock,
    main,
)


def _write_compose(project_root: Path) -> None:
    compose_path = project_root / "docker" / "phase4b" / "compose.acceptance.yaml"
    compose_path.parent.mkdir(parents=True)
    compose_path.write_text("services:\n  app:\n    image: test\n", encoding="utf-8")


def test_preflight_reports_missing_compose_without_host_path(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "--run-id",
            "missing-compose",
            "--mode",
            "preflight",
            "--model-base-url",
            "http://192.0.2.10:6013/v1",
            "--model-name",
            "test-model",
        ]
    )

    report_path = (
        project_root
        / "runtime"
        / "acceptance"
        / "missing-compose"
        / "reports"
        / "summary.json"
    )
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert exit_code == 2
    assert report == {
        "schema_version": "phase4b-8b1-acceptance-v1",
        "run_id": "missing-compose",
        "mode": "preflight",
        "status": "failed",
        "checks": [
            {
                "id": "CONFIG-COMPOSE-FILE",
                "status": "failed",
                "detail": "compose_file_missing",
            }
        ],
    }
    assert str(project_root.resolve()) not in report_text


def test_powershell_entrypoint_is_portable_and_allows_long_model_timeout() -> None:
    script = Path("scripts/acceptance/run_phase4b_8b1.ps1").read_text(
        encoding="utf-8"
    )

    assert '[int]$ModelTimeoutSeconds = 1800' in script
    assert '[ValidateRange(1, 7200)]' in script
    assert '[string]$PythonExecutable = "python"' in script
    assert script.count("[Parameter(Mandatory = $true)]") == 2
    assert "192.168." not in script
    assert "Qwen3.8" not in script
    assert "E:\\python" not in script


def test_python_entrypoint_can_be_executed_directly() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/acceptance/run_phase4b_8b1.py",
            "--help",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0
    assert "G5 本机前置" in completed.stdout


def test_preflight_rejects_unsafe_run_id_before_creating_output(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "--run-id",
            "../escape",
            "--mode",
            "preflight",
            "--model-base-url",
            "http://192.0.2.10:6013/v1",
            "--model-name",
            "test-model",
        ]
    )

    assert exit_code == 2
    assert not (project_root / "runtime").exists()


def test_preflight_runs_compose_config_once_and_refuses_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_compose(project_root)
    calls: list[list[str]] = []

    def complete(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["timeout"] == 60
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["MANGROVE_ACCEPTANCE_MODEL_BASE_URL"] == (
            "http://192.0.2.10:6013/v1"
        )
        assert environment["MANGROVE_ACCEPTANCE_MODEL_NAME"] == "test-model"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", complete)
    arguments = [
        "--project-root",
        str(project_root),
        "--run-id",
        "compose-valid",
        "--mode",
        "preflight",
        "--model-base-url",
        "http://192.0.2.10:6013/v1",
        "--model-name",
        "test-model",
    ]

    assert main(arguments) == 0
    report_path = (
        project_root
        / "runtime"
        / "acceptance"
        / "compose-valid"
        / "reports"
        / "summary.json"
    )
    report_before = report_path.read_bytes()
    assert main(arguments) == 2
    assert report_path.read_bytes() == report_before
    assert calls == [
        [
            "docker",
            "compose",
            "--file",
            str(project_root / "docker" / "phase4b" / "compose.acceptance.yaml"),
            "config",
            "--quiet",
        ]
    ]
    assert json.loads(report_before) == {
        "schema_version": "phase4b-8b1-acceptance-v1",
        "run_id": "compose-valid",
        "mode": "preflight",
        "status": "passed",
        "checks": [
            {
                "id": "CONFIG-COMPOSE-FILE",
                "status": "passed",
                "detail": "compose_file_present",
            },
            {
                "id": "RUNTIME-DOCKER-COMPOSE",
                "status": "passed",
                "detail": "compose_config_valid",
            },
        ],
    }


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (subprocess.TimeoutExpired(["docker", "compose"], 60), "compose_config_timeout"),
        (FileNotFoundError("docker"), "docker_compose_unavailable"),
    ],
)
def test_preflight_reports_compose_execution_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    detail: str,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_compose(project_root)

    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", fail)

    exit_code = main(
        [
            "--project-root",
            str(project_root),
            "--run-id",
            f"failure-{detail}",
            "--mode",
            "preflight",
            "--model-base-url",
            "http://192.0.2.10:6013/v1",
            "--model-name",
            "test-model",
        ]
    )
    report = json.loads(
        (
            project_root
            / "runtime"
            / "acceptance"
            / f"failure-{detail}"
            / "reports"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )

    assert exit_code == 2
    assert report["status"] == "failed"
    assert report["checks"][-1] == {
        "id": "RUNTIME-DOCKER-COMPOSE",
        "status": "failed",
        "detail": detail,
    }


def test_run_lock_rejects_concurrent_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "run.lock"

    with _exclusive_run_lock(lock_path):
        with pytest.raises(AcceptanceError, match="正在执行"):
            with _exclusive_run_lock(lock_path):
                pytest.fail("并发锁不应被重复取得")


def test_resource_identity_keeps_normalized_run_ids_isolated() -> None:
    assert acceptance._project_name("A_B", "main") != acceptance._project_name(
        "a-b", "main"
    )


def test_full_mode_runs_identity_bound_cleanup_after_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = acceptance._parser().parse_args(
        [
            "--project-root",
            str(tmp_path),
            "--run-id",
            "cleanup-error",
            "--mode",
            "full",
            "--model-base-url",
            "http://192.0.2.10:6013/v1",
            "--model-name",
            "test-model",
        ]
    )
    cleanups: list[tuple[str, ...]] = []

    def fail(**_: object) -> int:
        raise RuntimeError("unexpected")

    def cleanup(**kwargs: object) -> bool:
        cleanups.append(tuple(kwargs["projects"]))
        return True

    monkeypatch.setattr(acceptance, "_execute_full_acceptance", fail)
    monkeypatch.setattr(acceptance, "_best_effort_full_cleanup", cleanup)

    with pytest.raises(RuntimeError, match="unexpected"):
        acceptance._run_full_acceptance(
            args=args,
            project_root=tmp_path,
            compose_path=tmp_path / "compose.yaml",
            run_root=tmp_path / "runtime" / "acceptance" / "cleanup-error",
        )

    assert cleanups == [tuple(
        acceptance._project_name("cleanup-error", suffix)
        for suffix in ("main", "fault", "restore")
    )]


def test_cleanup_fails_when_compose_down_failed_even_if_probes_are_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acceptance, "_cleanup_project", lambda **_: False)
    monkeypatch.setattr(
        acceptance,
        "_run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, "", ""),
    )

    assert acceptance._best_effort_full_cleanup(
        project_root=tmp_path,
        compose_path=tmp_path / "compose.yaml",
        projects=("isolated-project",),
        environment={},
        image="isolated-image",
        run_root=tmp_path / "run",
    ) is False


@pytest.mark.parametrize("resource_kind", ["network", "volume"])
def test_cleanup_fails_when_labeled_resource_remains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource_kind: str,
) -> None:
    monkeypatch.setattr(acceptance, "_cleanup_project", lambda **_: True)

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = "leaked-resource\n" if command[1:3] == [resource_kind, "ls"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(acceptance, "_run", run)

    assert acceptance._best_effort_full_cleanup(
        project_root=tmp_path,
        compose_path=tmp_path / "compose.yaml",
        projects=("isolated-project",),
        environment={},
        image="isolated-image",
        run_root=tmp_path / "run",
    ) is False


def test_cleanup_refuses_container_with_unexpected_compose_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(
                command, 0, "isolated-project|unexpected\n", ""
            )
        raise AssertionError("标签不匹配时不应执行 compose down")

    monkeypatch.setattr(acceptance, "_run", run)

    assert acceptance._cleanup_project(
        project_root=tmp_path,
        compose_path=tmp_path / "compose.yaml",
        project="isolated-project",
        environment={},
    ) is False
    assert all("compose" not in command for command in commands)


def test_acceptance_compose_is_isolated_and_non_root() -> None:
    compose_path = Path("docker/phase4b/compose.acceptance.yaml")
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    app = compose["services"]["app"]

    assert set(compose["services"]) == {"app"}
    assert app["build"] == {
        "context": "../..",
        "dockerfile": "docker/phase4b/Dockerfile",
    }
    assert app["ports"] == ["127.0.0.1:${MANGROVE_ACCEPTANCE_PORT:-18089}:8088"]
    assert app["user"] == "10001:10001"
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert app["security_opt"] == ["no-new-privileges:true"]
    assert app["volumes"] == ["acceptance-data:/app/data"]
    assert "/app/logs:rw,noexec,nosuid,size=64m" in app["tmpfs"]
    assert app["environment"]["WEBUI_DB_PATH"] == "/app/data/webui.db"
    assert app["environment"]["SCHEDULER_DB_PATH"] == "/app/data/scheduler.db"
    assert set(compose["volumes"]) == {"acceptance-data"}
    rendered = json.dumps(compose, ensure_ascii=False)
    assert "/var/run/docker.sock" not in rendered
    assert "./data" not in rendered
    assert ".env" not in rendered


def test_clean_image_is_pinned_built_and_runs_as_non_root() -> None:
    dockerfile = Path("docker/phase4b/Dockerfile").read_text(encoding="utf-8")

    assert (
        "FROM node:22-bookworm-slim@sha256:"
        "6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 "
        "AS frontend-build"
    ) in dockerfile
    assert (
        "FROM python:3.13-slim-bookworm@sha256:"
        "9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 "
        "AS runtime"
    ) in dockerfile
    assert "RUN npm ci && npm run build" in dockerfile
    assert (
        "python -m pip install -r requirements.txt "
        "-r requirements-collectors.txt"
    ) in dockerfile
    python_build = dockerfile.split(" AS python-build", maxsplit=1)[1].split(
        " AS runtime", maxsplit=1
    )[0]
    assert "python -m pip install --upgrade pip==26.2" in python_build
    assert "COPY src ./src" in dockerfile
    assert "COPY --from=frontend-build /build/frontend/dist ./frontend/dist" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "WORKDIR /app" in dockerfile.split(" AS runtime", maxsplit=1)[1]
    assert "mkdir -p /app/data /app/logs" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'CMD ["python", "-m", "src.api.main"]' in dockerfile
    assert "constraints.txt" not in dockerfile


def test_system_dependency_downloads_are_cached_retried_and_not_in_runtime() -> None:
    dockerfile = Path("docker/phase4b/Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split(" AS runtime", maxsplit=1)[1]

    assert " AS python-build" in dockerfile
    assert dockerfile.count("id=phase4b-apt-cache") == 2
    assert dockerfile.count("id=phase4b-apt-lists") == 2
    assert dockerfile.count("Acquire::Retries=5") >= 2
    assert dockerfile.count("Acquire::http::Timeout=120") >= 2
    assert "build-essential" not in runtime_stage
    assert "COPY --from=python-build /opt/venv /opt/venv" in runtime_stage
    assert 'PATH="/opt/venv/bin:$PATH"' in runtime_stage


def test_docker_context_is_allowlisted_and_excludes_local_secrets() -> None:
    dockerignore = Path("docker/phase4b/Dockerfile.dockerignore").read_text(
        encoding="utf-8"
    ).splitlines()
    rules = [line for line in dockerignore if line and not line.startswith("#")]

    assert rules[0] == "**"
    assert {
        "!requirements.txt",
        "!requirements-collectors.txt",
        "!frontend/",
        "!frontend/**",
        "!src/",
        "!src/**",
        "!config/",
        "!config/**",
        "!skills/",
        "!skills/**",
        "!docker/",
        "!docker/phase4b/",
        "!docker/phase4b/Dockerfile",
    }.issubset(rules)
    assert {
        "frontend/node_modules/",
        "frontend/dist/",
        "frontend/test-results/",
        "frontend/e2e/",
        "**/.env",
        "**/.env.*",
        "**/*.key",
        "**/*.pem",
        "**/*.p12",
        "**/*.pfx",
    }.issubset(rules)
    for local_only in ("data", "evals", "docs", "runtime", "logs", "tests"):
        assert not any(rule.startswith(f"!{local_only}") for rule in rules)


def test_clean_requirements_satisfy_pdfplumber_pillow_floor() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "pdfplumber==0.11.10" in requirements
    assert "pillow==12.3.0" in requirements


def test_clean_requirements_resolve_crawler_lxml_ranges() -> None:
    requirements = (
        Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        + Path("requirements-collectors.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert "crawl4ai==0.9.1" in requirements
    assert "scrapling[fetchers]==0.4.9" in requirements
    assert "lxml==6.1.1" in requirements
    assert "orjson==3.11.8" in requirements


def test_clean_requirements_avoid_yanked_numeric_runtime_versions() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "numpy==2.4.1" in requirements
    assert "polars==1.43.2" in requirements
    assert "numpy==2.4.0" not in requirements
    assert "polars==1.43.0" not in requirements


def test_linux_image_uses_separate_digest_locked_supply_chain_tools() -> None:
    lock = json.loads(
        Path("config/supply-chain-tools.linux-amd64.lock.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {
        "cosign": (
            "3.0.6",
            "cosign",
            "c956e5dfcac53d52bcf058360d579472f0c1d2d9b69f55209e256fe7783f4c74",
        ),
        "oras": (
            "1.3.2",
            "oras",
            "22dc6b05994a786f36bc57de47c48562543f3fa0cc62366a9a9574b422a13ab2",
        ),
        "trivy": (
            "0.70.0",
            "trivy",
            "379d59f24a4a828c55de5f0b91b6805cc35d13580180b658820e648611256166",
        ),
        "syft": (
            "1.50.0",
            "syft",
            "22f2b95baf524d45ad16b0ad5cdeb200c4b8a816493768cec50e4682b1f24b0e",
        ),
    }

    for name, (version, executable, digest) in expected.items():
        assert lock[name]["version"] == version
        assert lock[name]["executable"] == executable
        assert lock[name]["executable_sha256"] == digest
        assert lock[name]["source_verification"]["verified"] is True
        assert ".exe" not in lock[name]["executable"]


def test_clean_image_fetches_and_copies_digest_locked_linux_tools() -> None:
    dockerfile = Path("docker/phase4b/Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split(" AS runtime", maxsplit=1)[1]
    compose = yaml.safe_load(
        Path("docker/phase4b/compose.acceptance.yaml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["app"]["environment"]

    assert " AS toolchain-build" in dockerfile
    for digest in (
        "c956e5dfcac53d52bcf058360d579472f0c1d2d9b69f55209e256fe7783f4c74",
        "9229ccc6d17bb282039ad4a69abb16dcb887a5bce567c075d731d9b3c7ad8eaf",
        "8b4376d5d6befe5c24d503f10ff136d9e0c49f9127a4279fd110b727929a5aa9",
        "bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788",
    ):
        assert f"ADD --checksum=sha256:{digest}" in dockerfile
    assert (
        "COPY --from=toolchain-build /opt/mangrove-tools /opt/mangrove-tools"
        in dockerfile
    )
    assert (
        "CAPABILITY_SUPPLY_CHAIN_TOOL_ROOT=/opt/mangrove-tools" in runtime_stage
    )
    assert (
        "CAPABILITY_SUPPLY_CHAIN_LOCK_PATH="
        "/app/config/supply-chain-tools.linux-amd64.lock.json" in runtime_stage
    )
    assert environment["CAPABILITY_SUPPLY_CHAIN_TOOL_ROOT"] == "/opt/mangrove-tools"
    assert environment["CAPABILITY_SUPPLY_CHAIN_LOCK_PATH"] == (
        "/app/config/supply-chain-tools.linux-amd64.lock.json"
    )


def test_acceptance_container_bootstraps_managed_roots_and_uses_readiness() -> None:
    compose = yaml.safe_load(
        Path("docker/phase4b/compose.acceptance.yaml").read_text(encoding="utf-8")
    )
    app = compose["services"]["app"]
    environment = app["environment"]
    health_script = " ".join(app["healthcheck"]["test"])
    dockerfile = Path("docker/phase4b/Dockerfile").read_text(encoding="utf-8")
    entrypoint = Path("docker/phase4b/entrypoint.sh").read_bytes()
    attributes = Path(".gitattributes").read_text(encoding="utf-8").splitlines()

    assert environment["DATA_PREP_UPLOAD_ROOT"] == "/app/data/uploads"
    assert environment["SEMANTIC_EXECUTION_ROOT"] == (
        "/app/data/semantic-executions"
    )
    assert environment["DATA_PREP_ARTIFACT_ROOT"] == "/app/data/downloads"
    assert environment["LLM_BASE_URL"] == (
        "${MANGROVE_ACCEPTANCE_MODEL_BASE_URL:?MANGROVE_ACCEPTANCE_MODEL_BASE_URL is required}"
    )
    assert environment["LLM_MODEL_NAME"] == (
        "${MANGROVE_ACCEPTANCE_MODEL_NAME:?MANGROVE_ACCEPTANCE_MODEL_NAME is required}"
    )
    assert environment["DOCUMENT_EXTRACTION_MODEL"] == (
        "local::${MANGROVE_ACCEPTANCE_MODEL_NAME:?MANGROVE_ACCEPTANCE_MODEL_NAME is required}"
    )
    assert environment["LLM_TIMEOUT"] == (
        "${MANGROVE_ACCEPTANCE_MODEL_TIMEOUT_SECONDS:-1800}"
    )
    assert environment["LLM_DEFAULT_PROVIDER"] == "local"
    assert app["entrypoint"] == ["/app/docker/phase4b/entrypoint.sh"]
    assert app["command"] == ["python", "-m", "src.api.main"]
    assert 'ENTRYPOINT ["/app/docker/phase4b/entrypoint.sh"]' in dockerfile
    assert entrypoint.startswith(b"#!/bin/sh\n")
    assert b"\r\n" not in entrypoint
    assert "docker/phase4b/entrypoint.sh text eol=lf" in attributes
    assert "/api/readiness" in health_script
    assert "/api/health" not in health_script
    assert "COPY docker/phase4b/entrypoint.sh" in dockerfile


def test_real_browser_acceptance_cannot_mock_backend_and_uses_external_base_url() -> None:
    config = Path("frontend/playwright.config.ts").read_text(encoding="utf-8")
    spec = Path("frontend/e2e/phase4b-8b1-real.spec.ts").read_text(
        encoding="utf-8"
    )

    assert "PLAYWRIGHT_BASE_URL" in config
    assert "PLAYWRIGHT_SKIP_WEBSERVER" in config
    assert "timeout: 60_000" in config
    assert "testIgnore" in config
    assert "phase4b-8b1-.*real" in config
    assert "page.route(" not in spec
    assert ".route(" not in spec
    assert "contract_public.docx" in spec
    assert "提取合同付款节点和付款比例" in spec
    assert 'throw new Error("PHASE4B_ACCEPTANCE_MODEL_NAME is required")' in spec
    assert "Qwen3.8" not in spec
    assert "`local::${expectedModel}`" in spec
    assert "PHASE4B_ACCEPTANCE_SUFFIX" in spec
    assert "PHASE4B_ACCEPTANCE_PASSWORD" in spec
    assert "PHASE4B_ACCEPTANCE_RESULT_PATH" in spec
    assert "PHASE4B_ACCEPTANCE_MODEL_NAME" in spec
    assert "crossOwnerOutputReads" in spec
    assert "repeatedExtractions" in spec
    assert "response.status() === 409" in spec
    assert "completedTaskBefore" in spec
    assert "completedManifestBefore" in spec
    assert "expect(stableTask).toEqual(completedTaskBefore)" in spec
    assert "expect(stableManifest).toEqual(completedManifestBefore)" in spec
    assert "下载权威结果 JSON/JSONL" in spec
    assert "下载 XLSX 查看副本" in spec
    assert "下载 Manifest" in spec


def test_real_fault_acceptance_keeps_failure_closed_without_frontend_retry() -> None:
    spec = Path("frontend/e2e/phase4b-8b1-fault-real.spec.ts").read_text(
        encoding="utf-8"
    )

    assert "page.route(" not in spec
    assert ".route(" not in spec
    assert "draftRequests" in spec
    assert "抽取方案生成失败" in spec
    assert "toEqual([])" in spec
