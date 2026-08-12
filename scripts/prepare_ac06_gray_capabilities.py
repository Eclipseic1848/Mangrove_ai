# -*- coding: utf-8 -*-
"""准备并验证 AC-06 管理员灰度能力包。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.capability_adapters import (
    CapabilityRuntimeManifest,
    LocalMcpAdapter,
    NodeAdapter,
)
from src.capability_catalog import OrasOciLayoutStore, SqliteCapabilityCatalogRepository
from src.capability_host import CapabilityHost, CapabilityHostRequest
from src.config.settings import settings
from src.conversation_steering import CapabilityMaturity, CapabilityPack, ProcedureScope


EVERYTHING_VERSION = "2026.7.4"
ARTIFACT_TYPE = "application/vnd.mangrove.capability.pack.v1"
LAYER_MEDIA_TYPE = "application/vnd.mangrove.capability.archive.v1+tar"


def _run(argv: tuple[str, ...], *, cwd: Path, timeout: int = 300) -> None:
    executable = shutil.which(argv[0]) or argv[0]
    completed = subprocess.run(
        (executable, *argv[1:]),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"命令失败 {argv!r}\n{completed.stdout[-1000:]}\n{completed.stderr[-1000:]}"
        )


def _command_output(argv: tuple[str, ...]) -> str:
    executable = shutil.which(argv[0]) or argv[0]
    return subprocess.check_output(
        (executable, *argv[1:]),
        text=True,
        encoding="utf-8",
    ).strip()


def _write_python_pack(root: Path) -> dict[str, str]:
    root.mkdir(parents=True)
    script = root / "table_summary.py"
    script.write_text(
        """# -*- coding: utf-8 -*-
import csv
import io
import json
import sys

if sys.argv[1:] == [\"--health\"]:
    print(\"ok\")
    raise SystemExit(0)
payload = json.loads(sys.argv[1])
group_field = str(payload[\"group_field\"])
value_field = str(payload[\"value_field\"])
rows = list(csv.DictReader(io.StringIO(str(payload[\"csv\"]))))
totals = {}
for row in rows:
    key = str(row[group_field]).strip()
    totals[key] = totals.get(key, 0.0) + float(str(row[value_field]).replace(\",\", \"\"))
print(json.dumps({\"groups\": totals, \"row_count\": len(rows)}, ensure_ascii=False, sort_keys=True))
""",
        encoding="utf-8",
    )
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "python-table-summary",
            "version": "1.0.0",
            "kind": "python",
            "purpose": "按指定字段对 CSV 行进行确定性分组汇总",
            "entrypoint": {
                "program": "python",
                "arguments": ["table_summary.py"],
                "timeout_seconds": 30,
            },
            "healthcheck": {
                "program": "python",
                "arguments": ["table_summary.py", "--health"],
                "timeout_seconds": 10,
            },
            "permissions": ["process:child", "network:none"],
        }
    )
    (root / "mangrove-capability.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    sample = json.dumps(
        {
            "csv": "部门,金额\n研发,10\n市场,20\n研发,5\n",
            "group_field": "部门",
            "value_field": "金额",
        },
        ensure_ascii=False,
    )
    completed = subprocess.run(
        (sys.executable, str(script), sample),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Python 表格能力无法运行："
            + (completed.stderr or completed.stdout)[-1000:]
        )
    result = json.loads(completed.stdout)
    if result != {"groups": {"市场": 20.0, "研发": 15.0}, "row_count": 3}:
        raise RuntimeError("Python 表格能力真实样例校验失败")
    return {
        "pack_id": "gray-python-table",
        "version": "1.0.0",
        "display_name": "Python 表格汇总",
        "kind": "tool",
        "purpose": manifest.purpose,
        "source": "Mangrove 内置确定性 Tool",
    }


async def _write_mcp_pack(root: Path) -> dict[str, str]:
    root.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "mangrove-ac06-everything-mcp",
                "version": "1.0.0",
                "private": True,
                "dependencies": {
                    "@modelcontextprotocol/server-everything": EVERYTHING_VERSION,
                    "@modelcontextprotocol/client": "2.0.0",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _run(
        ("npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"),
        cwd=root,
    )
    plan = NodeAdapter(
        node_version=_command_output(("node", "--version")),
        npm_version=_command_output(("npm", "--version")),
    ).prepare(root)
    for command in plan.commands:
        _run(command.argv, cwd=root)
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "everything-mcp",
            "version": EVERYTHING_VERSION,
            "kind": "mcp_local",
            "purpose": "验证并调用本地 MCP 标准工具协议",
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
    try:
        await adapter.prepare()
        tools = await adapter.list_tools()
        result = await adapter.invoke("echo", {"message": "mangrove-ac06"})
        if len(tools) < 1 or "mangrove-ac06" not in str(result):
            raise RuntimeError("Everything MCP 真实协议校验失败")
    finally:
        await adapter.cleanup()
    (root / "mangrove-capability.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return {
        "pack_id": "gray-everything-mcp",
        "version": EVERYTHING_VERSION,
        "display_name": "Everything MCP",
        "kind": "mcp_local",
        "purpose": manifest.purpose,
        "source": f"npm:@modelcontextprotocol/server-everything@{EVERYTHING_VERSION}",
    }


def _archive(root: Path, destination: Path) -> None:
    """生成内容稳定且不携带链接的能力归档。"""

    with tarfile.open(destination, "w") as bundle:
        for source in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = source.relative_to(root).as_posix()
            if source.is_symlink() and source.resolve(strict=True).is_dir():
                raise RuntimeError("能力包不得包含目录链接")
            if source.is_dir():
                info = tarfile.TarInfo(relative)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = 0
                bundle.addfile(info)
                continue
            resolved = source.resolve(strict=True)
            if root.resolve() not in resolved.parents:
                raise RuntimeError("能力包文件链接越过构建根目录")
            payload = resolved.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(payload)
            info.mode = 0o755 if relative.endswith((".py", ".js", ".mjs", ".cjs")) else 0o644
            info.mtime = 0
            bundle.addfile(info, io.BytesIO(payload))


def _register(
    repository: SqliteCapabilityCatalogRepository,
    store: OrasOciLayoutStore,
    root: Path,
    metadata: dict[str, str],
) -> dict[str, str]:
    archive = root.parent / "mangrove-capability.tar"
    _archive(root, archive)
    descriptor = store.push_file(
        archive,
        artifact_name=metadata["pack_id"],
        version=metadata["version"],
        artifact_type=ARTIFACT_TYPE,
        layer_media_type=LAYER_MEDIA_TYPE,
    )
    repository.save_pack(
        CapabilityPack(
            pack_id=metadata["pack_id"],
            version=metadata["version"],
            digest=descriptor.digest,
            scope=ProcedureScope.PLATFORM,
            maturity=CapabilityMaturity.VERIFIED,
            manifest=(
                ("display_name", metadata["display_name"]),
                ("kind", metadata["kind"]),
                ("purpose", metadata["purpose"]),
            ),
            source_provenance=(metadata["source"], descriptor.reference),
            permission_requirements=("admin_gray_only", "network:none"),
            created_by="ac06-gray-preparation",
        )
    )
    return {**metadata, "digest": descriptor.digest, "reference": descriptor.reference}


async def _verify_frozen_host(
    store: OrasOciLayoutStore,
    packs: list[dict[str, str]],
    workspace: Path,
) -> dict[str, object]:
    mounts = []
    for item in packs:
        mounts.append(
            store.materialize(
                artifact_name=item["pack_id"],
                version=item["version"],
                digest=item["digest"],
                destination=workspace / f"mounted-{item['pack_id']}",
            )
        )
    network = f"mangrove-ac06-gray-{os.getpid()}"
    _run(("docker", "network", "create", "--internal", network), cwd=PROJECT_ROOT)
    host = CapabilityHost(
        image=settings.pi_capability_host_image,
        execution_root=workspace / "hosts",
    )
    lease = None
    try:
        lease = await host.start(
            CapabilityHostRequest(
                user_id="ac06-gray-verifier",
                task_id="frozen-pack-smoke",
                revision=1,
                run_id="frozen-pack-smoke-run",
                network_name=network,
                capability_dirs=tuple(mounts),
            )
        )
        python_input = json.dumps(
            {
                "csv": "部门,金额\\n研发,10\\n市场,20\\n研发,5\\n",
                "group_field": "部门",
                "value_field": "金额",
            },
            ensure_ascii=False,
        )
        javascript = (
            "const h={authorization:'Bearer '+process.env.TOKEN,'content-type':'application/json'};"
            "const invoke=(body)=>fetch(process.env.RELAY+'/invoke',{method:'POST',headers:h,body:JSON.stringify(body)}).then(async r=>{const t=await r.text();if(!r.ok)throw new Error(t);return t});"
            "Promise.all(["
            "invoke({capability:'python-table-summary',arguments:[process.env.PY_INPUT]}),"
            "invoke({capability:'everything-mcp',tool:'echo',arguments:{message:'mangrove-ac06-frozen'}})"
            "]).then(x=>console.log(JSON.stringify(x))).catch(e=>{console.error(e);process.exit(1)});"
        )
        client_env = workspace / "capability-client.env"
        client_env.write_text(
            f"RELAY={lease.relay_url}\n"
            f"TOKEN={lease.relay_token}\n"
            f"PY_INPUT={python_input}\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            (
                shutil.which("docker") or "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--env-file",
                str(client_env),
                settings.pi_capability_host_image,
                "node",
                "-e",
                javascript,
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
        )
        combined = completed.stdout + completed.stderr
        if (
            completed.returncode != 0
            or "mangrove-ac06-frozen" not in combined
            or "row_count" not in combined
        ):
            raise RuntimeError("冻结能力 Sidecar 真实调用失败：" + combined[-1500:])
        return {
            "one_sidecar": True,
            "python_invoked": True,
            "mcp_invoked": True,
            "capabilities": list(lease.capability_names),
        }
    finally:
        if lease is not None:
            await host.stop(lease)
        subprocess.run(
            (shutil.which("docker") or "docker", "network", "rm", network),
            check=False,
            capture_output=True,
        )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="写入本机 OCI 目录和能力目录数据库")
    parser.add_argument("--output", default=".artifacts/ac06-gray/prepared-capabilities.json")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("必须显式传入 --apply，避免误写本机灰度目录")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(settings.webui_db_path).resolve()
    if db_path.is_file():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(db_path, output.parent / f"webui-before-ac06-{stamp}.db")

    with tempfile.TemporaryDirectory(prefix="mangrove-ac06-gray-") as temporary:
        workspace = Path(temporary)
        python_metadata = _write_python_pack(workspace / "python")
        mcp_metadata = await _write_mcp_pack(workspace / "mcp")
        repository = SqliteCapabilityCatalogRepository(str(db_path))
        store = OrasOciLayoutStore(
            settings.capability_oci_layout_path,
            layout_id="mangrove-capabilities",
        )
        packs = [
            _register(repository, store, workspace / "python", python_metadata),
            _register(repository, store, workspace / "mcp", mcp_metadata),
        ]
        result = {
            "schema_version": 1,
            "status": "prepared_admin_gray_only",
            "default_enabled": bool(settings.pi_capability_host_enabled),
            "packs": packs,
            "frozen_host_smoke": await _verify_frozen_host(
                store,
                packs,
                workspace,
            ),
        }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
