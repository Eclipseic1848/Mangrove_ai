# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from collections.abc import Iterator, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.acceptance.report_phase4b_8b1 import (
    SCHEMA_VERSION as CHECK_SCHEMA_VERSION,
    build_report,
)


PREFLIGHT_SCHEMA_VERSION = "phase4b-8b1-acceptance-v1"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
COMPOSE_CONFIG_TIMEOUT_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 180
BUILD_TIMEOUT_SECONDS = 3600
PLAYWRIGHT_TIMEOUT_SECONDS = 2100
READINESS_TIMEOUT_SECONDS = 180


class AcceptanceError(RuntimeError):
    """验收条件不满足；消息只能使用低敏稳定内容。"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行 G5 本机前置隔离验收")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", required=True, choices=("preflight", "full"))
    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--port", type=int, default=18088)
    parser.add_argument("--model-timeout-seconds", type=int, default=1800)
    return parser


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_run_lock(lock_path: Path):
    """防止同一 run-id 并发执行，避免重复外部请求。"""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise AcceptanceError("同一 run-id 正在执行") from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
    error_code: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise AcceptanceError(f"{error_code}_timeout") from exc
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise AcceptanceError(f"{error_code}_unavailable") from exc
    if completed.returncode != 0:
        raise AcceptanceError(f"{error_code}_failed")
    return completed


def _compose_check(
    compose_path: Path,
    project_root: Path,
    environment: dict[str, str],
) -> dict[str, str]:
    command = [
        "docker",
        "compose",
        "--file",
        str(compose_path),
        "config",
        "--quiet",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=COMPOSE_CONFIG_TIMEOUT_SECONDS,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        detail = "compose_config_timeout"
    except FileNotFoundError:
        detail = "docker_compose_unavailable"
    except PermissionError:
        detail = "docker_compose_permission_denied"
    except OSError:
        detail = "docker_compose_execution_failed"
    else:
        if completed.returncode == 0:
            return {
                "id": "RUNTIME-DOCKER-COMPOSE",
                "status": "passed",
                "detail": "compose_config_valid",
            }
        detail = "compose_config_invalid"
    return {
        "id": "RUNTIME-DOCKER-COMPOSE",
        "status": "failed",
        "detail": detail,
    }


def _preflight_report(
    *,
    run_id: str,
    mode: str,
    status: str,
    checks: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "checks": checks,
    }


def _validate_full_arguments(args: argparse.Namespace) -> None:
    parsed = urllib.parse.urlsplit(args.model_base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AcceptanceError("model_base_url_invalid")
    if not isinstance(args.model_name, str) or not args.model_name.strip():
        raise AcceptanceError("model_name_invalid")
    if not 1024 <= args.port <= 65532:
        raise AcceptanceError("acceptance_port_invalid")
    if not 1 <= args.model_timeout_seconds <= 7200:
        raise AcceptanceError("model_timeout_invalid")


def _resource_identity(run_id: str) -> str:
    normalized = run_id.lower().replace("_", "-")
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:31].rstrip('-')}-{digest}"


def _project_name(run_id: str, suffix: str) -> str:
    return f"mangrove-8b1-{_resource_identity(run_id)}-{suffix}"


def _image_tag(run_id: str) -> str:
    normalized = run_id.lower().replace("_", "-")
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:10]
    return f"8b1-{normalized[:35]}-{digest}"


def _compose_environment(
    *,
    port: int,
    image_tag: str,
    model_base_url: str,
    model_name: str,
    model_timeout_seconds: int,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "MANGROVE_ACCEPTANCE_PORT": str(port),
            "MANGROVE_ACCEPTANCE_IMAGE_TAG": image_tag,
            "MANGROVE_ACCEPTANCE_MODEL_BASE_URL": model_base_url,
            "MANGROVE_ACCEPTANCE_MODEL_NAME": model_name,
            "MANGROVE_ACCEPTANCE_MODEL_TIMEOUT_SECONDS": str(
                model_timeout_seconds
            ),
        }
    )
    return environment


def _compose_command(compose_path: Path, project: str, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--file",
        str(compose_path),
        "--project-name",
        project,
        *arguments,
    ]


def _find_app_container(project_root: Path, project: str) -> str:
    completed = _run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=app",
            "--format",
            "{{.ID}}",
        ],
        cwd=project_root,
        error_code="container_identity",
    )
    identities = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(identities) != 1:
        raise AcceptanceError("container_identity_ambiguous")
    return identities[0]


def _http_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    token: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, bytes, str]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "")


def _wait_readiness(base_url: str, *, timeout: int = READINESS_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body, _ = _http_request(f"{base_url}/api/readiness", timeout=5)
            if status == 200:
                payload = json.loads(body)
                if payload.get("ready") is True:
                    return payload
        except (OSError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(2)
    raise AcceptanceError("readiness_timeout")


def _start_project(
    *,
    project_root: Path,
    compose_path: Path,
    project: str,
    environment: dict[str, str],
) -> tuple[str, str, dict]:
    _run(
        _compose_command(compose_path, project, "up", "--detach", "--no-build"),
        cwd=project_root,
        env=environment,
        error_code="compose_up",
    )
    container = _find_app_container(project_root, project)
    base_url = f"http://127.0.0.1:{environment['MANGROVE_ACCEPTANCE_PORT']}"
    return container, base_url, _wait_readiness(base_url)


def _cleanup_project(
    *,
    project_root: Path,
    compose_path: Path,
    project: str,
    environment: dict[str, str],
) -> bool:
    try:
        containers = _run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            cwd=project_root,
            error_code="cleanup_identity_probe",
        )
        for container_id in containers.stdout.split():
            labels = _run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    (
                        '{{ index .Config.Labels "com.docker.compose.project" }}|'
                        '{{ index .Config.Labels "com.docker.compose.service" }}'
                    ),
                    container_id,
                ],
                cwd=project_root,
                error_code="cleanup_identity_inspect",
            ).stdout.strip()
            # 清理前同时绑定 project 和 service，避免误删名称碰撞的容器。
            if labels != f"{project}|app":
                return False
        _run(
            _compose_command(
                compose_path,
                project,
                "down",
                "--volumes",
                "--remove-orphans",
            ),
            cwd=project_root,
            env=environment,
            error_code="compose_cleanup",
        )
        return True
    except AcceptanceError:
        return False


def _best_effort_full_cleanup(
    *,
    project_root: Path,
    compose_path: Path,
    projects: Sequence[str],
    environment: dict[str, str],
    image: str,
    run_root: Path,
) -> bool:
    cleanup_ok = True
    for project in projects:
        if not _cleanup_project(
            project_root=project_root,
            compose_path=compose_path,
            project=project,
            environment=environment,
        ):
            cleanup_ok = False
    backup_root = (run_root / "backup-staging").resolve()
    backup_ok = True
    try:
        if run_root.resolve() in backup_root.parents and backup_root.exists():
            shutil.rmtree(backup_root)
    except OSError:
        backup_ok = False
    try:
        _run(
            ["docker", "image", "rm", image],
            cwd=project_root,
            error_code="image_cleanup",
        )
    except AcceptanceError:
        pass
    try:
        for project in projects:
            resource_commands = (
                ["docker", "ps", "--all", "--quiet"],
                ["docker", "network", "ls", "--quiet"],
                ["docker", "volume", "ls", "--quiet"],
            )
            for command in resource_commands:
                completed = _run(
                    [
                        *command,
                        "--filter",
                        f"label=com.docker.compose.project={project}",
                    ],
                    cwd=project_root,
                    error_code="cleanup_verify_project",
                )
                if completed.stdout.strip():
                    return False
        image_probe = _run(
            ["docker", "image", "ls", "--quiet", image],
            cwd=project_root,
            error_code="cleanup_verify_image",
        )
    except AcceptanceError:
        return False
    return (
        cleanup_ok
        and backup_ok
        and not image_probe.stdout.strip()
        and not backup_root.exists()
    )


def _check(
    check_id: str,
    status: str,
    summary: str,
    *,
    evidence: Sequence[str] = (),
    remediation: str | None = None,
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "status": status,
        "summary": summary,
        "evidence": list(evidence),
        "remediation": remediation,
    }


def _write_phase_result(
    path: Path,
    *,
    run_id: str,
    checks: Sequence[dict[str, object]],
) -> None:
    _write_json(
        path,
        {
            "schema_version": CHECK_SCHEMA_VERSION,
            "run_id": run_id,
            "checks": list(checks),
        },
    )


def _container_contract(
    project_root: Path,
    container: str,
    readiness: dict,
    image: str,
) -> list[dict[str, object]]:
    inspect = _run(
        ["docker", "inspect", container],
        cwd=project_root,
        error_code="container_inspect",
    )
    payload = json.loads(inspect.stdout)[0]
    host = payload["HostConfig"]
    config = payload["Config"]
    mounts = payload.get("Mounts") or []
    contract_ok = (
        config.get("User") == "10001:10001"
        and host.get("ReadonlyRootfs") is True
        and "ALL" in (host.get("CapDrop") or [])
        and "no-new-privileges:true" in (host.get("SecurityOpt") or [])
        and all(item.get("Type") != "bind" for item in mounts)
    )
    if not contract_ok:
        raise AcceptanceError("container_contract_invalid")
    expected_ids = {
        "CORE-API-001",
        "CORE-DB-001",
        "CORE-WORKER-001",
        "CORE-UPLOAD-001",
        "CORE-EXEC-001",
        "CORE-ARTIFACT-001",
    }
    actual_ids = {
        item.get("check_id")
        for item in readiness.get("checks", [])
        if item.get("status") == "passed"
    }
    if actual_ids != expected_ids:
        raise AcceptanceError("readiness_contract_invalid")
    _run(
        ["docker", "exec", container, "python", "-m", "pip", "check"],
        cwd=project_root,
        error_code="container_pip_check",
    )
    image_probe = (
        "import pathlib,sys;roots=[pathlib.Path('/app/.env'),"
        "pathlib.Path('/app/.git'),pathlib.Path('/app/tests'),"
        "pathlib.Path('/app/runtime'),pathlib.Path('/app/frontend/src')];"
        "sys.exit(1 if any(item.exists() for item in roots) else 0)"
    )
    _run(
        ["docker", "run", "--rm", "--entrypoint", "python", image, "-c", image_probe],
        cwd=project_root,
        error_code="image_content_probe",
    )
    return [
        _check("BUILD-IMAGE-001", "passed", "干净多阶段镜像构建成功"),
        _check("CORE-READINESS-001", "passed", "六项接单就绪检查通过"),
        _check("SEC-CONTAINER-001", "passed", "非 root、只读根、去能力且无源码挂载"),
        _check("DEP-LINUX-001", "passed", "Linux 运行镜像 pip check 通过"),
        _check("SEC-IMAGE-001", "passed", "镜像不含本地秘密和运行资料目录"),
    ]


def _run_playwright_flow(
    *,
    project_root: Path,
    base_url: str,
    reports_root: Path,
    model_name: str,
) -> dict[str, object]:
    npm = shutil.which("npm.cmd") if os.name == "nt" else shutil.which("npm")
    if npm is None:
        raise AcceptanceError("npm_unavailable")
    suffix = secrets.token_hex(6)
    password = f"G5-{secrets.token_urlsafe(24)}!"
    result_path = reports_root / "flow-result.json"
    environment = os.environ.copy()
    environment.update(
        {
            "PLAYWRIGHT_BASE_URL": base_url,
            "PLAYWRIGHT_SKIP_WEBSERVER": "1",
            "PLAYWRIGHT_OUTPUT_DIR": str(reports_root / "playwright"),
            "PHASE4B_ACCEPTANCE_SUFFIX": suffix,
            "PHASE4B_ACCEPTANCE_PASSWORD": password,
            "PHASE4B_ACCEPTANCE_RESULT_PATH": str(result_path),
            "PHASE4B_ACCEPTANCE_MODEL_NAME": model_name,
        }
    )
    _run(
        [
            npm,
            "run",
            "test:e2e",
            "--",
            "phase4b-8b1-real.spec.ts",
            "--reporter=line",
        ],
        cwd=project_root / "frontend",
        timeout=PLAYWRIGHT_TIMEOUT_SECONDS,
        env=environment,
        error_code="playwright_flow",
    )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("playwright_result_missing") from exc
    output_paths = result.get("output_paths")
    task_id = result.get("task_id")
    paths_are_safe = isinstance(output_paths, list) and isinstance(task_id, str) and all(
        isinstance(item, str)
        and bool(item)
        and "\\" not in item
        and not PurePosixPath(item).is_absolute()
        and all(part not in {"", ".", ".."} for part in PurePosixPath(item).parts)
        and PurePosixPath(item).parts[0] == task_id
        for item in output_paths
    )
    if (
        result.get("schema_version") != "phase4b-8b1-flow/v1"
        or not isinstance(result.get("upload_id"), str)
        or not isinstance(result.get("task_id"), str)
        or not paths_are_safe
        or not output_paths
        or len(set(output_paths)) != len(output_paths)
    ):
        raise AcceptanceError("playwright_result_invalid")
    return {
        **result,
        "suffix": suffix,
        "password": password,
        "checks": [
            _check(
                "FLOW-REAL-001",
                "passed",
                "真实登录、上传、外部模型抽取与结果展示通过",
                evidence=("flow-result.json",),
            ),
            _check(
                "FLOW-DOWNLOAD-001",
                "passed",
                "权威 JSONL、XLSX 与 Manifest 下载均非空",
                evidence=("flow-result.json",),
            ),
            _check(
                "CONC-OWNER-001",
                "passed",
                "20 用户并发交叉读取时跨 Owner 成功数为 0",
                evidence=("flow-result.json",),
            ),
            _check(
                "CONC-DUPLICATE-001",
                "passed",
                "40 次并发重复抽取均被 409 拒绝，终态和产物路径不变",
                evidence=("flow-result.json",),
            ),
        ],
    }


def _state_probe(project_root: Path, container: str) -> dict[str, object]:
    script = (
        "import json,pathlib,sqlite3;"
        "c=sqlite3.connect('file:/app/data/webui.db?mode=ro',uri=True);"
        "print(json.dumps({'quick_check':c.execute('PRAGMA quick_check').fetchone()[0],"
        "'users':c.execute('SELECT COUNT(*) FROM users').fetchone()[0],"
        "'tasks':c.execute('SELECT COUNT(*) FROM data_prep_tasks').fetchone()[0],"
        "'completed':c.execute('SELECT COUNT(*) FROM data_prep_tasks WHERE status=?',('COMPLETED',)).fetchone()[0],"
        "'files':sum(1 for p in pathlib.Path('/app/data').rglob('*') if p.is_file())},sort_keys=True))"
    )
    completed = _run(
        ["docker", "exec", container, "python", "-c", script],
        cwd=project_root,
        error_code="state_probe",
    )
    return json.loads(completed.stdout)


def _restart_probe(
    *,
    project_root: Path,
    container: str,
    base_url: str,
) -> dict[str, object]:
    before = _state_probe(project_root, container)
    _run(
        ["docker", "restart", container],
        cwd=project_root,
        error_code="container_restart",
    )
    _wait_readiness(base_url)
    after = _state_probe(project_root, container)
    if before != after or before.get("quick_check") != "ok":
        raise AcceptanceError("restart_state_changed")
    return _check(
        "FAULT-PROC-001",
        "passed",
        "API 重启后数据库、用户、任务、完成态和文件计数不变",
    )


class _SlowModelHandler(BaseHTTPRequestHandler):
    server: "_SlowModelServer"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self.send_error(404)
            return
        body = json.dumps({"data": [{"id": "fault-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self.server.request_count += 1
        time.sleep(self.server.delay_seconds)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_: object) -> None:
        return


class _SlowModelServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, delay_seconds: float) -> None:
        super().__init__(("0.0.0.0", 0), _SlowModelHandler)
        self.delay_seconds = delay_seconds
        self.request_count = 0


@contextmanager
def _slow_model_server(delay_seconds: float) -> Iterator[_SlowModelServer]:
    server = _SlowModelServer(delay_seconds)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_fault_flow(
    *,
    project_root: Path,
    compose_path: Path,
    project: str,
    port: int,
    image_tag: str,
) -> tuple[list[dict[str, object]], bool]:
    npm = shutil.which("npm.cmd") if os.name == "nt" else shutil.which("npm")
    if npm is None:
        raise AcceptanceError("npm_unavailable")
    cleaned = True
    with _slow_model_server(delay_seconds=10) as server:
        model_url = f"http://host.docker.internal:{server.server_address[1]}/v1"
        environment = _compose_environment(
            port=port,
            image_tag=image_tag,
            model_base_url=model_url,
            model_name="fault-model",
            model_timeout_seconds=2,
        )
        try:
            _, base_url, _ = _start_project(
                project_root=project_root,
                compose_path=compose_path,
                project=project,
                environment=environment,
            )
            playwright_environment = os.environ.copy()
            playwright_environment.update(
                {"PLAYWRIGHT_BASE_URL": base_url, "PLAYWRIGHT_SKIP_WEBSERVER": "1"}
            )
            _run(
                [
                    npm,
                    "run",
                    "test:e2e",
                    "--",
                    "phase4b-8b1-fault-real.spec.ts",
                    "--reporter=line",
                ],
                cwd=project_root / "frontend",
                timeout=180,
                env=playwright_environment,
                error_code="playwright_fault",
            )
            if server.request_count != 1:
                raise AcceptanceError("model_request_replayed")
        finally:
            cleaned = _cleanup_project(
                project_root=project_root,
                compose_path=compose_path,
                project=project,
                environment=environment,
            )
    return (
        [
            _check("FAULT-NET-001", "passed", "模型超时只发送 1 次 HTTP 请求并返回可理解失败"),
            _check("FAULT-CLOSE-001", "passed", "模型超时未创建假任务且服务恢复 ready"),
        ],
        cleaned,
    )


def _business_hash_probe(project_root: Path, container: str) -> list[dict]:
    script = (
        "import hashlib,json,pathlib;r=pathlib.Path('/app/data');"
        "print(json.dumps([{'path':str(p.relative_to(r)),'size':p.stat().st_size,"
        "'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} "
        "for p in sorted(r.rglob('*')) if p.is_file() and p.suffix!='.db'],sort_keys=True))"
    )
    completed = _run(
        ["docker", "exec", container, "python", "-c", script],
        cwd=project_root,
        error_code="business_hash_probe",
    )
    return json.loads(completed.stdout)


def _sqlite_snapshot_probe(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()


def _login(base_url: str, username: str, password: str) -> str:
    status, body, _ = _http_request(
        f"{base_url}/api/auth/login",
        method="POST",
        payload={"username": username, "password": password},
    )
    if status != 200:
        raise AcceptanceError("restore_login_failed")
    try:
        token = json.loads(body)["access_token"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AcceptanceError("restore_login_invalid") from exc
    if not isinstance(token, str) or not token:
        raise AcceptanceError("restore_login_invalid")
    return token


def _restore_public_probe(*, base_url: str, flow: dict[str, object]) -> None:
    suffix = str(flow["suffix"])
    password = str(flow["password"])
    owner_token = _login(base_url, f"g5_owner_a_{suffix}", password)
    other_token = _login(base_url, f"g5_owner_b_{suffix}", password)
    upload_id = urllib.parse.quote(str(flow["upload_id"]), safe="")
    task_id = urllib.parse.quote(str(flow["task_id"]), safe="")
    owner_urls = [
        f"{base_url}/api/data-sources/uploads/{upload_id}",
        f"{base_url}/api/data-tasks/{task_id}",
        f"{base_url}/api/data-tasks/{task_id}/manifest",
        f"{base_url}/api/downloads/{task_id}/manifest.json",
    ]
    for output_path in flow["output_paths"]:
        encoded_path = "/".join(
            urllib.parse.quote(part, safe="")
            for part in PurePosixPath(str(output_path)).parts
        )
        owner_urls.append(f"{base_url}/api/downloads/{encoded_path}")
    if any(_http_request(url, token=owner_token)[0] != 200 for url in owner_urls):
        raise AcceptanceError("restore_owner_read_failed")
    if any(_http_request(url, token=other_token)[0] != 404 for url in owner_urls):
        raise AcceptanceError("restore_cross_owner_visible")


def _backup_restore_probe(
    *,
    project_root: Path,
    compose_path: Path,
    source_container: str,
    restore_project: str,
    restore_port: int,
    image: str,
    image_tag: str,
    model_base_url: str,
    model_name: str,
    model_timeout_seconds: int,
    run_root: Path,
    flow: dict[str, object],
) -> tuple[list[dict[str, object]], bool]:
    backup_root = run_root / "backup-staging"
    if backup_root.exists():
        raise AcceptanceError("backup_staging_exists")
    backup_root.mkdir(parents=True)
    snapshot_dir = "/app/data/.acceptance-snapshot"
    snapshot_script = (
        "import pathlib,sqlite3;"
        f"out=pathlib.Path('{snapshot_dir}');out.mkdir();"
        "s=sqlite3.connect('file:/app/data/webui.db?mode=ro',uri=True);"
        "d=sqlite3.connect(str(out/'webui.db'));s.backup(d);d.close();s.close();"
        "s=sqlite3.connect('file:/app/data/scheduler.db?mode=ro',uri=True);"
        "d=sqlite3.connect(str(out/'scheduler.db'));s.backup(d);d.close();s.close()"
    )
    restore_environment = _compose_environment(
        port=restore_port,
        image_tag=image_tag,
        model_base_url=model_base_url,
        model_name=model_name,
        model_timeout_seconds=model_timeout_seconds,
    )
    cleaned = True
    try:
        _run(
            ["docker", "exec", source_container, "python", "-c", snapshot_script],
            cwd=project_root,
            error_code="sqlite_snapshot",
        )
        _run(
            ["docker", "cp", f"{source_container}:/app/data", str(backup_root)],
            cwd=project_root,
            error_code="backup_data_copy",
        )
        databases = backup_root / "consistent-databases"
        databases.mkdir()
        _run(
            ["docker", "cp", f"{source_container}:{snapshot_dir}/.", str(databases)],
            cwd=project_root,
            error_code="backup_database_copy",
        )
        embedded = backup_root / "data" / ".acceptance-snapshot"
        if embedded.is_dir():
            shutil.rmtree(embedded)
        _run(
            [
                "docker",
                "exec",
                source_container,
                "python",
                "-c",
                f"import shutil;shutil.rmtree('{snapshot_dir}')",
            ],
            cwd=project_root,
            error_code="snapshot_cleanup",
        )
        if any(
            _sqlite_snapshot_probe(databases / name) != "ok"
            for name in ("webui.db", "scheduler.db")
        ):
            raise AcceptanceError("sqlite_snapshot_invalid")
        _run(
            _compose_command(compose_path, restore_project, "create", "app"),
            cwd=project_root,
            env=restore_environment,
            error_code="restore_create",
        )
        restore_container = _find_app_container(project_root, restore_project)
        inspect = json.loads(
            _run(
                ["docker", "inspect", restore_container],
                cwd=project_root,
                error_code="restore_inspect",
            ).stdout
        )[0]
        data_mounts = [
            item
            for item in inspect.get("Mounts", [])
            if item.get("Destination") == "/app/data" and item.get("Type") == "volume"
        ]
        if len(data_mounts) != 1 or not data_mounts[0].get("Name"):
            raise AcceptanceError("restore_volume_ambiguous")
        volume = str(data_mounts[0]["Name"])
        helper_script = (
            "import os,pathlib,shutil;target=pathlib.Path('/target');"
            "shutil.copytree('/source',target,dirs_exist_ok=True);"
            "shutil.copy2('/databases/webui.db',target/'webui.db');"
            "shutil.copy2('/databases/scheduler.db',target/'scheduler.db');"
            "[os.chown(p,10001,10001) for p in [target,*target.rglob('*')]]"
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--entrypoint",
                "python",
                "--volume",
                f"{volume}:/target",
                "--mount",
                f"type=bind,source={backup_root / 'data'},target=/source,readonly",
                "--mount",
                f"type=bind,source={databases},target=/databases,readonly",
                image,
                "-c",
                helper_script,
            ],
            cwd=project_root,
            error_code="restore_copy",
        )
        _run(
            ["docker", "start", restore_container],
            cwd=project_root,
            error_code="restore_start",
        )
        restore_base_url = f"http://127.0.0.1:{restore_port}"
        _wait_readiness(restore_base_url)
        if _state_probe(project_root, source_container) != _state_probe(project_root, restore_container):
            raise AcceptanceError("restore_database_state_changed")
        if _business_hash_probe(project_root, source_container) != _business_hash_probe(
            project_root, restore_container
        ):
            raise AcceptanceError("restore_business_hash_changed")
        _restore_public_probe(base_url=restore_base_url, flow=flow)
    finally:
        try:
            _run(
                [
                    "docker",
                    "exec",
                    source_container,
                    "python",
                    "-c",
                    f"import pathlib,shutil;p=pathlib.Path('{snapshot_dir}');shutil.rmtree(p) if p.exists() else None",
                ],
                cwd=project_root,
                error_code="snapshot_final_cleanup",
            )
        except AcceptanceError:
            cleaned = False
        if not _cleanup_project(
            project_root=project_root,
            compose_path=compose_path,
            project=restore_project,
            environment=restore_environment,
        ):
            cleaned = False
        resolved_backup = backup_root.resolve()
        if run_root.resolve() not in resolved_backup.parents:
            cleaned = False
        elif resolved_backup.exists():
            shutil.rmtree(resolved_backup)
    return (
        [
            _check("BACKUP-SQLITE-001", "passed", "两个 SQLite 在线快照 quick_check 通过"),
            _check("BACKUP-HASH-001", "passed", "恢复前后业务文件大小和 SHA-256 全部一致"),
            _check("RESTORE-OWNER-001", "passed", "全新卷恢复后 Owner 可读可下载，跨 Owner 全部拒绝"),
        ],
        cleaned,
    )


def _execute_full_acceptance(
    *,
    args: argparse.Namespace,
    project_root: Path,
    compose_path: Path,
    run_root: Path,
) -> int:
    _validate_full_arguments(args)
    reports_root = run_root / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    image_tag = _image_tag(args.run_id)
    image = f"mangrove-phase4b-8b1:{image_tag}"
    normal_project = _project_name(args.run_id, "main")
    fault_project = _project_name(args.run_id, "fault")
    restore_project = _project_name(args.run_id, "restore")
    normal_environment = _compose_environment(
        port=args.port,
        image_tag=image_tag,
        model_base_url=args.model_base_url,
        model_name=args.model_name,
        model_timeout_seconds=args.model_timeout_seconds,
    )
    result_files: list[Path] = []
    core_ok = False
    flow_ok = False
    normal_container = ""
    normal_base_url = ""
    flow: dict[str, object] = {}

    config_path = reports_root / "01-config.json"
    _write_phase_result(
        config_path,
        run_id=args.run_id,
        checks=[
            _check("CONFIG-COMPOSE-001", "passed", "验收 Compose 配置有效"),
            _check("CONFIG-MODEL-001", "passed", "模型地址和超时参数通过低敏校验"),
        ],
    )
    result_files.append(config_path)

    build_path = reports_root / "02-build-core.json"
    try:
        _run(
            [
                "docker",
                "build",
                "--file",
                str(compose_path.parent / "Dockerfile"),
                "--tag",
                image,
                ".",
            ],
            cwd=project_root,
            timeout=BUILD_TIMEOUT_SECONDS,
            error_code="image_build",
        )
        normal_container, normal_base_url, readiness = _start_project(
            project_root=project_root,
            compose_path=compose_path,
            project=normal_project,
            environment=normal_environment,
        )
        build_checks = _container_contract(project_root, normal_container, readiness, image)
        core_ok = True
    except (AcceptanceError, json.JSONDecodeError, KeyError, TypeError):
        build_checks = [
            _check(
                "CORE-START-001",
                "failed",
                "干净镜像构建或核心启动失败",
                remediation="检查 Docker、依赖和 readiness",
            )
        ]
    _write_phase_result(build_path, run_id=args.run_id, checks=build_checks)
    result_files.append(build_path)

    flow_path = reports_root / "03-flow-concurrency.json"
    if core_ok:
        try:
            flow = _run_playwright_flow(
                project_root=project_root,
                base_url=normal_base_url,
                reports_root=reports_root,
                model_name=args.model_name,
            )
            flow_checks = list(flow["checks"])
            flow_checks.append(
                _restart_probe(
                    project_root=project_root,
                    container=normal_container,
                    base_url=normal_base_url,
                )
            )
            flow_ok = True
        except (AcceptanceError, OSError, json.JSONDecodeError, KeyError, TypeError):
            flow_checks = [
                _check(
                    "FLOW-REAL-001",
                    "failed",
                    "真实闭环、并发或重启恢复失败",
                    remediation="查看本次隔离 Playwright 结果",
                )
            ]
    else:
        flow_checks = [
            _check(
                "FLOW-REAL-001",
                "not_run",
                "核心环境未就绪，真实闭环未执行",
                remediation="先修复核心启动",
            )
        ]
    _write_phase_result(flow_path, run_id=args.run_id, checks=flow_checks)
    result_files.append(flow_path)

    fault_path = reports_root / "04-fault.json"
    if core_ok:
        try:
            fault_checks, _ = _run_fault_flow(
                project_root=project_root,
                compose_path=compose_path,
                project=fault_project,
                port=args.port + 1,
                image_tag=image_tag,
            )
        except (AcceptanceError, OSError):
            fault_checks = [
                _check(
                    "FAULT-NET-001",
                    "failed",
                    "隔离网络超时故障验证失败",
                    remediation="检查单次请求和失败关闭规则",
                )
            ]
    else:
        fault_checks = [
            _check(
                "FAULT-NET-001",
                "not_run",
                "核心环境未就绪，网络故障未执行",
                remediation="先修复核心启动",
            )
        ]
    _write_phase_result(fault_path, run_id=args.run_id, checks=fault_checks)
    result_files.append(fault_path)

    backup_path = reports_root / "05-backup-restore.json"
    if flow_ok:
        try:
            backup_checks, _ = _backup_restore_probe(
                project_root=project_root,
                compose_path=compose_path,
                source_container=normal_container,
                restore_project=restore_project,
                restore_port=args.port + 2,
                image=image,
                image_tag=image_tag,
                model_base_url=args.model_base_url,
                model_name=args.model_name,
                model_timeout_seconds=args.model_timeout_seconds,
                run_root=run_root,
                flow=flow,
            )
        except (AcceptanceError, OSError, sqlite3.Error):
            backup_checks = [
                _check(
                    "BACKUP-RESTORE-001",
                    "failed",
                    "隔离备份恢复闭环失败",
                    remediation="检查 SQLite 快照、文件哈希和 Owner 复验",
                )
            ]
    else:
        backup_checks = [
            _check(
                "BACKUP-RESTORE-001",
                "not_run",
                "真实闭环未通过，备份恢复未执行",
                remediation="先修复真实闭环",
            )
        ]
    _write_phase_result(backup_path, run_id=args.run_id, checks=backup_checks)
    result_files.append(backup_path)

    cleanup_ok = _best_effort_full_cleanup(
        project_root=project_root,
        compose_path=compose_path,
        projects=(normal_project, fault_project, restore_project),
        environment=normal_environment,
        image=image,
        run_root=run_root,
    )
    cleanup_path = reports_root / "06-cleanup-server.json"
    cleanup_checks = [
        _check(
            "CLEANUP-001",
            "passed" if cleanup_ok else "failed",
            "本次容器、网络、卷、临时备份和镜像已清理" if cleanup_ok else "本次隔离资源未完全清理",
            remediation=None if cleanup_ok else "只按本次 Compose project 身份清理",
        ),
        _check("SERVER-LINUX-001", "pending_8b2", "目标 Linux 服务器尚未提供"),
        _check("SERVER-GPU-001", "pending_8b2", "目标 GPU、驱动和 CUDA 尚未实机验证"),
        _check("SERVER-LOAD-001", "pending_8b2", "生产并发和容量尚未实机验证"),
        _check("SERVER-LONGRUN-001", "pending_8b2", "长期运行尚未实机验证"),
        _check("SERVER-DR-001", "pending_8b2", "RAID 与灾难恢复尚未实机验证"),
        _check("LAN-FLOW-001", "pending_8b2", "另一台可信 LAN PC 尚未人工验收"),
    ]
    _write_phase_result(cleanup_path, run_id=args.run_id, checks=cleanup_checks)
    result_files.append(cleanup_path)

    summary = build_report(
        result_files,
        output_markdown=reports_root / "acceptance.md",
        output_html=reports_root / "acceptance.html",
    )
    _write_json(
        reports_root / "summary.json",
        {
            "schema_version": CHECK_SCHEMA_VERSION,
            "run_id": summary.run_id,
            "status": summary.status,
            "pending_8b2": summary.pending_8b2,
            "checks": list(summary.checks),
        },
    )
    return 0 if summary.status == "passed" else 2


def _run_full_acceptance(
    *,
    args: argparse.Namespace,
    project_root: Path,
    compose_path: Path,
    run_root: Path,
) -> int:
    image_tag = _image_tag(args.run_id)
    image = f"mangrove-phase4b-8b1:{image_tag}"
    projects = (
        _project_name(args.run_id, "main"),
        _project_name(args.run_id, "fault"),
        _project_name(args.run_id, "restore"),
    )
    environment = _compose_environment(
        port=args.port,
        image_tag=image_tag,
        model_base_url=args.model_base_url,
        model_name=args.model_name,
        model_timeout_seconds=args.model_timeout_seconds,
    )
    try:
        return _execute_full_acceptance(
            args=args,
            project_root=project_root,
            compose_path=compose_path,
            run_root=run_root,
        )
    finally:
        # 即使验收脚本自身异常，也只按本次唯一 project 和镜像身份回收。
        _best_effort_full_cleanup(
            project_root=project_root,
            compose_path=compose_path,
            projects=projects,
            environment=environment,
            image=image,
            run_root=run_root,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if RUN_ID_PATTERN.fullmatch(args.run_id) is None:
        print("run-id 只允许 1 到 64 位字母、数字、下划线或连字符", file=sys.stderr)
        return 2
    project_root = args.project_root.resolve()
    acceptance_root = project_root / "runtime" / "acceptance"
    run_root = acceptance_root / args.run_id
    report_path = run_root / "reports" / "summary.json"
    lock_path = acceptance_root / ".locks" / f"{_resource_identity(args.run_id)}.lock"
    try:
        with _exclusive_run_lock(lock_path):
            if run_root.exists():
                raise AcceptanceError("run_id_exists")
            compose_path = project_root / "docker" / "phase4b" / "compose.acceptance.yaml"
            if not compose_path.is_file():
                _write_json(
                    report_path,
                    _preflight_report(
                        run_id=args.run_id,
                        mode=args.mode,
                        status="failed",
                        checks=[
                            {
                                "id": "CONFIG-COMPOSE-FILE",
                                "status": "failed",
                                "detail": "compose_file_missing",
                            }
                        ],
                    ),
                )
                return 2
            checks = [
                {
                    "id": "CONFIG-COMPOSE-FILE",
                    "status": "passed",
                    "detail": "compose_file_present",
                }
            ]
            compose_environment = _compose_environment(
                port=args.port,
                image_tag=_image_tag(args.run_id),
                model_base_url=args.model_base_url,
                model_name=args.model_name,
                model_timeout_seconds=args.model_timeout_seconds,
            )
            compose_check = _compose_check(
                compose_path,
                project_root,
                compose_environment,
            )
            checks.append(compose_check)
            if compose_check["status"] != "passed":
                _write_json(
                    report_path,
                    _preflight_report(
                        run_id=args.run_id,
                        mode=args.mode,
                        status="failed",
                        checks=checks,
                    ),
                )
                return 2
            if args.mode == "preflight":
                _write_json(
                    report_path,
                    _preflight_report(
                        run_id=args.run_id,
                        mode=args.mode,
                        status="passed",
                        checks=checks,
                    ),
                )
                return 0
            return _run_full_acceptance(
                args=args,
                project_root=project_root,
                compose_path=compose_path,
                run_root=run_root,
            )
    except (AcceptanceError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        print("验收脚本发生未处理错误，已按本次运行身份清理", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
