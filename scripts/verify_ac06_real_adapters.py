# -*- coding: utf-8 -*-
"""用无 Secret 合成数据复核 AC-06 五类真实 Adapter。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.capability_adapters import (
    CapabilityRuntimeManifest,
    CliAdapter,
    CommandCapabilityAdapter,
    LocalMcpAdapter,
    NodeAdapter,
    PythonAdapter,
    SkillAdapter,
)
from src.agentic_runtime.models import PiRuntimeRequest, SourceInput
from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError
from src.capability_host import CapabilityHost, CapabilityHostRequest


PYGMENTS_VERSION = "2.19.2"
PRETTIER_VERSION = "3.9.6"
EVERYTHING_VERSION = "2026.7.4"
FD_VERSION = "10.4.2"
FD_ASSET = f"fd-v{FD_VERSION}-x86_64-unknown-linux-gnu.tar.gz"
FD_URL = f"https://github.com/sharkdp/fd/releases/download/v{FD_VERSION}/{FD_ASSET}"
FD_ASSET_DIGEST = "sha256:def59805cd14b5651b68990855f426ad087f3b96881296d963910431ba3143c8"
SKILLS_REF_COMMIT = "217be548739f21d6008915c29aefe320ea1a90af"


def _run(argv: tuple[str, ...], *, cwd: Path, timeout: int = 300) -> float:
    executable = shutil.which(argv[0]) or argv[0]
    started = time.perf_counter()
    completed = subprocess.run(
        (executable, *argv[1:]),
        cwd=cwd,
        env={**os.environ, "PYTHONUTF8": "1"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"命令失败 {argv!r}\n{completed.stdout[-1000:]}\n{completed.stderr[-1000:]}"
        )
    return round((time.perf_counter() - started) * 1000, 2)


def _output(argv: tuple[str, ...]) -> str:
    executable = shutil.which(argv[0]) or argv[0]
    return subprocess.check_output(
        (executable, *argv[1:]),
        text=True,
        encoding="utf-8",
    ).strip()


def _output_in(argv: tuple[str, ...], *, cwd: Path, timeout: int = 120) -> str:
    executable = shutil.which(argv[0]) or argv[0]
    completed = subprocess.run(
        (executable, *argv[1:]),
        cwd=cwd,
        env={**os.environ, "PYTHONUTF8": "1"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout[-1000:] + completed.stderr[-1000:])
    return completed.stdout.strip()


def _size(root: Path) -> int:
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("Release 归档包含越界路径或链接")
        bundle.extractall(destination, filter="data")


async def _verify_python(root: Path) -> dict[str, object]:
    root.mkdir()
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "mangrove-ac06-pygments"\n'
        'version = "1.0.0"\n'
        f'requires-python = "=={sys.version_info.major}.{sys.version_info.minor}.*"\n'
        f'dependencies = ["pygments=={PYGMENTS_VERSION}"]\n',
        encoding="utf-8",
    )
    (root / ".python-version").write_text(version + "\n", encoding="utf-8")
    env = {**os.environ, "UV_PYTHON": sys.executable, "PYTHONUTF8": "1"}
    lock_started = time.perf_counter()
    completed = subprocess.run(
        ("uv", "lock", "--python", sys.executable),
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    lock_ms = round((time.perf_counter() - lock_started) * 1000, 2)
    plan = PythonAdapter().prepare(root)
    timings = []
    for command in plan.commands:
        timings.append(_run(command.argv, cwd=root))
    hot_ms = sum(_run(command.argv, cwd=root) for command in plan.commands)
    python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site_packages = root / ".venv" / (
        "Lib/site-packages" if sys.platform == "win32" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    vendor = root / "vendor"
    vendor.mkdir()
    shutil.copytree(site_packages / "pygments", vendor / "pygments")
    relative_python = python.relative_to(root).as_posix()
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "pygments",
            "version": PYGMENTS_VERSION,
            "kind": "python",
            "purpose": "语法高亮",
            "entrypoint": {
                "program": relative_python,
                "arguments": [
                    "-c",
                    "from pygments import highlight; from pygments.lexers import PythonLexer; from pygments.formatters import HtmlFormatter; print(highlight('x=1', PythonLexer(), HtmlFormatter()))",
                ],
            },
            "healthcheck": {
                "program": relative_python,
                "arguments": ["-c", "import pygments; print(pygments.__version__)"],
            },
            "permissions": ["process:child", "network:none"],
        }
    )
    runtime = CommandCapabilityAdapter(root, manifest)
    await runtime.prepare()
    health = await runtime.health()
    output = await runtime.invoke()
    await runtime.cleanup()
    return {
        "sample": f"Pygments {PYGMENTS_VERSION}",
        "runtime": plan.runtime_identity,
        "lock_ms": lock_ms,
        "cold_sync_ms": round(sum(timings), 2),
        "hot_sync_ms": round(hot_ms, 2),
        "bytes": _size(root),
        "health": health.stdout.strip(),
        "deterministic_output": '<span class="n">x</span>' in output.stdout,
    }


async def _verify_node(root: Path) -> dict[str, object]:
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "mangrove-ac06-prettier",
                "version": "1.0.0",
                "private": True,
                "dependencies": {"prettier": PRETTIER_VERSION},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    lock_ms = _run(
        ("npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"),
        cwd=root,
    )
    node_version = _output(("node", "--version"))
    npm_version = _output(("npm", "--version"))
    plan = NodeAdapter(
        node_version=node_version,
        npm_version=npm_version,
    ).prepare(root)
    cold_ms = sum(_run(command.argv, cwd=root) for command in plan.commands)
    hot_ms = sum(_run(command.argv, cwd=root) for command in plan.commands)
    (root / "sample.json").write_text('{"b":2,"a":1}', encoding="utf-8")
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "prettier",
            "version": PRETTIER_VERSION,
            "kind": "node",
            "purpose": "格式化 JSON",
            "entrypoint": {
                "program": "node",
                "arguments": ["node_modules/prettier/bin/prettier.cjs"],
            },
            "healthcheck": {
                "program": "node",
                "arguments": ["node_modules/prettier/bin/prettier.cjs", "--version"],
            },
            "permissions": ["process:child", "network:none", "workspace:read"],
        }
    )
    runtime = CommandCapabilityAdapter(
        root,
        manifest,
        runtime_aliases={"node": shutil.which("node") or "node"},
    )
    await runtime.prepare()
    health = await runtime.health()
    output = await runtime.invoke(("--parser", "json", "sample.json"))
    await runtime.cleanup()
    return {
        "sample": f"Prettier {PRETTIER_VERSION}",
        "runtime": plan.runtime_identity,
        "lock_ms": lock_ms,
        "cold_ci_ms": round(cold_ms, 2),
        "hot_ci_ms": round(hot_ms, 2),
        "bytes": _size(root),
        "health": health.stdout.strip(),
        "deterministic_output": '"a": 1' in output.stdout,
        "install_scripts": "blocked",
    }


async def _verify_cli(root: Path) -> dict[str, object]:
    root.mkdir()
    archive = root / FD_ASSET
    request = urllib.request.Request(FD_URL, headers={"User-Agent": "Mangrove-AC06/1.0"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        archive.write_bytes(response.read())
    download_ms = round((time.perf_counter() - started) * 1000, 2)
    unpacked = root / "unpacked"
    unpacked.mkdir()
    _safe_extract_tar(archive, unpacked)
    binary = next(unpacked.rglob("fd"))
    entry_digest = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    prepared = CliAdapter().prepare(
        unpacked,
        entrypoint=binary.relative_to(unpacked).as_posix(),
        expected_digest=entry_digest,
        platform="linux",
        architecture="x86_64",
        source_ref=f"sharkdp/fd@v{FD_VERSION}/{FD_ASSET}",
        asset_path=archive,
        expected_asset_digest=FD_ASSET_DIGEST,
    )
    return {
        "sample": f"fd {FD_VERSION}",
        "download_ms": download_ms,
        "bytes": _size(unpacked),
        "asset_digest": prepared.asset_digest,
        "entry_digest": prepared.digest,
        "health": "由固定 Pi Linux 任务镜像验证",
    }


async def _verify_skill(root: Path) -> dict[str, object]:
    skill = root / "invoice-fields"
    references = skill / "references"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: invoice-fields\ndescription: 将发票整理为固定 JSON 字段\n---\n\n"
        "读取 references/fields.md，并只输出其中声明的字段。\n",
        encoding="utf-8",
    )
    (references / "fields.md").write_text(
        "字段：invoice_no、amount、seller。\n",
        encoding="utf-8",
    )
    prepared = SkillAdapter().prepare(skill)
    validate_ms = _run(
        (
            "uvx",
            "--from",
            f"git+https://github.com/agentskills/agentskills.git@{SKILLS_REF_COMMIT}#subdirectory=skills-ref",
            "skills-ref",
            "validate",
            str(skill),
        ),
        cwd=root,
        timeout=300,
    )
    return {
        "sample": "Agent Skills 最小规范样本",
        "name": prepared.name,
        "skills_ref_commit": SKILLS_REF_COMMIT,
        "validate_ms": validate_ms,
        "bytes": _size(skill),
        "scripts": "none",
    }


async def _verify_mcp(root: Path) -> dict[str, object]:
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "mangrove-ac06-everything-mcp",
                "version": "1.0.0",
                "private": True,
                "dependencies": {
                    "@modelcontextprotocol/server-everything": EVERYTHING_VERSION,
                    "@modelcontextprotocol/client": "2.0.0",
                    "@modelcontextprotocol/server": "2.0.0",
                    "zod": "4.2.0",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    lock_ms = _run(
        ("npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"),
        cwd=root,
    )
    plan = NodeAdapter(
        node_version=_output(("node", "--version")),
        npm_version=_output(("npm", "--version")),
    ).prepare(root)
    cold_ms = sum(_run(command.argv, cwd=root) for command in plan.commands)
    hot_ms = sum(_run(command.argv, cwd=root) for command in plan.commands)
    (root / "modern-server.mjs").write_text(
        "import { McpServer } from '@modelcontextprotocol/server';\n"
        "import { serveStdio } from '@modelcontextprotocol/server/stdio';\n"
        "import * as z from 'zod/v4';\n"
        "await serveStdio(() => {\n"
        "  const server = new McpServer({ name: 'mangrove-modern-fixture', version: '1.0.0' });\n"
        "  server.registerTool('echo-modern', { inputSchema: z.object({ text: z.string() }) }, async ({ text }) => ({ content: [{ type: 'text', text }] }));\n"
        "  return server;\n"
        "});\n",
        encoding="utf-8",
    )
    (root / "protocol-check.cjs").write_text(
        "const { Client } = require('@modelcontextprotocol/client');\n"
        "const { StdioClientTransport } = require('@modelcontextprotocol/client/stdio');\n"
        "async function check(args) {\n"
        "  const client = new Client({ name: 'mangrove-smoke', version: '1.0.0' }, { versionNegotiation: { mode: 'auto', probe: { timeoutMs: 10000, maxRetries: 0 } } });\n"
        "  await client.connect(new StdioClientTransport({ command: process.execPath, args, cwd: process.cwd(), stderr: 'pipe' }));\n"
        "  const era = client.getProtocolEra();\n"
        "  await client.close();\n"
        "  return era;\n"
        "}\n"
        "(async () => {\n"
        "  const legacy = await check(['node_modules/@modelcontextprotocol/server-everything/dist/index.js']);\n"
        "  const modern = await check(['modern-server.mjs']);\n"
        "  console.log(JSON.stringify({ legacy, modern }));\n"
        "})().catch(error => { console.error(error); process.exit(1); });\n",
        encoding="utf-8",
    )
    protocol_eras = json.loads(
        _output_in(("node", "protocol-check.cjs"), cwd=root).splitlines()[-1]
    )
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "everything-mcp",
            "version": EVERYTHING_VERSION,
            "kind": "mcp_local",
            "purpose": "MCP 协议一致性验证",
            "entrypoint": {
                "program": "node",
                "arguments": [
                    "node_modules/@modelcontextprotocol/server-everything/dist/index.js"
                ],
                "timeout_seconds": 30,
            },
            "permissions": ["process:child", "mcp:stdio", "network:none"],
        }
    )
    adapter = LocalMcpAdapter(
        root,
        manifest,
        runtime_aliases={"node": shutil.which("node") or "node"},
    )
    started = time.perf_counter()
    await adapter.prepare()
    startup_ms = round((time.perf_counter() - started) * 1000, 2)
    tools = await adapter.list_tools()
    health = await adapter.health()
    session = adapter.session_identity
    result = await adapter.invoke("echo", {"message": "mangrove"})
    reused = adapter.session_identity == session
    await adapter.cleanup()
    (root / "mangrove-capability.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return {
        "sample": f"Everything MCP {EVERYTHING_VERSION}",
        "lock_ms": lock_ms,
        "cold_ci_ms": round(cold_ms, 2),
        "hot_ci_ms": round(hot_ms, 2),
        "startup_ms": startup_ms,
        "bytes": _size(root),
        "health": health,
        "tool_count": len(tools),
        "echo": "mangrove" in str(result),
        "session_reused": reused,
        "session_closed": adapter.session_identity is None,
        "protocol_eras": protocol_eras,
    }


async def _verify_mcp_capability_host(root: Path, runtime_root: Path) -> dict[str, object]:
    network = f"mangrove-cap-host-smoke-{os.getpid()}"
    _run(("docker", "network", "create", "--internal", network), cwd=runtime_root)
    host = CapabilityHost(
        image="mangrove/pi-coding-agent:0.80.10",
        execution_root=runtime_root / "capability-hosts",
    )
    lease = None
    try:
        lease = await host.start(
            CapabilityHostRequest(
                user_id="synthetic-user",
                task_id="synthetic-mcp",
                revision=1,
                run_id="synthetic-run",
                network_name=network,
                capability_dirs=(root,),
            )
        )
        completed = subprocess.run(
            (
                "docker", "run", "--rm", "--network", network,
                "--env", f"RELAY={lease.relay_url}",
                "--env", f"TOKEN={lease.relay_token}",
                "mangrove/pi-coding-agent:0.80.10", "node", "-e",
                "fetch(process.env.RELAY+'/invoke',{method:'POST',headers:{authorization:'Bearer '+process.env.TOKEN,'content-type':'application/json'},body:JSON.stringify({capability:'everything-mcp',tool:'echo',arguments:{message:'mangrove-host'}})}).then(async r=>{console.log(await r.text());if(!r.ok)process.exit(1)})",
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if completed.returncode != 0 or "mangrove-host" not in completed.stdout:
            raise RuntimeError(completed.stdout[-1000:] + completed.stderr[-1000:])
        return {
            "one_sidecar": True,
            "mcp_invoke": True,
            "business_source_mount": False,
            "model_config_mount": False,
            "container_name": lease.container_name,
        }
    finally:
        if lease is not None:
            await host.stop(lease)
        subprocess.run(
            ("docker", "network", "rm", network),
            check=False,
            capture_output=True,
        )


def _verify_production_isolation_gate(root: Path, *, node_root: Path) -> dict[str, object]:
    """证明固定镜像的挂载边界，并验证生产入口据此失败关闭。"""
    image = "mangrove/pi-coding-agent:0.80.10"
    source = root / "synthetic-source.txt"
    source.write_text("synthetic-no-secret", encoding="utf-8")
    probe = subprocess.run(
        (
            shutil.which("docker") or "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,source={source.resolve()},target=/probe/source.txt,readonly",
            "--entrypoint",
            "setpriv",
            image,
            "--reuid=65534",
            "--regid=65534",
            "--clear-groups",
            "head",
            "-c",
            "9",
            "/probe/source.txt",
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if probe.returncode != 0 or probe.stdout != "synthetic":
        raise RuntimeError("未复现 Docker Desktop bind mount 的降权可读边界")

    (node_root / "mangrove-capability.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "prettier",
                "version": PRETTIER_VERSION,
                "kind": "node",
                "purpose": "格式化 JSON",
                "entrypoint": {
                    "program": "node",
                    "arguments": ["node_modules/prettier/bin/prettier.cjs"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = root / "production-config"
    work = root / "production-work"
    config.mkdir()
    work.mkdir()
    request = PiRuntimeRequest(
        user_id="synthetic-user",
        task_id="synthetic-task",
        revision=1,
        objective_text="验证隔离门",
        requested_output_formats=("json",),
        sources=(
            SourceInput(
                upload_id="synthetic-upload",
                original_name=source.name,
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
        model="synthetic-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="synthetic-no-request",
    )
    refused = False
    try:
        PiRuntime._write_runtime_files(
            request,
            source_names=(source.name,),
            config_dir=config,
            work_dir=work,
            capability_dirs=(node_root,),
        )
    except PiRuntimeError as error:
        refused = "进程级隔离" in str(error)
    if not refused:
        raise RuntimeError("生产 Pi 入口没有拒绝未隔离的原生能力")
    return {
        "pi_image_id": _output(("docker", "image", "inspect", image, "--format", "{{.Id}}")),
        "docker_desktop_bind_mount_readable_after_uid_drop": True,
        "native_capability_fail_closed": True,
        "markdown_skill_readonly_path_remains_supported": True,
        "remote_mcp_enabled": False,
    }


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--mcp-host-only", action="store_true")
    args = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix="mangrove-ac06-"))
    report: dict[str, object] = {
        "schema_version": 1,
        "workspace": str(workspace),
        "samples": {},
    }
    try:
        samples = report["samples"]
        assert isinstance(samples, dict)
        if not args.mcp_host_only:
            samples["python"] = await _verify_python(workspace / "python")
            samples["node"] = await _verify_node(workspace / "node")
            samples["cli"] = await _verify_cli(workspace / "cli")
            samples["skill"] = await _verify_skill(workspace / "skill")
        samples["mcp_local"] = await _verify_mcp(workspace / "mcp")
        report["capability_host_mcp"] = await _verify_mcp_capability_host(
            workspace / "mcp",
            workspace,
        )
        mcp_sample = samples["mcp_local"]
        assert isinstance(mcp_sample, dict)
        protocol_eras = mcp_sample["protocol_eras"]
        assert isinstance(protocol_eras, dict)
        if not args.mcp_host_only:
            report["production_isolation_gate"] = _verify_production_isolation_gate(
                workspace,
                node_root=workspace / "node",
            )
    finally:
        if not args.keep:
            expected_parent = Path(tempfile.gettempdir()).resolve()
            resolved = workspace.resolve()
            if expected_parent not in resolved.parents or not resolved.name.startswith("mangrove-ac06-"):
                raise RuntimeError("拒绝清理未验证的临时目录")
            shutil.rmtree(resolved)
            report["temporary_workspace_removed"] = not resolved.exists()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
